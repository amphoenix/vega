"""
Risk Guard Orchestration Chain — runs all risk checks in sequence.

Pre-trade pipeline: Signal → RiskChain → approve/reject

Each guard in the chain is a pure function or stateful object that returns
a verdict. The chain short-circuits on the first rejection.

Guards (in order):
  1. KillSwitch — system-level circuit breaker
  2. TradeSupervisor — daily limits, cooldowns, max open trades
  3. ExposureGuard — net directional exposure cap
  4. RegimeGuard — market regime appropriateness
  5. BudgetGuard — daily loss headroom vs worst-case SL
  6. PositionSizer — compute final lot count (not a gate, but modifies qty)

Pure domain — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...shared.logger import get_logger
from ..safety.kill_switch import KillSwitchEngine
from ..supervisor.trade_supervisor import Denial, TradeSupervisor
from .budget_guard import BudgetVerdict, check_budget
from .exposure_guard import ExposureGuard, ExposureVerdict
from .position_sizer import SizeResult, compute_size
from .regime_guard import RegimeVerdict, check_regime

logger = get_logger('risk_chain')


@dataclass
class TradeRequest:
    """Input to the risk chain."""
    underlying: str
    direction: str              # CE or PE
    trade_mode: str             # swing or scalp
    symbol: str = ''
    premium: float = 0.0       # current option premium
    lot_size: int = 75
    desired_lots: int = 1
    sl_points: float = 15.0    # stop-loss in premium points
    is_safe_hours: bool = True
    regime: Any = None          # Regime enum from regime_engine


@dataclass
class RiskVerdict:
    """Output of the risk chain."""
    approved: bool
    qty: int = 0                # final quantity (lots × lot_size)
    lots: int = 0
    size_multiplier: float = 1.0
    rejection_gate: str = ''    # which gate rejected (empty if approved)
    rejection_reason: str = ''
    details: dict[str, Any] = field(default_factory=dict)


class RiskChain:
    """Orchestrates all risk guards in sequence.

    Usage:
        chain = RiskChain(kill_switch, supervisor, exposure_guard)
        verdict = chain.evaluate(request)
        if verdict.approved:
            # place order with verdict.qty
        else:
            # log verdict.rejection_gate + rejection_reason
    """

    def __init__(
        self,
        kill_switch: KillSwitchEngine,
        supervisor: TradeSupervisor,
        exposure_guard: ExposureGuard,
        # Config
        daily_loss_limit: float = 2500.0,
        max_risk_pct: float = 20.0,
        max_lots: int = 5,
    ) -> None:
        self._ks = kill_switch
        self._sup = supervisor
        self._exp = exposure_guard
        self._daily_loss_limit = daily_loss_limit
        self._max_risk_pct = max_risk_pct
        self._max_lots = max_lots

    def evaluate(
        self,
        req: TradeRequest,
        available_capital: float = 0.0,
        daily_realized_pnl: float = 0.0,
    ) -> RiskVerdict:
        """Run the full risk chain. Short-circuits on first rejection."""
        details: dict[str, Any] = {}

        # 1. Kill Switch
        if self._ks.is_blocked():
            return RiskVerdict(
                approved=False,
                rejection_gate='KILL_SWITCH',
                rejection_reason=f'Kill switch active (level={self._ks.level.value})',
                details={'kill_switch_level': self._ks.level.value},
            )
        details['kill_switch'] = 'off'

        # 2. Trade Supervisor
        denial: Denial | None = self._sup.check_permission(
            underlying=req.underlying,
            trade_mode=req.trade_mode,
            is_safe_hours=req.is_safe_hours,
            symbol=req.symbol,
        )
        if denial:
            return RiskVerdict(
                approved=False,
                rejection_gate=denial.rule,
                rejection_reason=denial.detail,
            )
        details['supervisor'] = 'passed'

        # 3. Exposure Guard
        exp_verdict: ExposureVerdict = self._exp.check(
            direction=req.direction, lots=req.desired_lots,
        )
        if not exp_verdict.allowed:
            return RiskVerdict(
                approved=False,
                rejection_gate='EXPOSURE',
                rejection_reason=exp_verdict.reason,
                details={'ce_lots': exp_verdict.ce_lots, 'pe_lots': exp_verdict.pe_lots},
            )
        details['exposure'] = {'ce': exp_verdict.ce_lots, 'pe': exp_verdict.pe_lots}

        # 4. Regime Guard
        regime_mult = 1.0
        if req.regime is not None:
            regime_v: RegimeVerdict = check_regime(
                regime=req.regime,
                trade_mode=req.trade_mode,
                direction=req.direction,
            )
            if not regime_v.allowed:
                return RiskVerdict(
                    approved=False,
                    rejection_gate='REGIME',
                    rejection_reason=regime_v.reason,
                    details={'regime': str(regime_v.regime)},
                )
            regime_mult = regime_v.size_multiplier
            details['regime'] = {'regime': str(regime_v.regime), 'multiplier': regime_mult}

        # 5. Budget Guard
        budget_v: BudgetVerdict = check_budget(
            daily_loss_limit=self._daily_loss_limit,
            daily_realized_pnl=daily_realized_pnl,
            sl_max_points=req.sl_points,
            qty=req.desired_lots * req.lot_size,
        )
        if not budget_v.allowed:
            return RiskVerdict(
                approved=False,
                rejection_gate='BUDGET',
                rejection_reason=budget_v.reason,
                details={'headroom': budget_v.headroom, 'worst_case': budget_v.worst_case_loss},
            )
        details['budget'] = {'headroom': budget_v.headroom}

        # 6. Position Sizer — compute final qty
        size: SizeResult = compute_size(
            available_capital=available_capital or self._daily_loss_limit * 10,
            max_risk_pct=self._max_risk_pct,
            sl_points=req.sl_points,
            lot_size=req.lot_size,
            premium=req.premium or 100.0,
            desired_lots=req.desired_lots,
            regime_multiplier=regime_mult,
            max_lots=self._max_lots,
        )
        details['sizer'] = {'lots': size.lots, 'qty': size.qty, 'risk_pct': size.risk_pct}
        if size.reason:
            details['sizer']['note'] = size.reason

        if size.lots <= 0 or size.qty <= 0:
            return RiskVerdict(
                approved=False,
                rejection_gate='SIZER',
                rejection_reason=size.reason or 'Position sizer returned 0 lots',
                details=details,
            )

        logger.info(
            'RISK APPROVED: %s %s %s — %d lots (%d qty) | regime_mult=%.1f',
            req.underlying, req.direction, req.trade_mode,
            size.lots, size.qty, regime_mult,
        )

        return RiskVerdict(
            approved=True,
            qty=size.qty,
            lots=size.lots,
            size_multiplier=regime_mult,
            details=details,
        )

    def record_entry(self, track_id: str, underlying: str, direction: str,
                     lots: int = 1, symbol: str = '') -> None:
        """Record a trade entry across all stateful guards."""
        self._sup.record_trade_opened(track_id, underlying, symbol)
        self._exp.add(direction, lots)

    def record_exit(self, track_id: str, pnl: float, was_sl: bool = False,
                    underlying: str = '', direction: str = '', lots: int = 1,
                    symbol: str = '') -> None:
        """Record a trade exit across all stateful guards."""
        self._sup.record_trade_closed(track_id, pnl, was_sl, underlying, symbol)
        self._exp.remove(direction, lots)
        self._ks.record_pnl(pnl)

    def reset_daily(self) -> None:
        """Reset all guards for a new trading day."""
        self._sup.reset_daily()
        self._exp.reset_daily()
        self._ks.reset_daily()
        logger.info('Risk chain daily reset complete')
