#!/usr/bin/env python3
"""Backfill paper tickets from public Bitget 1m/1H klines. No orders."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import engine as e
import scan as s

NY = ZoneInfo("America/New_York")


def session_now(day: datetime) -> float:
    local = day.astimezone(NY).replace(hour=e.SESSION_END.hour, minute=e.SESSION_END.minute, second=0, microsecond=0)
    return local.timestamp() - 1


def replay_day(con, symbols: list[str], day: datetime) -> int:
    now = session_now(day)
    if now > time.time():
        now = time.time()
    start, end = e.session_bounds(now if e.in_session(now) else now - 60)
    new = 0
    for symbol in symbols:
        if s.has_open(con, symbol):
            continue
        h1 = s.fetch_bars(symbol, "1H", int(start) - 4 * 3600, int(end) + 60)
        m1 = s.fetch_bars(symbol, "1m", int(start) - e.ACCUM_MINUTES * 60 - 60, int(end) + 60)
        setups = e.detect_setups(h1, m1, now, symbol)
        for setup in setups:
            fill = e.fill_from_setup(setup, m1, end)
            if not fill or fill.get("skip"):
                continue
            if s.has_open(con, symbol):
                break
            s.insert_trade(con, fill)
            new += 1
            break
    s.manage_opens(con, end + 5)
    return new


def main() -> None:
    import sys

    days = 5
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    tickers = s.fetch_tickers()
    hot = e.hot_symbols(tickers, limit=12)
    con = s.connect()
    today = datetime.now(tz=NY).replace(hour=12, minute=0, second=0, microsecond=0)
    added = 0
    for i in range(days, 0, -1):
        day = today - timedelta(days=i)
        if day.weekday() >= 5:
            continue
        print(f"replay {day.date()} symbols={len(hot)}", flush=True)
        added += replay_day(con, hot, day)
        con.commit()
    # also try today's session so far
    if e.in_session(time.time()) or True:
        print(f"replay today-so-far symbols={len(hot)}", flush=True)
        added += replay_day(con, hot, today)
        con.commit()
    open_n = con.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
    closed_n = con.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
    con.close()
    print({"added": added, "open": open_n, "closed": closed_n, "hot": hot})


if __name__ == "__main__":
    main()
