"""
A/B Testing Framework for Strategy Comparison.

Runs two variants of a strategy side-by-side on the same market data
and tracks performance metrics to determine which variant performs better.

Usage:
    ab = ABTest.from_config(config_dict)
    ab.start()

    # On each signal, route to A or B based on traffic split:
    variant = ab.assign_variant()          # 'A' or 'B'
    ab.record_trade(variant, trade_result)

    # After enough trades:
    report = ab.get_report()
    winner = ab.get_winner()               # 'A', 'B', or None (inconclusive)

Config comes from config/strategies.yaml under ab_tests section.
Pure domain — no I/O.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from ...shared.logger import get_logger
from ...shared.time import datetime, now_ist

logger = get_logger('ab_testing')


@dataclass
class VariantStats:
    """Performance statistics for one A/B test variant."""
    label: str
    trades: list[dict[str, Any]] = field(default_factory=list)

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.get('pnl', 0) > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.get('pnl', 0) <= 0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return self.wins / self.trade_count * 100

    @property
    def total_pnl(self) -> float:
        return sum(t.get('pnl', 0) for t in self.trades)

    @property
    def avg_pnl(self) -> float:
        if not self.trades:
            return 0.0
        return self.total_pnl / self.trade_count

    @property
    def max_drawdown(self) -> float:
        if not self.trades:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in self.trades:
            cumulative += t.get('pnl', 0)
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)
        return max_dd

    @property
    def sharpe(self) -> float:
        """Simplified Sharpe ratio (no risk-free rate)."""
        if len(self.trades) < 2:
            return 0.0
        pnls = [t.get('pnl', 0) for t in self.trades]
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = variance ** 0.5
        if std == 0:
            return 0.0
        return mean / std

    def to_dict(self) -> dict[str, Any]:
        return {
            'label': self.label,
            'trade_count': self.trade_count,
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': round(self.win_rate, 2),
            'total_pnl': round(self.total_pnl, 2),
            'avg_pnl': round(self.avg_pnl, 2),
            'max_drawdown': round(self.max_drawdown, 2),
            'sharpe': round(self.sharpe, 3),
        }


@dataclass
class ABTest:
    """A/B test runner for strategy comparison.

    Routes signals to variant A or B based on traffic_split,
    records trades, and computes comparative metrics.
    """
    name: str
    description: str
    strategy_name: str
    traffic_split: int = 50         # % routed to variant B
    min_trades: int = 20            # min trades before declaring winner
    metrics: list[str] = field(default_factory=lambda: ['win_rate', 'avg_pnl', 'max_drawdown', 'sharpe'])
    variant_a_params: dict[str, Any] = field(default_factory=dict)
    variant_b_params: dict[str, Any] = field(default_factory=dict)

    # Runtime state
    variant_a: VariantStats = field(default_factory=lambda: VariantStats(label='A'))
    variant_b: VariantStats = field(default_factory=lambda: VariantStats(label='B'))
    enabled: bool = False
    started_at: datetime | None = None

    def start(self) -> None:
        self.enabled = True
        self.started_at = now_ist()
        logger.info('A/B test started: %s — %s vs %s',
                     self.name, self.variant_a.label, self.variant_b.label)

    def stop(self) -> None:
        self.enabled = False
        logger.info('A/B test stopped: %s', self.name)

    def assign_variant(self) -> str:
        """Randomly assign a signal to variant A or B based on traffic_split."""
        if random.randint(1, 100) <= self.traffic_split:
            return 'B'
        return 'A'

    def get_params(self, variant: str) -> dict[str, Any]:
        """Get strategy params for the assigned variant."""
        return self.variant_b_params if variant == 'B' else self.variant_a_params

    def record_trade(self, variant: str, trade_result: dict[str, Any]) -> None:
        """Record a completed trade for the given variant."""
        trade_result['variant'] = variant
        trade_result['recorded_at'] = now_ist().isoformat()
        if variant == 'B':
            self.variant_b.trades.append(trade_result)
        else:
            self.variant_a.trades.append(trade_result)
        logger.debug('A/B %s: recorded trade for variant %s (pnl=%.2f)',
                      self.name, variant, trade_result.get('pnl', 0))

    @property
    def has_enough_data(self) -> bool:
        """Whether both variants have enough trades to draw conclusions."""
        return (self.variant_a.trade_count >= self.min_trades
                and self.variant_b.trade_count >= self.min_trades)

    def get_winner(self) -> str | None:
        """Determine the winning variant, or None if inconclusive.

        Winner is determined by comparing metrics. Each metric where
        one variant is better counts as a point. Most points wins.
        """
        if not self.has_enough_data:
            return None

        a = self.variant_a
        b = self.variant_b
        a_points = 0
        b_points = 0

        for metric in self.metrics:
            a_val = getattr(a, metric, 0)
            b_val = getattr(b, metric, 0)

            if metric == 'max_drawdown':
                # Lower drawdown is better
                if a_val < b_val:
                    a_points += 1
                elif b_val < a_val:
                    b_points += 1
            else:
                # Higher is better
                if a_val > b_val:
                    a_points += 1
                elif b_val > a_val:
                    b_points += 1

        if a_points > b_points:
            return 'A'
        elif b_points > a_points:
            return 'B'
        return None  # tie

    def get_report(self) -> dict[str, Any]:
        """Generate A/B test comparison report."""
        winner = self.get_winner()
        return {
            'name': self.name,
            'description': self.description,
            'strategy': self.strategy_name,
            'enabled': self.enabled,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'traffic_split': self.traffic_split,
            'min_trades': self.min_trades,
            'has_enough_data': self.has_enough_data,
            'winner': winner,
            'winner_label': (
                self.variant_a.label if winner == 'A'
                else self.variant_b.label if winner == 'B'
                else 'Inconclusive'
            ),
            'variant_a': {
                **self.variant_a.to_dict(),
                'params': self.variant_a_params,
            },
            'variant_b': {
                **self.variant_b.to_dict(),
                'params': self.variant_b_params,
            },
            'metrics_compared': self.metrics,
        }

    def reset(self) -> None:
        """Reset all trade data."""
        self.variant_a = VariantStats(label=self.variant_a.label)
        self.variant_b = VariantStats(label=self.variant_b.label)
        self.started_at = None
        logger.info('A/B test reset: %s', self.name)

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, name: str, config: dict[str, Any]) -> ABTest:
        """Create an ABTest from a YAML config dict.

        Expected shape (from config/strategies.yaml → ab_tests section):
            swing_ema_test:
              description: "Test fast EMA 9 vs 13"
              strategy: swing_ai
              enabled: true
              traffic_split: 50
              variant_a:
                label: "EMA 9/21"
                params: {ema_fast: 9, ema_slow: 21}
              variant_b:
                label: "EMA 13/34"
                params: {ema_fast: 13, ema_slow: 34}
              metrics: [win_rate, avg_pnl, max_drawdown, sharpe]
              min_trades: 20
        """
        va_cfg = config.get('variant_a', {})
        vb_cfg = config.get('variant_b', {})

        test = cls(
            name=name,
            description=config.get('description', ''),
            strategy_name=config.get('strategy', ''),
            traffic_split=config.get('traffic_split', 50),
            min_trades=config.get('min_trades', 20),
            metrics=config.get('metrics', ['win_rate', 'avg_pnl', 'max_drawdown', 'sharpe']),
            variant_a_params=va_cfg.get('params', {}),
            variant_b_params=vb_cfg.get('params', {}),
            variant_a=VariantStats(label=va_cfg.get('label', 'A')),
            variant_b=VariantStats(label=vb_cfg.get('label', 'B')),
            enabled=config.get('enabled', False),
        )
        return test
