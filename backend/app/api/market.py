"""
Market data API
Provides OHLCV candlestick data, news, Reddit signals, FOMO score,
and simulation sentiment overlay.
"""

import os
import json
import time
import threading as _threading
import requests
from datetime import datetime, timedelta
from flask import request, jsonify
from threading import Lock
from queue import Queue as _Queue, Empty as _QueueEmpty

# ── In-memory cache (thread-safe) ────────────────────────────────────────────
_cache: dict = {}
_cache_lock = Lock()

def _cache_get(key: str):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() < entry['expires']:
            return entry['data']
        return None

def _cache_set(key: str, data, ttl: int = 300):
    """Cache data for ttl seconds (default 5 min)."""
    with _cache_lock:
        _cache[key] = {'data': data, 'expires': time.time() + ttl}

def _cache_key(*parts) -> str:
    return ':'.join(str(p) for p in parts)

import uuid
import csv

from . import market_bp
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..config import Config

logger = get_logger('phoenixtrade.api.market')

# ── Ticker resolver: auto-append .NS / .BO for bare Indian symbols ────────────
_resolved_cache: dict = {}

def _resolve_ticker(ticker: str) -> str:
    """Return the correct Yahoo Finance symbol for a ticker.
    For bare symbols (no dot, no ^) that fail to fetch data, tries .NS then .BO.
    Result is cached so subsequent calls are instant.
    """
    t = ticker.upper().strip()
    if t in _resolved_cache:
        return _resolved_cache[t]
    # Already has exchange suffix or is an index — use as-is
    if '.' in t or t.startswith('^'):
        _resolved_cache[t] = t
        return t
    # Try .NS first (most Indian tickers), then .BO, then bare as last resort
    import yfinance as _yf
    for candidate in [t + '.NS', t + '.BO', t]:
        try:
            h = _yf.Ticker(candidate).history(period='5d', interval='1d')
            if not h.empty:
                _resolved_cache[t] = candidate
                if candidate != t:
                    logger.info(f"Resolved {t} → {candidate}")
                return candidate
        except Exception:
            continue
    _resolved_cache[t] = t   # fallback: return as-is, let caller handle error
    return t


