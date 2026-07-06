"""
Market data API — FastAPI router.

Covers: world indices, OHLCV, signals, universe, search, budget,
fundamentals, graph, AI predict, invest analysis, scan, MC news, quick sim.
"""

from __future__ import annotations

import json
import threading
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..config import settings
from ..dependencies import get_broker
from ..infrastructure.broker.base import resolve_option_security
from ..shared.logger import get_logger
from ..shared.time import clock, datetime, fmt_candle_date, now_ist

logger = get_logger('api.market')
router = APIRouter(prefix='/api/market', tags=['market'])


# ── In-memory cache ─────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, any]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str, ttl: float = 60.0):
    with _cache_lock:
        entry = _cache.get(key)
    if entry and clock() - entry[0] < ttl:
        return entry[1]
    return None


def _cache_set(key: str, value):
    with _cache_lock:
        _cache[key] = (clock(), value)


# ── World indices ────────────────────────────────────────────────────────────

_INDEX_TICKERS = {
    '^NSEI':     {'name': 'NIFTY 50',       'country': 'IN'},
    '^NSEBANK':  {'name': 'BANK NIFTY',     'country': 'IN'},
    '^CNXFIN':   {'name': 'FIN NIFTY',      'country': 'IN'},
    '^BSESN':    {'name': 'SENSEX',         'country': 'IN'},
    '^INDIAVIX': {'name': 'India VIX',      'country': 'IN'},
    '^GSPC':     {'name': 'S&P 500',        'country': 'US'},
    '^DJI':      {'name': 'Dow Jones',      'country': 'US'},
    '^IXIC':     {'name': 'NASDAQ',         'country': 'US'},
    '^FTSE':     {'name': 'FTSE 100',       'country': 'UK'},
    '^N225':     {'name': 'Nikkei 225',     'country': 'JP'},
    '^HSI':      {'name': 'Hang Seng',      'country': 'HK'},
}


@router.get('/world-indices')
def world_indices():
    cached = _cache_get('world_indices', 120)
    if cached:
        return {'success': True, 'data': cached, '_cached': True}

    broker = get_broker()

    def _fetch_one(yf_ticker: str, meta: dict) -> dict:
        entry = {
            'ticker': yf_ticker,
            'name': meta['name'],
            'country': meta['country'],
            'price': None,
            'change': None,
            'change_pct': None,
        }
        try:
            ltp = broker.get_ltp(yf_ticker)
            entry['price'] = ltp
        except Exception:
            pass
        if entry['price'] is None:
            try:
                import yfinance as yf
                info = yf.Ticker(yf_ticker).fast_info
                entry['price'] = getattr(info, 'last_price', None) or getattr(info, 'regularMarketPrice', None)
                prev = getattr(info, 'previous_close', None) or getattr(info, 'regularMarketPreviousClose', None)
                if entry['price'] and prev:
                    entry['change'] = round(entry['price'] - prev, 2)
                    entry['change_pct'] = round((entry['change'] / prev) * 100, 2)
            except Exception:
                pass
        return entry

    # Fetch all indices in PARALLEL — old code was sequential (10 × 4s = 40s worst case)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=10, thread_name_prefix='idx') as pool:
        futures = {pool.submit(_fetch_one, t, m): t for t, m in _INDEX_TICKERS.items()}
        results_map = {}
        for f in as_completed(futures):
            t = futures[f]
            try:
                results_map[t] = f.result()
            except Exception:
                results_map[t] = {'ticker': t, 'name': _INDEX_TICKERS[t]['name'],
                                  'country': _INDEX_TICKERS[t]['country'],
                                  'price': None, 'change': None, 'change_pct': None}
    # Preserve original order
    results = [results_map[t] for t in _INDEX_TICKERS if t in results_map]

    _cache_set('world_indices', results)
    return {'success': True, 'data': results}


# ── Ticker normalizer (broker symbol → yfinance-compatible) ──────────────────

_KNOWN_INDICES = {
    'NIFTY':      '^NSEI',     'NIFTY 50':    '^NSEI',     'NIFTY50':     '^NSEI',
    'BANKNIFTY':  '^NSEBANK',  'BANK NIFTY':  '^NSEBANK',  'NIFTYBANK':   '^NSEBANK',
    'FINNIFTY':   '^CNXFIN',   'FIN NIFTY':   '^CNXFIN',
    'SENSEX':     '^BSESN',
    'VIX':        '^INDIAVIX', 'INDIAVIX':    '^INDIAVIX', 'INDIA VIX':   '^INDIAVIX',
    'MIDCAP':     '^NSEMDCP50', 'NIFTYMIDCAP': '^NSEMDCP50',
    'SPX':        '^GSPC',     'S&P500':      '^GSPC',     'SP500':       '^GSPC',
    'DJI':        '^DJI',      'DOW':         '^DJI',      'DOWJONES':    '^DJI',
    'NASDAQ':     '^IXIC',     'IXIC':        '^IXIC',
}


