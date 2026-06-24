"""
Polymarket prediction market API — FastAPI router.

Market data:    real, via PolymarketAdapter.PublicClient (no auth needed)
Order execute:  paper only — PolymarketAdapter.paper_mode=True by default,
                which means place_order() simulates fills without touching CLOB.
SSE stream:     5-second poll (Polymarket has no unauthenticated public WS).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import httpx
import requests as _requests
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from ..domain.value_objects.instrument import Instrument
from ..domain.value_objects.market import MarketType
from ..shared.logger import get_logger

logger = get_logger('api.poly')
router = APIRouter(prefix='/api/poly', tags=['polymarket'])

_GAMMA = 'https://gamma-api.polymarket.com'


async def _adapter():
    from ..dependencies import get_polymarket_exchange
    a = get_polymarket_exchange()
    if not a.is_connected:
        await a.connect()
    return a


def _instr(market_id: str) -> Instrument:
    return Instrument(symbol=market_id, condition_id=market_id, market_type=MarketType.POLYMARKET)


def _gamma_market(m: dict) -> dict:
    """Normalize a Gamma API market object to our frontend shape."""
    tokens = m.get('tokens', []) or []
    outcomes = [
        {'name': t.get('outcome', ''), 'price': float(t.get('price', 0) or 0)}
        for t in tokens
    ]
    tags = m.get('tags', []) or []
    category = tags[0].get('label', '') if tags else m.get('category', '')
    return {
        'id':           m.get('conditionId') or m.get('id') or '',
        'condition_id': m.get('conditionId') or '',
        'question':     m.get('question', ''),
        'category':     category,
        'outcomes':     outcomes,
        'volume':       float(m.get('volume', 0) or 0),
        'liquidity':    float(m.get('liquidity', 0) or 0),
        'end_date':     m.get('endDate', ''),
        'active':       bool(m.get('active', True)),
        'slug':         m.get('slug', ''),
    }


def _gamma_list(category: str = '', sort: str = 'volume', limit: int = 20) -> list[dict]:
    """Fetch active markets from Gamma REST API (no auth needed)."""
    order_map = {
        'volume': 'volume', 'newest': 'startDate',
        'ending_soon': 'endDate', 'liquidity': 'liquidity',
    }
    params: dict = {
        'active': 'true', 'closed': 'false',
        'order': order_map.get(sort, 'volume'),
        'ascending': 'false',
        'limit': limit,
    }
    if category and category not in ('all', ''):
        params['tag'] = category
    try:
        r = _requests.get(f'{_GAMMA}/markets', params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            data = data.get('data', data.get('markets', []))
        return [_gamma_market(m) for m in (data or []) if m.get('conditionId')]
    except Exception as e:
        logger.error('Gamma list error: %s', e)
        return []


# ── Markets ───────────────────────────────────────────────────────────────────

@router.get('/markets')
async def list_markets(
    q: str = Query(''),
    category: str = Query(''),
    sort: str = Query('volume'),
    limit: int = Query(20, le=100),
):
    if q:
        # Slug-based search via Gamma
        try:
            r = _requests.get(f'{_GAMMA}/markets', params={'slug': q, 'limit': limit}, timeout=8)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                data = data.get('data', data.get('markets', [data]))
            return {'success': True, 'data': [_gamma_market(m) for m in data if m.get('conditionId')]}
        except Exception as e:
            return {'success': False, 'error': str(e), 'data': []}
    markets = _gamma_list(category=category, sort=sort, limit=limit)
    return {'success': True, 'data': markets}


@router.get('/markets/search')
async def search_markets(q: str = Query(...)):
    try:
        r = _requests.get(f'{_GAMMA}/markets', params={'slug': q, 'limit': 20}, timeout=8)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            data = data.get('data', data.get('markets', []))
        return {'success': True, 'data': [_gamma_market(m) for m in (data or []) if m.get('conditionId')]}
    except Exception as e:
        return {'success': False, 'error': str(e), 'data': []}


@router.get('/markets/{market_id}/order-book')
async def get_orderbook(market_id: str):
    a = await _adapter()
    book = await a.get_orderbook(_instr(market_id))
    return {
        'success': True,
        'data': {
            'market_id': market_id,
            'bids': book.get('bids', []),
            'asks': book.get('asks', []),
        },
    }


@router.get('/markets/{market_id}/price-history')
async def get_price_history(market_id: str, interval: str = Query('1d')):
    a = await _adapter()
    ticker = await a.get_ticker(_instr(market_id))
    return {
        'success': True,
        'data': {
            'market_id': market_id, 'interval': interval,
            'last': ticker.last, 'bid': ticker.bid, 'ask': ticker.ask,
        },
    }


@router.get('/markets/{market_id}')
async def get_market(market_id: str):
    # Try Gamma first (always works, no SDK needed)
    try:
        r = _requests.get(f'{_GAMMA}/markets/{market_id}', timeout=8)
        if r.status_code == 200:
            m = r.json()
            if m.get('conditionId'):
                return {'success': True, 'data': _gamma_market(m)}
    except Exception:
        pass
    # Fallback to adapter
    a = await _adapter()
    info = await a.get_market(market_id=market_id)
    if not info:
        return JSONResponse({'success': False, 'error': 'not found'}, status_code=404)
    return {'success': True, 'data': info}


# ── Paper order execution ─────────────────────────────────────────────────────

@router.post('/order')
async def place_order(payload: dict):
    """
    Routes through PolymarketAdapter.place_order.
    When paper_mode=True (default), fills are simulated — no real CLOB call.

    Example:
        {
            "market": "BTC_150K_DEC",   # condition_id / token_id
            "side": "BUY",
            "qty": 100,
            "price": 0.62               # 0 = fetch live price
        }
    """
    a = await _adapter()
    market_id = payload.get('market', '')
    if not market_id:
        return JSONResponse({'success': False, 'error': 'market required'}, status_code=400)

    side       = payload.get('side', 'BUY').upper()
    qty        = float(payload.get('qty', 100))
    price      = float(payload.get('price', 0))
    order_type = payload.get('order_type', 'LIMIT').upper()

    order = await a.place_order(
        instrument=_instr(market_id),
        side=side,
        qty=qty,
        order_type=order_type,
        price=price,
    )
    return {
        'success': order.is_filled or order.status not in ('REJECTED', ''),
        'data': {
            'order_id':   order.order_id,
            'status':     order.status,
            'fill_price': order.fill_price,
            'filled_qty': order.filled_qty,
            'paper':      order.raw.get('paper', False),
        },
    }


@router.post('/order/cancel')
async def cancel_order(payload: dict):
    a = await _adapter()
    ok = await a.cancel_order(payload.get('order_id', ''))
    return {'success': ok}


# ── Portfolio / balance ───────────────────────────────────────────────────────

@router.get('/portfolio')
async def get_portfolio():
    a = await _adapter()
    positions = await a.get_positions()
    return {
        'success': True,
        'data': [
            {
                'symbol':         p.symbol,
                'side':           p.side,
                'qty':            p.qty,
                'avg_entry':      p.avg_entry,
                'unrealized_pnl': p.unrealized_pnl,
            }
            for p in positions
        ],
    }


@router.get('/balance')
async def get_balance():
    a = await _adapter()
    bals = await a.get_balances()
    if not bals:
        return {'success': True, 'data': {'currency': 'USDC', 'free': 0.0, 'total': 0.0}}
    b = bals[0]
    return {
        'success': True,
        'data': {'currency': b.currency, 'free': b.free, 'used': b.used, 'total': b.total},
    }


# ── Status / auto-trading toggle ─────────────────────────────────────────────

@router.get('/status')
def get_status():
    from ..infrastructure.db import state_store
    auto = state_store.get_state('poly_auto_trading_enabled', 'false') == 'true'
    return {
        'success': True,
        'data': {
            'mode':       'paper',
            'auto_trade': auto,
            'exchange':   'polymarket',
        },
    }


@router.post('/auto-trading')
async def toggle_auto_trading(request: Request):
    from ..infrastructure.db import state_store
    body = await request.json()
    enabled = bool(body.get('enabled', False))
    state_store.set_state('poly_auto_trading_enabled', str(enabled).lower())
    return {'success': True, 'data': {'auto_trading_enabled': enabled}}


# ── News & AI Analysis ────────────────────────────────────────────────────────

_STOPWORDS = {
    'will', 'the', 'a', 'an', 'be', 'is', 'are', 'was', 'were', 'in', 'on',
    'at', 'to', 'for', 'of', 'and', 'or', 'by', 'with', 'what', 'when', 'how',
    'who', 'which', 'that', 'this', 'it', 'before', 'after', 'end', 'year',
    'date', 'does', 'do', 'did', 'have', 'has', 'had', 'not', 'no', 'yes',
    'than', 'then', 'from', 'its', 'their', 'his', 'her', 'win', 'lose',
    'happen', 'occur', 'least', 'most', 'more', 'less', 'get', 'got',
}

_NEWS_UA = 'Mozilla/5.0 (compatible; Vega/1.0; +https://github.com/vega-trade)'


def _extract_keywords(question: str, max_words: int = 6) -> str:
    words = re.findall(r'\b[A-Za-z]{3,}\b', question)
    return ' '.join(w for w in words if w.lower() not in _STOPWORDS)[:max_words * 8]


async def _fetch_rss(url: str, max_items: int = 6) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=7, follow_redirects=True) as client:
            r = await client.get(url, headers={'User-Agent': _NEWS_UA})
            r.raise_for_status()
        root = ET.fromstring(r.text)
        items = []
        for item in root.iter('item'):
            title  = (item.findtext('title')  or '').strip()
            link   = (item.findtext('link')   or '').strip()
            pub    = (item.findtext('pubDate') or '').strip()
            src_el = item.find('source')
            source = (src_el.text or '').strip() if src_el is not None else ''
            if title and link:
                items.append({'title': title, 'url': link, 'source': source, 'published': pub})
            if len(items) >= max_items:
                break
        return items
    except Exception as exc:
        logger.warning('RSS fetch [%s]: %s', url, exc)
        return []


async def _fetch_gdelt(keywords: str, max_items: int = 5) -> list[dict]:
    """GDELT Doc 2.0 API — free, no key, global news updated every 15 min."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                'https://api.gdeltproject.org/api/v2/doc/doc',
                params={
                    'query':      keywords,
                    'mode':       'artlist',
                    'maxrecords': max_items,
                    'format':     'json',
                    'timespan':   '1week',
                },
                headers={'User-Agent': _NEWS_UA},
            )
            r.raise_for_status()
        articles = r.json().get('articles', [])
        return [
            {
                'title':     a.get('title', '').strip(),
                'url':       a.get('url', ''),
                'source':    a.get('domain', ''),
                'published': a.get('seendate', ''),
            }
            for a in articles if a.get('title') and a.get('url')
        ]
    except Exception as exc:
        logger.warning('GDELT fetch error: %s', exc)
        return []


