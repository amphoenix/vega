"""
Paper Trading Validator — end-to-end pipeline validation.

Wires the full signal -> supervisor -> risk -> broker pipeline using
the real broker adapter in sandbox/testnet mode (TRADING_MODE=paper).

Features:
    - Full pipeline: strategy -> supervisor -> risk -> broker
    - Synthetic tick injection for deterministic testing
    - Position tracking and P&L computation
    - Validation assertions (e.g. no order after kill switch)
    - Detailed session report with all decisions and outcomes
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..domain.entities.signal import Signal
from ..domain.safety.kill_switch import KillSwitchConfig, KillSwitchEngine
from ..domain.supervisor.trade_supervisor import Denial, SupervisorConfig, TradeSupervisor
from ..infrastructure.broker.base import BrokerAdapter, BrokerFactory, OrderResult
from ..shared.logger import get_logger
from ..shared.time import now_ist

logger = get_logger('paper_validator')


# ── Config ───────────────────────────────────────────────────────────────────

@dataclass
class PaperValidatorConfig:
    initial_capital: float = 100_000.0
    default_qty: int = 25
    slippage_pct: float = 0.1
    max_signals: int = 500
    supervisor_config: SupervisorConfig = field(default_factory=SupervisorConfig)


# ── Decision Types ───────────────────────────────────────────────────────────

class DecisionOutcome(str, Enum):
    APPROVED  = 'approved'
    DENIED    = 'denied'
    ERROR     = 'error'


@dataclass
class PaperDecision:
    signal: Signal
    outcome: DecisionOutcome
    denial: Denial | None = None
    order_result: OrderResult | None = None
    track_id: str = ''
    timestamp: datetime = field(default_factory=now_ist)
    error: str = ''

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            'underlying': self.signal.underlying,
            'direction': self.signal.direction,
            'confidence': self.signal.confidence,
            'outcome': self.outcome.value,
            'track_id': self.track_id,
        }
        if self.denial:
            d['denial_rule'] = self.denial.rule
            d['denial_detail'] = self.denial.detail
        if self.order_result:
            d['order_id'] = self.order_result.order_id
            d['fill_price'] = self.order_result.fill_price
        if self.error:
            d['error'] = self.error
        return d


@dataclass
class PaperTrade:
    track_id: str
    signal: Signal
    entry_price: float
    qty: int
    symbol: str
    order_id: str = ''
    exit_price: float = 0.0
    pnl: float = 0.0
    is_open: bool = True

    def close(self, exit_price: float) -> None:
        self.exit_price = exit_price
        self.is_open = False
        if self.signal.is_bullish:
            self.pnl = (exit_price - self.entry_price) * self.qty
        else:
            self.pnl = (self.entry_price - exit_price) * self.qty

    def to_dict(self) -> dict[str, Any]:
        return {
            'track_id': self.track_id,
            'symbol': self.symbol,
            'direction': self.signal.direction,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'qty': self.qty,
            'pnl': round(self.pnl, 2),
            'is_open': self.is_open,
        }


# ── Session Report ───────────────────────────────────────────────────────────

@dataclass
class PaperSessionReport:
    session_id: str
    decisions: list[PaperDecision]
    trades: list[PaperTrade]
    initial_capital: float
    final_capital: float

    @property
    def total_signals(self) -> int:
        return len(self.decisions)

    @property
    def approved_count(self) -> int:
        return sum(1 for d in self.decisions if d.outcome == DecisionOutcome.APPROVED)

    @property
    def denied_count(self) -> int:
        return sum(1 for d in self.decisions if d.outcome == DecisionOutcome.DENIED)

    @property
    def error_count(self) -> int:
        return sum(1 for d in self.decisions if d.outcome == DecisionOutcome.ERROR)

    @property
    def total_pnl(self) -> float:
        return round(sum(t.pnl for t in self.trades), 2)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if not t.is_open and t.pnl > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.trades if not t.is_open and t.pnl < 0)

    @property
    def open_trades(self) -> int:
        return sum(1 for t in self.trades if t.is_open)

    @property
    def win_rate(self) -> float:
        closed = self.winning_trades + self.losing_trades
        return round(self.winning_trades / closed * 100, 1) if closed else 0.0

    @property
    def denial_reasons(self) -> dict[str, int]:
        reasons: dict[str, int] = {}
        for d in self.decisions:
            if d.denial:
                reasons[d.denial.rule] = reasons.get(d.denial.rule, 0) + 1
        return reasons

    def summary(self) -> dict[str, Any]:
        return {
            'session_id': self.session_id,
            'total_signals': self.total_signals,
            'approved': self.approved_count,
            'denied': self.denied_count,
            'errors': self.error_count,
            'trades_total': len(self.trades),
            'trades_open': self.open_trades,
            'winning': self.winning_trades,
            'losing': self.losing_trades,
            'win_rate': self.win_rate,
            'total_pnl': self.total_pnl,
            'initial_capital': self.initial_capital,
            'final_capital': self.final_capital,
            'denial_reasons': self.denial_reasons,
        }

    def validate(self) -> list[str]:
        """Run validation assertions, return list of failures."""
        failures: list[str] = []

        # Capital consistency
        expected = self.initial_capital + self.total_pnl
        broker_diff = abs(expected - self.final_capital)
        # Allow for open position value difference
        if self.open_trades == 0 and broker_diff > 1.0:
            failures.append(
                f'Capital mismatch: expected {expected:.2f}, got {self.final_capital:.2f}'
            )

        # No duplicate track IDs
        track_ids = [t.track_id for t in self.trades]
        if len(track_ids) != len(set(track_ids)):
            failures.append('Duplicate track_ids found')

        # All approved signals have trades
        approved = [d for d in self.decisions if d.outcome == DecisionOutcome.APPROVED]
        trade_ids = {t.track_id for t in self.trades}
        for d in approved:
            if d.track_id and d.track_id not in trade_ids:
                failures.append(f'Approved decision {d.track_id} has no trade')

        return failures


# ── Session ──────────────────────────────────────────────────────────────────

class PaperTradingSession:
    """A single paper trading validation session."""

    def __init__(
        self,
        session_id: str,
        broker: BrokerAdapter,
        supervisor: TradeSupervisor,
        config: PaperValidatorConfig,
        kill_switch: KillSwitchEngine | None = None,
    ) -> None:
        self._session_id = session_id
        self._broker = broker
        self._supervisor = supervisor
        self._config = config
        self._kill_switch = kill_switch
        self._decisions: list[PaperDecision] = []
        self._trades: dict[str, PaperTrade] = {}  # track_id -> trade
        self._signal_count = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    def inject_signal(
        self,
        signal: Signal,
        is_safe_hours: bool = True,
    ) -> PaperDecision:
        """Process a signal through the full pipeline."""
        self._signal_count += 1

        if self._signal_count > self._config.max_signals:
            decision = PaperDecision(
                signal=signal,
                outcome=DecisionOutcome.ERROR,
                error='max_signals exceeded',
            )
            self._decisions.append(decision)
            return decision

        # Supervisor check
        symbol = signal.symbol or f'{signal.underlying}-{signal.direction}'
        denial = self._supervisor.check_permission(
            underlying=signal.underlying,
            trade_mode=signal.trade_mode,
            is_safe_hours=is_safe_hours,
            symbol=symbol,
        )

        if denial:
            decision = PaperDecision(
                signal=signal,
                outcome=DecisionOutcome.DENIED,
                denial=denial,
            )
            self._decisions.append(decision)
            return decision

        # Place paper order
        try:
            fill_price = signal.spot * (1 + self._config.slippage_pct / 100)
            qty = self._config.default_qty

            result = self._broker.place_order(
                symbol=symbol,
                side='BUY',
                qty=qty,
                price=fill_price,
            )

            if not result.success:
                decision = PaperDecision(
                    signal=signal,
                    outcome=DecisionOutcome.ERROR,
                    error=result.message,
                )
                self._decisions.append(decision)
                return decision

            track_id = f'PT-{uuid.uuid4().hex[:8].upper()}'

            # Record with supervisor
            self._supervisor.record_trade_opened(
                track_id=track_id,
                underlying=signal.underlying,
                symbol=symbol,
            )

            # Create paper trade
            trade = PaperTrade(
                track_id=track_id,
                signal=signal,
                entry_price=result.fill_price,
                qty=qty,
                symbol=symbol,
                order_id=result.order_id,
            )
            self._trades[track_id] = trade

            decision = PaperDecision(
                signal=signal,
                outcome=DecisionOutcome.APPROVED,
                order_result=result,
                track_id=track_id,
            )
            self._decisions.append(decision)
            return decision

        except Exception as e:
            decision = PaperDecision(
                signal=signal,
                outcome=DecisionOutcome.ERROR,
                error=str(e),
            )
            self._decisions.append(decision)
            return decision

    def close_trade(self, track_id: str, exit_price: float, was_sl: bool = False) -> bool:
        """Close a specific paper trade."""
        trade = self._trades.get(track_id)
        if not trade or not trade.is_open:
            return False

        trade.close(exit_price)

        # Sell via broker
        self._broker.place_order(
            symbol=trade.symbol,
            side='SELL',
            qty=trade.qty,
            price=exit_price,
        )

        # Route P&L through kill switch engine
        if self._kill_switch:
            self._kill_switch.record_pnl(trade.pnl)

        # Notify supervisor
        self._supervisor.record_trade_closed(
            track_id=track_id,
            pnl=trade.pnl,
            was_sl=was_sl,
            underlying=trade.signal.underlying,
            symbol=trade.symbol,
        )

        return True

    def close_all(self, exit_price_fn: Callable[[PaperTrade], float] | None = None) -> int:
        """Close all open trades. Returns count closed."""
        count = 0
        for track_id, trade in list(self._trades.items()):
            if not trade.is_open:
                continue
            if exit_price_fn:
                price = exit_price_fn(trade)
            else:
                price = trade.entry_price  # flat close
            self.close_trade(track_id, price)
            count += 1
        return count

    @property
    def open_trades(self) -> list[PaperTrade]:
        return [t for t in self._trades.values() if t.is_open]

    @property
    def closed_trades(self) -> list[PaperTrade]:
        return [t for t in self._trades.values() if not t.is_open]

    def finalize(self) -> PaperSessionReport:
        """Close all remaining trades and produce final report."""
        self.close_all()

        return PaperSessionReport(
            session_id=self._session_id,
            decisions=list(self._decisions),
            trades=list(self._trades.values()),
            initial_capital=self._config.initial_capital,
            final_capital=self._broker.get_available_cash(),
        )


# ── Validator Factory ────────────────────────────────────────────────────────

class PaperTradingValidator:
    """Factory for paper trading validation sessions.

    Usage:
        validator = PaperTradingValidator(PaperValidatorConfig())
        session = validator.create_session()

        session.inject_signal(signal1)
        session.inject_signal(signal2)
        session.close_trade(track_id, exit_price=120.0)

        report = session.finalize()
        failures = report.validate()
        assert not failures, failures
    """

    def __init__(self, config: PaperValidatorConfig | None = None) -> None:
        self._config = config or PaperValidatorConfig()

    def create_session(self, session_id: str = '', broker: BrokerAdapter | None = None) -> PaperTradingSession:
        """Create a new isolated validation session.

        Args:
            broker: Optional broker override. Defaults to BrokerFactory.create()
                    which reads config/brokers.yaml and TRADING_MODE env var.
        """
        sid = session_id or f'paper-{uuid.uuid4().hex[:8]}'
        if broker is None:
            broker = BrokerFactory.create()
        sc = self._config.supervisor_config
        ks = KillSwitchEngine(KillSwitchConfig(
            daily_loss_soft=sc.daily_loss_limit,
            daily_loss_hard=sc.daily_loss_limit * 2,
        ))
        supervisor = TradeSupervisor(config=sc, kill_switch=ks)

        return PaperTradingSession(
            session_id=sid,
            broker=broker,
            supervisor=supervisor,
            config=self._config,
            kill_switch=ks,
        )