def _normalize_ticker(raw: str) -> str:
    """Convert broker/user-typed symbol to yfinance-compatible ticker.

    Rules:
        1. Already yfinance-compatible (^NSEI, SBIN.NS, AAPL) → pass through
        2. Known index alias (NIFTY, VIX, BANKNIFTY) → map to ^symbol
        3. Indian stock without suffix → append .NS
        4. F&O symbols (contain '-') → leave as-is (handled by broker)
    """
    t = raw.strip().upper()
    if not t:
        return t

    # Already has yfinance markers
    if t.startswith('^') or '.NS' in t or '.BO' in t or '=' in t:
        return t

    # Known index alias
    if t in _KNOWN_INDICES:
        return _KNOWN_INDICES[t]

    # F&O contract — extract underlying and map to index ticker for charting
    if '-' in t and any(x in t for x in ('CE', 'PE', 'FUT')):
        underlying = t.split('-')[0]
        if underlying in _KNOWN_INDICES:
            return _KNOWN_INDICES[underlying]
        return f'{underlying}.NS'

    # US tickers are typically short alpha (AAPL, MSFT, TSLA)
    # Indian tickers also look similar — default to .NS (NSE India)
    # Heuristic: if it looks like a plain equity symbol, append .NS
    # The caller can always pass AAPL directly if they mean US
    return f'{t}.NS'


# ── OHLCV ────────────────────────────────────────────────────────────────────

@router.get('/ohlcv')
def ohlcv(
    ticker: str = Query(...),
    interval: str = Query('1d'),
    start_date: str = Query(''),
    end_date: str = Query(''),
):
    days = 30
    if start_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            end = datetime.strptime(end_date, '%Y-%m-%d') if end_date else now_ist()
            days = max(1, (end - start).days)
        except ValueError:
            pass

    ticker = _normalize_ticker(ticker)

    # Cache avoids hitting broker/yfinance API on every FE poll/tab-switch.
    # Intraday (1m/5m) = 30s TTL, daily+ = 120s TTL.
    _ttl = 30.0 if interval in ('1m', '5m', '15m', '30m') else 120.0
    cache_key = f'ohlcv|{ticker}|{interval}|{days}'
    cached = _cache_get(cache_key, ttl=_ttl)
    if cached is not None:
        return cached

    _is_intraday = interval in ('1m', '5m', '15m', '30m', '1h', '2h')

    # ── 1) Try the active broker first — fastest, real-time data ────────────
    try:
        broker = get_broker()
        if broker.is_configured:
            # Option symbols (…-CE / …-PE) need a security_id — the bare synthetic
            # symbol won't resolve. Resolve via the broker adapter (agnostic) so the
            # 3rd chart pane can plot the contract when an F&O card is clicked.
            _tu = ticker.upper()
            _is_option = '-CE' in _tu or '-PE' in _tu
            _exch, _sec_id = resolve_option_security(broker, ticker) if _is_option else ('', '')
            if _sec_id:
                candles = broker.get_candles(ticker, interval=interval, days=days,
                                             exchange=_exch, security_id=_sec_id)
            elif _is_option:
                # Option with no resolved security_id: DO NOT fall back to bare
                # get_candles — for an unresolved option symbol it returns the
                # UNDERLYING INDEX candles (₹24k), which then render as a corrupt
                # index-scale chart. Return no data instead of wrong data.
                logger.warning(f'ohlcv: could not resolve option {ticker} — no chart data')
                candles = None
            else:
                candles = broker.get_candles(ticker, interval=interval, days=days)
            if candles:
                candles_list = [
                    {'date': c.date, 'open': c.open, 'high': c.high,
                     'low': c.low, 'close': c.close, 'volume': c.volume}
                    for c in candles
                ]
                result = {'success': True, 'data': {'ticker': ticker, 'ohlcv': candles_list}}
                _cache_set(cache_key, result)
                return result
    except Exception as e:
        logger.debug(f'ohlcv broker attempt failed for {ticker}: {e}')

    # ── 2) Fallback to yfinance ───────────────────────────────────────
    try:
        import yfinance as yf
        period = f'{days}d' if days <= 730 else 'max'
        _IV_MAP = {'1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
                   '1h': '1h', '1d': '1d', '1w': '1wk', '1M': '1mo'}
        yf_interval = _IV_MAP.get(interval, '1d')
        df = yf.download(ticker, period=period, interval=yf_interval,
                         progress=False, auto_adjust=True)
        if df is not None and not df.empty:
            candles_list = []
            for idx, row in df.iterrows():
                def _v(col):
                    v = row[col]
                    return float(v.iloc[0]) if hasattr(v, 'iloc') else float(v)
                candles_list.append({
                    'date': fmt_candle_date(str(idx), _is_intraday),
                    'open': _v('Open'), 'high': _v('High'),
                    'low': _v('Low'),   'close': _v('Close'),
                    'volume': int(_v('Volume')),
                })
            result = {'success': True, 'data': {'ticker': ticker, 'ohlcv': candles_list}}
            _cache_set(cache_key, result)
            return result
        return JSONResponse({'success': False, 'error': 'no data'}, 404)
    except Exception as e:
        logger.warning(f'ohlcv failed for {ticker}: {e}')
        return JSONResponse({'success': False, 'error': str(e)}, 500)



