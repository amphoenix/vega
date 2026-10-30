"""
Server-owned state — everything every client (web, mobile) must see identically.

Clients only display this; they never keep their own copy. Stored in its own
SQLite file (data/server_state.db), separate from the P&L ledger in pnl.db.

    settings   persisted runtime toggles / config (survive restarts)
    watchlist  the user's watchlist
    signals    append-only log of scanner signals (history for every device)
"""
