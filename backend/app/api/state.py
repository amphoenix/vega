"""
Server-owned client state: watchlist and signal history.

Clients (web, mobile) read and write these here instead of keeping their own copy,
so every device shows the same thing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..state import signals, watchlist

router = APIRouter(prefix='/api', tags=['state'])


class WatchlistBody(BaseModel):
    items: list[dict[str, Any]]


@router.get('/watchlist')
def get_watchlist():
    return {'success': True, 'data': watchlist.get_watchlist()}


@router.put('/watchlist')
def put_watchlist(body: WatchlistBody):
    return {'success': True, 'data': watchlist.set_watchlist(body.items)}


@router.get('/signals/history')
def signal_history(
    segment: str | None = Query(None, description='swing | scalp | btst'),
    since_id: int = Query(0, ge=0, description='only rows newer than this id'),
    limit: int = Query(100, ge=1, le=500),
):
    return {'success': True, 'data': signals.history(segment=segment, since_id=since_id, limit=limit)}