# ── Signals ──────────────────────────────────────────────────────────────────

_REDDIT_HEADERS = {
    'User-Agent': 'Vega/1.0 (market intelligence dashboard)',
    'Accept': 'application/json',
}
_REDDIT_SUBS = ['IndiaInvestments', 'IndianStockMarket', 'NSE', 'Nifty50']


async def _fetch_reddit_posts(ticker: str, limit: int = 8) -> list[dict]:
    query = ticker.replace('.NS', '').replace('.BO', '').replace('^', '')
    posts: list[dict] = []
    async with httpx.AsyncClient(timeout=8, headers=_REDDIT_HEADERS, follow_redirects=True) as client:
        for sub in _REDDIT_SUBS:
            try:
                resp = await client.get(
                    f'https://www.reddit.com/r/{sub}/search.rss',
                    params={'q': query, 'sort': 'new', 'restrict_sr': 1},
                )
                if resp.status_code != 200:
                    continue
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                root = ET.fromstring(resp.text)
                for entry in root.findall('atom:entry', ns)[:3]:
                    title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
                    link_el = entry.find('atom:link', ns)
                    link = link_el.get('href', '') if link_el is not None else ''
                    updated = (entry.findtext('atom:updated', namespaces=ns) or '').strip()
                    if not title:
                        continue
                    try:
                        created = datetime.fromisoformat(updated).timestamp() if updated else 0.0
                    except Exception:
                        created = 0.0
                    posts.append({
                        'title':     title,
                        'url':       link,
                        'subreddit': sub,
                        'score':     0,
                        'comments':  0,
                        'created':   created,
                    })
            except Exception as e:
                logger.debug('Reddit r/%s error: %s', sub, e)
    posts.sort(key=lambda x: x['created'], reverse=True)
    return posts[:limit]


