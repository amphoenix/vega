"""
Pydantic Settings — single source of truth for all configuration.

Reads from the project root .env file. Every env var is typed and validated.
No more os.environ.get() scattered across 15 files.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Resolve .env path relative to this file ──────────────────────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_ENV_FILE = os.path.join(_PROJECT_ROOT, '.env')


class Settings(BaseSettings):
    """All Vega configuration in one place."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding='utf-8',
        extra='ignore',           # silently ignore unknown .env vars
        case_sensitive=False,
    )

    # ── LLM Provider ─────────────────────────────────────────────────────────
    llm_provider: Literal['openai', 'gemini', 'bedrock', 'ollama'] = 'gemini'
    llm_api_key: str = ''
    llm_base_url: str = 'https://generativelanguage.googleapis.com/v1beta/openai'
    llm_model_name: str = 'gemma-4-31b-it'
    llm_api_key_2: str = ''
    llm_api_key_3: str = ''
    llm_acquire_timeout_sec: float = 5.0
    llm_request_timeout_sec: float = 60.0

    # ── AWS Bedrock ──────────────────────────────────────────────────────────
    aws_access_key_id: str = ''
    aws_secret_access_key: str = ''
    aws_region: str = 'us-east-1'
    bedrock_model_id: str = 'us.anthropic.claude-sonnet-4-6-20251001-v1:0'

    # ── Zep Memory ───────────────────────────────────────────────────────────
    zep_api_key: str = ''

    # ── Broker ───────────────────────────────────────────────────────────────
    indmoney_access_token: str = ''
    dhan_client_id: str = ''
    dhan_access_token: str = ''

    # ── Trading Mode ─────────────────────────────────────────────────────────
    # paper = connect to broker/exchange sandbox/testnet, no real money
    # live  = connect to production, real money
    trading_mode: Literal['paper', 'live'] = 'paper'

    # ── Trading Master Switches ──────────────────────────────────────────────
    live_trading_enabled: bool = False
    auto_trading_enabled: bool = True

    # ══════════════════════════════════════════════════════════════════════════
    # SWING (F&O)
    # ══════════════════════════════════════════════════════════════════════════
    fo_mode: Literal['paper', 'live'] = 'paper'
    fo_auto_trade: bool = True
    swing_capital_inr: float = 6_500.0
    fo_max_lots_per_trade: int = 1
    fo_max_risk_pct: float = 20.0
    fo_scan_interval_sec: int = 60
    fo_universe: str = 'ALL'   # 'ALL' = indices + every F&O stock (from broker master); or CSV like '^NSEI,^BSESN'
    fo_full_universe: bool = False   # True → scan full F&O stock universe; False → indices only (^NSEI, ^BSESN)
    fo_min_dte: int = 0
    fo_max_dte: int = 35
    fo_signal_min_dte: int = 0
    fo_min_confidence: int = 70
    fo_min_agents: int = 3
    fo_target_delta: float = 0.35
    auto_entry_min_confidence: int = 70   # pure-technical (no LLM boost) tops ~75-85; 85 was unreachable
    fo_max_positions: int = 2             # max concurrent open swing positions (risk + limits UI tick-streams)
    sl_max_points_sensex: int = 50
    sl_max_points_nifty: int = 15
    daily_loss_limit_inr: float = 1_300.0
    daily_loss_limit_base: float = 1_300.0
    allow_reentry_after_sl: bool = True
    max_reentries_per_day: int = 1

    # ══════════════════════════════════════════════════════════════════════════
    # SCALP
    # ══════════════════════════════════════════════════════════════════════════
    scalp_enabled: bool = True
    scalp_mode: Literal['paper', 'live'] = 'paper'
    scalp_auto_trade: bool = True
    scalp_capital_inr: float = 6_500.0
    scalp_max_lots_per_trade: int = 1
    scalp_universe: str = '^NSEI,^BSESN'
    scalp_sl_pts: float = 8.0
    scalp_t1_pts: float = 15.0
    scalp_sl_pts_sensex: float = 15.0
    scalp_t1_pts_sensex: float = 25.0
    scalp_sl_pts_nifty: float = 0.0
    scalp_t1_pts_nifty: float = 0.0
    scalp_max_hold_min: int = 10
    scalp_max_reentries: int = 10
    scalp_daily_loss_limit: float = 650.0
    scalp_daily_loss_limit_base: float = 650.0
    scalp_min_confidence: int = 70
    scalp_volume_mult: float = 2.0
    scalp_breakout_bars: int = 3
    scalp_opening_skip_min: int = 5
    scalp_max_concurrent: int = 1
    scalp_atr_sl_mult: float = 1.5
    scalp_atr_t1_mult: float = 2.0
    scalp_use_atr_sl: bool = True
    scalp_max_spread_pct: float = 2.0
    # Let winners run: at T1, tighten the trailing stop instead of hard-exiting,
    # so a runner rides toward T2 and beyond (trail-protected). False = classic T1 exit.
    scalp_let_winners_run: bool = True
    scalp_reentry_cooldown_sec: int = 45
    scalp_max_entries_per_day: int = 9999
    scalp_zerohero_exit_min: int = 50    # 14:50 — clear all scalp positions (ready for zero-hero)
    scalp_zerohero_reenter_min: int = 0  # 15:00 sharp — zero-hero OTM entry window opens
    scalp_zerohero_final_min: int = 20   # 15:20 — final exit

    # ══════════════════════════════════════════════════════════════════════════
    # FOREX (CDS Currency Futures)
    # ══════════════════════════════════════════════════════════════════════════
    forex_mode: Literal['paper', 'live'] = 'paper'
    forex_auto_trade: bool = True
    forex_capital_inr: float = 100_000.0
    forex_max_lots_per_trade: int = 20
    forex_daily_loss_limit: float = 25_000.0
    forex_universe: str = 'USDINR=X,EURINR=X,GBPINR=X,JPYINR=X'
    forex_scan_interval_sec: int = 180
    forex_min_confidence: int = 60
    forex_max_risk_pct: float = 2.0

    # ══════════════════════════════════════════════════════════════════════════
    # CRYPTO
    # ══════════════════════════════════════════════════════════════════════════
    crypto_mode: Literal['paper', 'live'] = 'paper'
    crypto_auto_trade: bool = True
    crypto_capital_usd: float = 100_000.0
    crypto_exchange: str = 'binance'
    crypto_api_key: str = ''
    crypto_api_secret: str = ''
    crypto_passphrase: str = ''
    crypto_scan_interval_sec: int = 300
    crypto_scan_symbols: str = 'BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,DOTUSDT'
    crypto_scan_timeframe: str = '5m'
    crypto_scan_trend_tf: str = '1h'
    crypto_min_confidence: int = 60
    # Chop filter: require the trend-TF (1h) to be genuinely TRENDING (ADX >= this)
    # before dip-buying/bounce-selling. Ranging 1h (low ADX) = whipsaw = losses.
    # 0 disables the filter.
    crypto_trend_adx_min: float = 15.0
    crypto_sl_pct: float = 0.10
    crypto_tp_pct: float = 0.0
    crypto_trail_pct: float = 0.02
    crypto_breakeven_pct: float = 0.03
    crypto_max_hold_hours: int = 48
    crypto_max_positions: int = 3
    crypto_position_size_usd: float = 1000.0
    crypto_daily_loss_limit_usd: float = 500.0
    crypto_trade_cooldown_sec: int = 600

    # ══════════════════════════════════════════════════════════════════════════
    # CRYPTO DERIVATIVES (Deribit — Options + Perpetuals)
    # ══════════════════════════════════════════════════════════════════════════
    crypto_fo_enabled: bool = False
    crypto_fo_mode: Literal['paper', 'live'] = 'paper'
    crypto_fo_auto_trade: bool = True
    crypto_fo_capital_usd: float = 10_000.0

    # Deribit API (keys in .env)
    deribit_client_id: str = ''
    deribit_client_secret: str = ''
    deribit_testnet: bool = True          # start on testnet, flip to False for prod

    # ── Options (short gamma scalp) ──
    crypto_fo_assets: str = 'BTC,ETH'     # underlying assets
    crypto_fo_structure: str = 'straddle' # straddle | strangle | put | call
    crypto_fo_target_dte: int = 7         # target days-to-expiry
    crypto_fo_min_dte: int = 3
    crypto_fo_max_dte: int = 14
    crypto_fo_roll_dte: int = 2           # roll when DTE drops below this
    crypto_fo_base_notional_usd: float = 10_000.0
    crypto_fo_max_notional_usd: float = 50_000.0
    # vol premium thresholds (IV - RV)
    crypto_fo_vol_entry_threshold: float = 0.05    # enter when IV-RV > 5%
    crypto_fo_vol_exit_threshold: float = 0.00
    crypto_fo_vol_emergency_exit: float = -0.15    # flatten when IV-RV < -15%

    # ── Delta hedging ──
    crypto_fo_delta_threshold: float = 0.05  # hedge when |delta| exceeds this
    crypto_fo_max_hedge_interval_h: float = 8.0

    # ── Perpetuals (momentum / funding carry) ──
    crypto_perp_enabled: bool = True
    crypto_perp_assets: str = 'BTC,ETH'
    crypto_perp_leverage: float = 2.0     # conservative, max 5x
    crypto_perp_position_size_usd: float = 5_000.0
    crypto_perp_max_positions: int = 2
    crypto_perp_sl_pct: float = 2.0       # 2% stop loss
    crypto_perp_tp_pct: float = 6.0       # 3:1 R:R
    crypto_perp_max_hold_hours: int = 168  # 7 days max
    crypto_perp_scan_interval_sec: int = 300
    crypto_perp_min_confidence: int = 65

    # ── Funding regime sizing (from gamma-scalper research) ──
    crypto_fo_funding_bull_mult: float = 1.0    # full size when funding > 5% ann
    crypto_fo_funding_neutral_mult: float = 0.7
    crypto_fo_funding_bear_mult: float = 0.3    # funding < 0%: stay small

    # ── Risk / kill switches ──
    crypto_fo_max_drawdown_usd: float = 2_000.0
    crypto_fo_max_drawdown_24h_usd: float = 3_000.0
    crypto_fo_rv_spike_halt: float = 3.0        # RV(1h)/RV(24h) > 3x → flatten
    crypto_fo_margin_util_halt: float = 0.80    # margin > 80% → flatten
    crypto_fo_daily_loss_limit_usd: float = 1_000.0

    # ══════════════════════════════════════════════════════════════════════════
    # POLYMARKET
    # ══════════════════════════════════════════════════════════════════════════
    polymarket_mode: Literal['paper', 'live'] = 'paper'
    polymarket_auto_trade: bool = True
    polymarket_capital_usd: float = 100_000.0
    polymarket_private_key: str = ''
    polymarket_wallet: str = ''
    polymarket_api_key: str = ''
    polymarket_api_secret: str = ''
    polymarket_api_passphrase: str = ''

    # ══════════════════════════════════════════════════════════════════════════
    # BTST
    # ══════════════════════════════════════════════════════════════════════════
    btst_enabled: bool = True
    btst_universe: str = "nifty50+sensex"
    btst_max_positions: int = 3
    btst_scan_start: str = "14:30"
    btst_scan_end: str = "15:10"

    # ══════════════════════════════════════════════════════════════════════════
    # SERVER
    # ══════════════════════════════════════════════════════════════════════════
    v2_port: int = Field(default=47293, description='Port for the v2 backend')
    debug: bool = False


    # ── Derived helpers ──────────────────────────────────────────────────────

    @field_validator('allow_reentry_after_sl', mode='before')
    @classmethod
    def _parse_reentry_flag(cls, v):
        """Accept 0/1 or true/false from .env."""
        if isinstance(v, str):
            return v.strip().lower() in ('1', 'true', 'yes')
        return bool(v)

    @property
    def fo_universe_list(self) -> list[str]:
        return [s.strip() for s in self.fo_universe.split(',') if s.strip()]

    @property
    def scalp_universe_list(self) -> list[str]:
        return [s.strip() for s in self.scalp_universe.split(',') if s.strip()]

    @property
    def llm_api_keys(self) -> list[str]:
        """All non-empty LLM keys for round-robin pool."""
        return [k for k in [self.llm_api_key, self.llm_api_key_2, self.llm_api_key_3] if k]

    def validate_llm(self) -> list[str]:
        """Return list of config errors (empty = valid)."""
        errors: list[str] = []
        if self.llm_provider == 'bedrock':
            if not self.aws_access_key_id or not self.aws_secret_access_key:
                errors.append('LLM_PROVIDER=bedrock requires AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY')
        elif self.llm_provider in ('openai', 'gemini'):
            if not self.llm_api_key:
                errors.append(f'LLM_API_KEY is required when LLM_PROVIDER={self.llm_provider}')
        return errors


# ── Singleton ────────────────────────────────────────────────────────────────
# Import `settings` anywhere; it loads once.
settings = Settings()