async def _fetch_reddit(subreddit: str, keywords: str, max_items: int = 4) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=7, follow_redirects=True) as client:
            r = await client.get(
                f'https://www.reddit.com/r/{subreddit}/search.json',
                params={'q': keywords, 'sort': 'new', 'limit': max_items, 't': 'month'},
                headers={'User-Agent': _NEWS_UA},
            )
            r.raise_for_status()
        posts = r.json().get('data', {}).get('children', [])
        items = []
        for post in posts:
            d = post.get('data', {})
            title = (d.get('title') or '').strip()
            if title:
                items.append({
                    'title':     title,
                    'url':       f"https://reddit.com{d.get('permalink', '')}",
                    'source':    f"r/{subreddit}",
                    'published': str(int(d.get('created_utc', 0))),
                    'score':     d.get('score', 0),
                })
        return items
    except Exception as exc:
        logger.warning('Reddit fetch [r/%s]: %s', subreddit, exc)
        return []


async def _get_market_question(market_id: str) -> tuple[str, float]:
    """Return (question, yes_price) for a market."""
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(f'{_GAMMA}/markets/{market_id}')
        if r.status_code == 200:
            m = r.json()
            q = m.get('question', '')
            tokens = m.get('tokens', []) or []
            yes_tok = next((t for t in tokens if str(t.get('outcome', '')).upper() == 'YES'), None)
            price = float((yes_tok or {}).get('price', 0.5) or 0.5)
            return q, price
    except Exception:
        pass
    return '', 0.5