@router.get('/signals/{ticker:path}')
async def signals(ticker: str):
    import re as _re

    import yfinance as yf

    ticker = _normalize_ticker(ticker)
    sig_key = f'signals:{ticker}'
    cached = _cache_get(sig_key, 30.0)
    if cached:
        return {'success': True, 'data': cached, '_cached': True}

    try:
        yf_ticker = yf.Ticker(ticker)

        # ── Stats ─────────────────────────────────────────────────────────
        hist = yf_ticker.history(period='10d', interval='1d')
        stats: dict = {}
        if not hist.empty:
            closes = hist['Close'].tolist()
            vols   = hist['Volume'].tolist()
            price  = round(float(closes[-1]), 2)
            chg1d  = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else 0.0
            chg5d  = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else 0.0
            avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1)
            vol_ratio = round(vols[-1] / avg_vol, 2) if avg_vol else 1.0
            company_name = ticker
            try:
                info = yf_ticker.info or {}
                company_name = info.get('longName') or info.get('shortName') or ticker
            except Exception:
                pass
            stats = {
                'price':        price,
                'change_1d':    chg1d,
                'change_5d':    chg5d,
                'volume_ratio': vol_ratio,
                'company_name': company_name,
            }

        # ── FOMO score ────────────────────────────────────────────────────
        fomo = 50
        if stats:
            vol_score  = min(100.0, (stats['volume_ratio'] - 1) * 40)
            mom_score  = min(100.0, max(0.0, stats['change_1d'] * 10 + 50))
            chg5_score = min(100.0, max(0.0, stats['change_5d'] * 4 + 50))
            fomo = max(0, min(100, int(vol_score * 0.4 + mom_score * 0.3 + chg5_score * 0.3)))

        # ── yfinance news ─────────────────────────────────────────────────
        news_items: list[dict] = []
        try:
            for item in (yf_ticker.news or [])[:10]:
                content  = item.get('content', {})
                title    = content.get('title', item.get('title', ''))
                summary  = content.get('summary', item.get('summary', ''))
                pub_date = content.get('pubDate', '')
                prov     = content.get('provider', {})
                provider = prov.get('displayName', '') if isinstance(prov, dict) else ''
                canon    = content.get('canonicalUrl', {})
                url      = canon.get('url', '') if isinstance(canon, dict) else ''
                if title:
                    news_items.append({
                        'title':    title,
                        'summary':  summary[:200] if summary else '',
                        'source':   provider,
                        'url':      url,
                        'pub_date': pub_date,
                    })
        except Exception as e:
            logger.warning('yfinance news failed for %s: %s', ticker, e)

        # ── Reddit (async) ────────────────────────────────────────────────
        reddit_posts = await _fetch_reddit_posts(ticker)
        reddit_sentiment = 0.5
        scored = [p for p in reddit_posts if p.get('score', 0) > 0]
        if scored:
            total_sc = sum(p['score'] for p in scored)
            reddit_sentiment = round(
                sum(p.get('upvote_ratio', 0.5) * p['score'] for p in scored) / total_sc, 3
            )

        # ── Moneycontrol RSS (Indian tickers) ────────────────────────────
        is_indian = '.NS' in ticker or '.BO' in ticker
        clean_q   = ticker.replace('.NS', '').replace('.BO', '').lower()
        if is_indian:
            try:
                async with httpx.AsyncClient(timeout=6) as _cl:
                    mc_r = await _cl.get(
                        'https://www.moneycontrol.com/rss/latestnews.xml',
                        headers={'User-Agent': 'Vega/1.0', 'Accept': 'application/rss+xml'},
                    )
                if mc_r.status_code == 200:
                    for it in ET.fromstring(mc_r.content).findall('.//item')[:30]:
                        t = (it.findtext('title') or '').strip()
                        if clean_q in t.lower() or ticker.lower() in t.lower():
                            desc = _re.sub(r'<[^>]+>', '', it.findtext('description') or '').strip()[:200]
                            news_items.insert(0, {
                                'title':    t,
                                'summary':  desc,
                                'source':   'Moneycontrol',
                                'url':      (it.findtext('link') or '').strip(),
                                'pub_date': (it.findtext('pubDate') or '').strip(),
                            })
            except Exception as mc_e:
                logger.debug('Moneycontrol for %s: %s', ticker, mc_e)

        # ── ET Markets / Seeking Alpha ────────────────────────────────────
        sa_posts: list[dict] = []
        if is_indian:
            for et_url in [
                'https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms',
                'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms',
            ]:
                try:
                    async with httpx.AsyncClient(timeout=6) as _cl:
                        et_r = await _cl.get(et_url, headers={'User-Agent': 'Vega/1.0'})
                    if et_r.status_code == 200:
                        for it in ET.fromstring(et_r.content).findall('.//item')[:30]:
                            t = (it.findtext('title') or '').strip()
                            if clean_q.upper() in t.upper():
                                sa_posts.append({
                                    'title':    t,
                                    'url':      (it.findtext('link') or '').strip(),
                                    'author':   'ET Markets',
                                    'source':   'Economic Times',
                                    'pub_date': (it.findtext('pubDate') or '').strip(),
                                })
                except Exception as e:
                    logger.debug('ET Markets: %s', e)
        else:
            try:
                async with httpx.AsyncClient(timeout=6) as _cl:
                    sa_r = await _cl.get(
                        f'https://seekingalpha.com/symbol/{clean_q.upper()}/feed.xml',
                        headers={'User-Agent': 'Mozilla/5.0'},
                    )
                if sa_r.status_code == 200:
                    for it in ET.fromstring(sa_r.content).findall('.//item')[:15]:
                        t = (it.findtext('title') or '').strip()
                        if t:
                            sa_posts.append({
                                'title':    t,
                                'url':      (it.findtext('link') or '').strip(),
                                'author':   'Seeking Alpha',
                                'source':   'Seeking Alpha',
                                'pub_date': (it.findtext('pubDate') or '').strip(),
                            })
            except Exception as e:
                logger.debug('Seeking Alpha for %s: %s', ticker, e)

        result = {
            'ticker':           ticker,
            'stats':            stats,
            'fomo_score':       fomo,
            'reddit_sentiment': reddit_sentiment,
            'news':             news_items,
            'reddit':           reddit_posts,
            'stocktwits':       sa_posts,
        }
        _cache_set(sig_key, result)
        return {'success': True, 'data': result}

    except Exception as e:
        logger.error('Signals fetch failed for %s: %s', ticker, e, exc_info=True)
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)


# ── AI predict ───────────────────────────────────────────────────────────────

@router.get('/ai-predict/{ticker:path}')
def ai_predict(ticker: str):
    return {
        'success': True,
        'data': {
            'ticker': ticker,
            'prediction': 'neutral',
            'confidence': 0,
        },
    }


# ── Universe ─────────────────────────────────────────────────────────────────

_ASSET_UNIVERSE = {
    'india_equity': [
        'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
        'HINDUNILVR.NS', 'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'KOTAKBANK.NS',
        'LT.NS', 'AXISBANK.NS', 'BAJFINANCE.NS', 'MARUTI.NS', 'TITAN.NS',
    ],
    'india_index': ['^NSEI', '^NSEBANK', '^CNXFIN', '^BSESN', '^INDIAVIX'],
    'us_equity': ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META'],
}


@router.get('/universe')
def universe(asset_class: str = '', country: str = ''):
    if asset_class or country:
        key = f'{country}_{asset_class}' if country and asset_class else (country or asset_class)
        data = _ASSET_UNIVERSE.get(key, [])
    else:
        data = _ASSET_UNIVERSE
    return {'success': True, 'data': data}


@router.get('/search')
def search_ticker(q: str = Query('')):
    if not q:
        return {'success': True, 'data': []}
    broker = get_broker()
    results = broker.search_instruments(q)
    return {
        'success': True,
        'data': [
            {
                'symbol': r.symbol,
                'name': r.name,
                'exchange': r.exchange,
                'security_id': r.security_id,
                'instrument_type': r.instrument_type,
            }
            for r in results[:20]
        ],
    }