@market_bp.route('/<simulation_id>/ohlcv', methods=['GET'])
def get_ohlcv(simulation_id: str):
    """
    Fetch OHLCV candlestick data for a ticker and overlay simulation sentiment.

    Query params:
        ticker     - Stock symbol, e.g. AAPL
        start_date - YYYY-MM-DD
        end_date   - YYYY-MM-DD
        interval   - yfinance interval: 1d (default), 1h, 30m, etc.

    Returns:
        ohlcv               - List of candles
        sentiment_by_round  - Per-round bullish % aggregated from agent stances
        round_to_date_map   - Maps round number to calendar date
    """
    ticker     = request.args.get('ticker', 'AAPL').upper().strip()
    start_date = request.args.get('start_date', '').strip()
    end_date   = request.args.get('end_date', '').strip()
    interval   = request.args.get('interval', '1d').strip()

    if not start_date or not end_date:
        return jsonify({"success": False, "error": "start_date and end_date are required (YYYY-MM-DD)"}), 400

    try:
        import yfinance as yf
        import pandas as pd

        # ── OHLCV fetch ──────────────────────────────────────────────────────
        df = yf.download(ticker, start=start_date, end=end_date,
                         interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return jsonify({"success": False, "error": f"No market data found for {ticker}"}), 404

        # Flatten MultiIndex columns that yfinance sometimes returns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.loc[:, ~df.columns.duplicated()]

        df.index = pd.to_datetime(df.index)
        dates   = [ts.strftime('%Y-%m-%d') for ts in df.index]
        opens   = df['Open'].to_numpy().flatten().tolist()
        highs   = df['High'].to_numpy().flatten().tolist()
        lows    = df['Low'].to_numpy().flatten().tolist()
        closes  = df['Close'].to_numpy().flatten().tolist()
        volumes = df['Volume'].to_numpy().flatten().tolist()
        ohlcv = [
            {"date": dates[i], "open": round(float(opens[i]), 4), "high": round(float(highs[i]), 4),
             "low": round(float(lows[i]), 4), "close": round(float(closes[i]), 4), "volume": int(volumes[i])}
            for i in range(len(dates))
        ]

        # ── Simulation config: agent stances ─────────────────────────────────
        sim_dir     = os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id)
        config_path = os.path.join(sim_dir, 'simulation_config.json')

        agent_stance      = {}   # entity_name -> stance string
        minutes_per_round = 60   # fallback

        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            minutes_per_round = cfg.get('time_config', {}).get('minutes_per_round', 60)
            for ac in cfg.get('agent_configs', []):
                agent_stance[ac.get('entity_name', '')] = ac.get('stance', 'neutral')

        # ── Read all simulation actions ───────────────────────────────────────
        all_actions = SimulationRunner.get_all_actions(simulation_id)

        # Group actions by round
        rounds_map: dict = {}
        for action in all_actions:
            rounds_map.setdefault(action.round_num, []).append(action)

        # ── Map round numbers → calendar dates ────────────────────────────────
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        round_to_date: dict = {}
        for rn in sorted(rounds_map.keys()):
            offset_minutes = (rn - 1) * minutes_per_round
            wall_dt = start_dt + timedelta(minutes=offset_minutes)
            round_to_date[str(rn)] = wall_dt.strftime('%Y-%m-%d')

        # ── Build per-round sentiment ─────────────────────────────────────────
        POSTING_TYPES = {'CREATE_POST', 'CREATE_COMMENT', 'QUOTE_POST'}
        sentiment_by_round = []

        for rn, actions in sorted(rounds_map.items()):
            posting = [a for a in actions if a.action_type in POSTING_TYPES]
            if not posting:
                continue

            bullish = sum(
                1 for a in posting
                if agent_stance.get(a.agent_name, 'neutral') == 'supportive'
            )
            total       = len(posting)
            bullish_pct = round(bullish / total, 4) if total else 0.0

            top_posts = []
            for a in posting:
                content = (a.action_args or {}).get('content', '')
                if content:
                    top_posts.append(f"{a.agent_name}: {content[:100]}")
                if len(top_posts) >= 3:
                    break

            sentiment_by_round.append({
                "round_num":      rn,
                "bullish_pct":    bullish_pct,
                "total_agents":   total,
                "bullish_agents": bullish,
                "top_posts":      top_posts,
                "date":           round_to_date.get(str(rn), ""),
            })

        return jsonify({
            "success": True,
            "data": {
                "ticker":             ticker,
                "ohlcv":              ohlcv,
                "sentiment_by_round": sentiment_by_round,
                "round_to_date_map":  round_to_date,
            }
        })

    except Exception as e:
        logger.error(f"OHLCV fetch failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Full asset universe ───────────────────────────────────────────────────────
# Each entry: symbol, name, country, exchange, asset_class
# asset_class: index | stock | commodity | etf | crypto

ASSET_UNIVERSE = [
    # ── World Indices ────────────────────────────────────────────────────────
    {"symbol": "^DJI",      "name": "Dow Jones",       "country": "US", "exchange": "NYSE",     "asset_class": "index"},
    {"symbol": "^GSPC",     "name": "S&P 500",         "country": "US", "exchange": "NYSE",     "asset_class": "index"},
    {"symbol": "^IXIC",     "name": "NASDAQ",          "country": "US", "exchange": "NASDAQ",   "asset_class": "index"},
    {"symbol": "^RUT",      "name": "Russell 2000",    "country": "US", "exchange": "NYSE",     "asset_class": "index"},
    {"symbol": "^FTSE",     "name": "FTSE 100",        "country": "UK", "exchange": "LSE",      "asset_class": "index"},
    {"symbol": "^GDAXI",    "name": "DAX",             "country": "DE", "exchange": "XETRA",    "asset_class": "index"},
    {"symbol": "^FCHI",     "name": "CAC 40",          "country": "FR", "exchange": "Euronext", "asset_class": "index"},
    {"symbol": "^STOXX50E", "name": "Euro Stoxx 50",   "country": "EU", "exchange": "Euronext", "asset_class": "index"},
    {"symbol": "^N225",     "name": "Nikkei 225",      "country": "JP", "exchange": "TSE",      "asset_class": "index"},
    {"symbol": "^HSI",      "name": "Hang Seng",       "country": "HK", "exchange": "HKEX",     "asset_class": "index"},
    {"symbol": "000001.SS", "name": "SSE Composite",   "country": "CN", "exchange": "SSE",      "asset_class": "index"},
    {"symbol": "^BSESN",    "name": "SENSEX",          "country": "IN", "exchange": "BSE",      "asset_class": "index"},
    {"symbol": "^NSEI",     "name": "NIFTY 50",        "country": "IN", "exchange": "NSE",      "asset_class": "index"},
    {"symbol": "^NSEBANK",  "name": "Bank Nifty",      "country": "IN", "exchange": "NSE",      "asset_class": "index"},
    {"symbol": "^CNXIT",    "name": "Nifty IT",        "country": "IN", "exchange": "NSE",      "asset_class": "index"},
    {"symbol": "GIFT_NIFTY","name": "GIFT Nifty",      "country": "IN", "exchange": "NSE IFSC", "asset_class": "index"},
    {"symbol": "^AXJO",     "name": "ASX 200",         "country": "AU", "exchange": "ASX",      "asset_class": "index"},
    {"symbol": "^KS11",     "name": "KOSPI",           "country": "KR", "exchange": "KRX",      "asset_class": "index"},
    {"symbol": "^GSPTSE",   "name": "TSX Composite",   "country": "CA", "exchange": "TSX",      "asset_class": "index"},
    {"symbol": "^BVSP",     "name": "IBOVESPA",        "country": "BR", "exchange": "B3",       "asset_class": "index"},

    # ── US Mega-Cap Stocks ───────────────────────────────────────────────────
    {"symbol": "AAPL",  "name": "Apple",        "country": "US", "exchange": "NASDAQ", "asset_class": "stock"},
    {"symbol": "MSFT",  "name": "Microsoft",    "country": "US", "exchange": "NASDAQ", "asset_class": "stock"},
    {"symbol": "NVDA",  "name": "NVIDIA",       "country": "US", "exchange": "NASDAQ", "asset_class": "stock"},
    {"symbol": "AMZN",  "name": "Amazon",       "country": "US", "exchange": "NASDAQ", "asset_class": "stock"},
    {"symbol": "GOOGL", "name": "Alphabet",     "country": "US", "exchange": "NASDAQ", "asset_class": "stock"},
    {"symbol": "META",  "name": "Meta",         "country": "US", "exchange": "NASDAQ", "asset_class": "stock"},
    {"symbol": "TSLA",  "name": "Tesla",        "country": "US", "exchange": "NASDAQ", "asset_class": "stock"},
    {"symbol": "JPM",   "name": "JPMorgan",     "country": "US", "exchange": "NYSE",   "asset_class": "stock"},
    {"symbol": "V",     "name": "Visa",         "country": "US", "exchange": "NYSE",   "asset_class": "stock"},
    {"symbol": "UNH",   "name": "UnitedHealth", "country": "US", "exchange": "NYSE",   "asset_class": "stock"},
    {"symbol": "XOM",   "name": "Exxon Mobil",  "country": "US", "exchange": "NYSE",   "asset_class": "stock"},
    {"symbol": "LLY",   "name": "Eli Lilly",    "country": "US", "exchange": "NYSE",   "asset_class": "stock"},
    {"symbol": "AVGO",  "name": "Broadcom",     "country": "US", "exchange": "NASDAQ", "asset_class": "stock"},
    {"symbol": "WMT",   "name": "Walmart",      "country": "US", "exchange": "NYSE",   "asset_class": "stock"},
    {"symbol": "MA",    "name": "Mastercard",   "country": "US", "exchange": "NYSE",   "asset_class": "stock"},

    # ── Indian Stocks (NSE) ──────────────────────────────────────────────────
    {"symbol": "RELIANCE.NS",  "name": "Reliance",     "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "TCS.NS",       "name": "TCS",          "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "HDFCBANK.NS",  "name": "HDFC Bank",    "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "INFY.NS",      "name": "Infosys",      "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "ICICIBANK.NS", "name": "ICICI Bank",   "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "WIPRO.NS",     "name": "Wipro",        "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "AXISBANK.NS",  "name": "Axis Bank",    "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "SBIN.NS",      "name": "SBI",          "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "BAJFINANCE.NS","name": "Bajaj Finance","country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "ADANIENT.NS",  "name": "Adani Ent.",   "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "HCLTECH.NS",   "name": "HCL Tech",     "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "MARUTI.NS",    "name": "Maruti",       "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "TATAMOTORS.NS","name": "Tata Motors",  "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "TATASTEEL.NS", "name": "Tata Steel",   "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "ITC.NS",       "name": "ITC",          "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "VEDL.NS",      "name": "Vedanta",      "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "ONGC.NS",      "name": "ONGC",         "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "NTPC.NS",      "name": "NTPC",         "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "POWERGRID.NS", "name": "Power Grid",   "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "SUNPHARMA.NS", "name": "Sun Pharma",   "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "DRREDDY.NS",   "name": "Dr Reddy's",   "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "CIPLA.NS",     "name": "Cipla",        "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "DIVISLAB.NS",  "name": "Divi's Lab",   "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "NESTLEIND.NS", "name": "Nestle India", "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "HINDUNILVR.NS","name": "HUL",          "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "TITAN.NS",     "name": "Titan",        "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "BAJAJFINSV.NS","name": "Bajaj Finserv","country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "COALINDIA.NS", "name": "Coal India",   "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "TECHM.NS",     "name": "Tech Mahindra","country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "LTIM.NS",      "name": "LTIMindtree",  "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "HINDALCO.NS",  "name": "Hindalco",     "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "JSWSTEEL.NS",  "name": "JSW Steel",    "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "M&M.NS",       "name": "M&M",          "country": "IN", "exchange": "NSE", "asset_class": "stock"},
    {"symbol": "EICHERMOT.NS", "name": "Eicher Motors","country": "IN", "exchange": "NSE", "asset_class": "stock"},

    # ── International Stocks ─────────────────────────────────────────────────
    {"symbol": "HSBA.L",  "name": "HSBC",        "country": "UK", "exchange": "LSE",   "asset_class": "stock"},
    {"symbol": "SHEL.L",  "name": "Shell",       "country": "UK", "exchange": "LSE",   "asset_class": "stock"},
    {"symbol": "AZN.L",   "name": "AstraZeneca", "country": "UK", "exchange": "LSE",   "asset_class": "stock"},
    {"symbol": "SAP.DE",  "name": "SAP",         "country": "DE", "exchange": "XETRA", "asset_class": "stock"},
    {"symbol": "SIE.DE",  "name": "Siemens",     "country": "DE", "exchange": "XETRA", "asset_class": "stock"},
    {"symbol": "ASML",    "name": "ASML",        "country": "NL", "exchange": "NASDAQ","asset_class": "stock"},
    {"symbol": "9988.HK", "name": "Alibaba HK",  "country": "HK", "exchange": "HKEX",  "asset_class": "stock"},
    {"symbol": "0700.HK", "name": "Tencent",     "country": "HK", "exchange": "HKEX",  "asset_class": "stock"},
    {"symbol": "7203.T",  "name": "Toyota",      "country": "JP", "exchange": "TSE",   "asset_class": "stock"},
    {"symbol": "6758.T",  "name": "Sony",        "country": "JP", "exchange": "TSE",   "asset_class": "stock"},

    # ── Commodities (Futures) ────────────────────────────────────────────────
    {"symbol": "GC=F",  "name": "Gold",         "country": "US", "exchange": "COMEX", "asset_class": "commodity"},
    {"symbol": "SI=F",  "name": "Silver",       "country": "US", "exchange": "COMEX", "asset_class": "commodity"},
    {"symbol": "PL=F",  "name": "Platinum",     "country": "US", "exchange": "COMEX", "asset_class": "commodity"},
    {"symbol": "CL=F",  "name": "Crude Oil WTI","country": "US", "exchange": "NYMEX", "asset_class": "commodity"},
    {"symbol": "BZ=F",  "name": "Brent Crude",  "country": "US", "exchange": "ICE",   "asset_class": "commodity"},
    {"symbol": "NG=F",  "name": "Natural Gas",  "country": "US", "exchange": "NYMEX", "asset_class": "commodity"},
    {"symbol": "HG=F",  "name": "Copper",       "country": "US", "exchange": "COMEX", "asset_class": "commodity"},
    {"symbol": "ZW=F",  "name": "Wheat",        "country": "US", "exchange": "CBOT",  "asset_class": "commodity"},
    {"symbol": "ZC=F",  "name": "Corn",         "country": "US", "exchange": "CBOT",  "asset_class": "commodity"},
    {"symbol": "ZS=F",  "name": "Soybeans",     "country": "US", "exchange": "CBOT",  "asset_class": "commodity"},
    {"symbol": "KC=F",  "name": "Coffee",       "country": "US", "exchange": "ICE",   "asset_class": "commodity"},
    {"symbol": "SB=F",  "name": "Sugar",        "country": "US", "exchange": "ICE",   "asset_class": "commodity"},
    {"symbol": "PA=F",  "name": "Palladium",    "country": "US", "exchange": "NYMEX", "asset_class": "commodity"},

    # ── Global ETFs ──────────────────────────────────────────────────────────
    {"symbol": "SPY",  "name": "SPDR S&P 500",      "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "QQQ",  "name": "NASDAQ 100",         "country": "US", "exchange": "NASDAQ", "asset_class": "etf"},
    {"symbol": "VTI",  "name": "Vanguard Total Mkt", "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "EEM",  "name": "iShares EM",         "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "FXI",  "name": "China Large-Cap",    "country": "CN", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "EWJ",  "name": "Japan ETF",          "country": "JP", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "INDA", "name": "India ETF",          "country": "IN", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "EWG",  "name": "Germany ETF",        "country": "DE", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "EWZ",  "name": "Brazil ETF",         "country": "BR", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "VGK",  "name": "Europe ETF",         "country": "EU", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "GLD",  "name": "Gold ETF",           "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "SLV",  "name": "Silver ETF",         "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "USO",  "name": "Oil ETF",            "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "TLT",  "name": "20Y Treasury",       "country": "US", "exchange": "NASDAQ", "asset_class": "etf"},
    {"symbol": "VNQ",  "name": "Real Estate ETF",    "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "XLK",  "name": "Tech Sector",        "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "XLF",  "name": "Financials Sector",  "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "XLV",  "name": "Healthcare Sector",  "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "XLE",  "name": "Energy Sector",      "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    {"symbol": "XLI",  "name": "Industrials Sector", "country": "US", "exchange": "NYSE",   "asset_class": "etf"},
    # Indian ETFs (NSE)
    {"symbol": "NIFTYBEES.NS", "name": "Nifty BeES",  "country": "IN", "exchange": "NSE", "asset_class": "etf"},
    {"symbol": "GOLDBEES.NS",  "name": "Gold BeES",   "country": "IN", "exchange": "NSE", "asset_class": "etf"},
    {"symbol": "BANKBEES.NS",  "name": "Bank BeES",   "country": "IN", "exchange": "NSE", "asset_class": "etf"},
    {"symbol": "ITBEES.NS",    "name": "IT BeES",     "country": "IN", "exchange": "NSE", "asset_class": "etf"},
]

# Legacy alias for existing endpoint
WORLD_INDICES = [a for a in ASSET_UNIVERSE if a["asset_class"] == "index"]

# Fast company-name lookup from local universe — avoids yfinance quoteSummary calls
_COMPANY_NAME_MAP: dict[str, str] = {a['symbol'].upper(): a['name'] for a in ASSET_UNIVERSE}

# Suppress yfinance's internal HTTP error spam (404s for quoteSummary etc.)
import logging as _logging
_logging.getLogger('yfinance').setLevel(_logging.CRITICAL)

@market_bp.route('/world-indices', methods=['GET'])
def get_world_indices():
    """Fetch current price + daily change for all major world indices."""
    cached = _cache_get('world_indices')
    if cached:
        return jsonify({"success": True, "data": cached, "_cached": True})
    try:
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor

        # Indian index → IndStocks symbol map (for live prices)
        _IND_LIVE = {
            '^NSEI':     '^NSEI',
            '^NSEBANK':  '^NSEBANK',
            '^BSESN':    '^BSESN',
            '^CNXIT':    '^CNXIT',
            # GIFT_NIFTY handled separately via _nearest_nifty_future()
        }

        def _fetch_one(meta):
            sym = meta['symbol']
            try:
                # ── Indian indices: IndStocks live price first ──────────────
                if sym in _IND_LIVE:
                    try:
                        from .indmoney import _ind_ltp, _connected
                        if _connected():
                            ind_sym  = _IND_LIVE[sym]
                            ltp      = _ind_ltp(ind_sym)
                            if ltp:
                                # get prev close from yfinance for change %
                                try:
                                    hist = yf.Ticker(ind_sym if sym != 'GIFT_NIFTY' else '^NSEI').history(period='2d', interval='1d')
                                    closes = hist['Close'].dropna().tolist() if not hist.empty else []
                                    prev   = closes[-2] if len(closes) >= 2 else closes[-1] if closes else ltp
                                    chg    = round((ltp - prev) / prev * 100, 2)
                                except Exception:
                                    chg = 0.0
                                return {**meta, "price": round(ltp, 2), "change_pct": chg, "volume": None, "live": True}
                    except Exception:
                        pass  # fall through to yfinance

                # ── All others (and Indian fallback): yfinance ──────────────
                yf_sym = '^NSEI' if sym == 'GIFT_NIFTY' else sym
                hist    = yf.Ticker(yf_sym).history(period='2d', interval='1d')
                if hist is None or hist.empty:
                    return {**meta, "price": None, "change_pct": None, "volume": None}
                closes  = hist['Close'].dropna().tolist()
                volumes = hist['Volume'].dropna().tolist() if 'Volume' in hist.columns else []
                price   = round(float(closes[-1]), 2) if closes else None
                chg_pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else 0.0
                volume  = int(volumes[-1]) if volumes else None
                return {**meta, "price": price, "change_pct": chg_pct, "volume": volume}
            except Exception:
                return {**meta, "price": None, "change_pct": None, "volume": None}

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(_fetch_one, WORLD_INDICES))

        _cache_set('world_indices', results, ttl=10)
        return jsonify({"success": True, "data": results})

    except Exception as e:
        logger.error(f"World indices fetch failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Asset universe listing ────────────────────────────────────────────────────
@market_bp.route('/universe', methods=['GET'])
def get_universe():
    """Return the full asset universe metadata (no prices — fast)."""
    asset_class = request.args.get('asset_class', '')  # filter by class
    country     = request.args.get('country', '')
    result = ASSET_UNIVERSE
    if asset_class:
        result = [a for a in result if a['asset_class'] == asset_class.lower()]
    if country:
        result = [a for a in result if a['country'] == country.upper()]
    return jsonify({"success": True, "data": result})


# ── Smart Scanner ─────────────────────────────────────────────────────────────
@market_bp.route('/scan', methods=['GET'])
def scan_universe():
    """
    Scan stocks using TradingView real-time data (vendored library).
    Returns ranked BUY / SELL / HOLD signals using TradingView's own RSI, EMA, MACD values.

    Query params:
        market  - 'india' (default) | 'america' | 'both'
        limit   - max results per category (default 15)
    """
    import sys, os as _os
    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', 'vendor'))
    from tradingview_screener import Query, col

    market = request.args.get('market', 'india').lower()
    limit  = int(request.args.get('limit', 50))

    scan_key = _cache_key('tv_scan', market, limit)
    cached = _cache_get(scan_key)
    if cached:
        return jsonify({"success": True, "data": cached, "_cached": True})

    try:
        fields = ['name', 'description', 'close', 'change', 'change_abs',
                  'volume', 'relative_volume_10d_calc',
                  'RSI', 'RSI[1]',
                  'MACD.macd', 'MACD.signal',
                  'EMA20', 'EMA50', 'EMA200',
                  'ATR', 'market_cap_basic', 'sector', 'exchange']

        markets = ['india', 'america'] if market == 'both' else [market]
        all_results = []

        for mkt in markets:
            country = 'IN' if mkt == 'india' else 'US'
            currency = '₹' if mkt == 'india' else '$'

            # BUY candidates: RSI oversold/mid-recovery, price above EMA20
            try:
                _, buy_df = (Query()
                    .set_markets(mkt)
                    .select(*fields)
                    .where(
                        col('RSI').between(30, 60),
                        col('close') > col('EMA20'),
                        col('relative_volume_10d_calc') > 1.0,
                    )
                    .order_by('relative_volume_10d_calc', ascending=False)
                    .limit(limit)
                    .get_scanner_data())
            except Exception:
                buy_df = None

            # SELL candidates: RSI overbought, price below EMA20
            try:
                _, sell_df = (Query()
                    .set_markets(mkt)
                    .select(*fields)
                    .where(
                        col('RSI') > 65,
                        col('close') < col('EMA20'),
                        col('relative_volume_10d_calc') > 1.0,
                    )
                    .order_by('RSI', ascending=False)
                    .limit(limit)
                    .get_scanner_data())
            except Exception:
                sell_df = None

            # HOLD candidates: RSI in neutral zone
            try:
                _, hold_df = (Query()
                    .set_markets(mkt)
                    .select(*fields)
                    .where(
                        col('RSI').between(45, 55),
                        col('relative_volume_10d_calc') > 0.8,
                    )
                    .order_by('volume', ascending=False)
                    .limit(limit // 2)
                    .get_scanner_data())
            except Exception:
                hold_df = None

            def _sf(v, default=0.0):
                """Safe float: converts value, returns default for None/NaN."""
                try:
                    f = float(v) if v is not None else default
                    return default if f != f else f  # NaN self-comparison
                except Exception:
                    return default

            def _row(row, action):
                price    = _sf(row.get('close'), 0)
                rsi      = round(_sf(row.get('RSI'), 50), 1)
                atr      = _sf(row.get('ATR'), price * 0.015)
                chg1d    = round(_sf(row.get('change'), 0), 2)
                vol_ratio= round(_sf(row.get('relative_volume_10d_calc'), 1), 2)
                ema20    = _sf(row.get('EMA20'), price)
                ema50    = _sf(row.get('EMA50'), price)
                ema200   = _sf(row.get('EMA200'), price)
                macd     = _sf(row.get('MACD.macd'), 0)
                macd_sig = _sf(row.get('MACD.signal'), 0)
                trend    = ('BULL' if price > ema20 > ema50 else
                            'BEAR' if price < ema20 < ema50 else 'MIXED')

                # Composite score
                rsi_score = max(0, min(100, (50 - rsi) * 2 + 50))
                ema_score = 100 if price > ema20 > ema50 else (50 if price > ema20 else 0)
                vol_score = min(100, max(0, (vol_ratio - 1) * 40 + 50))
                mom_score = min(100, max(0, chg1d * 5 + 50))
                score     = round(rsi_score * 0.30 + ema_score * 0.25 + vol_score * 0.25 + mom_score * 0.20, 1)

                entry = round(price, 2)
                sl    = round(price - atr * 1.5, 2) if 'BUY' in action else round(price + atr * 1.5, 2)
                t1    = round(price + atr * 2.0, 2) if 'BUY' in action else round(price - atr * 2.0, 2)
                t2    = round(price + atr * 3.5, 2) if 'BUY' in action else round(price - atr * 3.5, 2)

                sym = str(row.get('name', ''))
                return {
                    "symbol":       sym + ('.NS' if mkt == 'india' else ''),
                    "name":         str(row.get('description') or sym),
                    "country":      country,
                    "exchange":     str(row.get('exchange') or ('NSE' if mkt == 'india' else 'NASDAQ')),
                    "asset_class":  "stock",
                    "sector":       str(row.get('sector') or ''),
                    "price":        entry,
                    "change_1d":    chg1d,
                    "volume_ratio": vol_ratio,
                    "rsi":          rsi,
                    "macd":         round(macd - macd_sig, 3),
                    "ema20":        round(ema20, 2),
                    "ema50":        round(ema50, 2),
                    "ema200":       round(ema200, 2),
                    "trend":        trend,
                    "score":        score,
                    "score_detail": {
                        "rsi_score": round(rsi_score, 1),
                        "ema_score": round(ema_score, 1),
                        "vol_score": round(vol_score, 1),
                        "mom_score": round(mom_score, 1),
                    },
                    "action":       action,
                    "entry":        entry,
                    "sl":           sl,
                    "t1":           t1,
                    "t2":           t2,
                    "currency":     currency,
                    "source":       "TradingView",
                }

            import pandas as _pd
            n = max(1, limit // 3)
            if buy_df is not None and not buy_df.empty:
                for _, row in buy_df.head(n).iterrows():
                    all_results.append(_row(row, 'BUY'))
            if sell_df is not None and not sell_df.empty:
                for _, row in sell_df.head(n).iterrows():
                    all_results.append(_row(row, 'SELL'))
            if hold_df is not None and not hold_df.empty:
                for _, row in hold_df.head(n).iterrows():
                    all_results.append(_row(row, 'HOLD'))

        # Deduplicate: same stock can appear via NSE+BSE or overlapping RSI ranges.
        # Keep the highest-scored entry per base symbol (strip exchange suffix).
        seen: dict = {}
        for item in all_results:
            base = item['symbol'].split('.')[0]
            if base not in seen or item['score'] > seen[base]['score']:
                seen[base] = item
        deduped = sorted(seen.values(), key=lambda x: x['score'], reverse=True)

        _cache_set(scan_key, deduped, ttl=60)
        return jsonify({"success": True, "data": deduped})

    except Exception as e:
        logger.error(f"TradingView scan failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Standalone OHLCV (no simulation required) ────────────────────────────────
@market_bp.route('/ohlcv', methods=['GET'])
def get_ohlcv_standalone():
    """
    Fetch OHLCV data for any ticker without needing a simulation.
    Query params: ticker, start_date, end_date, interval (default 1d)
    """
    ticker     = _resolve_ticker(request.args.get('ticker', 'AAPL'))
    start_date = request.args.get('start_date', '').strip()
    end_date   = request.args.get('end_date', '').strip()
    interval   = request.args.get('interval', '1d').strip()

    if not start_date or not end_date:
        # Default: last 90 days
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=90)
        start_date = start_dt.strftime('%Y-%m-%d')
        end_date   = end_dt.strftime('%Y-%m-%d')

    ohlcv_key = _cache_key('ohlcv', ticker, start_date, end_date, interval)
    cached = _cache_get(ohlcv_key)
    if cached:
        return jsonify({"success": True, "data": cached, "_cached": True})

    try:
        # ── 1. IndMoney candles ───────────────────────────────────────────────
        from .indmoney import _ind_candles
        from datetime import datetime as _dt
        ohlcv = None

        # Map generic interval strings to IndMoney format
        _IV_MAP = {'1m':'1m','5m':'5m','15m':'15m','30m':'30m',
                   '1h':'1h','60m':'1h','1d':'1d','1D':'1d','day':'1d'}
        _iv = _IV_MAP.get(interval, '1d')

        _start = _dt.strptime(start_date, '%Y-%m-%d')
        _end   = _dt.strptime(end_date,   '%Y-%m-%d')
        _days  = max(1, (_end - _start).days + 2)   # +2 buffer

        _raw = _ind_candles(ticker, _iv, days=_days)
        if _raw and len(_raw) > 2:
            ohlcv = [{
                "date":   c['date'][:10],
                "open":   round(float(c['open']),   4),
                "high":   round(float(c['high']),   4),
                "low":    round(float(c['low']),    4),
                "close":  round(float(c['close']),  4),
                "volume": int(c['volume']),
            } for c in _raw]
            logger.debug(f"OHLCV via IndMoney for {ticker}: {len(ohlcv)} candles")

        # ── 2. yfinance fallback (non-Indian / IndMoney unavailable) ─────────
        if not ohlcv:
            import yfinance as yf
            import pandas as pd
            df = yf.download(ticker, start=start_date, end=end_date,
                             interval=interval, progress=False, auto_adjust=True)
            if df is None or df.empty:
                return jsonify({"success": False, "error": f"No data for {ticker}"}), 404
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.loc[:, ~df.columns.duplicated()]
            df.index = pd.to_datetime(df.index)
            dates   = [ts.strftime('%Y-%m-%d') for ts in df.index]
            opens   = df['Open'].to_numpy().flatten().tolist()
            highs   = df['High'].to_numpy().flatten().tolist()
            lows    = df['Low'].to_numpy().flatten().tolist()
            closes  = df['Close'].to_numpy().flatten().tolist()
            volumes = df['Volume'].to_numpy().flatten().tolist()
            ohlcv = [{"date": dates[i], "open": round(float(opens[i]), 4),
                      "high": round(float(highs[i]), 4), "low": round(float(lows[i]), 4),
                      "close": round(float(closes[i]), 4),
                      "volume": 0 if volumes[i] != volumes[i] else int(volumes[i])}
                     for i in range(len(dates))]
            logger.debug(f"OHLCV via yfinance fallback for {ticker}: {len(ohlcv)} candles")

        result = {"ticker": ticker, "ohlcv": ohlcv}
        _cache_set(ohlcv_key, result, ttl=10)
        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"Standalone OHLCV failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Ticker search by name or symbol ──────────────────────────────────────────
@market_bp.route('/search', methods=['GET'])
def search_ticker():
    """
    Search tickers using only IndStocks instrument masters.
    Equity master matches by TRADING_SYMBOL / SYMBOL_NAME / CUSTOM_SYMBOL.
    F&O master matches by TRADING_SYMBOL token-overlap.

    Query examples:
      q=SBIN                    → equity contracts
      q=state bank              → equity (matches SYMBOL_NAME)
      q=NIFTY 24500 CE          → option contracts at strike 24500
      q=NIFTY-APR2026-24500-CE  → exact contract
      q=RELIANCE FUT            → futures
    """
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({"success": False, "error": "query too short"}), 400

    qu = q.upper()
    # Strip Google-Finance / exchange prefixes: "INDEXBOM:SENSEX", "NSE:SBIN", "BSE: SENSEX"
    for _pfx in ('INDEXBOM:', 'INDEXNSE:', 'INDEXSP:', 'INDEXDJX:',
                 'NSE:', 'BSE:', 'BOM:', 'NS:', 'BO:'):
        if qu.startswith(_pfx):
            qu = qu[len(_pfx):].strip()
            break
    # Common synonyms users type
    _SYNONYMS = {
        'BSE SENSEX':  'SENSEX',
        'BSE 30':      'SENSEX',
        'NSE NIFTY':   'NIFTY 50',
        'NIFTY':       'NIFTY 50',
        'BANK NIFTY':  'NIFTY BANK',
        'BANKNIFTY':   'NIFTY BANK',
        'VIX':         'INDIA VIX',
    }
    qu = _SYNONYMS.get(qu, qu)

    qu_sym  = qu.replace(' ', '-')
    tokens  = [t for t in qu_sym.split('-') if t]
    # Only treat as F&O if the user gave an explicit derivative hint —
    # CE/PE/FUT token or a numeric strike. Bare "SENSEX" / "NIFTY" alone
    # should return the spot index, not 25 option contracts.
    has_strike = any(t.isdigit() and len(t) >= 3 for t in tokens)
    looks_fno = (
        any(t in tokens for t in ('CE', 'PE', 'FUT', 'CALL', 'PUT'))
        or has_strike
    )

    results: list = []
    try:
        from .indmoney import _load_instruments

        # ── Index master search (FIRST so spot index ranks above F&O contracts) ──
        # The index master only has EXCH/SEGMENT/SECURITY_ID columns — match
        # the segment name and emit the canonical ^TICKER form so the rest
        # of the app (charts, signals, currency detection) works seamlessly.
        _INDEX_SEGMENT_TICKER = {
            'NIFTY 50':            ('^NSEI',      'NIFTY 50'),
            'NIFTY BANK':          ('^NSEBANK',   'NIFTY BANK'),
            'BANKNIFTY':           ('^NSEBANK',   'NIFTY BANK'),
            'NIFTY IT':            ('^CNXIT',     'NIFTY IT'),
            'NIFTY PHARMA':        ('^CNXPHARMA', 'NIFTY PHARMA'),
            'NIFTY FIN SERVICE':   ('^CNXFIN',    'NIFTY FIN SERVICE'),
            'NIFTY MIDCAP 50':     ('^NSEMDCP50', 'NIFTY MIDCAP 50'),
            'INDIA VIX':           ('^INDIAVIX',  'INDIA VIX'),
            'SENSEX':              ('^BSESN',     'SENSEX'),
            'BSE SENSEX':          ('^BSESN',     'SENSEX'),
        }
        # Hard-coded fallback for indices known to be in IndStocks index master
        # but where SEGMENT name doesn't match user query exactly.
        _STATIC_INDEX_HITS = {
            'SENSEX':     ('^BSESN',     'SENSEX',     'BSE'),
            'NIFTY 50':   ('^NSEI',      'NIFTY 50',   'NSE'),
            'NIFTY BANK': ('^NSEBANK',   'NIFTY BANK', 'NSE'),
            'INDIA VIX':  ('^INDIAVIX',  'INDIA VIX',  'NSE'),
        }
        seen_syms: set = set()
        for inst in _load_instruments('index'):
            seg = (inst.get('SEGMENT') or '').strip()
            if not seg:
                continue
            seg_u = seg.upper()
            # Token-overlap match — handles "vix" → "INDIA VIX", "bank" → "NIFTY BANK"
            if qu in seg_u or all(t in seg_u for t in qu.split()):
                canon = _INDEX_SEGMENT_TICKER.get(seg_u) or _INDEX_SEGMENT_TICKER.get(seg)
                if canon:
                    sym, name = canon
                else:
                    sym  = '^' + ''.join(c for c in seg_u if c.isalnum())
                    name = seg
                if sym in seen_syms:
                    continue
                seen_syms.add(sym)
                results.append({
                    'symbol':   sym,
                    'name':     name,
                    'exchange': (inst.get('EXCH') or 'NSE').upper(),
                    'type':     'INDEX',
                    'indian':   True,
                })
                if len(results) >= 5:
                    break
        # Static fallback — always run so canonical spot indices appear even
        # when the IndStocks index master segment name doesn't match exactly
        # (e.g. master uses 'BSE SENSEX' or has the row missing entirely).
        for key, (sym, name, exch) in _STATIC_INDEX_HITS.items():
            if qu in key or key in qu or any(t in key for t in qu.split() if len(t) >= 3):
                if sym in seen_syms:
                    continue
                seen_syms.add(sym)
                results.append({
                    'symbol': sym, 'name': name, 'exchange': exch,
                    'type': 'INDEX', 'indian': True,
                })

        # ── F&O contract search ─────────────────────────────────────────────
        # Drop exchange-prefix tokens — they aren't part of TRADING_SYMBOL.
        fno_tokens = [t for t in tokens if t not in ('NSE', 'BSE', 'BOM', 'NS', 'BO')]
        if looks_fno and fno_tokens:
            for inst in _load_instruments('fno'):
                sym = (inst.get('TRADING_SYMBOL') or '').upper()
                if not sym:
                    continue
                if all(tok in sym for tok in fno_tokens):
                    results.append({
                        'symbol':   sym,
                        'name':     sym,
                        'exchange': (inst.get('EXCH') or 'NSE').upper(),
                        'type':     'OPTION' if ('CE' in sym or 'PE' in sym) else 'FUTURE',
                        'indian':   True,
                    })
                    if len(results) >= 25:
                        break

        # ── Equity master search ────────────────────────────────────────────
        # Two-pass: prefer prefix matches (sym starts with qu OR a name word
        # starts with qu) so 'SENSEX' doesn't dredge up every BSE-Sensex ETF.
        # Substring matches only fill the bottom if there's room.
        eq_prefix:    list = []
        eq_substring: list = []
        for inst in _load_instruments('equity'):
            sym  = (inst.get('TRADING_SYMBOL') or '').upper()
            name = (inst.get('SYMBOL_NAME') or inst.get('CUSTOM_SYMBOL') or sym).upper()
            if not sym:
                continue
            name_words = name.replace('.', ' ').replace('-', ' ').split()
            is_prefix = sym.startswith(qu) or any(w.startswith(qu) for w in name_words)
            is_substr = (qu in sym) or (qu in name)
            if not (is_prefix or is_substr):
                continue
            exch   = (inst.get('EXCH') or '').upper()
            yf_sym = sym + ('.BO' if exch.startswith('B') else '.NS')
            entry = {
                'symbol':   yf_sym,
                'name':     name.title() if name != sym else sym,
                'exchange': exch or 'NSE',
                'type':     'EQUITY',
                'indian':   True,
            }
            (eq_prefix if is_prefix else eq_substring).append(entry)
            if len(eq_prefix) >= 20:
                break
        results.extend(eq_prefix[:20])
        if len(results) < 30:
            results.extend(eq_substring[: 30 - len(results)])
    except Exception as e:
        logger.warning(f"IndStocks search failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    # Dedupe by symbol; prefer F&O when the query looks F&O-ish
    seen = set()
    deduped = []
    for r in results:
        if r['symbol'] in seen:
            continue
        seen.add(r['symbol'])
        deduped.append(r)
    # Sort priority:
    #   0 = F&O contract when query is F&O-ish (user typed strike/CE/PE)
    #   1 = Spot INDEX (always wants to surface for bare 'SENSEX' / 'NIFTY')
    #   2 = Equity, F&O when not F&O-ish, etc.
    def _rank(x: dict) -> tuple:
        t = x.get('type')
        if t in ('OPTION', 'FUTURE') and looks_fno:
            return (0, x['symbol'])
        if t == 'INDEX':
            return (1, x['symbol'])
        return (2, x['symbol'])
    deduped.sort(key=_rank)
    return jsonify({"success": True, "data": deduped[:12]})


# ── Market signals: news + Reddit + FOMO ─────────────────────────────────────
@market_bp.route('/signals/<ticker>', methods=['GET'])
def get_signals(ticker: str):
    """
    Aggregate market signals for a ticker:
    - Recent news headlines (via yfinance)
    - Reddit posts (r/wallstreetbets, r/stocks, r/investing)
    - FOMO score (volume spike + price momentum)
    - Quick stats (current price, 1d change, 5d change)
    """
    ticker = _resolve_ticker(ticker)

    cached = _cache_get(_cache_key('signals', ticker))
    if cached:
        return jsonify({"success": True, "data": cached, "_cached": True})

    try:
        import yfinance as yf
        import pandas as pd

        yf_ticker = yf.Ticker(ticker)

        # ── Current price + stats ────────────────────────────────────────────
        hist = yf_ticker.history(period='10d', interval='1d')
        stats = {}
        if not hist.empty:
            closes = hist['Close'].tolist()
            vols   = hist['Volume'].tolist()
            price  = round(float(closes[-1]), 2)
            chg1d  = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else 0
            chg5d  = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else 0
            avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1)
            vol_ratio = round(vols[-1] / avg_vol, 2) if avg_vol else 1.0
            # Try local map first (avoids yfinance quoteSummary 404 spam)
            company_name = _COMPANY_NAME_MAP.get(ticker.upper(), '')
            if not company_name:
                try:
                    info = yf_ticker.info or {}
                    company_name = info.get('longName') or info.get('shortName') or ticker
                except Exception:
                    company_name = ticker
            if not company_name:
                company_name = ticker
            stats = {
                "price":        price,
                "change_1d":    chg1d,
                "change_5d":    chg5d,
                "volume_ratio": vol_ratio,  # today vol vs 9d avg
                "company_name": company_name,
            }

        # ── FOMO score (0-100) ────────────────────────────────────────────────
        # Based on: vol spike (40%), price momentum (30%), RSI-like (30%)
        fomo = 50
        if stats:
            vol_score  = min(100, (stats['volume_ratio'] - 1) * 40)
            mom_score  = min(100, max(0, stats['change_1d'] * 10 + 50))
            chg5_score = min(100, max(0, stats['change_5d'] * 4 + 50))
            fomo = int(vol_score * 0.4 + mom_score * 0.3 + chg5_score * 0.3)
            fomo = max(0, min(100, fomo))

        # ── News ─────────────────────────────────────────────────────────────
        news_items = []
        try:
            raw_news = yf_ticker.news or []
            for item in raw_news[:10]:
                content = item.get('content', {})
                title     = content.get('title', item.get('title', ''))
                summary   = content.get('summary', item.get('summary', ''))
                pub_date  = content.get('pubDate', '')
                provider  = ''
                provider_info = content.get('provider', {})
                if isinstance(provider_info, dict):
                    provider = provider_info.get('displayName', '')
                url = ''
                canon = content.get('canonicalUrl', {})
                if isinstance(canon, dict):
                    url = canon.get('url', '')

                if title:
                    news_items.append({
                        "title":    title,
                        "summary":  summary[:200] if summary else '',
                        "source":   provider,
                        "url":      url,
                        "pub_date": pub_date,
                    })
        except Exception as e:
            logger.warning(f"News fetch failed for {ticker}: {e}")

        # ── Reddit posts ──────────────────────────────────────────────────────
        reddit_posts = []
        clean_q = ticker.replace('.NS', '').replace('.BO', '')  # search "SBIN" not "SBIN.NS"
        is_indian = '.NS' in ticker or '.BO' in ticker
        subreddits = (
            ['IndiaInvestments', 'IndianStockMarket', 'Nifty', 'stocks', 'investing']
            if is_indian else
            ['wallstreetbets', 'stocks', 'investing', 'options']
        )
        headers = {'User-Agent': 'PhoenixTrade/1.0 signal-fetcher'}

        for sub in subreddits:
            try:
                url = f'https://www.reddit.com/r/{sub}/search.json'
                params = {'q': clean_q, 'sort': 'hot', 'limit': 5, 't': 'week', 'restrict_sr': 1}
                resp = requests.get(url, headers=headers, params=params, timeout=2)
                if resp.status_code == 200:
                    posts = resp.json().get('data', {}).get('children', [])
                    for p in posts:
                        d = p.get('data', {})
                        if d.get('title'):
                            score     = d.get('score', 0)
                            comments  = d.get('num_comments', 0)
                            upvote_rt = d.get('upvote_ratio', 0.5)
                            reddit_posts.append({
                                "title":        d['title'],
                                "subreddit":    sub,
                                "score":        score,
                                "comments":     comments,
                                "upvote_ratio": upvote_rt,
                                "url":          f"https://reddit.com{d.get('permalink', '')}",
                                "created_utc":  d.get('created_utc', 0),
                            })
            except Exception as e:
                logger.warning(f"Reddit fetch failed for r/{sub}: {e}")

        # Sort by score
        reddit_posts.sort(key=lambda x: x['score'], reverse=True)
        reddit_posts = reddit_posts[:10]

        # ── Reddit sentiment score (upvote ratio weighted by score) ──────────
        reddit_sentiment = 0.5
        if reddit_posts:
            total_score = sum(p['score'] for p in reddit_posts)
            if total_score > 0:
                reddit_sentiment = round(
                    sum(p['upvote_ratio'] * p['score'] for p in reddit_posts) / total_score, 3
                )

        # ── Moneycontrol news for Indian tickers ──────────────────────────────
        if '.NS' in ticker or '.BO' in ticker:
            try:
                import xml.etree.ElementTree as ET
                import re as _re
                mc_url = 'https://www.moneycontrol.com/rss/latestnews.xml'
                mc_headers = {'User-Agent': 'PhoenixTrade/1.0 signal-fetcher',
                              'Accept': 'application/rss+xml, text/xml'}
                mc_resp = requests.get(mc_url, headers=mc_headers, timeout=6)
                if mc_resp.ok:
                    root = ET.fromstring(mc_resp.content)
                    clean_sym = ticker.replace('.NS', '').replace('.BO', '').lower()
                    for item in root.findall('.//item')[:30]:
                        title = item.findtext('title', '').strip()
                        if not title:
                            continue
                        # Only include items mentioning the ticker or company name
                        title_lower = title.lower()
                        if clean_sym in title_lower or ticker.lower() in title_lower:
                            link     = item.findtext('link', '').strip()
                            pub_date = item.findtext('pubDate', '').strip()
                            desc     = _re.sub(r'<[^>]+>', '', item.findtext('description', '')).strip()[:200]
                            news_items.insert(0, {
                                "title":    title,
                                "summary":  desc,
                                "source":   "Moneycontrol",
                                "url":      link,
                                "pub_date": pub_date,
                            })
            except Exception as mc_err:
                logger.debug(f"Moneycontrol news for {ticker} failed: {mc_err}")

        # ── Analyst posts ─────────────────────────────────────────────────────
        # Indian tickers: Economic Times Markets RSS (filtered by symbol)
        # Global tickers: Seeking Alpha RSS
        sa_posts = []
        import xml.etree.ElementTree as ET
        import re as _re
        if is_indian:
            et_feeds = [
                'https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms',
                'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms',
            ]
            for et_url in et_feeds:
                try:
                    et_resp = requests.get(et_url, headers={'User-Agent': 'PhoenixTrade/1.0'}, timeout=6)
                    if et_resp.ok:
                        et_root = ET.fromstring(et_resp.content)
                        for item in et_root.findall('.//item')[:30]:
                            title = item.findtext('title', '').strip()
                            if not title:
                                continue
                            if clean_q.lower() in title.lower():
                                link = item.findtext('link', '').strip()
                                pub  = item.findtext('pubDate', '').strip()
                                desc = _re.sub(r'<[^>]+>', '', item.findtext('description', '')).strip()[:200]
                                sa_posts.append({
                                    "title":    title,
                                    "url":      link,
                                    "author":   "ET Markets",
                                    "source":   "Economic Times",
                                    "pub_date": pub,
                                })
                except Exception as e:
                    logger.debug(f"ET Markets fetch failed: {e}")
        else:
            try:
                sa_url = f'https://seekingalpha.com/symbol/{clean_q}/feed.xml'
                sa_resp = requests.get(sa_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=6)
                if sa_resp.ok:
                    sa_root = ET.fromstring(sa_resp.content)
                    for item in sa_root.findall('.//item')[:15]:
                        title   = item.findtext('title', '').strip()
                        link    = item.findtext('link', '').strip()
                        pub     = item.findtext('pubDate', '').strip()
                        authors = [c.text for c in item.findall('category') if c.text and ',' in c.text]
                        author  = authors[0] if authors else 'Seeking Alpha'
                        if title:
                            sa_posts.append({
                                "title":    title,
                                "url":      link,
                                "author":   author,
                                "source":   "Seeking Alpha",
                                "pub_date": pub,
                            })
            except Exception as e:
                logger.debug(f"Seeking Alpha fetch failed for {ticker}: {e}")

        result = {
            "ticker":           ticker,
            "stats":            stats,
            "fomo_score":       fomo,
            "reddit_sentiment": reddit_sentiment,
            "news":             news_items,
            "reddit":           reddit_posts,
            "stocktwits":       sa_posts,
        }
        _cache_set(_cache_key('signals', ticker), result, ttl=30)   # 30s
        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"Signals fetch failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── AI Prediction (full-stack) ────────────────────────────────────────────────
@market_bp.route('/ai-predict/<ticker>', methods=['GET'])
def ai_predict(ticker: str):
    """
    Full-stack AI prediction: 1-year technicals, multi-source news (MC/ET/SA/Reddit),
    fundamentals, India VIX / CNN Fear & Greed, Nifty/SPY context, screener.in.
    Cache: 5 min. Called by frontend every 2 min.
    """
    ticker = _resolve_ticker(ticker)

    # Short-circuit option contracts (e.g. BANKNIFTY-MAY2026-56600-PE).
    # AI predict scrapes news / fundamentals / screener — none of which exist
    # for an option symbol. Without this guard the endpoint hangs on retries
    # and 5-min frontend timeouts. Use the F&O scanner / option chain panel
    # for option-specific analysis.
    import re as _re_opt
    if _re_opt.match(r'^[A-Z]+-[A-Z]{3}\d{4}-\d+-(CE|PE)$', ticker):
        return jsonify({
            "success": False,
            "error": ("AI predict is not available for option contracts. "
                      "Run it on the underlying (e.g. ^NSEBANK / BANKNIFTY) "
                      "for directional analysis, then use the F&O scanner / "
                      "option chain to translate to a strike."),
            "data": None,
        }), 400

    predict_key = _cache_key('ai_predict', ticker)
    cached = _cache_get(predict_key)
    if cached:
        return jsonify({"success": True, "data": cached, "_cached": True})

    try:
        import yfinance as yf
        import numpy as np
        import xml.etree.ElementTree as _ET
        import re as _re

        is_indian = ticker.endswith('.NS') or ticker.endswith('.BO')
        clean_q   = ticker.replace('.NS', '').replace('.BO', '')
        currency  = '₹' if is_indian else '$'
        _hdrs     = {'User-Agent': 'PhoenixTrade/1.0 ai-predict'}

        # ── 1. Full-year OHLCV + technicals (IndMoney first, yfinance fallback) ─
        from ..services.ta_utils import compute_all as _compute_all
        from .indmoney import _ind_ltp, _ind_candles

        ind_price = _ind_ltp(ticker)
        _raw_ind  = _ind_candles(ticker, '1d', days=380)

        if _raw_ind and len(_raw_ind) >= 20:
            _candles_raw = [{'open': c['open'], 'high': c['high'], 'low': c['low'],
                              'close': c['close'], 'volume': c['volume']} for c in _raw_ind]
            yf_ticker = yf.Ticker(ticker)
            hist = None
            logger.debug(f"ai_predict: IndMoney candles for {ticker}: {len(_candles_raw)}")
        else:
            yf_ticker = yf.Ticker(ticker)
            hist = yf_ticker.history(period='1y', interval='1d')
            if hist.empty or len(hist) < 20:
                raise ValueError(f"Insufficient price data for {ticker}")
            _candles_raw = [{'open': float(hist['Open'].iloc[i]), 'high': float(hist['High'].iloc[i]),
                              'low': float(hist['Low'].iloc[i]), 'close': float(hist['Close'].iloc[i]),
                              'volume': int(hist['Volume'].iloc[i])} for i in range(len(hist))]

        ta      = _compute_all(_candles_raw)
        closes  = [c['close']  for c in _candles_raw]
        opens   = [c['open']   for c in _candles_raw]
        highs   = [c['high']   for c in _candles_raw]
        lows    = [c['low']    for c in _candles_raw]
        vols    = [c['volume'] for c in _candles_raw]

        price     = ind_price or closes[-1]
        rsi       = ta.get('rsi') or 50.0
        ema9      = ta.get('ema9')  or price
        ema21     = ta.get('ema20') or price   # use ema20 as ema21 proxy
        ema50     = ta.get('ema50') or price
        ema200    = ta.get('ema200') or price
        atr       = ta.get('atr') or price * 0.01
        vwap      = ta.get('vwap') or price
        vol_ratio = ta.get('vol_ratio') or 1.0
        w52h      = ta.get('w52_high') or price
        w52l      = ta.get('w52_low')  or price
        pct_from_high = ta.get('pct_from_52h') or 0.0
        pct_from_low  = ta.get('pct_from_52l') or 0.0

        n = len(closes)
        chg_1d = (closes[-1] - closes[-2])  / closes[-2]  * 100 if n >= 2  else 0.0
        chg_5d = (closes[-1] - closes[-6])  / closes[-6]  * 100 if n >= 6  else 0.0
        chg_1m = (closes[-1] - closes[-22]) / closes[-22] * 100 if n >= 22 else 0.0
        chg_3m = (closes[-1] - closes[-63]) / closes[-63] * 100 if n >= 63 else 0.0

        # Composite score — now includes Supertrend + ADX
        rsi_score  = max(0, min(100, (50 - rsi) * 2 + 50))
        ema_score  = 80 if ema9 > ema21 else 20
        vwap_score = 70 if price > vwap else 30
        vol_score  = min(100, max(0, (vol_ratio - 1) * 40 + 50))
        mom_score  = min(100, max(0, chg_1d * 10 + 50))
        st_score   = 80 if ta.get('supertrend_dir') == 1 else 20 if ta.get('supertrend_dir') == -1 else 50
        adx_score  = min(100, max(0, (ta.get('adx') or 20) * 2))
        score = round(rsi_score*0.20 + ema_score*0.20 + vwap_score*0.15
                      + vol_score*0.10 + mom_score*0.10 + st_score*0.15 + adx_score*0.10, 1)

        fomo = int(max(0, min(100, vol_score*0.4 + mom_score*0.3
                              + min(100, max(0, chg_5d*5+50))*0.3)))

        # Candlestick patterns (last 3 bars) — using list-based candles
        def _candle(i):
            op, hi, lo, cl = opens[i], highs[i], lows[i], closes[i]
            body = abs(cl - op); rng = hi - lo or 0.001; bull = cl > op
            uw = hi - max(op, cl); lw = min(op, cl) - lo
            if body / rng < 0.1:
                return 'Gravestone Doji' if uw > 2*lw else 'Dragonfly Doji' if lw > 2*uw else 'Doji'
            if body / rng > 0.85:
                return f"{'Bullish' if bull else 'Bearish'} Marubozu"
            if lw > 2*body and uw < body: return 'Hammer (bullish)'
            if uw > 2*body and lw < body: return 'Shooting Star' if bull else 'Inverted Hammer'
            return f"{'Bullish' if bull else 'Bearish'} candle ({body/rng*100:.0f}% body)"

        clines = []
        for i in range(max(0, n-3), n):
            clines.append(f"  {['3 bars ago','2 bars ago','Last bar'][i-(n-3)]}: {_candle(i)}")
        if n >= 3:
            o1,c1 = opens[-3], closes[-3]
            o2,c2 = opens[-2], closes[-2]
            o3,c3 = opens[-1], closes[-1]
            b1,b3 = abs(c1-o1), abs(c3-o3)
            if c1<o1 and abs(c2-o2)<0.3*b1 and c3>o3 and b3>0.5*b1:
                clines.append('  ⭐ Morning Star (BULLISH REVERSAL)')
            if c1>o1 and abs(c2-o2)<0.3*b1 and c3<o3 and b3>0.5*b1:
                clines.append('  ⭐ Evening Star (BEARISH REVERSAL)')
        if n >= 2:
            pb = abs(closes[-2]-opens[-2]); cb = abs(closes[-1]-opens[-1])
            if closes[-2]<opens[-2] and closes[-1]>opens[-1] and cb>pb:
                clines.append('  ⭐ Bullish Engulfing (STRONG BUY SIGNAL)')
            if closes[-2]>opens[-2] and closes[-1]<opens[-1] and cb>pb:
                clines.append('  ⭐ Bearish Engulfing (STRONG SELL SIGNAL)')
        # Add Supertrend + ADX context
        if ta.get('supertrend_dir'):
            clines.append(f"  Supertrend: {'BULLISH ↑' if ta['supertrend_dir']==1 else 'BEARISH ↓'} (line={ta.get('supertrend_line')})")
        if ta.get('adx'):
            clines.append(f"  ADX={ta['adx']:.1f} ({ta.get('adx_trend_strength','')}) +DI={ta.get('adx_plus_di')} -DI={ta.get('adx_minus_di')}")
        candles_desc = '\n'.join(clines)

        # ── 2. Fundamentals ───────────────────────────────────────────────────
        fund_lines = []
        try:
            info = yf_ticker.info
            def _fi(k):
                val = info.get(k)
                return None if (val != val) else val  # NaN → None
            pe  = _fi('trailingPE') or _fi('forwardPE')
            pb  = _fi('priceToBook')
            de  = _fi('debtToEquity')
            roe = round(_fi('returnOnEquity')*100, 1) if _fi('returnOnEquity') else None
            rev_g = round(_fi('revenueGrowth')*100, 1) if _fi('revenueGrowth') else None
            pm  = round(_fi('profitMargins')*100, 1) if _fi('profitMargins') else None
            mcap = _fi('marketCap')
            sector = _fi('sector') or _fi('industry')
            tgt = _fi('targetMeanPrice')
            div = round(_fi('dividendYield')*100, 2) if _fi('dividendYield') else None
            beta = _fi('beta')
            inst = round(_fi('heldPercentInstitutions')*100, 1) if _fi('heldPercentInstitutions') else None
            rec  = _fi('recommendationKey')
            n_analysts = _fi('numberOfAnalystOpinions')
            if pe:    fund_lines.append(f"PE: {pe:.1f}")
            if pb:    fund_lines.append(f"PB: {pb:.1f}")
            if de is not None: fund_lines.append(f"D/E: {de:.1f}")
            if roe:   fund_lines.append(f"ROE: {roe}%")
            if rev_g: fund_lines.append(f"Revenue growth: {rev_g}%")
            if pm:    fund_lines.append(f"Profit margin: {pm}%")
            if mcap:  fund_lines.append(f"Market cap: {currency}{mcap/1e7:.0f} Cr" if is_indian else f"Market cap: ${mcap/1e9:.1f}B")
            if sector: fund_lines.append(f"Sector: {sector}")
            if tgt:   fund_lines.append(f"Analyst target: {currency}{tgt:.2f} ({(tgt-price)/price*100:+.1f}% upside)")
            if div:   fund_lines.append(f"Dividend yield: {div}%")
            if beta:  fund_lines.append(f"Beta: {beta:.2f}")
            if inst:  fund_lines.append(f"Institutional holding: {inst}%")
            if rec and n_analysts: fund_lines.append(f"Analyst consensus: {rec.upper()} ({n_analysts} analysts)")
        except Exception:
            pass

        # ── 3. Market context: India VIX / CNN Fear & Greed / Nifty / SPY ────
        # Use IndMoney for real-time quotes, yfinance as fallback
        from .indmoney import _ind_ltp as _ind_last_price
        macro_lines = []

        if is_indian:
            try:
                # India VIX — Kite quote first, yfinance fallback
                ivix = _ind_last_price('^INDIAVIX')
                if not ivix:
                    _vh = yf.Ticker('^INDIAVIX').history(period='2d', interval='1d')
                    ivix = float(_vh['Close'].iloc[-1]) if not _vh.empty else None
                if ivix:
                    macro_lines.append(
                        f"India VIX: {ivix:.1f} — "
                        f"{'HIGH FEAR — cut size 50%' if ivix > 20 else 'ELEVATED' if ivix > 15 else 'CALM'}")
            except Exception:
                pass
            try:
                nifty_p = _ind_last_price('^NSEI')
                if not nifty_p:
                    _nh = yf.Ticker('^NSEI').history(period='2d', interval='1d')
                    if not _nh.empty and len(_nh) >= 2:
                        nc = (_nh['Close'].iloc[-1]-_nh['Close'].iloc[-2])/_nh['Close'].iloc[-2]*100
                        macro_lines.append(f"Nifty 50: {float(nc):+.2f}% today")
                else:
                    macro_lines.append(f"Nifty 50: ₹{nifty_p:,.0f} (live)")
            except Exception:
                pass
            try:
                bnk_p = _ind_last_price('^NSEBANK')
                if not bnk_p:
                    _bh = yf.Ticker('^NSEBANK').history(period='2d', interval='1d')
                    if not _bh.empty and len(_bh) >= 2:
                        bc = (_bh['Close'].iloc[-1]-_bh['Close'].iloc[-2])/_bh['Close'].iloc[-2]*100
                        macro_lines.append(f"Bank Nifty: {float(bc):+.2f}% today")
                else:
                    macro_lines.append(f"Bank Nifty: ₹{bnk_p:,.0f} (live)")
            except Exception:
                pass
        else:
            try:
                fg = requests.get('https://production.dataviz.cnn.io/index/fearandgreed/graphdata',
                                  headers={'User-Agent': 'Mozilla/5.0'}, timeout=2)
                if fg.ok:
                    fg_data = fg.json().get('fear_and_greed', {})
                    fg_score = fg_data.get('score')
                    fg_rating = fg_data.get('rating', '')
                    if fg_score is not None:
                        macro_lines.append(f"CNN Fear & Greed: {fg_score:.0f}/100 — {fg_rating.upper()}")
            except Exception:
                pass
            try:
                spy = yf.Ticker('^GSPC').history(period='5d', interval='1d')
                if not spy.empty and len(spy) >= 2:
                    sc = float((spy['Close'].iloc[-1]-spy['Close'].iloc[-2])/spy['Close'].iloc[-2]*100)
                    macro_lines.append(f"S&P 500: {sc:+.2f}% today ({'RISK ON' if sc > 0.3 else 'RISK OFF' if sc < -0.3 else 'FLAT'})")
            except Exception:
                pass

        # ── 4. Multi-source news (signals cache → fresh fetch) ────────────────
        news_headlines = []
        reddit_posts   = []
        reddit_sentiment = 0.5

        sig_cached = _cache_get(_cache_key('signals', ticker))
        if sig_cached:
            news_headlines   = [x.get('title','') for x in sig_cached.get('news',[]) if x.get('title')][:10]
            reddit_posts     = [f"[r/{p.get('subreddit','')}] {p.get('title','')}"
                                for p in sig_cached.get('reddit',[]) if p.get('title')][:6]
            reddit_sentiment = sig_cached.get('reddit_sentiment', 0.5)

        if not news_headlines:
            # yfinance
            try:
                for item in (yf_ticker.news or [])[:5]:
                    t = item.get('content', {}).get('title', item.get('title', ''))
                    if t: news_headlines.append(t)
            except Exception:
                pass
            # Indian RSS feeds
            if is_indian:
                for feed_url, src in [
                    ('https://www.moneycontrol.com/rss/latestnews.xml', 'Moneycontrol'),
                    ('https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms', 'ET Markets'),
                    ('https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms', 'ET Markets'),
                ]:
                    try:
                        r = requests.get(feed_url, headers=_hdrs, timeout=2)
                        if r.ok:
                            root = _ET.fromstring(r.content)
                            for item in root.findall('./channel/item')[:30]:
                                t = (item.findtext('title') or '').strip()
                                if t and _re.search(clean_q, t, _re.I):
                                    news_headlines.append(f'[{src}] {t}')
                    except Exception:
                        pass
            else:
                # Seeking Alpha
                try:
                    r = requests.get(f'https://seekingalpha.com/api/sa/combined/{clean_q}.xml',
                                     headers=_hdrs, timeout=2)
                    if r.ok:
                        root = _ET.fromstring(r.content)
                        for item in root.findall('./channel/item')[:6]:
                            t = (item.findtext('title') or '').strip()
                            if t: news_headlines.append(f'[Seeking Alpha] {t}')
                except Exception:
                    pass
            # Reddit
            try:
                subs = ['IndiaInvestments','IndianStockMarket','Nifty','stocks'] if is_indian \
                       else ['wallstreetbets','stocks','investing']
                _rp = []
                for sub in subs[:3]:
                    r = requests.get(f'https://www.reddit.com/r/{sub}/search.json',
                                     headers=_hdrs,
                                     params={'q': clean_q, 'sort': 'hot', 'limit': 5,
                                             't': 'week', 'restrict_sr': 1}, timeout=4)
                    if r.status_code == 200:
                        for p in r.json().get('data', {}).get('children', []):
                            d = p.get('data', {})
                            if d.get('title'):
                                _rp.append({'title': d['title'], 'sub': sub,
                                            'score': d.get('score', 1),
                                            'ratio': d.get('upvote_ratio', 0.5)})
                if _rp:
                    total = sum(x['score'] for x in _rp)
                    if total > 0:
                        reddit_sentiment = round(
                            sum(x['ratio']*x['score'] for x in _rp) / total, 3)
                    reddit_posts = [f"[r/{x['sub']}] {x['title']}"
                                    for x in sorted(_rp, key=lambda x: x['score'], reverse=True)[:6]]
            except Exception:
                pass

        news_headlines = [x for x in news_headlines if x][:10]

        # ── 5. Screener.in for Indian stocks ──────────────────────────────────
        screener_lines = []
        if is_indian:
            try:
                sc = requests.get(f'https://www.screener.in/company/{clean_q}/',
                                  headers={**_hdrs, 'Accept': 'text/html'}, timeout=8)
                if sc.ok:
                    for label, pat in [
                        ('ROCE',              r'ROCE\D{0,5}([\d.]+)\s*%'),
                        ('Promoter holding',  r'Promoter[^%\n]{0,20}([\d.]+)\s*%'),
                        ('FII holding',       r'FII[^%\n]{0,20}([\d.]+)\s*%'),
                        ('DII holding',       r'DII[^%\n]{0,20}([\d.]+)\s*%'),
                        ('Sales growth 3yr',  r'Sales\s+[Gg]rowth[^%\n]{0,30}([\d.]+)\s*%'),
                    ]:
                        m = _re.search(pat, sc.text, _re.I)
                        if m: screener_lines.append(f"{label}: {m.group(1)}%")
            except Exception:
                pass

        # ── 6. Build prompt & call LLM ────────────────────────────────────────
        sent_label = 'BULLISH' if reddit_sentiment > 0.6 else 'BEARISH' if reddit_sentiment < 0.4 else 'NEUTRAL'
        fomo_label = 'HIGH' if fomo > 70 else 'LOW' if fomo < 30 else 'MODERATE'

        from ..knowledge.trading_decision_rules import TRADING_SYSTEM_PROMPT

        prompt = f"""Analyze {ticker} ({'Indian NSE/BSE' if is_indian else 'US market'}) and give a precise prediction.

═══ TECHNICALS ═══
Price: {currency}{price:.2f}
52W High: {currency}{w52h:.2f} ({pct_from_high:.1f}% from high) | 52W Low: {currency}{w52l:.2f} (+{pct_from_low:.1f}% from low)
Momentum: 1D {chg_1d:+.2f}% | 5D {chg_5d:+.2f}% | 1M {chg_1m:+.2f}% | 3M {chg_3m:+.2f}%
RSI(14): {rsi:.1f}  {'⚠ OVERSOLD' if rsi < 35 else '⚠ OVERBOUGHT' if rsi > 68 else ''}
EMA9: {ema9:.2f} | EMA21: {ema21:.2f} | EMA50: {ema50:.2f} | EMA200: {ema200:.2f}
EMA alignment: Price {'ABOVE' if price>ema9 else 'BELOW'} EMA9 | {'ABOVE' if price>ema21 else 'BELOW'} EMA21 | {'ABOVE' if price>ema50 else 'BELOW'} EMA50 | {'ABOVE' if price>ema200 else 'BELOW'} EMA200
EMA cross: {'BULL (9>21)' if ema9>ema21 else 'BEAR (9<21)'}
VWAP: {currency}{vwap:.2f} — price {'ABOVE (bullish bias)' if price>vwap else 'BELOW (bearish bias)'}
Volume: {vol_ratio:.2f}x avg {'⚡ HIGH VOLUME' if vol_ratio > 1.8 else ''}
ATR(14): {currency}{atr:.2f} ({atr/price*100:.1f}% volatility)
Composite score: {score:.0f}/100

═══ CANDLESTICKS (last 3 bars) ═══
{candles_desc}

═══ MARKET CONTEXT ═══
{chr(10).join(f'- {x}' for x in macro_lines) if macro_lines else '- No macro data'}
FOMO: {fomo}/100 ({fomo_label}) | Reddit sentiment: {reddit_sentiment:.2f} ({sent_label})

═══ NEWS ═══
{chr(10).join(f'• {x}' for x in news_headlines) if news_headlines else '• No news found'}

═══ REDDIT / SOCIAL ═══
{chr(10).join(f'• {x}' for x in reddit_posts) if reddit_posts else '• No Reddit posts'}

═══ FUNDAMENTALS ═══
{chr(10).join(f'- {x}' for x in fund_lines) if fund_lines else '- Not available'}
{('═══ SCREENER.IN ═══\n' + chr(10).join(f'- {x}' for x in screener_lines)) if screener_lines else ''}

Respond with JSON only:
{{
  "short_term": "BULLISH" | "BEARISH" | "NEUTRAL",
  "short_confidence": <0-100>,
  "long_term": "BULLISH" | "BEARISH" | "NEUTRAL",
  "long_confidence": <0-100>,
  "reasoning": "<2-3 sentences citing specific numbers>",
  "key_factors": ["<factor with number>", "<factor>", "<factor>"],
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "watch_levels": {{"support": <price>, "resistance": <price>}}
}}"""

        llm    = LLMClient()
        result = llm.chat_json(
            [{"role": "system", "content": TRADING_SYSTEM_PROMPT},
             {"role": "user",   "content": prompt}],
            temperature=0.2,
            max_tokens=700,
        )

        payload = {
            "ticker": ticker,
            "stats": {
                "price": price, "change_1d": chg_1d, "change_5d": chg_5d,
                "change_1m": chg_1m, "change_3m": chg_3m,
                "volume_ratio": vol_ratio, "rsi": rsi, "score": score,
                "week52_high": w52h, "week52_low": w52l,
            },
            "fomo_score": fomo,
            "reddit_sentiment": reddit_sentiment,
            **result,
        }
        _cache_set(predict_key, payload, ttl=60)    # 1-min cache
        return jsonify({"success": True, "data": payload})

    except Exception as e:
        logger.error(f"AI predict failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Quick Simulation: generate + launch OASIS sim from live signals ───────────
def _parse_json_from_llm(raw: str) -> dict:
    """Extract and parse a JSON object from raw LLM output."""
    import re as _re
    raw = raw.strip()
    raw = _re.sub(r'^```(?:json)?\s*\n?', '', raw, flags=_re.IGNORECASE)
    raw = _re.sub(r'\n?```\s*$', '', raw).strip()
    m = _re.search(r'\{[\s\S]*\}', raw)
    if not m:
        raise ValueError(f"No JSON in LLM response: {raw[:300]}")
    return json.loads(m.group())


def _default_agents(ticker: str, market_direction: str = 'neutral') -> list:
    """Hardcoded agent archetypes used as fallback when LLM JSON parsing fails."""
    return [
        {"name": "Jake Turner",    "username": "jake_turner",    "role": "Retail Investor",      "bio": "YOLO options trader, all-in on tech",   "stance": "bullish",   "activity_level": 0.9, "age": 28, "topics": [ticker, "options", "FOMO"]},
        {"name": "Sara Wells",     "username": "sara_wells",     "role": "Hedge Fund Trader",     "bio": "Systematic quant, manages $200M book",  "stance": "bearish",   "activity_level": 0.7, "age": 38, "topics": [ticker, "quant", "risk"]},
        {"name": "Dan Park",       "username": "dan_park",       "role": "Reddit Day Trader",     "bio": "r/wallstreetbets mod, loves momentum",  "stance": "bullish",   "activity_level": 1.0, "age": 24, "topics": [ticker, "momentum", "WSB"]},
        {"name": "Lisa Chen",      "username": "lisa_chen",      "role": "Financial Journalist",  "bio": "Bloomberg correspondent, 10 years exp",  "stance": "neutral",   "activity_level": 0.6, "age": 35, "topics": [ticker, "macro", "earnings"]},
        {"name": "Tom Briggs",     "username": "tom_briggs",     "role": "Options Trader",        "bio": "Theta gang, sells premium for income",  "stance": "bearish",   "activity_level": 0.8, "age": 42, "topics": [ticker, "options", "IV"]},
        {"name": "Priya Sharma",   "username": "priya_sharma",   "role": "Institutional Analyst", "bio": "Goldman Sachs equity research, buy-side","stance": "bullish",   "activity_level": 0.5, "age": 31, "topics": [ticker, "research", "EPS"]},
        {"name": "Carlos Rivera",  "username": "carlos_rivera",  "role": "Retail Investor",       "bio": "Long-term holder, 401k maximizer",       "stance": "neutral",   "activity_level": 0.4, "age": 45, "topics": [ticker, "longterm", "index"]},
        {"name": "Alex Wong",      "username": "alex_wong",      "role": "Reddit Day Trader",     "bio": "Chart pattern enthusiast, TA only",     "stance": "bullish",   "activity_level": 0.9, "age": 26, "topics": [ticker, "TA", "breakout"]},
        {"name": "Morgan Lee",     "username": "morgan_lee",     "role": "Hedge Fund Trader",     "bio": "Macro hedge fund PM, short bias",        "stance": "bearish",   "activity_level": 0.7, "age": 40, "topics": [ticker, "macro", "short"]},
        {"name": "David Kim",      "username": "david_kim",      "role": "Options Trader",        "bio": "0DTE specialist, loves high IV events",  "stance": "bullish",   "activity_level": 0.8, "age": 33, "topics": [ticker, "0DTE", "gamma"]},
        {"name": "Rachel Torres",  "username": "rachel_torres",  "role": "Financial Journalist",  "bio": "CNBC anchor, market open commentary",   "stance": "neutral",   "activity_level": 0.5, "age": 37, "topics": [ticker, "news", "TV"]},
        {"name": "Kevin Zhao",     "username": "kevin_zhao",     "role": "Retail Investor",       "bio": "Crypto refugee now into AI stocks",     "stance": "bearish",   "activity_level": 0.6, "age": 29, "topics": [ticker, "AI", "speculative"]},
    ]


@market_bp.route('/quick-sim/<ticker>', methods=['POST'])
def launch_quick_sim(ticker: str):
    """
    Generate a full OASIS simulation from live signals for a ticker and launch it.

    Steps:
    1. Fetch live news + Reddit signals
    2. Use Claude to generate agent profiles + simulation config
    3. Write simulation_config.json, twitter_profiles.csv, reddit_profiles.json
    4. Start SimulationRunner
    5. Return simulation_id for polling

    Body (optional JSON):
        { "max_rounds": 8 }   # default 6
    """
    ticker = _resolve_ticker(ticker)
    body = request.get_json(silent=True) or {}
    max_rounds = int(body.get('max_rounds', 6))

    try:
        import yfinance as yf
        import pandas as pd

        # ── 1. Fetch live signals ────────────────────────────────────────────
        yf_ticker = yf.Ticker(ticker)
        hist = yf_ticker.history(period='10d', interval='1d')
        stats = {}
        if not hist.empty:
            closes = hist['Close'].tolist()
            vols   = hist['Volume'].tolist()
            price  = round(float(closes[-1]), 2)
            chg1d  = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else 0
            chg5d  = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else 0
            avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1)
            vol_ratio = round(vols[-1] / avg_vol, 2) if avg_vol else 1.0
            stats = {"price": price, "change_1d": chg1d, "change_5d": chg5d, "volume_ratio": vol_ratio}

        fomo = 50
        if stats:
            vol_score  = min(100, (stats['volume_ratio'] - 1) * 40)
            mom_score  = min(100, max(0, stats['change_1d'] * 10 + 50))
            chg5_score = min(100, max(0, stats['change_5d'] * 4 + 50))
            fomo = max(0, min(100, int(vol_score * 0.4 + mom_score * 0.3 + chg5_score * 0.3)))

        news_items = []
        try:
            for item in (yf_ticker.news or [])[:8]:
                content = item.get('content', {})
                title = content.get('title', item.get('title', ''))
                if title:
                    news_items.append(title)
        except Exception:
            pass

        reddit_items = []
        headers = {'User-Agent': 'PhoenixTrade/1.0 quick-sim'}
        for sub in ['wallstreetbets', 'stocks', 'investing']:
            try:
                url = f'https://www.reddit.com/r/{sub}/search.json'
                resp = requests.get(url, headers=headers,
                                    params={'q': ticker, 'sort': 'hot', 'limit': 4, 't': 'week', 'restrict_sr': 1},
                                    timeout=2)
                if resp.status_code == 200:
                    for p in resp.json().get('data', {}).get('children', []):
                        t = p.get('data', {}).get('title', '')
                        if t:
                            reddit_items.append(f"[r/{sub}] {t}")
            except Exception:
                pass

        # ── 2. Use Claude to generate simulation config ──────────────────────
        llm = LLMClient()

        context_block = f"""Ticker: {ticker}
Price: ${stats.get('price', 'N/A')} | 1D: {stats.get('change_1d', 0)}% | 5D: {stats.get('change_5d', 0)}% | Vol: {stats.get('volume_ratio', 1)}x avg | FOMO: {fomo}/100

Recent news:
{chr(10).join(f'- {n}' for n in news_items[:6]) if news_items else '- No news'}

Reddit posts:
{chr(10).join(f'- {r}' for r in reddit_items[:6]) if reddit_items else '- No Reddit activity'}"""

        # ── Call 1: generate 12 agent profiles (short, reliable JSON) ───────────
        # Use pre-defined archetypes — LLM only fills in names/bios/stance
        market_direction = 'bullish' if (stats.get('change_1d', 0) or 0) > 0 else 'bearish'
        agent_prompt = f"""Create 12 stock market traders who are posting about {ticker} on social media.
Market: price ${stats.get('price','?')}, 1-day change {stats.get('change_1d',0)}%, FOMO score {fomo}/100.

Rules:
- Assign each person a stance: use exactly one of these words: bullish, bearish, neutral
- activity_level is a number between 0.3 and 1.0
- bio must be under 12 words
- Use only standard ASCII characters in all strings
- Include 5 bullish, 4 bearish, 3 neutral traders

Return ONLY a valid JSON array. Example of one element:
{{"name":"Mike Chen","username":"mike_chen","role":"Retail Investor","bio":"Tech stock day trader since 2018","stance":"bullish","activity_level":0.8,"age":34,"topics":["{ticker}","options","tech"]}}

Now return the full array of 12 elements:"""

        raw_agents = llm.chat([{"role": "user", "content": agent_prompt}], temperature=0.5, max_tokens=2000)
        # Extract JSON array
        import re as _re
        raw_agents = raw_agents.strip()
        raw_agents = _re.sub(r'^```(?:json)?\s*\n?', '', raw_agents, flags=_re.IGNORECASE)
        raw_agents = _re.sub(r'\n?```\s*$', '', raw_agents).strip()
        am = _re.search(r'\[[\s\S]*\]', raw_agents)
        try:
            agents = json.loads(am.group()) if am else []
        except json.JSONDecodeError:
            # Fallback: use hard-coded archetypes if LLM JSON is broken
            agents = _default_agents(ticker, market_direction)

        # ── Call 2: generate opening post + narrative (small call) ───────────
        post_prompt = f"""Write an opening Reddit post about {ticker} (price ${stats.get('price','?')}, 1D {stats.get('change_1d',0)}%) for r/wallstreetbets.
Max 3 sentences. Use only ASCII. No special characters.

Respond with JSON: {{"post": "<text>", "sim_req": "<1 sentence describing the simulation>", "narrative": "<1 sentence on how sentiment will evolve>"}}"""

        raw_post = llm.chat([{"role": "user", "content": post_prompt}], temperature=0.7, max_tokens=400)
        post_gen = _parse_json_from_llm(raw_post)

        sim_req   = post_gen.get('sim_req',   f'Simulate social media reaction to {ticker} market developments')
        narrative = post_gen.get('narrative', '')
        init_post = {"content": post_gen.get('post', f'What is everyone thinking about {ticker} right now?'), "subreddit": "wallstreetbets"}

        # ── 3. Build simulation config + profile files ───────────────────────
        sim_id  = f"sim_{uuid.uuid4().hex[:12]}"
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, sim_id)
        os.makedirs(sim_dir, exist_ok=True)
        os.makedirs(os.path.join(sim_dir, 'log'), exist_ok=True)
        os.makedirs(os.path.join(sim_dir, 'ipc_commands'), exist_ok=True)
        os.makedirs(os.path.join(sim_dir, 'ipc_responses'), exist_ok=True)

        # Build agent_configs
        agent_configs = []
        for i, ag in enumerate(agents):
            stance_map = {'bullish': 'supportive', 'bearish': 'critical', 'neutral': 'neutral'}
            activity   = float(ag.get('activity_level', 0.6))
            agent_configs.append({
                "agent_id": i,
                "entity_uuid": f"uuid-{i:04d}",
                "entity_name": ag['name'],
                "entity_type": ag.get('role', 'Trader'),
                "activity_level": activity,
                "posts_per_hour": round(activity * 1.5, 2),
                "comments_per_hour": round(activity * 2.5, 2),
                "active_hours": list(range(0, 24)),
                "response_delay_min": 5,
                "response_delay_max": 45,
                "sentiment_bias": 0.2 if ag.get('stance') == 'bullish' else (-0.2 if ag.get('stance') == 'bearish' else 0.0),
                "stance": stance_map.get(ag.get('stance', 'neutral'), 'neutral'),
                "influence_weight": round(0.4 + activity * 0.6, 2),
            })

        simulation_config = {
            "simulation_id": sim_id,
            "project_id": f"quick_{ticker.lower()}",
            "graph_id": f"quick_{ticker.lower()}_{sim_id}",
            "ticker": ticker,
            "simulation_requirement": sim_req,
            "time_config": {
                # total_simulation_hours * 60 / minutes_per_round = total rounds
                # Use 30-min rounds so max_rounds maps directly to that many active rounds
                "total_simulation_hours": max_rounds,
                "minutes_per_round": 30,
                "agents_per_hour_min": 4,
                "agents_per_hour_max": 12,
                # All hours are peak — quick-sim isn't simulating daily rhythms
                "peak_hours": list(range(0, 24)),
                "peak_activity_multiplier": 1.5,
                "off_peak_hours": [],
                "off_peak_activity_multiplier": 1.0,
                "morning_hours": [],
                "morning_activity_multiplier": 1.0,
                "work_hours": list(range(0, 24)),
                "work_activity_multiplier": 1.0,
            },
            "agent_configs": agent_configs,
            "event_config": {
                "initial_posts": [{
                    "agent_id": 0,
                    "platform": "reddit",
                    "content": init_post.get('content', f"What does everyone think about {ticker} right now?"),
                    "subreddit": init_post.get('subreddit', 'r/wallstreetbets').lstrip('r/'),
                    "post_hour": 0,
                }],
                "scheduled_events": [],
                "hot_topics": [ticker] + [n[:50] for n in news_items[:3]],
                "narrative_direction": narrative,
            },
            "twitter_config": {
                "platform": "twitter",
                "recency_weight": 0.4,
                "popularity_weight": 0.3,
                "relevance_weight": 0.3,
                "viral_threshold": 50,
                "echo_chamber_strength": 0.3,
            },
            "reddit_config": {
                "platform": "reddit",
                "recency_weight": 0.3,
                "popularity_weight": 0.4,
                "relevance_weight": 0.3,
                "viral_threshold": 30,
                "echo_chamber_strength": 0.4,
            },
            "llm_model":    Config.LLM_MODEL_NAME,
            "llm_base_url": Config.LLM_BASE_URL,
            "generated_at": datetime.now().isoformat(),
            "generation_reasoning": f"Auto-generated from live {ticker} signals: FOMO={fomo}, {len(news_items)} news, {len(reddit_items)} Reddit posts",
        }

        with open(os.path.join(sim_dir, 'simulation_config.json'), 'w', encoding='utf-8') as f:
            json.dump(simulation_config, f, ensure_ascii=False, indent=2)

        # Twitter profiles CSV — OASIS requires: user_char, username, description
        # user_char = full character/persona prompt used as the agent's system context
        twitter_rows = [["user_char", "username", "description", "following_count", "followers_count"]]
        for ag in agents:
            stance     = ag.get('stance', 'neutral')
            role       = ag.get('role', 'Trader')
            bio        = ag.get('bio', '')
            topics_str = ', '.join(ag.get('topics', [ticker, 'stocks']))
            user_char  = (
                f"You are {ag['name']}, a {role} who is {stance} on {ticker}. "
                f"{bio} "
                f"You are passionate about {topics_str}. "
                f"You post on Twitter about market movements, share your analysis, "
                f"and react to other traders' opinions about {ticker}."
            )
            follower_count = int(float(ag.get('activity_level', 0.6)) * 8000 + 500)
            following_count = int(follower_count * 0.6)
            twitter_rows.append([
                user_char,
                ag.get('username', ag['name'].lower().replace(' ', '_')),
                bio,
                following_count,
                follower_count,
            ])
        with open(os.path.join(sim_dir, 'twitter_profiles.csv'), 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerows(twitter_rows)

        # Reddit profiles JSON — OASIS requires: persona, mbti, gender, age, country, username, bio, realname
        # persona = full character/persona prompt used as the agent's system context
        mbti_by_role = {
            'Retail Investor': 'ENFP', 'Hedge Fund Trader': 'INTJ', 'Reddit Day Trader': 'ESTP',
            'Financial Journalist': 'ENTP', 'Options Trader': 'ISTP', 'Institutional Analyst': 'ISTJ',
        }
        reddit_profiles = []
        for ag in agents:
            stance     = ag.get('stance', 'neutral')
            role       = ag.get('role', 'Trader')
            bio        = ag.get('bio', '')
            topics_str = ', '.join(ag.get('topics', [ticker, 'stocks']))
            persona    = (
                f"You are {ag['name']}, a {role} who is {stance} on {ticker}. "
                f"{bio} "
                f"You follow {topics_str} closely. "
                f"You post on Reddit (r/wallstreetbets, r/stocks, r/investing) sharing your market views, "
                f"DD, and reactions to news about {ticker}. "
                f"Your posts reflect your {stance} stance with supporting reasoning."
            )
            reddit_profiles.append({
                "realname": ag['name'],
                "username": ag.get('username', ag['name'].lower().replace(' ', '_')),
                "bio": bio,
                "persona": persona,
                "age": ag.get('age', 30),
                "gender": "male" if ag.get('age', 30) % 2 == 0 else "female",
                "mbti": mbti_by_role.get(role, 'ENTP'),
                "country": "US",
            })
        with open(os.path.join(sim_dir, 'reddit_profiles.json'), 'w', encoding='utf-8') as f:
            json.dump(reddit_profiles, f, ensure_ascii=False, indent=2)

        # state.json (marks sim as ready)
        state_data = {
            "simulation_id": sim_id,
            "status": "ready",
            "created_at": datetime.now().isoformat(),
            "ticker": ticker,
            "quick_sim": True,
        }
        with open(os.path.join(sim_dir, 'state.json'), 'w', encoding='utf-8') as f:
            json.dump(state_data, f)

        # ── 4. Start the simulation ──────────────────────────────────────────
        SimulationRunner.start_simulation(
            simulation_id=sim_id,
            platform='parallel',
            max_rounds=max_rounds,
            enable_graph_memory_update=False,
        )

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": sim_id,
                "ticker": ticker,
                "agents": len(agents),
                "max_rounds": max_rounds,
                "status": "started",
                "sim_req": sim_req,
            }
        })

    except Exception as e:
        logger.error(f"Quick sim failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@market_bp.route('/budget', methods=['GET'])
def get_budget():
    """Token usage and estimated cost.

    Query params:
      ?period=today|month|session|all   single-period summary (back-compat)
      (no param)                         bundle with today/month/session/all
                                         tiles plus today's breakdown at top
                                         level for the existing UI shape.
    """
    from ..services.kernel import budget as _budget
    period = request.args.get('period')
    if period:
        return jsonify({"success": True, "data": _budget.summary(period)})
    return jsonify({"success": True, "data": _budget.summary_all_periods()})

@market_bp.route('/budget/reset', methods=['POST'])
def reset_budget():
    """Reset budget ledger."""
    from ..services.kernel import budget as _budget
    _budget.reset()
    return jsonify({"success": True})

@market_bp.route('/processes', methods=['GET'])
def get_processes():
    """Agent execution history."""
    from ..services.kernel import process_store as _ps
    limit = int(request.args.get('limit', 50))
    return jsonify({"success": True, "data": _ps.recent(limit)})

@market_bp.route('/mc-news', methods=['GET'])
def get_mc_news():
    """
    Fetch latest news from Moneycontrol RSS feeds.
    Query params:
        feed  - 'latest' (default), 'markets', 'stocks', 'mutual-funds', 'economy'
        limit - number of items to return (default 20)
    """
    feed_map = {
        'latest':       'https://www.moneycontrol.com/rss/latestnews.xml',
        'markets':      'https://www.moneycontrol.com/rss/marketreports.xml',
        'stocks':       'https://www.moneycontrol.com/rss/buzzingstocks.xml',
        'mutual-funds': 'https://www.moneycontrol.com/rss/mutualfunds.xml',
        'economy':      'https://www.moneycontrol.com/rss/economy.xml',
    }

    feed_key = request.args.get('feed', 'latest')
    limit    = min(int(request.args.get('limit', 20)), 50)
    url      = feed_map.get(feed_key, feed_map['latest'])

    try:
        import xml.etree.ElementTree as ET
        headers = {
            'User-Agent': 'PhoenixTrade/1.0 news-fetcher',
            'Accept':     'application/rss+xml, application/xml, text/xml',
        }
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()

        root  = ET.fromstring(resp.content)
        items = []
        for item in root.findall('.//item')[:limit]:
            title   = item.findtext('title', '').strip()
            link    = item.findtext('link', '').strip()
            pub     = item.findtext('pubDate', '').strip()
            desc    = item.findtext('description', '').strip()
            # Strip HTML tags from description
            import re
            desc = re.sub(r'<[^>]+>', '', desc)[:300]
            if title:
                items.append({
                    'title':    title,
                    'url':      link,
                    'summary':  desc,
                    'pub_date': pub,
                    'source':   'Moneycontrol',
                    'feed':     feed_key,
                })

        return jsonify({"success": True, "data": {"items": items, "count": len(items), "feed": feed_key}})

    except Exception as e:
        logger.error(f"Moneycontrol news fetch failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@market_bp.route('/fundamentals/<ticker>', methods=['GET'])
def get_fundamentals(ticker: str):
    """
    Fetch fundamental data for a stock ticker.
    Uses screener.in for Indian stocks (NSE/BSE), yfinance info for others.

    Returns P/E, revenue, profit, promoter holding, debt-to-equity, etc.
    """
    ticker = _resolve_ticker(ticker)
    # Normalise NSE suffix for screener.in lookup
    clean_ticker = ticker.replace('.NS', '').replace('.BO', '').replace('.BSE', '')

    result = {
        'ticker':   ticker,
        'source':   None,
        'pe_ratio': None,
        'revenue':  None,
        'net_profit': None,
        'promoter_holding': None,
        'debt_equity': None,
        'market_cap': None,
        'book_value': None,
        'dividend_yield': None,
        'roe': None,
        'notes': [],
    }

    # ── Try screener.in for Indian tickers ───────────────────────────────────
    is_indian = '.NS' in ticker or '.BO' in ticker or '.' not in ticker
    if is_indian:
        try:
            search_url = f'https://www.screener.in/api/company/search/?q={clean_ticker}&v=3'
            headers    = {'User-Agent': 'PhoenixTrade/1.0 fundamentals-fetcher'}
            resp = requests.get(search_url, headers=headers, timeout=6)
            if resp.ok:
                companies = resp.json()
                if companies:
                    company_url = companies[0].get('url', '')
                    company_name = companies[0].get('name', '')
                    result['company_name'] = company_name

                    # Fetch company page meta description for key ratios
                    page_url = f'https://www.screener.in{company_url}'
                    page_resp = requests.get(page_url, headers=headers, timeout=8)
                    if page_resp.ok:
                        import re
                        html = page_resp.text
                        # Extract meta description
                        meta_match = re.search(r'<meta name="description" content="([^"]+)"', html)
                        if meta_match:
                            meta = meta_match.group(1)
                            result['meta_description'] = meta

                            # Parse common fields from description
                            pe_m = re.search(r'P/E[:\s]+([0-9.]+)', meta, re.I)
                            if pe_m: result['pe_ratio'] = float(pe_m.group(1))

                        # Extract structured data from ratio section
                        # Look for spans with id=
                        mcap_m = re.search(r'id="market-cap"[^>]*>.*?<span[^>]*>([\d,]+\.?\d*)\s*(Cr|B|M)?', html, re.S)
                        if mcap_m:
                            val = float(mcap_m.group(1).replace(',', ''))
                            suffix = mcap_m.group(2) or ''
                            result['market_cap'] = f"₹{val:,.0f} {suffix}".strip()

                        result['source']   = 'screener.in'
                        result['page_url'] = page_url
        except Exception as e:
            result['notes'].append(f'screener.in lookup failed: {e}')

    # ── Fallback / complement with yfinance info ─────────────────────────────
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}

        def _get(key, fallback=None):
            v = info.get(key)
            return v if v not in (None, 'N/A', 0) else fallback

        if result['pe_ratio']   is None: result['pe_ratio']   = _get('trailingPE') or _get('forwardPE')
        if result['market_cap'] is None:
            mc = _get('marketCap')
            if mc: result['market_cap'] = f"${mc/1e9:.2f}B" if mc >= 1e9 else f"${mc/1e6:.1f}M"
        if result['dividend_yield'] is None: result['dividend_yield'] = _get('dividendYield')
        if result['book_value']     is None: result['book_value']     = _get('bookValue')
        if result['debt_equity']    is None: result['debt_equity']    = _get('debtToEquity')
        if result['roe']            is None: result['roe']            = _get('returnOnEquity')
        if result['revenue']        is None:
            rev = _get('totalRevenue')
            if rev: result['revenue'] = f"${rev/1e9:.2f}B" if rev >= 1e9 else f"${rev/1e6:.1f}M"
        if result['net_profit'] is None:
            np_ = _get('netIncomeToCommon')
            if np_: result['net_profit'] = f"${np_/1e9:.2f}B" if abs(np_) >= 1e9 else f"${np_/1e6:.1f}M"
        if not result.get('company_name'): result['company_name'] = _get('longName') or _get('shortName')
        if result['source'] is None: result['source'] = 'yfinance'

    except Exception as e:
        result['notes'].append(f'yfinance info failed: {e}')

    return jsonify({"success": True, "data": result})


@market_bp.route('/quick-sim-status/<simulation_id>', methods=['GET'])
def quick_sim_status(simulation_id: str):
    """
    Poll simulation status and return new agent actions since last_round.
    Query params:
        since_round  - return actions from rounds > this number (default 0)
    """
    since_round = int(request.args.get('since_round', 0))

    try:
        state = SimulationRunner.get_run_state(simulation_id)
        if not state:
            return jsonify({"success": False, "error": "Simulation not found"}), 404

        all_actions = SimulationRunner.get_all_actions(simulation_id)
        new_actions = [
            a.to_dict() for a in all_actions
            if a.round_num > since_round
            and a.action_type in {'CREATE_POST', 'CREATE_COMMENT', 'QUOTE_POST'}
        ]

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "status": state.runner_status,
                "current_round": state.current_round,
                "total_rounds": state.total_rounds,
                "new_actions": new_actions,
                "completed": state.runner_status in [RunnerStatus.COMPLETED, RunnerStatus.STOPPED, RunnerStatus.FAILED],
            }
        })

    except Exception as e:
        logger.error(f"Quick sim status failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Entity Knowledge Graph ────────────────────────────────────────────────────
@market_bp.route('/graph/<ticker>', methods=['GET'])
def get_entity_graph(ticker: str):
    """
    Build a knowledge graph of entities extracted from live news + Reddit for a ticker.

    Nodes: companies, people, sectors, events, concepts
    Edges: relationships inferred from co-occurrence and context

    Returns D3-compatible { nodes: [...], links: [...] } structure.
    """
    ticker = _resolve_ticker(ticker)

    graph_key = _cache_key('graph', ticker)
    cached = _cache_get(graph_key)
    if cached:
        return jsonify({"success": True, "data": cached, "_cached": True})

    try:
        import yfinance as yf
        import re as _re

        # ── 1. Collect raw text from news + Reddit ───────────────────────────
        texts = []
        yf_ticker = yf.Ticker(ticker)

        try:
            for item in (yf_ticker.news or [])[:15]:
                content = item.get('content', {})
                title   = content.get('title', item.get('title', ''))
                summary = content.get('summary', item.get('summary', ''))
                if title:   texts.append(title)
                if summary: texts.append(summary[:200])
        except Exception:
            pass

        headers = {'User-Agent': 'PhoenixTrade/1.0 graph-builder'}
        for sub in ['wallstreetbets', 'stocks', 'investing']:
            try:
                resp = requests.get(
                    f'https://www.reddit.com/r/{sub}/search.json',
                    headers=headers,
                    params={'q': ticker, 'sort': 'hot', 'limit': 8, 't': 'week', 'restrict_sr': 1},
                    timeout=2
                )
                if resp.status_code == 200:
                    for p in resp.json().get('data', {}).get('children', []):
                        d = p.get('data', {})
                        if d.get('title'): texts.append(d['title'])
            except Exception:
                pass

        # ── 2. Use LLM to extract entities + relationships ───────────────────
        llm = LLMClient()
        combined_text = '\n'.join(texts[:30])

        extract_prompt = f"""Analyze these market texts about {ticker} and extract a knowledge graph.

Texts:
{combined_text[:3000]}

Return ONLY valid JSON with this structure:
{{
  "nodes": [
    {{"id": "unique_id", "label": "Display Name", "type": "company|person|sector|event|concept|ticker", "sentiment": 0.6}}
  ],
  "links": [
    {{"source": "id1", "target": "id2", "relation": "relationship label", "weight": 0.8}}
  ]
}}

Rules:
- Extract 15-25 nodes: real companies, executives, sectors, macro events, competitor tickers
- Always include {ticker} as a central node of type "ticker"
- sentiment is 0.0 (bearish) to 1.0 (bullish) based on context
- weight is 0.1 to 1.0 based on relationship strength
- relations: "competes_with", "CEO_of", "analyst_at", "covers", "impacts", "part_of", "acquired", "partnered_with", "bullish_on", "bearish_on"
- Only ASCII characters"""

        raw = llm.chat([{"role": "user", "content": extract_prompt}], temperature=0.3, max_tokens=2000)

        import re as _re
        raw = raw.strip()
        raw = _re.sub(r'^```(?:json)?\s*\n?', '', raw, flags=_re.IGNORECASE)
        raw = _re.sub(r'\n?```\s*$', '', raw).strip()
        m = _re.search(r'\{[\s\S]*\}', raw)
        graph = json.loads(m.group()) if m else {"nodes": [], "links": []}

        # Ensure ticker node exists
        node_ids = {n['id'] for n in graph.get('nodes', [])}
        if ticker not in node_ids:
            graph.setdefault('nodes', []).insert(0, {
                "id": ticker, "label": ticker, "type": "ticker", "sentiment": 0.5
            })

        # Add yfinance metadata to ticker node
        try:
            info = yf_ticker.info or {}
            for n in graph['nodes']:
                if n['id'] == ticker:
                    n['price']    = info.get('currentPrice') or info.get('regularMarketPrice')
                    n['sector']   = info.get('sector', '')
                    n['industry'] = info.get('industry', '')
                    n['mktcap']   = info.get('marketCap')
                    break
        except Exception:
            pass

        graph_data = {
            "ticker":     ticker,
            "nodes":      graph.get('nodes', []),
            "links":      graph.get('links', []),
            "text_count": len(texts),
        }
        _cache_set(graph_key, graph_data, ttl=900)
        return jsonify({"success": True, "data": graph_data})

    except Exception as e:
        logger.error(f"Graph build failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Multi-Agent Investment Analysis ──────────────────────────────────────────
@market_bp.route('/invest-analysis/<ticker>', methods=['GET'])
def invest_analysis(ticker: str):
    """
    Step 1: Extract real entities (CEO, analysts, competitors, macro factors) from live data.
    Step 2: LLM generates 20 agent personas grounded in those real entities.
    Step 3: All 20 agents run in parallel, each from their expert perspective.
    Step 4: CIO coordinator synthesizes into final investment decision.
    Returns D3 graph + structured report.
    """
    import re as _re
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ticker = _resolve_ticker(ticker)

    analysis_key = _cache_key('invest_analysis', ticker)
    cached = _cache_get(analysis_key)
    if cached:
        return jsonify({"success": True, "data": cached, "_cached": True})

    try:
        import yfinance as yf
        import numpy as np

        yf_ticker = yf.Ticker(ticker)

        # ── 1. Collect all raw data ──────────────────────────────────────────
        hist = yf_ticker.history(period='200d', interval='1d')
        info = {}
        try:
            info = yf_ticker.info or {}
        except Exception:
            pass

        news_titles = []
        news_full   = []   # full news items for richer text
        try:
            for item in (yf_ticker.news or [])[:15]:
                c = item.get('content', {})
                t = c.get('title', item.get('title', ''))
                s = c.get('summary', item.get('summary', ''))
                if t:
                    news_titles.append(t)
                    news_full.append(f"{t}. {s}" if s else t)
        except Exception:
            pass

        # Real analyst recommendations from yfinance
        real_analysts = []
        try:
            recs = yf_ticker.recommendations
            if recs is not None and not recs.empty:
                recs = recs.sort_index(ascending=False)
                seen_firms = set()
                for idx, row in recs.iterrows():
                    firm = str(row.get('Firm', '')).strip()
                    grade = str(row.get('To Grade', row.get('Action', ''))).strip()
                    if firm and firm not in seen_firms:
                        seen_firms.add(firm)
                        real_analysts.append({'firm': firm, 'grade': grade})
                    if len(real_analysts) >= 8:
                        break
        except Exception:
            pass

        # Real institutional holders from yfinance
        real_holders = []
        try:
            holders = yf_ticker.institutional_holders
            if holders is not None and not holders.empty:
                for _, row in holders.head(6).iterrows():
                    holder_name = str(row.get('Holder', row.get('Name', ''))).strip()
                    pct = row.get('% Out', row.get('pctHeld', 0))
                    shares = row.get('Shares', 0)
                    if holder_name:
                        real_holders.append({'name': holder_name, 'pct': float(pct) if pct else 0, 'shares': int(shares) if shares else 0})
        except Exception:
            pass

        # Financial statements
        financials_block = ""
        financials_data  = {}
        try:
            inc  = yf_ticker.financials        # income statement (columns = dates, rows = line items)
            bal  = yf_ticker.balance_sheet
            cf   = yf_ticker.cashflow

            def _fmt(df, row_key, n=3):
                """Extract last n years for a row, return list of (year, value_cr)."""
                out = []
                if df is not None and not df.empty:
                    for col in list(df.columns)[:n]:
                        try:
                            v = float(df.loc[row_key, col]) if row_key in df.index else None
                            yr = str(col)[:4]
                            out.append((yr, round(v/1e7, 1) if v else None))  # convert to Crores
                        except:
                            pass
                return out

            rev     = _fmt(inc, 'Total Revenue')
            net_inc = _fmt(inc, 'Net Income')
            op_inc  = _fmt(inc, 'Operating Income') or _fmt(inc, 'EBIT')
            fcf_raw = _fmt(cf,  'Free Cash Flow')
            total_debt = None
            equity_val  = None
            current_ratio = None
            try:
                if bal is not None and not bal.empty:
                    col0 = bal.columns[0]
                    td   = float(bal.loc['Total Debt', col0]) if 'Total Debt' in bal.index else None
                    eq   = float(bal.loc['Stockholders Equity', col0]) if 'Stockholders Equity' in bal.index else (
                           float(bal.loc['Total Stockholders Equity', col0]) if 'Total Stockholders Equity' in bal.index else None)
                    ca   = float(bal.loc['Current Assets', col0]) if 'Current Assets' in bal.index else None
                    cl   = float(bal.loc['Current Liabilities', col0]) if 'Current Liabilities' in bal.index else None
                    total_debt = round(td/1e7, 1) if td else None
                    equity_val = round(eq/1e7, 1) if eq else None
                    current_ratio = round(ca/cl, 2) if ca and cl and cl != 0 else None
            except:
                pass

            de_ratio = round(total_debt / equity_val, 2) if total_debt and equity_val and equity_val != 0 else None

            financials_data = {
                "revenue_cr":       rev,
                "net_income_cr":    net_inc,
                "operating_income_cr": op_inc,
                "free_cash_flow_cr": fcf_raw,
                "total_debt_cr":    total_debt,
                "equity_cr":        equity_val,
                "debt_equity":      de_ratio,
                "current_ratio":    current_ratio,
            }

            lines = []
            if rev:
                lines.append("Revenue (₹Cr): " + " | ".join(f"{y}: {v}" for y,v in rev if v))
            if net_inc:
                lines.append("Net Profit (₹Cr): " + " | ".join(f"{y}: {v}" for y,v in net_inc if v))
            if op_inc:
                lines.append("Operating Income (₹Cr): " + " | ".join(f"{y}: {v}" for y,v in op_inc if v))
            if fcf_raw:
                lines.append("Free Cash Flow (₹Cr): " + " | ".join(f"{y}: {v}" for y,v in fcf_raw if v))
            if de_ratio is not None: lines.append(f"Debt/Equity: {de_ratio}")
            if current_ratio:        lines.append(f"Current Ratio: {current_ratio}")
            if total_debt is not None: lines.append(f"Total Debt (₹Cr): {total_debt}")
            financials_block = "\n".join(lines)
        except Exception as _fe:
            pass

        reddit_posts = []
        headers = {'User-Agent': 'PhoenixTrade/1.0 invest-analysis'}
        for sub in ['wallstreetbets', 'stocks', 'investing', 'SecurityAnalysis']:
            try:
                resp = requests.get(
                    f'https://www.reddit.com/r/{sub}/search.json',
                    headers=headers,
                    params={'q': ticker, 'sort': 'hot', 'limit': 5, 't': 'week'},
                    timeout=4
                )
                if resp.status_code == 200:
                    for p in resp.json().get('data', {}).get('children', []):
                        d = p.get('data', {})
                        if d.get('title'):
                            reddit_posts.append(f"[r/{sub}] {d['title']} (score:{d.get('score',0)}, comments:{d.get('num_comments',0)})")
            except Exception:
                pass

        # ── 2. Compute technicals ────────────────────────────────────────────
        price = float(info.get('currentPrice') or info.get('regularMarketPrice') or 0)
        rsi = 50.0; atr = 0.0; ema20 = ema50 = ema200 = price
        chg1d = chg1m = chg3m = 0.0; vol_ratio = 1.0
        bb_upper = bb_lower = price; macd_signal = 'neutral'

        if not hist.empty:
            closes  = hist['Close'].squeeze().tolist()
            highs   = hist['High'].squeeze().tolist()
            lows    = hist['Low'].squeeze().tolist()
            volumes = hist['Volume'].squeeze().tolist()
            price   = float(closes[-1])

            c_arr = np.array(closes, dtype=float)
            h_arr = np.array(highs,  dtype=float)
            l_arr = np.array(lows,   dtype=float)

            tr  = np.maximum(h_arr[1:]-l_arr[1:], np.maximum(np.abs(h_arr[1:]-c_arr[:-1]), np.abs(l_arr[1:]-c_arr[:-1])))
            atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.mean(tr)) if len(tr) else price*0.02

            diffs  = np.diff(c_arr)
            gains  = np.where(diffs > 0, diffs, 0.0)
            losses = np.where(diffs < 0, -diffs, 0.0)
            ag = np.mean(gains[-14:]); al = np.mean(losses[-14:])
            rsi = float(100 - 100 / (1 + ag / (al + 1e-10)))

            def ema_val(data, n):
                k = 2 / (n + 1); e = float(data[0])
                for v in data[1:]: e = float(v) * k + e * (1 - k)
                return e

            ema12  = ema_val(closes[-12:], 12)  if len(closes) >= 12  else price
            ema26  = ema_val(closes[-26:], 26)  if len(closes) >= 26  else price
            ema20  = ema_val(closes[-20:], 20)  if len(closes) >= 20  else price
            ema50  = ema_val(closes[-50:], 50)  if len(closes) >= 50  else price
            ema200 = ema_val(closes[-200:], 200) if len(closes) >= 200 else price
            macd   = ema12 - ema26
            macd_signal = 'bullish' if macd > 0 else 'bearish'

            sma20    = float(np.mean(c_arr[-20:])) if len(c_arr) >= 20 else price
            std20    = float(np.std(c_arr[-20:]))  if len(c_arr) >= 20 else price*0.02
            bb_upper = sma20 + 2 * std20
            bb_lower = sma20 - 2 * std20

            chg1d = (closes[-1]-closes[-2]) /closes[-2]*100  if len(closes)>=2  else 0
            chg1m = (closes[-1]-closes[-22])/closes[-22]*100 if len(closes)>=22 else 0
            chg3m = (closes[-1]-closes[-66])/closes[-66]*100 if len(closes)>=66 else 0

            avg_vol  = sum(volumes[-11:-1]) / 10 if len(volumes) >= 11 else volumes[-1]
            vol_ratio = float(volumes[-1]) / avg_vol if avg_vol else 1.0

        trend = ('BULLISH' if price > ema20 > ema50 else 'BEARISH' if price < ema20 < ema50 else 'MIXED')
        company_name  = info.get('longName') or info.get('shortName') or ticker
        sector        = info.get('sector', 'N/A')
        industry      = info.get('industry', 'N/A')
        market_cap    = info.get('marketCap', 0)
        country       = info.get('country', 'US')
        beta          = info.get('beta', 1.0)
        week52_high   = info.get('fiftyTwoWeekHigh', price * 1.3)
        week52_low    = info.get('fiftyTwoWeekLow',  price * 0.7)
        analyst_target = info.get('targetMeanPrice', 0)
        analyst_rec    = info.get('recommendationKey', 'N/A')

        # Extract officers
        officers = []
        for o in (info.get('companyOfficers') or [])[:4]:
            title = o.get('title', '')
            name  = o.get('name', '')
            if name and title:
                officers.append(f"{name} ({title})")

        # ── 3. Single LLM call: extract real entities from news + fill roster ──
        llm = LLMClient()

        # Build structured context from yfinance (no LLM needed for these)
        agent_specs_raw = []
        seen_names = set()

        focus_map = {
            'CEO': 'fundamental', 'CFO': 'fundamental', 'COO': 'fundamental',
            'CTO': 'fundamental', 'President': 'fundamental', 'Director': 'fundamental',
            'hedge fund': 'risk', 'bank': 'fundamental', 'regulator': 'risk',
            'competitor': 'fundamental', 'partner': 'macro', 'media': 'sentiment',
        }

        # 1. Real company executives (no LLM needed)
        for o in (info.get('companyOfficers') or [])[:4]:
            name  = (o.get('name') or '').strip()
            title = (o.get('title') or '').strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            focus = next((v for k, v in focus_map.items() if k.upper() in title.upper()), 'fundamental')
            agent_specs_raw.append({
                "name": name, "role": title, "firm": company_name,
                "perspective": f"As {title} of {company_name}, has direct insight into operations, strategy, and financials of {ticker}.",
                "data_focus": focus, "entity_source": "company_officer"
            })

        # 2. Real institutional holders (no LLM needed)
        for h in real_holders[:4]:
            name = h['name']
            if name in seen_names:
                continue
            seen_names.add(name)
            pct_str = f"{h['pct']*100:.1f}%" if h['pct'] < 1 else f"{h['pct']:.1f}%"
            agent_specs_raw.append({
                "name": name, "role": "Institutional Portfolio Manager", "firm": name,
                "perspective": f"Holds {pct_str} of {ticker} shares. Risk/reward and position sizing focus.",
                "data_focus": "risk", "entity_source": "institutional_holder"
            })

        # 3. Real analyst firms (no LLM needed)
        for a in real_analysts[:3]:
            firm = a['firm']
            if firm in seen_names:
                continue
            seen_names.add(firm)
            agent_specs_raw.append({
                "name": f"{firm} Analyst", "role": "Sell-Side Equity Analyst", "firm": firm,
                "perspective": f"{firm} rated {ticker} '{a['grade']}'. Re-evaluating from fundamentals.",
                "data_focus": "fundamental", "entity_source": "analyst_rating"
            })

        # ONE combined LLM call: extract real people from news + fill domain gaps
        news_text   = '\n'.join(f'- {t}' for t in news_full[:10]) or 'none'
        reddit_text = '\n'.join(f'- {p}' for p in reddit_posts[:6]) or 'none'
        already_have = [a['name'] for a in agent_specs_raw]
        needed_domains = [d for d in ['technical', 'macro', 'sentiment', 'risk', 'fundamental']
                          if not any(a['data_focus'] == d for a in agent_specs_raw)]

        combined_prompt = f"""You are building an investment simulation for {ticker} ({company_name}, {sector}).

TASK 1 — Extract real named people from the news/Reddit below (max 4):
NEWS: {news_text}
REDDIT: {reddit_text}

TASK 2 — Add real domain experts to cover missing perspectives.
Already have: {already_have}
Missing domains: {needed_domains}
For each missing domain, add 1 real well-known market participant (real names: fund managers, short sellers, macro strategists, quants, journalists who would credibly comment on {sector} stocks).

Return ONLY valid JSON:
{{
  "news_people": [
    {{"name": "Full Name", "role": "their actual role", "affiliation": "company/outlet", "context": "relevance to {ticker}", "data_focus": "sentiment"}}
  ],
  "domain_experts": [
    {{"name": "Real Person Name", "role": "exact title", "firm": "their firm", "perspective": "their specific view on {ticker}", "data_focus": "technical"|"fundamental"|"macro"|"sentiment"|"risk"}}
  ]
}}
Rules: only real people actually mentioned in news, or real well-known figures. No fictional names."""

        combined_raw = llm.chat([{"role": "user", "content": combined_prompt}], temperature=0.2, max_tokens=800)
        combined_raw = _re.sub(r'^```(?:json)?\s*\n?', '', combined_raw.strip(), flags=_re.IGNORECASE)
        combined_raw = _re.sub(r'\n?```\s*$', '', combined_raw).strip()
        combined_data = {}
        for m_combined in _re.finditer(r'\{[\s\S]*?\}(?=\s*\{|\s*$)', combined_raw):
            try:
                combined_data = json.loads(m_combined.group())
                if 'news_people' in combined_data or 'domain_experts' in combined_data:
                    break
            except json.JSONDecodeError:
                pass
        if not combined_data:
            m_combined = _re.search(r'\{[\s\S]*\}', combined_raw)
            if m_combined:
                try:
                    combined_data = json.loads(m_combined.group())
                except json.JSONDecodeError:
                    combined_data = {}

        for p in (combined_data.get('news_people') or [])[:4]:
            nm = (p.get('name') or '').strip()
            if nm and nm not in seen_names and len(nm.split()) >= 2:
                seen_names.add(nm)
                agent_specs_raw.append({
                    "name": nm, "role": p.get('role', 'Market Participant'),
                    "firm": p.get('affiliation', 'Independent'),
                    "perspective": p.get('context', f"Mentioned in recent {ticker} news."),
                    "data_focus": "sentiment", "entity_source": "news_ner"
                })

        for e in (combined_data.get('domain_experts') or [])[:5]:
            nm = (e.get('name') or '').strip()
            if nm and nm not in seen_names:
                seen_names.add(nm)
                e['entity_source'] = 'llm_grounded'
                agent_specs_raw.append(e)

        # Cap at 12
        agent_specs_raw = agent_specs_raw[:12]

        # Prepare shared data context blocks
        tech_block = f"""TECHNICAL DATA for {ticker} @ ${price:.2f}:
RSI-14={rsi:.1f} | ATR=${atr:.2f} | MACD={macd_signal}
EMA20=${ema20:.2f} EMA50=${ema50:.2f} EMA200=${ema200:.2f} | Trend={trend}
BB Upper=${bb_upper:.2f} Lower=${bb_lower:.2f}
1D={chg1d:+.2f}% 1M={chg1m:+.2f}% 3M={chg3m:+.2f}% | Volume={vol_ratio:.1f}x avg
52W: Low=${week52_low:.2f} High=${week52_high:.2f}"""

        fund_block = f"""FUNDAMENTAL DATA for {ticker}:
Company={company_name} | Sector={sector} | Industry={industry}
MktCap=${market_cap:,.0f} | P/E={info.get('trailingPE','N/A')} | FwdPE={info.get('forwardPE','N/A')} | PEG={info.get('pegRatio','N/A')}
Revenue=${info.get('totalRevenue',0):,.0f} | RevGrowth={info.get('revenueGrowth','N/A')} | GrossMargin={info.get('grossMargins','N/A')}
OpMargin={info.get('operatingMargins','N/A')} | ROE={info.get('returnOnEquity','N/A')} | D/E={info.get('debtToEquity','N/A')}
EPS={info.get('trailingEps','N/A')} | AnalystTarget=${analyst_target:.2f} | Consensus={analyst_rec}
Officers: {', '.join(officers[:3]) if officers else 'N/A'}"""

        macro_block = f"""MACRO & SECTOR DATA for {ticker}:
Sector={sector} | Industry={industry} | Country={country} | Beta={beta}
Market Cap Tier={'Large-cap' if market_cap > 10e9 else 'Mid-cap' if market_cap > 2e9 else 'Small-cap'}
News: {' | '.join(news_titles[:8]) if news_titles else 'none'}"""

        sent_block = f"""SENTIMENT DATA for {ticker}:
News: {chr(10).join(f'- {t}' for t in news_titles[:10]) if news_titles else 'none'}
Reddit: {chr(10).join(f'- {p}' for p in reddit_posts[:10]) if reddit_posts else 'none'}"""

        risk_block = f"""RISK DATA for {ticker}:
Price=${price:.2f} | ATR=${atr:.2f} ({atr/price*100:.1f}% daily range)
Beta={beta} | RSI={rsi:.1f} | Vol={vol_ratio:.1f}x
52W drawdown potential: ${price - week52_low:.2f} ({(price/week52_low-1)*100:.1f}% above 52W low)"""

        data_blocks = {
            'technical':   tech_block,
            'fundamental': fund_block,
            'macro':       macro_block,
            'sentiment':   sent_block,
            'risk':        risk_block,
        }

        # Append financials to the fundamental data block
        if financials_block:
            data_blocks['fundamental'] = data_blocks.get('fundamental', '') + "\n\nFINANCIAL STATEMENTS:\n" + financials_block

        # Domain expertise instructions per focus type
        domain_instructions = {
            'technical':   "You are a TECHNICAL ANALYST. Your entire analysis must be driven by price action, chart patterns, momentum indicators (RSI, MACD, EMA crossovers, Bollinger Bands), volume signals, and support/resistance levels. Ignore fundamentals — focus purely on what the chart is telling you.",
            'fundamental': "You are a FUNDAMENTAL ANALYST. Your analysis must be driven by valuation metrics (P/E, PEG, P/S), revenue growth, margins, balance sheet health, competitive moat, and management quality. Cite specific financial ratios and compare to sector peers.",
            'macro':       "You are a MACRO STRATEGIST. Your analysis must focus on sector rotation, interest rate environment, geopolitical risk, currency effects, and how the broader economic cycle affects this specific industry and ticker. Tie your thesis to macro regime.",
            'sentiment':   "You are a SENTIMENT & NEWS ANALYST. Your analysis must be grounded in news flow, social media sentiment, analyst upgrades/downgrades, short interest, and narrative momentum. Identify if the market story is improving or deteriorating.",
            'risk':        "You are a RISK MANAGER. Your analysis must quantify downside risk using ATR, beta, drawdown potential, options skew, leverage (D/E ratio), and tail scenarios. Your job is to stress-test the position and set appropriate stop losses.",
        }

        # ── 4. Run all agents in parallel ────────────────────────────────────
        def run_agent(spec):
            focus = spec.get('data_focus', 'fundamental')
            context = data_blocks.get(focus, tech_block)
            domain_instr = domain_instructions.get(focus, domain_instructions['fundamental'])
            prompt = f"""You are {spec['name']}, {spec['role']} at {spec['firm']}.
Your background: {spec['perspective']}

DOMAIN EXPERTISE: {domain_instr}

Now analyze {ticker} ({company_name}) using ONLY your domain lens on the data below:

{context}

Respond ONLY with this JSON (no markdown, no preamble):
{{
  "verdict": "STRONG BUY" | "BUY" | "HOLD" | "SELL" | "STRONG SELL",
  "confidence": <integer 0-100>,
  "entry_price": <number>,
  "stop_loss": <number>,
  "target_price": <number>,
  "key_findings": ["<domain-specific finding with exact numbers>", "<finding 2>", "<finding 3>"],
  "reasoning": "<2 sentences from your domain perspective — cite actual numbers>"
}}"""
            raw = llm.chat([{"role": "user", "content": prompt}], temperature=0.25, max_tokens=350)
            raw = _re.sub(r'^```(?:json)?\s*\n?', '', raw.strip(), flags=_re.IGNORECASE)
            raw = _re.sub(r'\n?```\s*$', '', raw).strip()
            m   = _re.search(r'\{[\s\S]*\}', raw)
            res = json.loads(m.group()) if m else {}
            res.update({'agent': spec['name'], 'role': spec['role'],
                        'firm': spec['firm'], 'data_focus': focus,
                        'entity_source': spec.get('entity_source', 'unknown')})
            return spec['name'], res

        agent_results = {}
        with ThreadPoolExecutor(max_workers=min(20, len(agent_specs_raw))) as pool:
            futures = {pool.submit(run_agent, spec): spec['name'] for spec in agent_specs_raw}
            for fut in as_completed(futures):
                try:
                    name, res = fut.result()
                    agent_results[name] = res
                except Exception as exc:
                    n = futures[fut]
                    spec = next((s for s in agent_specs_raw if s['name'] == n), {})
                    agent_results[n] = {
                        'agent': n, 'role': spec.get('role', ''), 'firm': spec.get('firm', ''),
                        'verdict': 'HOLD', 'confidence': 50,
                        'key_findings': ['Analysis failed'], 'reasoning': str(exc),
                        'entry_price': price, 'stop_loss': price * 0.95, 'target_price': price * 1.1,
                        'data_focus': spec.get('data_focus', 'fundamental')
                    }

        # ── 4.5 Adversarial Debate: Bull vs Bear, then CIO judges ────────────
        bull_agents = [(n, r) for n, r in agent_results.items()
                       if r.get('verdict') in ('STRONG BUY', 'BUY')]
        bear_agents = [(n, r) for n, r in agent_results.items()
                       if r.get('verdict') in ('STRONG SELL', 'SELL')]

        bull_summary = '\n'.join(
            f"  {r.get('role','')}/{r.get('firm','')} ({n}): {r.get('verdict')} "
            f"({r.get('confidence',50)}%) — {r.get('reasoning','')}"
            for n, r in bull_agents
        ) or "No explicit BUY verdicts."

        bear_summary = '\n'.join(
            f"  {r.get('role','')}/{r.get('firm','')} ({n}): {r.get('verdict')} "
            f"({r.get('confidence',50)}%) — {r.get('reasoning','')}"
            for n, r in bear_agents
        ) or "No explicit SELL verdicts."

        # Round 1 — Bull Advocate: strongest case FOR buying
        bull_prompt = f"""You are the Bull Advocate in a structured investment debate about {ticker} ({company_name}) at ${price:.2f}.
Your job is to argue STRONGLY IN FAVOUR of buying this stock.

{len(bull_agents)} analysts rated it BUY/STRONG BUY:
{bull_summary}

{tech_block}
{fund_block}

Build the strongest possible bull case (200-300 words). Cite exact numbers (P/E, RSI, price targets, revenue growth).
Do NOT hedge. Pre-emptively counter the most obvious bearish objection."""

        bull_arg = llm.chat([{"role": "user", "content": bull_prompt}], temperature=0.3, max_tokens=500)

        # Round 2 — Bear Advocate: strongest case AGAINST, having seen Bull's argument
        bear_prompt = f"""You are the Bear Advocate in a structured investment debate about {ticker} ({company_name}) at ${price:.2f}.
Your job is to argue STRONGLY AGAINST buying this stock. Identify risks, overvaluation, or deteriorating fundamentals.

{len(bear_agents)} analysts rated it SELL/STRONG SELL:
{bear_summary}

The Bull Advocate argued:
{bull_arg}

{tech_block}
{risk_block}

Build the strongest possible bear case (200-300 words). Cite exact numbers. Directly rebut the bull's strongest points.
Do NOT simply say "it depends". A bear must have a clear position."""

        bear_arg = llm.chat([{"role": "user", "content": bear_prompt}], temperature=0.3, max_tokens=500)

        # ── 5. CIO synthesis — Judge ruling after full debate ────────────────
        verdicts_txt = '\n'.join(
            f"  {r.get('role','')}/{r.get('firm','')} ({n}): {r.get('verdict','?')} "
            f"({r.get('confidence','?')}%) entry=${r.get('entry_price',0):.2f} "
            f"sl=${r.get('stop_loss',0):.2f} t=${r.get('target_price',0):.2f} — {r.get('reasoning','')}"
            for n, r in agent_results.items()
        )

        coord_prompt = f"""You are the Chief Investment Officer and Judge. {len(agent_results)} expert analysts
have independently evaluated {ticker} ({company_name}) at ${price:.2f}.

All Expert Verdicts:
{verdicts_txt}

## Bull Advocate's Case
{bull_arg}

## Bear Advocate's Case
{bear_arg}

You have heard the full adversarial debate. Now give your definitive ruling.
Be decisive — a CIO must commit to a position. Your investment_thesis must explicitly reference which side's argument was stronger and why.

Respond ONLY with this JSON:
{{
  "final_verdict": "STRONG BUY" | "BUY" | "HOLD" | "SELL" | "STRONG SELL",
  "consensus_score": <0-100>,
  "bull_count": <int>,
  "bear_count": <int>,
  "neutral_count": <int>,
  "entry_price": <number>,
  "stop_loss": <number>,
  "target_1": <number>,
  "target_2": <number>,
  "target_3": <number>,
  "time_horizon": "short-term (1-5 days)" | "medium-term (2-6 weeks)" | "long-term (3-6 months)",
  "investment_thesis": "<4-5 sentences — weigh bull vs bear explicitly, state which side won and why>",
  "bull_case": "<2 sentences — strongest bull arguments from the debate>",
  "bear_case": "<2 sentences — strongest bear arguments from the debate>",
  "key_risks": ["<risk 1>", "<risk 2>", "<risk 3>"],
  "position_size_pct": <integer 1-10>,
  "short_term_action": "BUY NOW" | "BUY ON DIP" | "HOLD" | "REDUCE" | "SELL NOW" | "AVOID",
  "short_term_reason": "<1 sentence — what should a trader do TODAY and why, citing price levels>"
}}"""

        coord_raw = llm.chat([{"role": "user", "content": coord_prompt}], temperature=0.2, max_tokens=1000)
        coord_raw = _re.sub(r'^```(?:json)?\s*\n?', '', coord_raw.strip(), flags=_re.IGNORECASE)
        coord_raw = _re.sub(r'\n?```\s*$', '', coord_raw).strip()
        m     = _re.search(r'\{[\s\S]*\}', coord_raw)
        final = json.loads(m.group()) if m else {}

        # ── 6. Build D3 graph ────────────────────────────────────────────────
        v_score = {'STRONG BUY': 1.0, 'BUY': 0.75, 'HOLD': 0.5, 'SELL': 0.25, 'STRONG SELL': 0.0}
        nodes, links = [], []

        nodes.append({
            "id": ticker, "label": ticker, "type": "ticker",
            "sentiment": v_score.get(final.get('final_verdict', 'HOLD'), 0.5),
            "price": round(price, 2), "rsi": round(rsi, 1),
            "trend": trend, "company": company_name,
        })

        for name, result in agent_results.items():
            aid     = f"ag_{name.replace(' ', '_').lower()}"
            verdict = result.get('verdict', 'HOLD')
            nodes.append({
                "id": aid, "label": name, "type": "agent",
                "sentiment":    v_score.get(verdict, 0.5),
                "verdict":      verdict,
                "confidence":   result.get('confidence', 50),
                "role":         result.get('role', ''),
                "firm":         result.get('firm', ''),
                "reasoning":    result.get('reasoning', ''),
                "data_focus":   result.get('data_focus', 'fundamental'),
                "entry_price":  result.get('entry_price'),
                "stop_loss":    result.get('stop_loss'),
                "target_price": result.get('target_price'),
                "key_findings":   result.get('key_findings', []),
                "entity_source":  result.get('entity_source', 'unknown'),
            })
            links.append({"source": aid, "target": ticker, "relation": "analyzes", "weight": 0.8})

            for i, finding in enumerate(result.get('key_findings', [])[:2]):
                eid = f"{aid}_e{i}"
                nodes.append({"id": eid, "label": finding[:60], "type": "evidence",
                               "sentiment": v_score.get(verdict, 0.5)})
                links.append({"source": eid, "target": aid, "relation": "supports", "weight": 0.35})

        fv = final.get('final_verdict', 'HOLD')
        nodes.append({
            "id": "verdict", "label": f"▶ {fv}", "type": "verdict",
            "sentiment": v_score.get(fv, 0.5),
            "entry": final.get('entry_price'), "stop_loss": final.get('stop_loss'),
            "t1": final.get('target_1'), "t2": final.get('target_2'), "t3": final.get('target_3'),
        })
        for r in agent_results.values():
            aid = f"ag_{r.get('agent','').replace(' ', '_').lower()}"
            links.append({"source": aid, "target": "verdict", "relation": "vote", "weight": 0.5})

        for i, risk in enumerate(final.get('key_risks', [])[:3]):
            rid = f"risk_{i}"
            nodes.append({"id": rid, "label": risk[:60], "type": "risk", "sentiment": 0.15})
            links.append({"source": rid, "target": "verdict", "relation": "risk", "weight": 0.3})

        real_count = sum(1 for a in agent_specs_raw if a.get('entity_source') in
                        ('company_officer', 'news_ner', 'institutional_holder', 'analyst_rating', 'news_ner_inst'))
        data = {
            "ticker":       ticker,
            "company":      company_name,
            "price":        round(price, 2),
            "agent_count":  len(agent_results),
            "real_agent_count": real_count,
            "nodes":        nodes,
            "links":        links,
            "agents":       agent_results,
            "final":        final,
            "financials":   financials_data,
            "debate": {
                "bull_arg":         bull_arg,
                "bear_arg":         bear_arg,
                "bull_agent_count": len(bull_agents),
                "bear_agent_count": len(bear_agents),
            },
        }

        _cache_set(analysis_key, data, ttl=600)
        return jsonify({"success": True, "data": data})

    except Exception as e:
        logger.error(f"Invest analysis failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500





# ── Invest Analysis — SSE Streaming ──────────────────────────────────────────
# Fetches market data, runs 20 parallel agents via AnalysisRunner (cerebrum),
# streams each result to the browser, then runs Bull/Bear debate and CIO verdict.


def _auto_trade_on_verdict(ticker: str, price: float, cio: dict, emit_fn) -> None:
    """
    Called after CIO verdict. Opens a paper (or live) position if the signal
    is strong enough and no position already exists.

    Rules:
      - Only acts on STRONG BUY or BUY with short_term_action == 'BUY NOW'
      - Skips if a position in this ticker is already open in the wallet
      - Position size = min(cio.position_size_pct, 10%) of wallet cash
      - SL / T1 / T2 come directly from CIO verdict
      - Live trading enabled only when LIVE_TRADING=1 env var is set
    """
    verdict       = cio.get('final_verdict', 'HOLD')
    short_action  = cio.get('short_term_action', '')
    position_pct  = min(float(cio.get('position_size_pct', 2)), 10) / 100.0

    # Only act on clear directional signals
    if verdict not in ('STRONG BUY', 'BUY', 'STRONG SELL', 'SELL') or \
       short_action not in ('BUY NOW', 'SELL NOW'):
        logger.info(f"Auto-trade: no action for {ticker} — verdict={verdict} action={short_action}")
        return

    from ..services.position_monitor import _load_wallet, open_position
    from ..services import broker_utils as bu

    if not bu.is_safe_hours():
        logger.info(f"Auto-trade skipped for {ticker}: outside safe hours / NSE holiday")
        return

    wallet = _load_wallet()
    if wallet.get('kill_switch'):
        emit_fn('trade', ticker=ticker, action='SKIP',
                reason=f"kill_switch: {wallet.get('kill_reason')}")
        return
    if ticker in wallet.get('positions', {}):
        logger.info(f"Auto-trade: {ticker} already in portfolio, skipping")
        emit_fn('trade', ticker=ticker, action='SKIP',
                reason='Already holding position')
        return

    from ..services.fo_scanner import (
        _build_trading_symbol, UNDERLYING_MAP, _resolve_option_contract, _lot_size_for,
    )
    itype  = cio.get('instrument_type', 'EQ')
    strike = float(cio.get('strike_price', 0) or 0)

    # ── Resolve real broker contract (CE/PE) from instrument master ───────────
    trading_symbol = None
    sec_id, exch   = '', ''
    expiry_iso     = ''
    lot_size       = None
    premium        = float(cio.get('estimated_premium', 0) or 0)

    if itype in ('CE', 'PE'):
        contract = _resolve_option_contract(ticker, itype, strike)
        if not contract:
            emit_fn('trade', ticker=ticker, action='SKIP',
                    reason=f'No {itype} contract for strike {strike}')
            return
        meta = bu.fno_meta(contract['trading_symbol'])
        if not meta or not meta.get('lot_size'):
            emit_fn('trade', ticker=ticker, action='SKIP',
                    reason='Missing lot_size in master')
            return
        trading_symbol = contract['trading_symbol']
        lot_size       = meta['lot_size']
        sec_id, exch   = meta['security_id'], meta['exchange']
        expiry_iso     = meta['expiry'].isoformat() if meta.get('expiry') else ''
        if meta.get('expiry') == bu.today_ist() and bu.now_ist().time() >= bu.THETA_EXIT:
            emit_fn('trade', ticker=ticker, action='SKIP', reason='theta-exit zone')
            return
        if contract.get('ltp'):
            premium = float(contract['ltp'])
        strike = float(meta.get('strike') or strike)
    elif itype == 'FUT':
        lot_size = _lot_size_for(ticker)
        if not lot_size:
            emit_fn('trade', ticker=ticker, action='SKIP', reason='no lot_size for FUT')
            return
        trading_symbol = _build_trading_symbol(ticker, cio, price)
        premium = price
    else:
        lot_size       = 1
        trading_symbol = _build_trading_symbol(ticker, cio, price)
        premium        = price

    if premium <= 0:
        emit_fn('trade', ticker=ticker, action='SKIP', reason='invalid premium')
        return

    cash         = wallet.get('cash', 0)
    budget       = cash * position_pct
    cost_per_lot = premium * lot_size if itype != 'EQ' else premium
    if cost_per_lot <= 0 or cost_per_lot > cash:
        emit_fn('trade', ticker=ticker, action='SKIP',
                reason=f'Insufficient cash (₹{cash:.0f}) for cost/lot ₹{cost_per_lot:.0f}')
        return
    qty = max(1, int(budget // cost_per_lot))

    stop_loss = float(cio.get('stop_loss', round(price * 0.95, 2)))
    target_1  = float(cio.get('target_1',  round(price * 1.08, 2)))
    target_2  = float(cio.get('target_2',  round(price * 1.15, 2)))
    live      = bu.is_live_mode()

    reason = (f"CIO: {verdict} | {cio.get('short_term_reason', '')} | "
              f"thesis: {cio.get('investment_thesis', '')[:120]}")

    underlying = UNDERLYING_MAP.get(ticker, ticker)
    result = open_position(
        pos_key=trading_symbol,
        underlying=underlying,
        instrument_type=itype,
        qty=qty,
        lot_size=lot_size,
        avg_entry=premium,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        strike_price=strike,
        expiry=expiry_iso,
        security_id=sec_id,
        exchange=exch,
        reason=reason,
        live=live,
    )

    emit_fn('trade',
            ticker=ticker,
            trading_symbol=trading_symbol,
            instrument_type=itype,
            action='BUY',
            qty=qty,
            lot_size=lot_size,
            premium=premium,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            verdict=verdict,
            mode='live' if live else 'paper',
            order_status=result.get('status'),
            reason=reason)

    logger.info(f"Auto-trade executed: {trading_symbol} ({itype}) BUY qty={qty} lots "
                f"premium={premium:.2f} SL={stop_loss} T1={target_1} T2={target_2} "
                f"mode={'live' if live else 'paper'}")


def _premarket_macro_snapshot() -> dict:
    """
    Real-time pre-market & cross-asset snapshot fed to every Cerebrum agent.
    Cached 60 s so a full F&O scan cycle (16 tickers) makes one network call.

    Returns a flat dict of human-readable lines + structured numbers:
      {
        'india_vix':         14.2,
        'india_vix_regime':  'CALM',
        'gift_nifty_pts':    +85.5,
        'gift_nifty_pct':    +0.36,
        'nikkei_pct':        +0.42,
        'hang_seng_pct':     -0.18,
        ...
        'premarket_signal':  'BULLISH GAP UP ~85pts',
        'premarket_lines':   ['India VIX: 14.2 (CALM)', 'GIFT Nifty: +85 pts ...']
      }
    Every line that fails fetch is silently dropped — never blocks downstream.
    """
    cached = _cache_get('premarket_macro')
    if cached:
        return cached

    import yfinance as _yf
    from concurrent.futures import ThreadPoolExecutor

    # ── universe of indicators ──────────────────────────────────────────────
    # (key, yfinance_symbol, label, group)
    SOURCES = [
        ('gift_nifty',  'NIFTY_F1.NS',  'GIFT Nifty',     'pre_market'),
        ('sgx_nifty',   '^NSEI',        'NIFTY 50 close', 'pre_market'),  # last close ref
        ('nikkei',      '^N225',        'Nikkei 225',     'asia'),
        ('hang_seng',   '^HSI',         'Hang Seng',      'asia'),
        ('kospi',       '^KS11',        'KOSPI',          'asia'),
        ('shanghai',    '000001.SS',    'Shanghai Comp.', 'asia'),
        ('dow',         '^DJI',         'Dow Jones',      'us'),
        ('sp500',       '^GSPC',        'S&P 500',        'us'),
        ('nasdaq',      '^IXIC',        'NASDAQ',         'us'),
        ('vix_us',      '^VIX',         'CBOE VIX',       'us'),
        ('dxy',         'DX-Y.NYB',     'Dollar Index',   'fx_commod'),
        ('usd_inr',     'INR=X',        'USD/INR',        'fx_commod'),
        ('brent',       'BZ=F',         'Brent crude',    'fx_commod'),
        ('us_10y',      '^TNX',         'US 10Y yield',   'fx_commod'),
        ('gold',        'GC=F',         'Gold futures',   'fx_commod'),
    ]

    def _fetch(item):
        key, sym, label, group = item
        try:
            h = _yf.Ticker(sym).history(period='5d', interval='1d')
            if h is None or h.empty or len(h) < 1:
                return None
            last = float(h['Close'].iloc[-1])
            prev = float(h['Close'].iloc[-2]) if len(h) >= 2 else last
            chg_abs = last - prev
            chg_pct = (chg_abs / prev * 100.0) if prev else 0.0
            return {
                'key': key, 'label': label, 'group': group,
                'last': last, 'change_abs': chg_abs, 'change_pct': chg_pct,
            }
        except Exception:
            return None

    out: dict = {}
    lines: list = []
    rows: dict = {}
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for r in pool.map(_fetch, SOURCES):
                if r:
                    rows[r['key']] = r
    except Exception:
        pass

    # ── India VIX (real-time via IndMoney first) ────────────────────────────
    try:
        from .indmoney import _ind_ltp
        ivix = _ind_ltp('^INDIAVIX')
        if not ivix:
            v = _yf.Ticker('^INDIAVIX').history(period='2d', interval='1d')
            ivix = float(v['Close'].iloc[-1]) if v is not None and not v.empty else None
        if ivix:
            regime = ('CALM'      if ivix < 13 else
                      'NORMAL'    if ivix < 18 else
                      'ELEVATED'  if ivix < 22 else
                      'FEAR'      if ivix < 28 else 'PANIC')
            out['india_vix'] = round(ivix, 2)
            out['india_vix_regime'] = regime
            lines.append(f"India VIX: {ivix:.1f} ({regime})")
    except Exception:
        pass

    # ── GIFT Nifty (most important pre-market signal) ───────────────────────
    if rows.get('gift_nifty'):
        g = rows['gift_nifty']
        out['gift_nifty_pts'] = round(g['change_abs'], 1)
        out['gift_nifty_pct'] = round(g['change_pct'], 2)
        out['gift_nifty_last'] = round(g['last'], 1)
        sig = ('STRONG GAP UP'  if g['change_abs'] > 100 else
               'GAP UP'         if g['change_abs'] > 30  else
               'STRONG GAP DN'  if g['change_abs'] < -100 else
               'GAP DOWN'       if g['change_abs'] < -30  else
               'FLAT OPEN')
        out['premarket_signal'] = sig
        lines.append(f"GIFT Nifty: {g['change_abs']:+.0f} pts ({g['change_pct']:+.2f}%) → {sig}")

    # ── Asia (open before us) ──────────────────────────────────────────────
    asia = []
    for k in ('nikkei', 'hang_seng', 'kospi', 'shanghai'):
        if rows.get(k):
            r = rows[k]
            out[f'{k}_pct'] = round(r['change_pct'], 2)
            asia.append(f"{r['label']} {r['change_pct']:+.2f}%")
    if asia:
        # net Asian sentiment
        avg = sum(rows[k]['change_pct'] for k in ('nikkei','hang_seng','kospi','shanghai') if rows.get(k))
        n = sum(1 for k in ('nikkei','hang_seng','kospi','shanghai') if rows.get(k))
        avg = avg / n if n else 0
        out['asia_avg_pct'] = round(avg, 2)
        tone = 'risk-on' if avg > 0.3 else 'risk-off' if avg < -0.3 else 'mixed'
        lines.append(f"Asia: {' · '.join(asia)} (avg {avg:+.2f}% — {tone})")

    # ── US (Friday close → leads tomorrow's mood) ──────────────────────────
    us = []
    for k in ('dow', 'sp500', 'nasdaq'):
        if rows.get(k):
            r = rows[k]
            out[f'{k}_pct'] = round(r['change_pct'], 2)
            us.append(f"{r['label']} {r['change_pct']:+.2f}%")
    if rows.get('vix_us'):
        out['us_vix'] = round(rows['vix_us']['last'], 2)
        us.append(f"US VIX {rows['vix_us']['last']:.1f}")
    if us:
        lines.append(f"US Friday: {' · '.join(us)}")

    # ── FX & commodities (drive FII flows + import inflation) ──────────────
    fx = []
    if rows.get('dxy'):
        out['dxy_pct'] = round(rows['dxy']['change_pct'], 2)
        fx.append(f"DXY {rows['dxy']['last']:.1f} ({rows['dxy']['change_pct']:+.2f}%)")
    if rows.get('usd_inr'):
        out['usd_inr'] = round(rows['usd_inr']['last'], 2)
        out['usd_inr_pct'] = round(rows['usd_inr']['change_pct'], 2)
        fx.append(f"USD/INR ₹{rows['usd_inr']['last']:.2f} ({rows['usd_inr']['change_pct']:+.2f}%)")
    if rows.get('brent'):
        out['brent_usd'] = round(rows['brent']['last'], 2)
        out['brent_pct'] = round(rows['brent']['change_pct'], 2)
        fx.append(f"Brent ${rows['brent']['last']:.1f} ({rows['brent']['change_pct']:+.2f}%)")
    if rows.get('us_10y'):
        out['us_10y'] = round(rows['us_10y']['last'], 2)
        fx.append(f"US 10Y {rows['us_10y']['last']:.2f}%")
    if rows.get('gold'):
        out['gold_usd'] = round(rows['gold']['last'], 2)
        fx.append(f"Gold ${rows['gold']['last']:.0f} ({rows['gold']['change_pct']:+.2f}%)")
    if fx:
        lines.append(f"FX/Commod: {' · '.join(fx)}")

    out['premarket_lines'] = lines
    out['premarket_summary'] = '\n'.join(f"- {x}" for x in lines) if lines else ''
    out['premarket_fetched_at'] = datetime.now().isoformat()
    _cache_set('premarket_macro', out, ttl=60)
    return out


def _fetch_market_data(ticker: str) -> dict:
    """
    Fetch all market data needed for the analysis pipeline.
    Returns a structured dict that maps directly to blackboard keys.

    Price priority:  IndMoney WebSocket cache → IndMoney REST → yfinance close
    OHLCV priority:  IndMoney 5-min (7 days) → IndMoney daily (200 days) → yfinance
    """
    import yfinance as yf
    from .indmoney import _ind_ltp, _ind_candles

    _INDEX_NAMES = {
        '^NSEI':      'NIFTY 50',
        '^NSEBANK':   'BANKNIFTY',
        '^NSEMDCP50': 'NIFTY MIDCAP 50',
        '^CNXFIN':    'FINNIFTY',
        '^INDIAVIX':  'India VIX',
        '^GSPC':      'S&P 500',
    }

    is_index = ticker.startswith('^')

    # ── 1. IndMoney live price + OHLCV ───────────────────────────────────────
    ind_price: float | None = _ind_ltp(ticker)

    # 5-min candles first (intraday signals), fall back to daily (for EMA200 etc.)
    ind_hist = _ind_candles(ticker, '5m', days=7)
    if not ind_hist or len(ind_hist) < 20:
        ind_hist = _ind_candles(ticker, '1d', days=200)
    if ind_hist and len(ind_hist) >= 20:
        logger.debug(f"IndMoney candles for {ticker}: {len(ind_hist)} bars")

    # ── 2. yfinance — OHLCV fallback + fundamentals ──────────────────────────
    yf_ticker = yf.Ticker(ticker)

    if ind_hist and len(ind_hist) >= 20:
        import pandas as _pd
        hist = _pd.DataFrame([{
            'Close':  c['close'],  'Open':  c['open'],
            'High':   c['high'],   'Low':   c['low'],
            'Volume': c['volume'],
        } for c in ind_hist],
        index=_pd.to_datetime([c['date'] for c in ind_hist]))
    else:
        hist = yf_ticker.history(period='200d', interval='1d')

    info: dict = {}
    if not is_index:
        try:
            info = yf_ticker.info or {}
        except Exception:
            pass

    # Price: IndMoney wins, then yfinance close, then info field
    price        = ind_price or (float(hist['Close'].iloc[-1]) if not hist.empty else float(info.get('currentPrice', 0) or 0))
    company_name = (_INDEX_NAMES.get(ticker)
                        or _COMPANY_NAME_MAP.get(ticker.upper())
                        or info.get('longName') or info.get('shortName')
                        or ticker)

    logger.debug(f"{'IndMoney' if ind_price else 'yfinance'} price for {ticker}: {price}")

    # ── News (indices: skip yfinance news, fetch from RSS) ────────────────────
    news: list = []
    if not is_index:
        try:
            for item in (yf_ticker.news or [])[:15]:
                c = item.get('content', {})
                t = c.get('title', item.get('title', ''))
                s = c.get('summary', item.get('summary', ''))
                if t:
                    news.append({'title': t, 'summary': s})
        except Exception:
            pass

    # ── Analyst ratings (indices have none) ───────────────────────────────────
    analysts: list = []
    if not is_index:
        try:
            recs = yf_ticker.recommendations
            if recs is not None and not recs.empty:
                seen: set = set()
                for _, row in recs.sort_index(ascending=False).iterrows():
                    firm  = str(row.get('Firm', '')).strip()
                    grade = str(row.get('To Grade', row.get('Action', ''))).strip()
                    if firm and firm not in seen:
                        seen.add(firm)
                        analysts.append({'firm': firm, 'grade': grade})
                    if len(analysts) >= 8:
                        break
        except Exception:
            pass

    # ── Institutional holders (indices have none) ─────────────────────────────
    holders: list = []
    if not is_index:
        try:
            h = yf_ticker.institutional_holders
            if h is not None and not h.empty:
                for _, row in h.head(6).iterrows():
                    hn  = str(row.get('Holder', row.get('Name', ''))).strip()
                    pct = row.get('% Out', row.get('pctHeld', 0))
                    sh  = row.get('Shares', 0)
                    if hn:
                        holders.append({'name': hn, 'pct': float(pct) if pct else 0,
                                        'shares': int(sh) if sh else 0})
        except Exception:
            pass

    # ── Financials (indices have none) ────────────────────────────────────────
    financials_data: dict = {}
    if not is_index:
        try:
            inc = yf_ticker.financials
            bal = yf_ticker.balance_sheet
            cf  = yf_ticker.cashflow

            def _fmt(df, row_key, n=3):
                out = []
                if df is not None and not df.empty:
                    for col in list(df.columns)[:n]:
                        try:
                            v = float(df.loc[row_key, col]) if row_key in df.index else None
                            out.append((str(col)[:4], round(v / 1e7, 1) if v else None))
                        except Exception:
                            out.append((str(col)[:4], None))
                return out

            rev  = _fmt(inc, 'Total Revenue')
            net  = _fmt(inc, 'Net Income')
            ebit = _fmt(inc, 'EBIT')
            fcf  = _fmt(cf,  'Free Cash Flow')
            de   = _fmt(bal, 'Total Debt')
            cr_n = _fmt(bal, 'Current Assets')
            cr_d = _fmt(bal, 'Current Liabilities')

            def _yr_map(lst): return {yr: v for yr, v in lst}
            cr_n_m = _yr_map(cr_n)
            cr_d_m = _yr_map(cr_d)
            cr_lst = [(yr, round(cr_n_m[yr] / cr_d_m[yr], 2) if cr_d_m.get(yr) else None) for yr, _ in cr_n]

            financials_data = {
                'revenue': rev, 'net_income': net, 'ebit': ebit,
                'fcf': fcf, 'debt_equity': de, 'current_ratio': cr_lst,
            }
        except Exception:
            pass

    # ── Technicals — computed via pure-Python ta_utils (Supertrend, ADX, etc.) ─
    technicals: dict = {}
    candlesticks: dict = {}
    try:
        from ..services.ta_utils import compute_all as _compute_all

        # Build candles list from hist DataFrame
        _candles = []
        for _idx in hist.index:
            try:
                _candles.append({
                    'open':   float(hist.loc[_idx, 'Open']),
                    'high':   float(hist.loc[_idx, 'High']),
                    'low':    float(hist.loc[_idx, 'Low']),
                    'close':  float(hist.loc[_idx, 'Close']),
                    'volume': int(hist.loc[_idx, 'Volume']) if 'Volume' in hist.columns else 0,
                })
            except Exception:
                continue

        ta = _compute_all(_candles)
        trend = ('Bullish' if (ta.get('ema50') and ta.get('ema200') and price > ta['ema50'] > ta['ema200'])
                 else 'Bearish' if (ta.get('ema50') and price < ta['ema50']) else 'Mixed')

        technicals = {
            **ta,
            'trend':      trend,
            'beta':       info.get('beta'),
            'avg_volume': info.get('averageVolume'),
        }

        # Pass last 75 bars as list of objects (75 × 5-min = full trading day)
        # LLM can read {date,open,high,low,close,volume} dicts far better than
        # parallel arrays — enables accurate pattern and price-action analysis
        _recent = _candles[-75:] if len(_candles) >= 75 else _candles[-20:]
        _dates  = [str(d.date() if hasattr(d, 'date') else d) for d in hist.index[-len(_recent):]]
        candlesticks = [
            {
                'date':   _dates[_k] if _k < len(_dates) else '',
                'open':   round(_recent[_k]['open'],   2),
                'high':   round(_recent[_k]['high'],   2),
                'low':    round(_recent[_k]['low'],    2),
                'close':  round(_recent[_k]['close'],  2),
                'volume': int(_recent[_k].get('volume', 0)),
            }
            for _k in range(len(_recent))
        ]
    except Exception as _te:
        logger.warning(f"Technicals computation failed for {ticker}: {_te}")

    # ── Fundamentals ──────────────────────────────────────────────────────────
    fundamentals = {
        'pe':             info.get('trailingPE'),
        'forward_pe':     info.get('forwardPE'),
        'pb':             info.get('priceToBook'),
        'roe':            info.get('returnOnEquity'),
        'net_margin':     info.get('profitMargins'),
        'debt_to_equity': info.get('debtToEquity'),
        'market_cap':     info.get('marketCap'),
        'analyst_target': info.get('targetMeanPrice'),
        'recommendation': info.get('recommendationKey'),
        'sector':         info.get('sector'),
        'industry':       info.get('industry'),
        'earnings_growth': info.get('earningsGrowth'),
        'revenue_growth':  info.get('revenueGrowth'),
    }

    # ── Macro (available context) ─────────────────────────────────────────────
    macro = {
        'market_context': f'{ticker} ({company_name}) at ₹{price:.2f}',
        'sector':  'Index' if is_index else info.get('sector', 'Unknown'),
        'country': 'India' if is_index else info.get('country', 'India'),
        'beta':    info.get('beta'),
    }
    # Enrich with pre-market / global / cross-asset snapshot so all Cerebrum
    # agents (macro, risk, technical, sentiment) see the same global context.
    try:
        macro.update(_premarket_macro_snapshot())
    except Exception as _e:
        logger.debug(f"premarket snapshot failed: {_e}")

    # ── Reddit ────────────────────────────────────────────────────────────────
    # Reddit: skip for indices (no useful posts for ^NSEI), 2s timeout to not block scanner
    reddit: list = []
    if not is_index:
        try:
            import urllib.request
            is_indian  = ticker.endswith('.NS') or ticker.endswith('.BO')
            subs       = (['IndiaInvestments', 'IndianStockMarket', 'stocks']
                          if is_indian else ['wallstreetbets', 'stocks', 'investing'])
            sym_base   = ticker.replace('.NS', '').replace('.BO', '')
            for sub in subs[:2]:
                url = f"https://www.reddit.com/r/{sub}/search.json?q={sym_base}&sort=hot&limit=5&t=week"
                req = urllib.request.Request(url, headers={'User-Agent': 'PhoenixTrade/1.0'})
                try:
                    with urllib.request.urlopen(req, timeout=2) as resp:
                        rj = json.loads(resp.read())
                        for p in rj.get('data', {}).get('children', []):
                            pd_ = p.get('data', {})
                            title = pd_.get('title', '')
                            score = pd_.get('score', 0)
                            if title and score > 5:
                                reddit.append(f"{title} (score: {score})")
                except Exception:
                    pass
        except Exception:
            pass

    return {
        'company_name': company_name,
        'price':        price,
        'technicals':   technicals,
        'candlesticks': candlesticks,
        'fundamentals': fundamentals,
        'financials':   financials_data,
        'macro':        macro,
        'news':         news,
        'reddit':       reddit,
        'fomo_score':   0,
        'analysts':     analysts,
        'holders':      holders,
    }


def _build_invest_graph(agents_list: list, final: dict, ticker: str,
                        company_name: str, price: float) -> tuple:
    """Build D3 force-graph nodes and links from agent output list."""
    v_score = {'STRONG BUY': 1.0, 'BUY': 0.75, 'HOLD': 0.5, 'SELL': 0.25, 'STRONG SELL': 0.0}
    fv      = final.get('final_verdict', 'HOLD')
    nodes, links = [], []

    nodes.append({
        'id': ticker, 'label': ticker, 'type': 'ticker',
        'sentiment': v_score.get(fv, 0.5),
        'price': round(price, 2), 'company': company_name,
    })

    for r in agents_list:
        name    = r.get('name', r.get('agent_id', ''))
        aid     = f"ag_{name.replace(' ', '_').lower()}"
        verdict = r.get('verdict', 'HOLD')
        nodes.append({
            'id': aid, 'label': name, 'type': 'agent',
            'sentiment':   v_score.get(verdict, 0.5),
            'verdict':     verdict,
            'confidence':  r.get('confidence', 50),
            'role':        r.get('role', ''),
            'firm':        r.get('firm', ''),
            'reasoning':   r.get('reasoning', ''),
            'data_focus':  r.get('data_focus', ''),
            'entry_price': r.get('entry_price'),
            'stop_loss':   r.get('stop_loss'),
            'target_price': r.get('target_price'),
            'key_findings': r.get('key_findings', []),
            'entity_source': r.get('entity_source', 'domain_expert'),
        })
        links.append({'source': aid, 'target': ticker, 'relation': 'analyzes', 'weight': 0.8})
        for i, finding in enumerate(r.get('key_findings', [])[:2]):
            eid = f"{aid}_e{i}"
            nodes.append({'id': eid, 'label': finding[:60], 'type': 'evidence',
                          'sentiment': v_score.get(verdict, 0.5)})
            links.append({'source': eid, 'target': aid, 'relation': 'supports', 'weight': 0.35})

    nodes.append({
        'id': 'verdict', 'label': f'▶ {fv}', 'type': 'verdict',
        'sentiment': v_score.get(fv, 0.5),
        'entry':     final.get('entry_price'),
        'stop_loss': final.get('stop_loss'),
        't1': final.get('target_1'), 't2': final.get('target_2'), 't3': final.get('target_3'),
    })
    for r in agents_list:
        name = r.get('name', r.get('agent_id', ''))
        aid  = f"ag_{name.replace(' ', '_').lower()}"
        links.append({'source': aid, 'target': 'verdict', 'relation': 'vote', 'weight': 0.5})
    for i, risk in enumerate(final.get('key_risks', [])[:3]):
        rid = f"risk_{i}"
        nodes.append({'id': rid, 'label': risk[:60], 'type': 'risk', 'sentiment': 0.15})
        links.append({'source': rid, 'target': 'verdict', 'relation': 'risk', 'weight': 0.3})

    return nodes, links


@market_bp.route('/invest-analysis-stream/<ticker>', methods=['GET'])
def invest_analysis_stream(ticker: str):
    from flask import stream_with_context, Response

    ticker       = _resolve_ticker(ticker)
    analysis_key = _cache_key('invest_analysis', ticker)

    cached = _cache_get(analysis_key)
    if cached:
        def _cached_gen():
            yield f"data: {json.dumps({'type': 'done', 'data': cached, '_cached': True})}\n\n"
        return Response(stream_with_context(_cached_gen()),
                        mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    sq = _Queue()

    def _emit(event_type: str, **kw):
        sq.put(json.dumps({'type': event_type, **kw}, ensure_ascii=False, default=str))

    def _run():
        try:
            from ..services.cerebrum.runner import AnalysisRunner

            llm       = LLMClient()
            _raw_ref  = [{}]

            def _market_data_fn(t):
                data = _fetch_market_data(t)
                _raw_ref[0] = data
                return data

            def _runner_emit(evt, payload):
                _emit(evt, **payload)

            result = AnalysisRunner().run(ticker, _runner_emit, llm,
                                          market_data_fn=_market_data_fn)

            raw         = _raw_ref[0]
            agents_list = result['agents']   # list of AgentOutput.to_dict()

            _REAL_SOURCES = {'company_officer', 'news_ner', 'institutional_holder', 'analyst_rating'}
            agents_dict = {}
            for r in agents_list:
                r = dict(r)
                r['agent'] = r['name']       # frontend compat: cards use result.agent key
                agents_dict[r['name']] = r

            debate     = result['debate']
            final      = result['cio']
            nodes, links = _build_invest_graph(agents_list, final, ticker,
                                               result['company_name'], result['price'])

            data = {
                'ticker':           ticker,
                'company':          result['company_name'],
                'price':            round(result['price'], 2),
                'agent_count':      len(agents_dict),
                'real_agent_count': sum(1 for r in agents_list
                                        if r.get('entity_source') in _REAL_SOURCES),
                'nodes':            nodes,
                'links':            links,
                'agents':           agents_dict,
                'final':            final,
                'financials':       raw.get('financials', {}),
                'debate': {
                    'bull_arg':         debate.get('bull_arg', ''),
                    'bear_arg':         debate.get('bear_arg', ''),
                    'bull_agent_count': debate.get('bull_count', 0),
                    'bear_agent_count': debate.get('bear_count', 0),
                },
            }
            _cache_set(analysis_key, data, ttl=600)

            try:
                from ..services.kernel.memory import memory_manager as _mem
                _mem.store_cio_decision(
                    ticker=ticker, price=result['price'],
                    verdict=final.get('final_verdict', 'HOLD'),
                    thesis=final.get('investment_thesis', ''),
                    technicals=str(raw.get('technicals', '')),
                )
            except Exception:
                pass

            # ── Auto-trade: wire CIO verdict → position ───────────────────────
            try:
                _auto_trade_on_verdict(ticker, result['price'], final, _emit)
            except Exception as _ate:
                logger.warning(f"Auto-trade skipped for {ticker}: {_ate}")

            _emit('done', data=data)

        except Exception as exc:
            logger.error(f"invest_analysis_stream failed for {ticker}: {exc}", exc_info=True)
            _emit('error', msg=str(exc))
        finally:
            sq.put(None)

    _threading.Thread(target=_run, daemon=True).start()

    def _gen():
        while True:
            try:
                msg = sq.get(timeout=120)
                if msg is None:
                    break
                yield f"data: {msg}\n\n"
            except _QueueEmpty:
                yield 'data: {"type":"heartbeat"}\n\n'

    return Response(stream_with_context(_gen()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ── Portfolio Diversification Allocator — 30 Agents, 3 Rounds, SSE ────────────
# (_threading, _Queue, _QueueEmpty already imported at top of file)

# 30 diverse global investor personas
_PORTFOLIO_AGENTS = [
    {"name": "Rajiv Sharma",     "city": "Mumbai",     "style": "Aggressive Growth",       "focus": "Indian large-caps, momentum"},
    {"name": "Priya Nair",       "city": "Bangalore",  "style": "SIP / Systematic",        "focus": "IT sector, index ETFs"},
    {"name": "Deepak Mehta",     "city": "Delhi",      "style": "Value Investing",         "focus": "Undervalued Indian bluechips"},
    {"name": "Sarah Johnson",    "city": "New York",   "style": "EM Fund Manager",         "focus": "India macro, foreign capital flows"},
    {"name": "Michael Chen",     "city": "Singapore",  "style": "Quantitative / Algo",     "focus": "Momentum, mean-reversion signals"},
    {"name": "Emma Wilson",      "city": "London",     "style": "ESG / Sustainability",    "focus": "Green energy, responsible companies"},
    {"name": "Hiroshi Tanaka",   "city": "Tokyo",      "style": "Conservative Institution","focus": "Dividend yield, low-volatility assets"},
    {"name": "Carlos Rodriguez", "city": "São Paulo",  "style": "EM Specialist",           "focus": "BRICS growth, India story"},
    {"name": "Ahmed Al-Rashid",  "city": "Dubai",      "style": "NRI / Gulf Investor",     "focus": "Gold, commodities, India realty plays"},
    {"name": "Aditya Kumar",     "city": "Pune",       "style": "Tech Enthusiast",         "focus": "IT, fintech, digital India"},
    {"name": "Thomas Anderson",  "city": "Chicago",    "style": "Derivatives / Volatility","focus": "Options strategy, ATR-based plays"},
    {"name": "Preeti Agarwal",   "city": "Mumbai",     "style": "Mutual Fund Manager",     "focus": "Index funds, balanced allocation"},
    {"name": "James O'Brien",    "city": "Dublin",     "style": "ETF Strategist",          "focus": "Passive, factor investing, low cost"},
    {"name": "Anand Krishnan",   "city": "Chennai",    "style": "Fundamental Analyst",     "focus": "P&L, balance sheet quality"},
    {"name": "Maria Garcia",     "city": "Madrid",     "style": "Global Asset Allocator",  "focus": "Cross-asset, portfolio construction"},
    {"name": "Robert Taylor",    "city": "Sydney",     "style": "Infrastructure / Energy", "focus": "Power, utilities, PSU capex cycle"},
    {"name": "Nisha Patel",      "city": "Ahmedabad",  "style": "Small Cap Hunter",        "focus": "Mid/small Indian growth companies"},
    {"name": "Ravi Pillai",      "city": "Kerala/UAE", "style": "NRI Diaspora",            "focus": "Remittance investment, USD/INR plays"},
    {"name": "Arjun Singh",      "city": "Toronto",    "style": "Macro Trader",            "focus": "RBI policy, FII flows, sector rotation"},
    {"name": "Jennifer Wu",      "city": "Hong Kong",  "style": "Asia Comparative",        "focus": "India vs China, Asian allocation"},
    {"name": "Klaus Mueller",    "city": "Frankfurt",  "style": "Dividend Investor",       "focus": "High yield, steady compounders"},
    {"name": "Yuki Nakamura",    "city": "Osaka",      "style": "Conservative / Safe",     "focus": "Gold, sovereign bonds, capital preservation"},
    {"name": "Boris Kovalev",    "city": "Warsaw",     "style": "Contrarian",              "focus": "Beaten-down sectors, mean reversion"},
    {"name": "Fatima Hassan",    "city": "Cairo",      "style": "Commodity Specialist",    "focus": "Gold, silver, energy, metals"},
    {"name": "David Kim",        "city": "Seoul",      "style": "Technology Growth",       "focus": "Semiconductors, AI, cloud exposure"},
    {"name": "Vikram Malhotra",  "city": "Hyderabad",  "style": "Pharma / Healthcare",     "focus": "Pharma exports, API companies, hospitals"},
    {"name": "Sunita Verma",     "city": "Kolkata",    "style": "PSU / Govt Schemes",      "focus": "PSU banks, defence PSUs, CPSE ETFs"},
    {"name": "Sofia Petrov",     "city": "Moscow",     "style": "Energy / Resources",      "focus": "Oil, gas, metals, global commodity cycle"},
    {"name": "Amit Joshi",       "city": "Jaipur",     "style": "Real Estate Alternatives","focus": "REITs, InvITs, infrastructure trusts"},
    {"name": "Li Wei",           "city": "Shanghai",   "style": "Global Diversifier",      "focus": "International ETFs, USD assets from India"},
]

_PORTFOLIO_UNIVERSE = {
    "large_cap": [
        ("RELIANCE.NS","Reliance Industries","Energy+Retail conglomerate"),
        ("TCS.NS","TCS","IT services, AI exposure"),
        ("HDFCBANK.NS","HDFC Bank","Largest private bank"),
        ("INFY.NS","Infosys","IT, strong USD revenue"),
        ("ICICIBANK.NS","ICICI Bank","Retail+corporate banking"),
        ("SBIN.NS","SBI","Largest PSU bank"),
        ("BHARTIARTL.NS","Airtel","Telecom, Africa growth"),
        ("ITC.NS","ITC","FMCG+hotel diversification"),
        ("LT.NS","L&T","Infrastructure/capex play"),
        ("HINDUNILVR.NS","Hindustan Unilever","FMCG, consumer staples"),
        ("BAJFINANCE.NS","Bajaj Finance","Consumer lending"),
        ("MARUTI.NS","Maruti Suzuki","Auto, EV transition"),
        ("TATAMOTORS.NS","Tata Motors","Auto+JLR, EV leader"),
        ("SUNPHARMA.NS","Sun Pharma","Pharma, US generics"),
        ("KOTAKBANK.NS","Kotak Bank","Private bank, wealth mgmt"),
    ],
    "mid_cap": [
        ("TATAPOWER.NS","Tata Power","Renewables capex"),
        ("IRFC.NS","IRFC","Railway financing, govt-backed"),
        ("PERSISTENT.NS","Persistent Systems","IT mid-cap, AI"),
        ("COFORGE.NS","Coforge","IT services growth"),
        ("POLYCAB.NS","Polycab","Cables+wires, infra"),
        ("DIXON.NS","Dixon Tech","Electronics manufacturing"),
        ("HAL.NS","HAL","Defence, Make-in-India"),
        ("BEL.NS","BEL","Defence electronics PSU"),
        ("RVNL.NS","RVNL","Railway construction"),
        ("ZOMATO.NS","Zomato","Food delivery, Blinkit"),
        ("DRREDDY.NS","Dr Reddy's","Pharma, generics"),
        ("CIPLA.NS","Cipla","Pharma, chronic disease"),
    ],
    "etf_index": [
        ("NIFTYBEES.NS","Nifty 50 ETF","Tracks NIFTY 50 index"),
        ("JUNIORBEES.NS","Nifty Next 50 ETF","Mid-cap index exposure"),
        ("BANKBEES.NS","Nifty Bank ETF","Banking sector ETF"),
        ("ITBEES.NS","Nifty IT ETF","IT sector index"),
        ("MOM100.NS","Nifty Momentum 100 ETF","Momentum factor ETF"),
        ("CPSEETF.NS","CPSE ETF","PSU basket"),
        ("MAFANG.NS","MAFANG ETF","US tech - Meta/Apple/FANG"),
        ("MON100.NS","Motilal Oswal Nasdaq 100","US Nasdaq exposure"),
    ],
    "commodity": [
        ("GOLDBEES.NS","Gold ETF","Physical gold, inflation hedge"),
        ("SILVERBEES.NS","Silver ETF","Industrial+store of value"),
        ("NMDC.NS","NMDC","Iron ore, steel raw material"),
        ("COALINDIA.NS","Coal India","Energy security, dividend"),
    ],
    "reit_invit": [
        ("EMBASSY.NS","Embassy REIT","Commercial real estate"),
        ("MINDSPACE.NS","Mindspace REIT","IT park rentals"),
        ("POWERGRID.NS","PowerGrid InvIT","Transmission infra"),
    ],
}


@market_bp.route('/portfolio-sim-stream', methods=['GET'])
def portfolio_sim_stream():
    """
    SSE endpoint: streams 30-agent 3-round portfolio simulation in real-time.
    Each agent's output is pushed the instant that LLM call completes.
    Query params: capital (float), horizon (short|long|both)
    """
    from flask import stream_with_context, Response as _Response
    import re as _re
    import yfinance as yf
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor, as_completed

    capital = float(request.args.get('capital', 10000))
    horizon = request.args.get('horizon', '3m').lower()
    scope   = request.args.get('scope', 'all').lower()

    q = _Queue()   # thread-safe bridge between parallel agents and SSE generator

    def _emit(event_type: str, **data):
        payload = json.dumps({"type": event_type, **data}, ensure_ascii=False)
        q.put(payload)

    def _run():
        try:
            llm = LLMClient()

            horizon_txt = {
                "1w":   "VERY SHORT-TERM (1 week) — momentum breakouts, technical setups only.",
                "2w":   "SHORT-TERM (2 weeks) — swing trades, strong technical setups.",
                "1m":   "SHORT-TERM (1 month) — event-driven and momentum plays.",
                "3m":   "MEDIUM-TERM (3 months) — sector rotation, earnings growth catalysts.",
                "6m":   "MEDIUM-TERM (6 months) — fundamental + technical confluence plays.",
                "1y":   "LONG-TERM (1 year) — quality compounders, PE re-rating stories.",
                "3y":   "LONG-TERM (3 years) — structural growth themes, buy-and-hold.",
                # legacy fallbacks
                "short": "SHORT-TERM (1-6 weeks) — momentum and technical setups.",
                "long":  "LONG-TERM (6-36 months) — fundamentals, growth, compounding.",
                "both":  "MIX of short-term momentum plays AND long-term compounders.",
            }.get(horizon, "MEDIUM-TERM (3 months) — balanced opportunity mix.")

            # Filter universe by scope
            if scope == 'all' or scope not in _PORTFOLIO_UNIVERSE:
                active_universe = _PORTFOLIO_UNIVERSE
            else:
                active_universe = {scope: _PORTFOLIO_UNIVERSE[scope]}

            # ── Fetch live prices ─────────────────────────────────────────────
            _emit("status", msg="Fetching live prices across all asset classes…", round=0)

            def _price_data(sym):
                try:
                    _end   = datetime.now()
                    _start = _end - timedelta(days=90)
                    df = None
                    for _attempt in range(2):
                        df = yf.download(sym, start=_start.strftime('%Y-%m-%d'), end=_end.strftime('%Y-%m-%d'),
                                         interval='1d', progress=False, auto_adjust=True)
                        if df is not None and len(df) >= 15:
                            break
                    if df is None or len(df) < 15:
                        return None
                    c = df['Close'].squeeze()
                    h = df['High'].squeeze()
                    l = df['Low'].squeeze()
                    price = float(c.iloc[-1])
                    delta = c.diff()
                    gain  = delta.clip(lower=0).rolling(14).mean()
                    loss  = (-delta.clip(upper=0)).rolling(14).mean()
                    rsi   = float((100 - (100 / (1 + gain / loss.replace(0, np.nan)))).iloc[-1])
                    ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
                    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
                    chg1m = float((c.iloc[-1] - c.iloc[max(0, len(c)-22)]) / c.iloc[max(0, len(c)-22)] * 100)
                    tr    = np.maximum(h-l, np.maximum(abs(h-c.shift()), abs(l-c.shift())))
                    atr   = float(tr.rolling(14).mean().iloc[-1])
                    return {"price": round(price,2), "rsi": round(rsi,1),
                            "ema20": round(ema20,2), "ema50": round(ema50,2),
                            "chg1m": round(chg1m,2), "atr": round(atr,4),
                            "sl": round(price - atr*1.5, 2), "t1": round(price + atr*1.5, 2),
                            "t2": round(price + atr*3.0, 2)}
                except:
                    return None

            all_syms = [(cls, sym, name, desc)
                        for cls, items in active_universe.items()
                        for sym, name, desc in items]
            market_data = {}
            with ThreadPoolExecutor(max_workers=15) as pool:
                futs = {pool.submit(_price_data, sym): (cls, sym, name, desc)
                        for cls, sym, name, desc in all_syms}
                for fut in as_completed(futs):
                    cls, sym, name, desc = futs[fut]
                    try:
                        d = fut.result()
                        if d:
                            market_data[(cls, sym)] = {"name": name, "desc": desc, **d}
                    except:
                        pass

            def _universe_txt():
                lines = []
                for cls, items in active_universe.items():
                    lines.append(f"\n[{cls.upper().replace('_',' ')}]")
                    for sym, name, desc in items:
                        d = market_data.get((cls, sym))
                        if d:
                            lines.append(f"  {name} ({sym}) [{desc}]: ₹{d['price']} | RSI={d['rsi']} | "
                                         f"1M={d['chg1m']:+.1f}% | SL=₹{d['sl']} | T1=₹{d['t1']}")
                        else:
                            lines.append(f"  {name} ({sym}) [{desc}]: price unavailable")
                return "\n".join(lines)

            universe_txt = _universe_txt()
            _emit("status", msg=f"Live data ready for {len(market_data)} instruments. Launching Round 1 — 30 agents in parallel…", round=0)

            # ── ROUND 1 ───────────────────────────────────────────────────────
            _emit("round_start", round=1, total_rounds=3,
                  label="Round 1 — Independent Analysis",
                  desc="30 agents independently scan the universe and pick their best opportunity.")

            round1_results = {}

            def _r1(agent):
                llm = LLMClient()
                prompt = f"""You are {agent['name']} from {agent['city']}, a {agent['style']} investor.
Your focus: {agent['focus']}

INVESTMENT MISSION: Allocate ₹{capital:,.0f} for an Indian investor. Horizon: {horizon_txt}

LIVE MARKET UNIVERSE:
{universe_txt}

Recommend your TOP 1-2 picks based on your expertise and the live data.

Respond ONLY with JSON (no markdown):
{{
  "picks": [
    {{
      "ticker": "<NSE symbol>",
      "name": "<instrument name>",
      "asset_class": "<large_cap|mid_cap|etf_index|commodity|reit_invit>",
      "conviction": <60-99>,
      "suggested_alloc_pct": <5-25>,
      "entry": <price>,
      "stop_loss": <price>,
      "target_1": <price>,
      "target_2": <price>,
      "timeframe": "<e.g. 3-6 weeks | 6-12 months>",
      "thesis": "<1-2 sentences why>"
    }}
  ],
  "market_view": "<1 sentence on market conditions>"
}}"""
                raw = llm.chat([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=400)
                raw = _re.sub(r'^```(?:json)?\s*\n?', '', raw.strip(), flags=_re.IGNORECASE)
                raw = _re.sub(r'\n?```\s*$', '', raw).strip()
                m   = _re.search(r'\{[\s\S]*\}', raw)
                return json.loads(m.group()) if m else {}

            with ThreadPoolExecutor(max_workers=10) as pool:
                futs = {pool.submit(_r1, ag): ag for ag in _PORTFOLIO_AGENTS}
                for fut in as_completed(futs):
                    ag = futs[fut]
                    try:
                        result = fut.result()
                        round1_results[ag["name"]] = {"agent": ag, "result": result}
                        picks_summary = "; ".join(
                            f"{p.get('ticker','?').split('.')[0]} ({p.get('conviction','?')}%)"
                            for p in result.get("picks", [])
                        ) or "no picks"
                        # Push event immediately as this agent finishes
                        _emit("agent_action",
                              round=1,
                              agent=ag["name"], city=ag["city"], style=ag["style"],
                              picks=result.get("picks", []),
                              market_view=result.get("market_view", ""),
                              msg=f"{picks_summary}")
                    except Exception as e:
                        round1_results[ag["name"]] = {"agent": ag, "result": {}}

            # Tally votes
            ticker_votes: dict = {}
            for name, data in round1_results.items():
                for p in data["result"].get("picks", []):
                    t = p.get("ticker", "")
                    if t:
                        if t not in ticker_votes:
                            ticker_votes[t] = {"count": 0, "total_conviction": 0, "picks": [], "name": ""}
                        ticker_votes[t]["count"]            += 1
                        ticker_votes[t]["total_conviction"] += p.get("conviction", 70)
                        ticker_votes[t]["picks"].append(p)
                        ticker_votes[t]["name"] = p.get("name", t)

            top_voted = sorted(ticker_votes.items(), key=lambda x: x[1]["count"], reverse=True)[:8]
            top_txt   = "\n".join(
                f"  {sym} ({d['name']}): {d['count']} agents, avg conviction {d['total_conviction']//max(d['count'],1)}%"
                for sym, d in top_voted
            )
            _emit("round_end", round=1,
                  top_picks=[{"ticker": s, **d} for s, d in top_voted],
                  msg=f"Round 1 done. Top consensus picks:\n{top_txt}")

            # ── ROUND 2 ───────────────────────────────────────────────────────
            _emit("round_start", round=2, total_rounds=3,
                  label="Round 2 — Debate & Peer Review",
                  desc="Agents see the group consensus and challenge or reinforce positions.")

            round2_results = {}

            def _r2(agent):
                llm = LLMClient()
                r1 = round1_results.get(agent["name"], {}).get("result", {})
                my_picks = "; ".join(p.get("ticker","").split(".")[0] for p in r1.get("picks",[]))
                prompt = f"""You are {agent['name']} from {agent['city']}, a {agent['style']} investor.

ROUND 1 CONSENSUS — most popular picks across 30 global agents:
{top_txt}

YOUR Round 1 picks: {my_picks or 'none'}

Do you AGREE with the consensus, DISAGREE, or see a CONTRARIAN opportunity the crowd missed?

Respond ONLY with JSON:
{{
  "stance": "AGREE" | "PARTIALLY_AGREE" | "CONTRARIAN",
  "support": ["<ticker you reinforce>"],
  "challenge": "<ticker you think is overhyped or empty string>",
  "challenge_reason": "<why, if any>",
  "contrarian_pick": "<overlooked ticker not in top-8, or empty>",
  "contrarian_thesis": "<why, if any>",
  "comment": "<1 sentence debate contribution>"
}}"""
                raw = llm.chat([{"role": "user", "content": prompt}], temperature=0.45, max_tokens=250)
                raw = _re.sub(r'^```(?:json)?\s*\n?', '', raw.strip(), flags=_re.IGNORECASE)
                raw = _re.sub(r'\n?```\s*$', '', raw).strip()
                m   = _re.search(r'\{[\s\S]*\}', raw)
                return json.loads(m.group()) if m else {}

            with ThreadPoolExecutor(max_workers=10) as pool:
                futs = {pool.submit(_r2, ag): ag for ag in _PORTFOLIO_AGENTS}
                for fut in as_completed(futs):
                    ag = futs[fut]
                    try:
                        result = fut.result()
                        round2_results[ag["name"]] = result
                        _emit("agent_action",
                              round=2,
                              agent=ag["name"], city=ag["city"], style=ag["style"],
                              stance=result.get("stance",""),
                              support=result.get("support",[]),
                              challenge=result.get("challenge",""),
                              contrarian=result.get("contrarian_pick",""),
                              msg=_re.sub(r'\b(\w+)\.(NS|BO)\b', r'\1', result.get("comment","")))
                    except:
                        round2_results[ag["name"]] = {}

            # Adjust scores based on debate
            for r2 in round2_results.values():
                for t in r2.get("support", []):
                    if t in ticker_votes:
                        ticker_votes[t]["count"] += 0.5
                challenged = r2.get("challenge", "")
                if challenged and challenged in ticker_votes:
                    ticker_votes[challenged]["count"] = max(0, ticker_votes[challenged]["count"] - 0.3)
                contrarian = r2.get("contrarian_pick", "")
                if contrarian and contrarian not in ticker_votes:
                    ticker_votes[contrarian] = {"count": 1, "total_conviction": 65, "picks": [], "name": contrarian}

            top_final = sorted(ticker_votes.items(), key=lambda x: x[1]["count"], reverse=True)[:8]
            _emit("round_end", round=2, msg="Round 2 debate complete. Portfolio Manager synthesizing final allocation…")

            # ── ROUND 3: Portfolio Manager ─────────────────────────────────────
            _emit("round_start", round=3, total_rounds=3,
                  label="Round 3 — Portfolio Manager Synthesis",
                  desc="Portfolio Manager builds the final diversified allocation from all debate.")

            top_detail = []
            for sym, d in top_final:
                mdata = next((v for (cls, s), v in market_data.items() if s == sym), None)
                top_detail.append({
                    "ticker": sym, "name": d.get("name", sym),
                    "agent_votes": round(d["count"], 1),
                    "avg_conviction": d["total_conviction"] // max(int(d["count"]), 1),
                    "price": mdata["price"] if mdata else None,
                    "sl": mdata["sl"] if mdata else None,
                    "t1": mdata["t1"] if mdata else None,
                    "t2": mdata["t2"] if mdata else None,
                })

            pm_prompt = f"""You are the Portfolio Manager. 30 global specialists debated for 2 rounds and converged on:

{json.dumps(top_detail, indent=2)}

CAPITAL: ₹{capital:,.0f} | HORIZON: {horizon_txt}

Build the FINAL portfolio. Rules:
- allocation_pct must sum to exactly 100
- 5-10% CASH buffer required
- No single equity > 25%
- At least 3 different asset classes
- Use live prices from data above

Respond ONLY with JSON:
{{
  "portfolio": [
    {{
      "ticker": "<symbol or CASH>",
      "name": "<name>",
      "asset_class": "<large_cap|mid_cap|etf_index|commodity|reit_invit|cash>",
      "allocation_pct": <int>,
      "allocation_amt": <float>,
      "entry": <price or null>,
      "stop_loss": <price or null>,
      "target_1": <price or null>,
      "target_2": <price or null>,
      "timeframe": "<timeframe or liquid>",
      "verdict": "STRONG BUY|BUY|HOLD|CASH",
      "confidence": <int>,
      "agent_votes": <number>,
      "rationale": "<2 sentences: why this allocation>"
    }}
  ],
  "total_invested": <float>,
  "cash_kept": <float>,
  "expected_return_pct": <annual estimate>,
  "max_risk_pct": <worst case drawdown>,
  "diversification_score": <0-100>,
  "summary": "<3-4 sentence portfolio thesis>"
}}"""

            pm_raw = llm.chat([{"role": "user", "content": pm_prompt}], temperature=0.2, max_tokens=2500)
            pm_raw = _re.sub(r'^```(?:json)?\s*\n?', '', pm_raw.strip(), flags=_re.IGNORECASE)
            pm_raw = _re.sub(r'\n?```\s*$', '', pm_raw).strip()
            pm_m   = _re.search(r'\{[\s\S]*\}', pm_raw)
            portfolio_out = {}
            if pm_m:
                raw_json = pm_m.group()
                try:
                    portfolio_out = json.loads(raw_json)
                except json.JSONDecodeError:
                    # Truncated JSON — strip last incomplete entry and close brackets
                    fixed = _re.sub(r',\s*\{[^}]*$', '', raw_json.rstrip())
                    fixed = _re.sub(r',\s*$', '', fixed)
                    fixed += ']' * max(0, fixed.count('[') - fixed.count(']'))
                    fixed += '}' * max(0, fixed.count('{') - fixed.count('}'))
                    try:
                        portfolio_out = json.loads(fixed)
                    except Exception:
                        pass

            # Rule-based fallback if JSON still failed
            if not portfolio_out.get("portfolio"):
                cash_pct   = 8
                invest_pct = 100 - cash_pct
                items = []
                total_votes = sum(d["count"] for _, d in top_final) or 1
                for sym, d in top_final[:6]:
                    alloc_pct = max(5, min(25, round(d["count"] / total_votes * invest_pct)))
                    mdata = next((v for (cls, s), v in market_data.items() if s == sym), None)
                    items.append({
                        "ticker": sym, "name": d.get("name", sym),
                        "asset_class": next((cls for (cls, s) in market_data if s == sym), "large_cap"),
                        "allocation_pct": alloc_pct,
                        "allocation_amt": round(capital * alloc_pct / 100, 2),
                        "entry": mdata["price"] if mdata else None,
                        "stop_loss": mdata["sl"]   if mdata else None,
                        "target_1":  mdata["t1"]   if mdata else None,
                        "target_2":  mdata["t2"]   if mdata else None,
                        "timeframe": "3-6 months", "verdict": "BUY",
                        "confidence": d["total_conviction"] // max(int(d["count"]), 1),
                        "agent_votes": round(d["count"], 1),
                        "rationale": f"Chosen by {int(d['count'])} agents, avg conviction {d['total_conviction']//max(int(d['count']),1)}%.",
                    })
                # Re-normalise to invest_pct
                total = sum(i["allocation_pct"] for i in items)
                if items and total != invest_pct:
                    items[-1]["allocation_pct"] += invest_pct - total
                    items[-1]["allocation_amt"]  = round(capital * items[-1]["allocation_pct"] / 100, 2)
                items.append({
                    "ticker": "CASH", "name": "Cash Buffer", "asset_class": "cash",
                    "allocation_pct": cash_pct, "allocation_amt": round(capital * cash_pct / 100, 2),
                    "entry": None, "stop_loss": None, "target_1": None, "target_2": None,
                    "timeframe": "liquid", "verdict": "CASH", "confidence": 100, "agent_votes": 0,
                    "rationale": "Emergency buffer and opportunity reserve.",
                })
                portfolio_out = {
                    "portfolio": items,
                    "total_invested": round(capital * invest_pct / 100, 2),
                    "cash_kept": round(capital * cash_pct / 100, 2),
                    "expected_return_pct": 15, "max_risk_pct": 20,
                    "diversification_score": min(100, len(items) * 14),
                    "summary": f"Consensus portfolio of {len(items)-1} positions selected by 30 global agents across 3 rounds.",
                }

            _emit("agent_action", round=3,
                  agent="Portfolio Manager", city="Global HQ", style="Chief Investment Officer",
                  picks=[], msg=f"Final allocation built across {len(portfolio_out.get('portfolio',[]))} positions.")

            _emit("completed",
                  portfolio={
                      "capital": capital, "horizon": horizon, "scope": scope,
                      "agent_count": len(_PORTFOLIO_AGENTS), "rounds": 3,
                      "portfolio": portfolio_out,
                  })

        except Exception as e:
            _emit("error", msg=str(e))
        finally:
            q.put(None)   # sentinel — tells generator to close stream

    _threading.Thread(target=_run, daemon=True).start()

    def _generate():
        while True:
            try:
                msg = q.get(timeout=120)
                if msg is None:
                    break
                yield f"data: {msg}\n\n"
            except _QueueEmpty:
                yield "data: {\"type\":\"heartbeat\"}\n\n"

    from flask import stream_with_context, Response as _FlaskResponse
    resp = _FlaskResponse(
        stream_with_context(_generate()),
        mimetype='text/event-stream'
    )
    resp.headers['Cache-Control']      = 'no-cache'
    resp.headers['X-Accel-Buffering']  = 'no'
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp
