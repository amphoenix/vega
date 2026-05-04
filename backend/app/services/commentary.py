"""
AI commentary loop — every COMMENTARY_INTERVAL seconds, summarise live market
conditions and F&O scanner signals in plain English. Pushed to the
live_monitor SSE channel as `type: 'commentary'`.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from ..utils.logger import get_logger
from . import broker_utils as bu

logger = get_logger('phoenixtrade.commentary')

COMMENTARY_INTERVAL = int(os.environ.get('COMMENTARY_INTERVAL', '30'))   # sec

_thread: Optional[threading.Thread] = None
_stop   = threading.Event()


def _build_brief() -> dict:
    nifty_price = None
    bank_price  = None
    vix         = None
    try:
        from ..api.indmoney import _ind_ltp
        nifty_price = _ind_ltp('^NSEI')
        bank_price  = _ind_ltp('^NSEBANK')
        vix         = _ind_ltp('^INDIAVIX')
    except Exception:
        pass

    scanner_signals = []
    try:
        from .fo_scanner import get_state
        state = get_state()
        for sig in (state.get('signals') or [])[-5:]:
            if sig.get('instrument') in ('CE', 'PE') and sig.get('option_symbol'):
                scanner_signals.append(
                    f"{sig['option_symbol']} ({sig.get('verdict','?')} conf={sig.get('confidence','?')}%)"
                )
    except Exception:
        pass

    return {
        'vix':             round(vix, 2) if vix else None,
        'nifty':           round(nifty_price, 2) if nifty_price else None,
        'banknifty':       round(bank_price, 2) if bank_price else None,
        'scanner_signals': scanner_signals,
    }


def _make_commentary(brief: dict) -> str:
    n = bu.now_ist()
    parts = [f"[{n.strftime('%H:%M IST')}]"]

    mkt_parts = []
    if brief.get('nifty'):
        mkt_parts.append(f"Nifty ₹{brief['nifty']:,.2f}")
    if brief.get('banknifty'):
        mkt_parts.append(f"BankNifty ₹{brief['banknifty']:,.2f}")
    if brief.get('vix'):
        v = brief['vix']
        regime = 'CALM' if v < 13 else 'NORMAL' if v < 18 else 'ELEVATED' if v < 22 else 'FEAR'
        mkt_parts.append(f"VIX {v:.1f} ({regime})")
    if mkt_parts:
        parts.append("  ".join(mkt_parts))

    sigs = brief.get('scanner_signals', [])
    if sigs:
        parts.append("Scanner: " + ", ".join(sigs[:3]))
    else:
        parts.append("Scanner watching market.")

    if os.environ.get('COMMENTARY_USE_LLM', '').lower() in ('1', 'true', 'yes'):
        try:
            from ..utils.llm_client import LLMClient
            llm = LLMClient()
            prompt = (
                "You are a calm options-trading desk commentator. In ≤80 words, "
                "narrate this market snapshot in plain English. Comment on VIX regime, "
                "Nifty trend, and any scanner signals. No advice, just observation.\n\n"
                + "\n".join(parts)
            )
            return llm.complete(prompt) or "  ".join(parts)
        except Exception as e:
            logger.debug(f"LLM commentary failed, using deterministic: {e}")
    return "  ".join(parts)


def _loop():
    from . import live_monitor as lm
    logger.info(f"Commentary loop started (interval={COMMENTARY_INTERVAL}s)")
    while not _stop.is_set():
        try:
            if bu.is_market_hours():
                brief = _build_brief()
                text  = _make_commentary(brief)
                lm._broadcast({
                    'type':  'commentary',
                    'text':  text,
                    'brief': brief,
                    'ts':    bu.now_ist().isoformat(),
                })
        except Exception as e:
            logger.warning(f"commentary loop error: {e}")
        _stop.wait(COMMENTARY_INTERVAL)
    logger.info("Commentary loop stopped")


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name='Commentary', daemon=True)
    _thread.start()


def stop():
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