# ── Scan ─────────────────────────────────────────────────────────────────────

@router.get('/scan')
def scan_universe(market: str = 'india', limit: int = 50):
    import os as _os
    import sys
    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', 'vendor'))
    try:
        from tradingview_screener import Query, col
    except ImportError:
        return JSONResponse({'success': False, 'error': 'tradingview_screener vendor missing'}, status_code=501)

    market = market.lower()
    scan_key = f'tv_scan:{market}:{limit}'
    cached = _cache_get(scan_key, 60.0)
    if cached:
        return {'success': True, 'data': cached, '_cached': True}

    try:
        fields = ['name', 'description', 'close', 'change', 'change_abs',
                  'volume', 'relative_volume_10d_calc',
                  'RSI', 'RSI[1]', 'MACD.macd', 'MACD.signal',
                  'EMA20', 'EMA50', 'EMA200', 'ATR',
                  'market_cap_basic', 'sector', 'exchange']

        markets = ['india', 'america'] if market == 'both' else [market]
        all_results = []

        for mkt in markets:
            country  = 'IN' if mkt == 'india' else 'US'
            currency = '₹' if mkt == 'india' else '$'

            try:
                _, buy_df = (Query().set_markets(mkt).select(*fields)
                    .where(col('RSI').between(30, 60),
                           col('close') > col('EMA20'),
                           col('relative_volume_10d_calc') > 1.0)
                    .order_by('relative_volume_10d_calc', ascending=False)
                    .limit(limit).get_scanner_data())
            except Exception:
                buy_df = None

            try:
                _, sell_df = (Query().set_markets(mkt).select(*fields)
                    .where(col('RSI') > 65,
                           col('close') < col('EMA20'),
                           col('relative_volume_10d_calc') > 1.0)
                    .order_by('RSI', ascending=False)
                    .limit(limit).get_scanner_data())
            except Exception:
                sell_df = None

            try:
                _, hold_df = (Query().set_markets(mkt).select(*fields)
                    .where(col('RSI').between(45, 55),
                           col('relative_volume_10d_calc') > 0.8)
                    .order_by('volume', ascending=False)
                    .limit(limit // 2).get_scanner_data())
            except Exception:
                hold_df = None

            def _sf(v, default=0.0):
                try:
                    f = float(v) if v is not None else default
                    return default if f != f else f
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
                    'symbol':       sym + ('.NS' if mkt == 'india' else ''),
                    'name':         str(row.get('description') or sym),
                    'country':      country,
                    'exchange':     str(row.get('exchange') or ('NSE' if mkt == 'india' else 'NASDAQ')),
                    'asset_class':  'stock',
                    'sector':       str(row.get('sector') or ''),
                    'price':        entry,
                    'change_1d':    chg1d,
                    'volume_ratio': vol_ratio,
                    'rsi':          rsi,
                    'macd':         round(macd - macd_sig, 3),
                    'ema20':        round(ema20, 2),
                    'ema50':        round(ema50, 2),
                    'ema200':       round(ema200, 2),
                    'trend':        trend,
                    'score':        score,
                    'score_detail': {
                        'rsi_score': round(rsi_score, 1),
                        'ema_score': round(ema_score, 1),
                        'vol_score': round(vol_score, 1),
                        'mom_score': round(mom_score, 1),
                    },
                    'action':    action,
                    'entry':     entry,
                    'sl':        sl,
                    't1':        t1,
                    't2':        t2,
                    'currency':  currency,
                    'source':    'TradingView',
                }

            n = max(1, limit // 3)
            for df_, action in [(buy_df, 'BUY'), (sell_df, 'SELL'), (hold_df, 'HOLD')]:
                if df_ is not None and not df_.empty:
                    for _, row in df_.head(n).iterrows():
                        all_results.append(_row(row, action))

        seen: dict = {}
        for item in all_results:
            base = item['symbol'].split('.')[0]
            if base not in seen or item['score'] > seen[base]['score']:
                seen[base] = item
        deduped = sorted(seen.values(), key=lambda x: x['score'], reverse=True)

        _cache_set(scan_key, deduped)
        return {'success': True, 'data': deduped}

    except Exception as e:
        logger.error('TradingView scan failed: %s', e, exc_info=True)
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)


# ── Budget ───────────────────────────────────────────────────────────────────

@router.get('/budget')
def get_budget():
    broker = get_broker()
    cash = broker.get_available_cash()
    return {
        'success': True,
        'data': {
            'available_cash': cash,
            'daily_budget': settings.daily_loss_limit_inr,
            'used': 0.0,
        },
    }


@router.post('/budget/reset')
def reset_budget():
    return {'success': True, 'message': 'Budget reset'}


# ── Fundamentals ─────────────────────────────────────────────────────────────

@router.get('/fundamentals/{ticker:path}')
def fundamentals(ticker: str):
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return {'success': True, 'data': info}
    except Exception as e:
        return JSONResponse({'success': False, 'error': str(e)}, 500)


# ── MC News ──────────────────────────────────────────────────────────────────

@router.get('/mc-news')
def mc_news(feed: str = 'latest', limit: int = 20):
    import re as _re

    import requests as _req

    _FEEDS = {
        'latest':       'https://www.moneycontrol.com/rss/latestnews.xml',
        'markets':      'https://www.moneycontrol.com/rss/marketreports.xml',
        'stocks':       'https://www.moneycontrol.com/rss/buzzingstocks.xml',
        'mutual-funds': 'https://www.moneycontrol.com/rss/mutualfunds.xml',
        'economy':      'https://www.moneycontrol.com/rss/economy.xml',
    }
    limit = min(limit, 50)
    url   = _FEEDS.get(feed, _FEEDS['latest'])

    cache_key = f'mc_news:{feed}:{limit}'
    cached = _cache_get(cache_key, 120.0)
    if cached:
        return {'success': True, 'data': cached, '_cached': True}

    try:
        resp = _req.get(url, headers={'User-Agent': 'Vega/1.0', 'Accept': 'application/rss+xml'}, timeout=8)
        resp.raise_for_status()
        root  = ET.fromstring(resp.content)
        items = []
        for it in root.findall('.//item')[:limit]:
            title = (it.findtext('title') or '').strip()
            if not title:
                continue
            desc = _re.sub(r'<[^>]+>', '', it.findtext('description') or '').strip()[:300]
            items.append({
                'title':    title,
                'url':      (it.findtext('link') or '').strip(),
                'summary':  desc,
                'pub_date': (it.findtext('pubDate') or '').strip(),
                'source':   'Moneycontrol',
                'feed':     feed,
            })
        result = {'items': items, 'count': len(items), 'feed': feed}
        _cache_set(cache_key, result)
        return {'success': True, 'data': result}
    except Exception as e:
        logger.error('Moneycontrol news fetch failed: %s', e, exc_info=True)
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)



