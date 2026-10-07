#!/usr/bin/env python3
"""Paper scanner for the H1 sweep engine. Bitget public klines only."""
from __future__ import annotations

import json
import sqlite3
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import engine as e

ROOT = Path(__file__).resolve().parent
DB = ROOT / "journal.db"
LAST = ROOT / "last_scan.json"
LOG = ROOT / "logs" / "scan.log"
NY = e.NY
UTC = timezone.utc
PAGE = 200
SLEEP = 20

UA = {"User-Agent": "h1-sweep-paper"}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ny_s(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=NY).strftime("%Y-%m-%dT%H:%M:%S")


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            status TEXT NOT NULL,
            entry_price REAL,
            stop_price REAL,
            target_price REAL,
            rr REAL,
            exit_price REAL,
            pnl_usd REAL,
            sweep_extreme REAL,
            fvg_low REAL,
            fvg_high REAL,
            opened_at_utc TEXT,
            opened_at_ny TEXT,
            closed_at_utc TEXT,
            closed_at_ny TEXT,
            session_end_utc TEXT,
            exit_reason TEXT,
            last_price REAL,
            skip_reason TEXT,
            created_at_utc TEXT NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY,
            scanned_at_utc TEXT,
            symbols INTEGER,
            hot_count INTEGER,
            new_trades INTEGER,
            open_count INTEGER,
            closed_this INTEGER,
            error TEXT
        )"""
    )
    con.commit()
    return con


def fetch_tickers() -> dict:
    url = "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.load(resp)
    rows = body.get("data") or []
    out = {}
    for row in rows:
        if isinstance(row, dict) and row.get("symbol"):
            out[row["symbol"]] = row
    return out


def fetch_bars(symbol: str, gran: str, start: int, end: int):
    merged = {}
    gran_s = 60 if gran == "1m" else 3600
    cursor = int(start)
    while cursor < end:
        page_end = min(cursor + PAGE * gran_s, int(end))
        url = (
            "https://api.bitget.com/api/v2/mix/market/history-candles?"
            f"symbol={symbol}&productType=USDT-FUTURES&granularity={gran}"
            f"&startTime={cursor * 1000}&endTime={page_end * 1000}&limit={PAGE}"
        )
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.load(resp)
        except Exception:
            break
        rows = body.get("data") or []
        if not rows:
            break
        last = cursor
        for row in rows:
            ts = int(row[0]) // 1000
            merged[ts] = (ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]))
            last = max(last, ts)
        nxt = last + gran_s
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.05)
    return [merged[k] for k in sorted(merged)]


def has_open(con, symbol: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM trades WHERE symbol=? AND status='open' LIMIT 1", (symbol,)
    ).fetchone()
    return row is not None


def insert_trade(con, fill: dict):
    now = time.time()
    con.execute(
        """INSERT INTO trades (
            symbol, direction, status, entry_price, stop_price, target_price, rr,
            sweep_extreme, fvg_low, fvg_high, opened_at_utc, opened_at_ny,
            session_end_utc, last_price, created_at_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            fill["symbol"],
            fill["direction"],
            "open",
            fill["entry"],
            fill["stop"],
            fill["target"],
            fill["rr"],
            fill["sweep_extreme"],
            fill["fvg_low"],
            fill["fvg_high"],
            iso(fill["opened_at"]),
            ny_s(fill["opened_at"]),
            iso(fill["session_end"]),
            fill["entry"],
            iso(now),
        ),
    )


def parse_iso(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def manage_opens(con, now: float) -> int:
    closed = 0
    rows = list(con.execute("SELECT * FROM trades WHERE status='open'"))
    cols = [d[0] for d in con.execute("PRAGMA table_info(trades)")]
    for raw in rows:
        trade = dict(zip(cols, raw))
        t_open = parse_iso(trade["opened_at_utc"])
        bars = fetch_bars(trade["symbol"], "1m", int(t_open) - 60, int(now) + 60)
        payload = {
            "direction": trade["direction"],
            "entry": trade["entry_price"],
            "stop": trade["stop_price"],
            "target": trade["target_price"],
            "opened_at": t_open,
            "session_end": parse_iso(trade["session_end_utc"]),
        }
        result = e.manage_open(payload, bars, now)
        if result["status"] == "open":
            con.execute(
                "UPDATE trades SET last_price=? WHERE id=?",
                (result.get("last_price"), trade["id"]),
            )
            continue
        pnl = e.net_pnl(trade["direction"], trade["entry_price"], result["exit_price"])
        con.execute(
            """UPDATE trades SET status='closed', exit_price=?, pnl_usd=?, exit_reason=?,
               closed_at_utc=?, closed_at_ny=?, last_price=? WHERE id=?""",
            (
                result["exit_price"],
                pnl,
                result["exit_reason"],
                iso(result["closed_at"]),
                ny_s(result["closed_at"]),
                result["exit_price"],
                trade["id"],
            ),
        )
        closed += 1
    return closed


def arm_new(con, symbols: list[str], now: float) -> int:
    if not e.in_session(now):
        return 0
    start, end = e.session_bounds(now)
    new = 0
    for symbol in symbols:
        if has_open(con, symbol):
            continue
        h1 = fetch_bars(symbol, "1H", int(start) - 4 * 3600, int(now) + 60)
        m1 = fetch_bars(symbol, "1m", int(start) - e.ACCUM_MINUTES * 60 - 60, int(now) + 60)
        setups = e.detect_setups(h1, m1, now, symbol)
        for setup in setups:
            fill = e.fill_from_setup(setup, m1, now)
            if not fill:
                continue
            if fill.get("skip"):
                continue
            if has_open(con, symbol):
                break
            insert_trade(con, fill)
            new += 1
            break
    return new


def run_once() -> dict:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    error = None
    hot = []
    new_trades = 0
    closed_this = 0
    try:
        tickers = fetch_tickers()
        hot = e.hot_symbols(tickers)
        con = connect()
        closed_this = manage_opens(con, now)
        new_trades = arm_new(con, hot, now)
        open_n = con.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
        con.execute(
            "INSERT INTO scans (scanned_at_utc, symbols, hot_count, new_trades, open_count, closed_this, error) VALUES (?,?,?,?,?,?,?)",
            (iso(now), len(tickers), len(hot), new_trades, open_n, closed_this, None),
        )
        con.commit()
        con.close()
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        open_n = 0
    payload = {
        "time_utc": iso(now),
        "time_ny": ny_s(now),
        "hot_count": len(hot),
        "new_trades": new_trades,
        "closed_this": closed_this,
        "open_count": open_n if error is None else None,
        "error": error,
        "paper": True,
        "engine": "h1-sweep",
    }
    LAST.write_text(json.dumps(payload, indent=2) + "\n")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")
    print(json.dumps(payload))
    return payload


def main():
    import sys

    if "--once" in sys.argv:
        run_once()
        return
    print("usage: scan.py --once", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