@router.get('/news/{market_id:path}')
async def get_market_news(market_id: str):
    question, _ = await _get_market_question(market_id)
    if not question:
        return {'success': False, 'error': 'market not found', 'data': []}

    keywords = _extract_keywords(question)
    encoded  = quote_plus(keywords)

    gnews_url = f'https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en'
    gnews, reddit_poly, reddit_world, gdelt = await asyncio.gather(
        _fetch_rss(gnews_url, max_items=5),
        _fetch_reddit('Polymarket', keywords, max_items=3),
        _fetch_reddit('worldnews', keywords, max_items=2),
        _fetch_gdelt(keywords, max_items=5),
    )

    seen, unique = set(), []
    for a in gnews + gdelt + reddit_poly + reddit_world:
        if a['url'] not in seen:
            seen.add(a['url'])
            unique.append(a)

    return {
        'success': True,
        'data': {'market_id': market_id, 'question': question, 'keywords': keywords, 'articles': unique[:10]},
    }


@router.get('/ai-analysis/{market_id:path}')
async def get_ai_analysis(market_id: str):
    question, yes_price = await _get_market_question(market_id)
    if not question:
        return {'success': False, 'error': 'market not found'}

    keywords = _extract_keywords(question)
    encoded  = quote_plus(keywords)
    news_articles = await _fetch_rss(
        f'https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en',
        max_items=5,
    )

    headlines = '\n'.join(f'- {a["title"]} ({a.get("source", "news")})' for a in news_articles) \
                or 'No recent news found.'

    prompt = f"""You are an expert prediction market analyst. Analyze this market.

Market Question: "{question}"
Current YES Probability: {yes_price * 100:.1f}%

Recent News Headlines:
{headlines}

Return ONLY valid JSON in this exact format:
{{
  "signal": "YES" or "NO" or "NEUTRAL",
  "confidence": <integer 0-100>,
  "reasoning": "<2-3 sentence explanation>",
  "key_factors": ["<factor1>", "<factor2>", "<factor3>"],
  "suggested_action": "BUY YES" or "BUY NO" or "HOLD"
}}"""

    try:
        from ..infrastructure.llm.client import LLMClient
        from ..config import get_settings

        loop = asyncio.get_running_loop()

        def _llm_call():
            c = LLMClient.from_settings(get_settings())
            return c.chat_json(
                messages=[
                    {'role': 'system', 'content': 'You are a prediction market analyst. Always respond with valid JSON.'},
                    {'role': 'user',   'content': prompt},
                ],
                temperature=0.3,
                max_tokens=600,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            analysis = await loop.run_in_executor(pool, _llm_call)

        return {
            'success': True,
            'data': {
                'market_id':  market_id,
                'question':   question,
                'yes_price':  yes_price,
                'analysis':   analysis,
                'news_count': len(news_articles),
            },
        }
    except Exception as exc:
        logger.error('AI analysis error: %s', exc)
        return {'success': False, 'error': str(exc)}


# ── SSE stream ────────────────────────────────────────────────────────────────

@router.get('/stream/{market_id}')
async def stream_market(market_id: str, request: Request):
    """
    SSE ticker stream for a Polymarket market.
    Polls get_ticker every 5 seconds — no public WS available in the SDK.
    """
    a = await _adapter()
    instr = _instr(market_id)

    async def _gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ticker = await a.get_ticker(instr)
                    yield {
                        'event': 'tick',
                        'data': json.dumps({
                            'market_id': market_id,
                            'last':   ticker.last,
                            'bid':    ticker.bid,
                            'ask':    ticker.ask,
                            'spread': ticker.spread,
                            'mid':    ticker.mid,
                        }),
                    }
                except Exception as e:
                    logger.warning('Poly stream [%s]: %s', market_id, e)
                    yield {'event': 'error', 'data': json.dumps({'error': str(e)})}
                await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            pass

    return EventSourceResponse(
        _gen(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