# ── Direct RSS news feeds (no RSSHub required) ────────────────────────────────

_RSS_FEEDS: dict[str, str] = {
    'economictimes':   'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms',
    'livemint':        'https://www.livemint.com/rss/markets',
    'finshots':        'https://finshots.in/rss/',
}

_RSS_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; Vega/1.0; +https://github.com)'
}

_rss_cache: dict[str, tuple[float, list]] = {}
_RSS_TTL = 300  # 5 min cache


def _parse_rss(xml_text: str, source: str, limit: int = 20) -> list[dict]:
    """Parse RSS/Atom XML into feed items."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        # RSS 2.0
        for item in root.findall('.//item')[:limit]:
            title = (item.findtext('title') or '').strip()
            link = (item.findtext('link') or '').strip()
            pub = (item.findtext('pubDate') or '').strip()
            desc = (item.findtext('description') or '').strip()
            if title:
                items.append({
                    'title': title,
                    'link': link,
                    'published': pub,
                    'source': source,
                    'summary': desc[:200] if desc else '',
                })
        if items:
            return items
        # Atom fallback
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        for entry in root.findall('.//atom:entry', ns)[:limit]:
            title = (entry.findtext('atom:title', namespaces=ns) or '').strip()
            link_el = entry.find('atom:link', ns)
            link = link_el.get('href', '') if link_el is not None else ''
            pub = (entry.findtext('atom:published', namespaces=ns)
                   or entry.findtext('atom:updated', namespaces=ns) or '').strip()
            summary = (entry.findtext('atom:summary', namespaces=ns) or '').strip()
            if title:
                items.append({
                    'title': title,
                    'link': link,
                    'published': pub,
                    'source': source,
                    'summary': summary[:200] if summary else '',
                })
    except Exception as e:
        logger.warning(f'RSS parse error ({source}): {e}')
    return items


@router.get('/news')
async def news_feed(
    source: str | None = None,
    limit: int = Query(default=30, le=100),
):
    """
    Fetch news from direct RSS feeds.
    ?source=moneycontrol  — single feed
    (no source)           — merge all feeds
    """
    now = clock()
    sources = [source] if source and source in _RSS_FEEDS else list(_RSS_FEEDS.keys())

    all_items: list[dict] = []
    async with httpx.AsyncClient(timeout=10, headers=_RSS_HEADERS) as client:
        for src in sources:
            cached = _rss_cache.get(src)
            if cached and (now - cached[0]) < _RSS_TTL:
                all_items.extend(cached[1])
                continue
            url = _RSS_FEEDS[src]
            try:
                resp = await client.get(url, follow_redirects=True)
                if resp.status_code == 200:
                    items = _parse_rss(resp.text, src, limit=limit)
                    _rss_cache[src] = (now, items)
                    all_items.extend(items)
                else:
                    logger.debug(f'RSS {src}: HTTP {resp.status_code}')
            except Exception as e:
                logger.debug(f'RSS {src} error: {e}')

    def _dt(s: str) -> float:
        try:
            return parsedate_to_datetime(s).timestamp()
        except Exception:
            return 0.0

    all_items.sort(key=lambda x: _dt(x.get('published', '')), reverse=True)
    return {'success': True, 'data': all_items[:limit]}


# ── F&O scanner data feed ────────────────────────────────────────────────────
# Used by engines/fo_scanner._scan_one — must not be removed.

_equity_meta_cache: dict[str, tuple] = {}   # ticker → (base, security_id, exchange)


def _resolve_equity_meta(ticker: str) -> tuple:
    """Resolve a stock ticker → (base, security_id, exchange) via the ACTIVE broker's
    instrument master, so stocks fetch LIVE broker candles instead of delayed (15-min)
    yfinance. Broker-agnostic: uses BrokerAdapter.search_instruments. Cached per ticker."""
    key = ticker.upper()
    if key in _equity_meta_cache:
        return _equity_meta_cache[key]
    base = key.replace('.NS', '').replace('.BO', '')
    meta = (base, '', 'NSE')
    try:
        for inst in (get_broker().search_instruments(base, exchange='NSE') or []):
            if (inst.instrument_type or '').upper() == 'EQUITY' and inst.symbol.upper() == base:
                meta = (base, str(inst.security_id), inst.exchange or 'NSE')
                break
    except Exception:
        pass
    _equity_meta_cache[key] = meta
    return meta


def _fetch_market_data(ticker: str) -> dict:
    """Fetch all market data needed for the F&O scan pipeline.

    Broker-first (LIVE). yfinance is a last-resort fallback only (it's ~15-min
    delayed) — never the primary source for price/OHLCV.
      Index  : broker maps ^NSEI/^BSESN internally.
      Stock  : resolve equity security_id + exchange → broker 5-min candles.
    """
    import yfinance as yf

    from ..shared.indicators import CandleData, adx_full, atr, ema, macd, rsi, supertrend

    _INDEX_NAMES = {
        '^NSEI': 'NIFTY 50', '^NSEBANK': 'BANKNIFTY',
        '^NSEMDCP50': 'NIFTY MIDCAP 50', '^CNXFIN': 'FINNIFTY',
        '^INDIAVIX': 'India VIX', '^GSPC': 'S&P 500',
    }
    is_index = ticker.startswith('^')
    broker = get_broker()

    # ── 1. Live price + OHLCV via broker ─────────────────────────────────
    if is_index:
        ind_price = broker.get_ltp(ticker)
        raw_candles = broker.get_candles(ticker, interval='5m', days=7) or []
        if len(raw_candles) < 20:
            raw_candles = broker.get_candles(ticker, interval='1d', days=200) or []
    else:
        # Stock: resolve equity security_id so we hit the broker (live), not yfinance.
        # Skip a separate get_ltp call (one throttled Dhan call per stock × 200 stocks
        # is the scan-time killer, and equity LTP isn't reliably served) — use the last
        # 5-min candle close as price instead.
        _eq_base, _eq_sec, _eq_exch = _resolve_equity_meta(ticker)
        ind_price = None
        if _eq_sec:
            raw_candles = broker.get_candles(_eq_base, interval='5m', days=7,
                                             exchange=_eq_exch, security_id=_eq_sec) or []
            if len(raw_candles) < 20:
                raw_candles = broker.get_candles(_eq_base, interval='1d', days=200,
                                                 exchange=_eq_exch, security_id=_eq_sec) or []
        else:
            raw_candles = []

    broker_hist: list[dict] = []
    for c in raw_candles:
        try:
            broker_hist.append({
                'date': str(getattr(c, 'date', '')),
                'open': float(c.open), 'high': float(c.high),
                'low': float(c.low), 'close': float(c.close),
                'volume': int(c.volume or 0),
            })
        except Exception:
            continue

    # ── 2. yfinance OHLCV + fundamentals fallback ─────────────────────────
    yf_ticker = yf.Ticker(ticker)
    if len(broker_hist) >= 20:
        import pandas as _pd
        hist = _pd.DataFrame([{
            'Close': c['close'], 'Open': c['open'],
            'High': c['high'], 'Low': c['low'], 'Volume': c['volume'],
        } for c in broker_hist],
        index=_pd.to_datetime([c['date'] for c in broker_hist])).sort_index()
    else:
        hist = yf_ticker.history(period='200d', interval='1d')

    info: dict = {}
    if not is_index:
        try:
            info = yf_ticker.info or {}
        except Exception:
            pass

    price = ind_price or (float(hist['Close'].iloc[-1]) if not hist.empty else float(info.get('currentPrice', 0) or 0))
    company_name = (_INDEX_NAMES.get(ticker) or info.get('longName') or info.get('shortName') or ticker)

    # ── 3. Technicals ─────────────────────────────────────────────────────
    technicals: dict = {}
    candlesticks: list = []
    try:
        candles_for_ta = broker_hist if len(broker_hist) >= 20 else []
        if not candles_for_ta and not hist.empty:
            for idx in hist.index:
                try:
                    candles_for_ta.append({
                        'open': float(hist.loc[idx, 'Open']),
                        'high': float(hist.loc[idx, 'High']),
                        'low':  float(hist.loc[idx, 'Low']),
                        'close': float(hist.loc[idx, 'Close']),
                        'volume': int(hist.loc[idx, 'Volume']) if 'Volume' in hist.columns else 0,
                    })
                except Exception:
                    continue

        if len(candles_for_ta) >= 20:
            cd = [CandleData(c['open'], c['high'], c['low'], c['close']) for c in candles_for_ta]
            closes = [c['close'] for c in candles_for_ta]

            st    = supertrend(cd, period=10, multiplier=3.0)
            adx_r = adx_full(cd, period=14)
            macd_r = macd(closes)
            rsi_v  = rsi(closes, 14)
            ema9_v   = ema(closes, 9)
            ema20_v  = ema(closes, 20)
            ema50_v  = ema(closes, 50)
            ema200_v = ema(closes, 200)
            atr_v    = atr(cd, 14)

            technicals = {
                'supertrend_dir': st['direction'] if st else None,
                'adx':           adx_r['adx']      if adx_r else 0,
                'adx_plus_di':   adx_r['plus_di']  if adx_r else 0,
                'adx_minus_di':  adx_r['minus_di'] if adx_r else 0,
                'rsi':    rsi_v or 50,
                'ema9':   ema9_v,   'ema20':  ema20_v,
                'ema50':  ema50_v,  'ema200': ema200_v,
                'atr':    atr_v,
                'macd_hist':  macd_r['histogram'] if macd_r else 0,
                'macd_cross': macd_r['cross']     if macd_r else 'NONE',
                'trend': ('Bullish' if (ema50_v and ema200_v and price > ema50_v > ema200_v)
                          else 'Bearish' if (ema50_v and price < ema50_v) else 'Mixed'),
            }

            _recent = candles_for_ta[-75:] if len(candles_for_ta) >= 75 else candles_for_ta[-20:]
            candlesticks = [
                {
                    'date':   c.get('date', ''),
                    'open':   round(c['open'], 2),  'high':  round(c['high'], 2),
                    'low':    round(c['low'], 2),    'close': round(c['close'], 2),
                    'volume': int(c.get('volume', 0)),
                }
                for c in _recent
            ]
    except Exception as _te:
        logger.warning(f'Technicals failed for {ticker}: {_te}')

    # ── 4. News ───────────────────────────────────────────────────────────
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

    # ── 5. Analysts + holders ─────────────────────────────────────────────
    analysts: list = []
    holders: list = []
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

    # ── 6. Fundamentals ──────────────────────────────────────────────────
    fundamentals = {
        'pe': info.get('trailingPE'), 'forward_pe': info.get('forwardPE'),
        'pb': info.get('priceToBook'), 'roe': info.get('returnOnEquity'),
        'net_margin': info.get('profitMargins'), 'debt_to_equity': info.get('debtToEquity'),
        'market_cap': info.get('marketCap'), 'analyst_target': info.get('targetMeanPrice'),
        'recommendation': info.get('recommendationKey'),
        'sector': info.get('sector'), 'industry': info.get('industry'),
        'earnings_growth': info.get('earningsGrowth'), 'revenue_growth': info.get('revenueGrowth'),
    }

    # ── 7. Reddit ─────────────────────────────────────────────────────────
    reddit: list = []
    if not is_index:
        try:
            import urllib.request
            is_indian = ticker.endswith('.NS') or ticker.endswith('.BO')
            subs = (['IndiaInvestments', 'IndianStockMarket'] if is_indian
                    else ['wallstreetbets', 'stocks'])
            sym_base = ticker.replace('.NS', '').replace('.BO', '')
            for sub in subs[:2]:
                url = f'https://www.reddit.com/r/{sub}/search.json?q={sym_base}&sort=hot&limit=5&t=week'
                req = urllib.request.Request(url, headers={'User-Agent': 'Vega/1.0'})
                try:
                    with urllib.request.urlopen(req, timeout=2) as resp:
                        rj = json.loads(resp.read())
                        for p in rj.get('data', {}).get('children', []):
                            pd_ = p.get('data', {})
                            title = pd_.get('title', '')
                            score = pd_.get('score', 0)
                            if title and score > 5:
                                reddit.append(f'{title} (score: {score})')
                except Exception:
                    pass
        except Exception:
            pass

    # ── 8. Macro ──────────────────────────────────────────────────────────
    macro = {
        'market_context': f'{ticker} ({company_name}) at ₹{price:.2f}',
        'sector':  'Index' if is_index else info.get('sector', 'Unknown'),
        'country': 'India' if is_index else info.get('country', 'India'),
        'beta':    info.get('beta'),
    }

    return {
        'company_name': company_name,
        'price':        price,
        'technicals':   technicals,
        'candlesticks': candlesticks,
        'fundamentals': fundamentals,
        'financials':   {},
        'macro':        macro,
        'news':         news,
        'reddit':       reddit,
        'fomo_score':   0,
        'analysts':     analysts,
        'holders':      holders,
    }
