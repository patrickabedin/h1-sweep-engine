#!/usr/bin/env python3
"""H1 sweep + M1 FVG paper rules. No orders. No Cornix."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

SESSION_START = time(3, 5)
SESSION_END = time(9, 11)

MARGIN_USDT = 100.0
LEVERAGE = 10
NOTIONAL = MARGIN_USDT * LEVERAGE
FEE_RT = 0.0012
FEE_USDT = NOTIONAL * FEE_RT

STOP_BUFFER = 0.0005
MAX_STOP_PCT = 0.02
MIN_R = 2.0
MAX_R = 4.0
ACCUM_MINUTES = 20
HOT_LIMIT = 40

STABLE = frozenset(
    {
        "USDCUSDT",
        "FDUSDUSDT",
        "DAIUSDT",
        "TUSDUSDT",
        "USDEUSDT",
        "USD1USDT",
        "USDTUSDT",
    }
)


def fnum(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ny_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=NY)


def session_bounds(ts: float) -> tuple[float, float]:
    local = ny_dt(ts)
    start = local.replace(hour=SESSION_START.hour, minute=SESSION_START.minute, second=0, microsecond=0)
    end = local.replace(hour=SESSION_END.hour, minute=SESSION_END.minute, second=0, microsecond=0)
    return start.timestamp(), end.timestamp()


def in_session(ts: float) -> bool:
    start, end = session_bounds(ts)
    return start <= ts < end


def session_end_ts(ts: float) -> float:
    return session_bounds(ts)[1]


def hour_open(ts: float) -> int:
    return (int(ts) // 3600) * 3600


def is_bullish(bar) -> bool:
    return bar[4] > bar[1]


def is_bearish(bar) -> bool:
    return bar[4] < bar[1]


def bearish_fvg(c1, c2, c3) -> bool:
    """Candle 3 high < candle 1 low."""
    return c3[2] < c1[3]


def bullish_fvg(c1, c2, c3) -> bool:
    """Candle 3 low > candle 1 high."""
    return c3[3] > c1[2]


def net_pnl(direction: str, entry: float, exit_px: float, notional: float = NOTIONAL) -> float:
    if entry is None or exit_px is None or entry <= 0:
        return 0.0
    sign = 1.0 if direction == "LONG" else -1.0
    return notional * sign * (exit_px - entry) / entry - notional * FEE_RT


def stop_beyond_sweep(direction: str, sweep_extreme: float, buffer: float = STOP_BUFFER) -> float:
    if direction == "SHORT":
        return sweep_extreme * (1.0 + buffer)
    return sweep_extreme * (1.0 - buffer)


def size_trade(direction: str, entry: float, sweep_extreme: float, range_opposite: float):
    """Return (stop, target, r, skip_reason) or skip if RR / stop-width fail."""
    if entry is None or entry <= 0 or sweep_extreme is None or range_opposite is None:
        return None, None, None, "missing_prices"
    stop = stop_beyond_sweep(direction, sweep_extreme)
    if direction == "SHORT":
        if stop <= entry:
            return None, None, None, "stop_not_beyond"
        risk = stop - entry
        room = entry - range_opposite
    else:
        if stop >= entry:
            return None, None, None, "stop_not_beyond"
        risk = entry - stop
        room = range_opposite - entry
    if risk <= 0:
        return None, None, None, "zero_risk"
    stop_pct = risk / entry
    if stop_pct > MAX_STOP_PCT + 1e-12:
        return None, None, None, "stop_over_2pct"
    need = MIN_R * risk
    if room + 1e-12 < need:
        return None, None, None, "opp_closer_than_2r"
    reward = min(MAX_R * risk, room)
    if reward + 1e-12 < need:
        return None, None, None, "opp_closer_than_2r"
    if direction == "SHORT":
        target = entry - reward
    else:
        target = entry + reward
    return stop, target, reward / risk, None


def accum_held(m1, hour_ts: int, prev_extreme: float, side: str, minutes: int = ACCUM_MINUTES) -> bool:
    """M1 bars in the minutes before the new H1 must not already take the H1 level."""
    start = hour_ts - minutes * 60
    held = False
    for bar in m1:
        if bar[0] < start or bar[0] >= hour_ts:
            continue
        held = True
        if side == "SHORT" and bar[2] > prev_extreme:
            return False
        if side == "LONG" and bar[3] < prev_extreme:
            return False
    return held


def first_sweep_bar(m1, hour_ts: int, prev_extreme: float, side: str, session_start: float):
    for bar in m1:
        if bar[0] < max(hour_ts, int(session_start)):
            continue
        if bar[0] >= hour_ts + 3600:
            break
        if side == "SHORT" and bar[2] > prev_extreme:
            return bar
        if side == "LONG" and bar[3] < prev_extreme:
            return bar
    return None


def sweep_extreme_until(m1, hour_ts: int, until_ts: int, side: str) -> float | None:
    extreme = None
    for bar in m1:
        if bar[0] < hour_ts or bar[0] > until_ts:
            continue
        px = bar[2] if side == "SHORT" else bar[3]
        extreme = px if extreme is None else (max(extreme, px) if side == "SHORT" else min(extreme, px))
    return extreme


def find_fvgs(m1, after_ts: int, hour_end: int, side: str):
    """Yield (c1, c2, c3, zone_low, zone_high) after the sweep."""
    bars = [b for b in m1 if after_ts <= b[0] < hour_end]
    out = []
    for i in range(len(bars) - 2):
        c1, c2, c3 = bars[i], bars[i + 1], bars[i + 2]
        if side == "SHORT":
            if not bearish_fvg(c1, c2, c3):
                continue
            if not is_bearish(c2):
                continue
            out.append((c1, c2, c3, c3[2], c1[3]))
        else:
            if not bullish_fvg(c1, c2, c3):
                continue
            if not is_bullish(c2):
                continue
            out.append((c1, c2, c3, c1[2], c3[3]))
    return out


def tap_fill(bar, zone_low: float, zone_high: float, side: str):
    """First touch of the FVG on this bar, or None."""
    if side == "SHORT":
        if bar[2] < zone_low:
            return None
        if bar[1] > zone_high:
            return zone_high
        if bar[1] >= zone_low:
            return bar[1]
        return zone_low
    if bar[3] > zone_high:
        return None
    if bar[1] < zone_low:
        return zone_low
    if bar[1] <= zone_high:
        return bar[1]
    return zone_high


def range_opposite(m1, start_ts: int, end_ts: int, side: str) -> float | None:
    px = None
    for bar in m1:
        if bar[0] < start_ts or bar[0] > end_ts:
            continue
        v = bar[3] if side == "SHORT" else bar[2]
        px = v if px is None else (min(px, v) if side == "SHORT" else max(px, v))
    return px


def hits_stop(direction: str, bar, stop: float) -> bool:
    return bar[2] >= stop if direction == "SHORT" else bar[3] <= stop


def hits_take(direction: str, bar, take: float) -> bool:
    return bar[3] <= take if direction == "SHORT" else bar[2] >= take


def detect_setups(h1, m1, now: float, symbol: str):
    """Find unfilled setups in the current NY session. h1/m1 are (ts,o,h,l,c)."""
    if not in_session(now) and not h1:
        return []
    start, end = session_bounds(now if in_session(now) else (now - 3600))
    by_hour = {int(bar[0]): bar for bar in h1}
    found = []
    hour = hour_open(max(start, hour_open(now)))
    # Walk each hour that overlaps the session
    cursor = hour_open(start)
    while cursor < end:
        prev = by_hour.get(cursor - 3600)
        cur = by_hour.get(cursor)
        if prev is None:
            cursor += 3600
            continue
        sides = []
        if is_bullish(prev):
            sides.append("SHORT")
        if is_bearish(prev):
            sides.append("LONG")
        for side in sides:
            prev_ext = prev[2] if side == "SHORT" else prev[3]
            if not accum_held(m1, cursor, prev_ext, side):
                continue
            sweep = first_sweep_bar(m1, cursor, prev_ext, side, start)
            if sweep is None:
                continue
            hour_end = min(cursor + 3600, int(end))
            fvgs = find_fvgs(m1, sweep[0], hour_end, side)
            if not fvgs:
                continue
            c1, c2, c3, zlo, zhi = fvgs[0]
            extreme = sweep_extreme_until(m1, cursor, c3[0], side)
            if extreme is None:
                continue
            opp = range_opposite(m1, cursor - ACCUM_MINUTES * 60, c3[0], side)
            found.append(
                {
                    "symbol": symbol,
                    "direction": side,
                    "hour_ts": cursor,
                    "prev_extreme": prev_ext,
                    "sweep_ts": sweep[0],
                    "sweep_extreme": extreme,
                    "fvg_low": zlo,
                    "fvg_high": zhi,
                    "fvg_ts": c3[0],
                    "range_opposite": opp,
                    "session_end": end,
                }
            )
        cursor += 3600
        if cursor > hour_open(now) and not in_session(now):
            break
    return found


def fill_from_setup(setup: dict, m1, now: float):
    """If a later M1 taps the FVG, return a sized fill dict or a skip."""
    side = setup["direction"]
    for bar in m1:
        if bar[0] <= setup["fvg_ts"]:
            continue
        if bar[0] >= setup["session_end"] or bar[0] >= now:
            break
        fill = tap_fill(bar, setup["fvg_low"], setup["fvg_high"], side)
        if fill is None:
            continue
        stop, target, rr, reason = size_trade(side, fill, setup["sweep_extreme"], setup["range_opposite"])
        if reason:
            return {"skip": reason, "setup": setup, "would_entry": fill}
        return {
            "symbol": setup["symbol"],
            "direction": side,
            "entry": fill,
            "stop": stop,
            "target": target,
            "rr": rr,
            "opened_at": bar[0],
            "session_end": setup["session_end"],
            "sweep_extreme": setup["sweep_extreme"],
            "fvg_low": setup["fvg_low"],
            "fvg_high": setup["fvg_high"],
        }
    return None


def manage_open(trade: dict, bars, now: float):
    """Walk 1m after entry. Same-bar stop first. Session mark close at 09:11 NY."""
    direction = trade["direction"]
    entry = trade["entry"]
    stop = trade["stop"]
    target = trade["target"]
    deadline = float(trade["session_end"])
    last = None
    for bar in bars:
        if bar[0] + 59 < trade["opened_at"]:
            continue
        if bar[0] >= deadline:
            break
        if bar[0] + 60 > now:
            break
        last = bar
        if hits_stop(direction, bar, stop):
            return {"status": "closed", "exit_reason": "STOP", "exit_price": stop, "closed_at": min(bar[0] + 59, deadline)}
        if hits_take(direction, bar, target):
            return {"status": "closed", "exit_reason": "TARGET", "exit_price": target, "closed_at": min(bar[0] + 59, deadline)}
    if now >= deadline:
        mark = None
        for bar in bars:
            if bar[0] < deadline:
                mark = bar
        if mark:
            return {"status": "closed", "exit_reason": "SESSION", "exit_price": mark[4], "closed_at": deadline}
    if last:
        return {"status": "open", "last_price": last[4]}
    return {"status": "open", "last_price": entry}


def hot_symbols(tickers: dict, limit: int = HOT_LIMIT) -> list[str]:
    rows = []
    for key, raw in (tickers or {}).items():
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or key).upper()
        if not symbol.endswith("USDT") or symbol in STABLE:
            continue
        vol = fnum(raw.get("quoteVolume") or raw.get("usdtVolume") or raw.get("turnover24h")) or 0.0
        rows.append((vol, symbol))
    rows.sort(reverse=True)
    out = []
    seen = set()
    for _, symbol in rows:
        if symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
        if len(out) >= limit:
            break
    return out
