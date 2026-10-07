#!/usr/bin/env python3
"""Build the paper desk snapshot. Optionally deploy the H1 sweep Vercel project."""
from __future__ import annotations

import calendar
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import engine as e

ROOT = Path(__file__).resolve().parent
DB = ROOT / "journal.db"
DESK = ROOT / "desk"
SNAP = DESK / "snapshot.json"
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
TEAM_ID = "team_KqbTwWH1Wg15woEkjvXwD8is"
NAME = "h1-sweep-desk"
ALIAS = "h1-sweep-desk.vercel.app"


def iso_utc(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def session_date(opened_at: str | None, created_at: str | None) -> str:
    raw = opened_at or created_at
    if not raw:
        return datetime.now(tz=NY).strftime("%Y-%m-%d")
    ts = parse_iso(raw) if "T" in raw and raw.endswith("Z") else None
    if ts is None:
        # already NY wall time YYYY-MM-DDTHH:MM:SS
        return raw[:10]
    return datetime.fromtimestamp(ts, tz=NY).strftime("%Y-%m-%d")


def row_to_trade(row: dict) -> dict:
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "direction": row["direction"],
        "status": row["status"],
        "entry_price": row["entry_price"],
        "stop_price": row["stop_price"],
        "target_price": row["target_price"],
        "rr": row["rr"],
        "exit_price": row["exit_price"],
        "pnl_usd": row["pnl_usd"],
        "sweep_extreme": row["sweep_extreme"],
        "fvg_low": row["fvg_low"],
        "fvg_high": row["fvg_high"],
        "opened_at_utc": row["opened_at_utc"],
        "opened_at_ny": row["opened_at_ny"],
        "closed_at_utc": row["closed_at_utc"],
        "closed_at_ny": row["closed_at_ny"],
        "exit_reason": row["exit_reason"],
        "last_price": row["last_price"],
        "session_date": session_date(row["opened_at_utc"] or row["opened_at_ny"], row["created_at_utc"]),
    }


def month_days(month: str) -> list[str]:
    year, mon = [int(x) for x in month.split("-")]
    last = calendar.monthrange(year, mon)[1]
    return [f"{year:04d}-{mon:02d}-{d:02d}" for d in range(1, last + 1)]


def build_snapshot() -> dict:
    now = time.time()
    today = datetime.now(tz=NY).strftime("%Y-%m-%d")
    month = today[:7]
    open_rows: list[dict] = []
    closed_rows: list[dict] = []
    if DB.exists():
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        for raw in con.execute("SELECT * FROM trades ORDER BY id"):
            trade = row_to_trade(dict(raw))
            if trade["status"] == "open":
                open_rows.append(trade)
            else:
                closed_rows.append(trade)
        con.close()
    by_day: dict[str, list[dict]] = {}
    for trade in closed_rows:
        by_day.setdefault(trade["session_date"], []).append(trade)
    days = []
    month_net = 0.0
    n = 0
    for date in month_days(month):
        trades = by_day.get(date, [])
        net = sum(float(t["pnl_usd"] or 0) for t in trades)
        month_net += net
        n += len(trades)
        days.append(
            {
                "date": date,
                "dom": int(date[-2:]),
                "n_full": len(trades),
                "net_full": round(net, 2),
                "trades": trades,
            }
        )
    last = {}
    last_path = ROOT / "last_scan.json"
    if last_path.exists():
        try:
            last = json.loads(last_path.read_text())
        except json.JSONDecodeError:
            last = {}
    return {
        "engine": "h1-sweep",
        "label": "Paper · H1 sweep",
        "paper": True,
        "month": month,
        "today": today,
        "month_net": round(month_net, 2),
        "n": n,
        "open_count": len(open_rows),
        "margin_usdt": e.MARGIN_USDT,
        "leverage": e.LEVERAGE,
        "notional": e.NOTIONAL,
        "fee_rt_usdt": e.FEE_USDT,
        "min_r": e.MIN_R,
        "max_r": e.MAX_R,
        "max_stop_pct": e.MAX_STOP_PCT,
        "session": "03:05–09:11 America/New_York",
        "generated_at": iso_utc(now),
        "last_scan": last,
        "open": open_rows,
        "closed": closed_rows,
        "days": days,
    }


def write_snapshot() -> dict:
    DESK.mkdir(parents=True, exist_ok=True)
    payload = build_snapshot()
    SNAP.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def _api(token: str, method: str, url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"{method} HTTP {exc.code}: {detail}") from exc
    return json.loads(raw) if raw.strip() else {}


def file_entry(path: Path, name: str) -> dict:
    return {"file": name, "data": path.read_text(encoding="utf-8"), "encoding": "utf-8"}


def deploy(token: str) -> str:
    write_snapshot()
    files = []
    for rel in (
        "index.html",
        "styles.css",
        "app.js",
        "snapshot.json",
        "calendar/index.html",
        "tickets/index.html",
        "vercel.json",
    ):
        path = DESK / rel
        if path.exists():
            files.append(file_entry(path, rel))
    body = {
        "name": NAME,
        "project": NAME,
        "target": "production",
        "files": files,
        "projectSettings": {"framework": None},
    }
    info = _api(token, "POST", f"https://api.vercel.com/v13/deployments?teamId={TEAM_ID}&skipAutoDetectWarning=1", body)
    dep = info.get("id") or info.get("uid")
    if not dep:
        raise RuntimeError(f"deploy missing id: {info}")
    state = ""
    deadline = time.time() + 180
    while time.time() < deadline:
        ready = _api(token, "GET", f"https://api.vercel.com/v13/deployments/{dep}?teamId={TEAM_ID}")
        state = str(ready.get("readyState") or "")
        if state == "READY":
            break
        if state in {"ERROR", "CANCELED"}:
            raise RuntimeError(f"deploy {dep} {state}")
        time.sleep(2)
    else:
        raise RuntimeError(f"deploy {dep} not ready ({state})")
    _api(token, "POST", f"https://api.vercel.com/v2/deployments/{dep}/aliases?teamId={TEAM_ID}", {"alias": ALIAS})
    return f"https://{ALIAS}"


def main() -> None:
    import sys

    args = set(sys.argv[1:])
    payload = write_snapshot()
    print(json.dumps({"wrote": str(SNAP), "n": payload["n"], "open": payload["open_count"], "month_net": payload["month_net"]}))
    if "--deploy" in args:
        token = (os.environ.get("VERCEL_TOKEN") or "").strip()
        if not token:
            raise SystemExit("VERCEL_TOKEN is not set")
        url = deploy(token)
        print(json.dumps({"deployed": url}))


if __name__ == "__main__":
    main()
