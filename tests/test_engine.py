#!/usr/bin/env python3
import unittest
from datetime import datetime

import engine as e

# Wednesday 7 Oct 2026 07:00 UTC = 03:00 America/New_York (EDT)
H = int(datetime(2026, 10, 7, 8, 0, tzinfo=e.UTC).timestamp())  # 04:00 NY, inside 03:05–09:11
PREV = H - 3600


def bar(ts, o, h, l, c):
    return (int(ts), float(o), float(h), float(l), float(c))


def m1_flat(start, n, px, step=0.0):
    out = []
    for i in range(n):
        p = px + i * step
        out.append(bar(start + i * 60, p, p + 0.1, p - 0.1, p))
    return out


class SessionTests(unittest.TestCase):
    def test_window_ny(self):
        inside = datetime(2026, 10, 7, 7, 10, tzinfo=e.UTC).timestamp()  # 03:10 NY
        early = datetime(2026, 10, 7, 7, 0, tzinfo=e.UTC).timestamp()  # 03:00 NY
        late = datetime(2026, 10, 7, 13, 11, tzinfo=e.UTC).timestamp()  # 09:11 NY
        self.assertTrue(e.in_session(inside))
        self.assertFalse(e.in_session(early))
        self.assertFalse(e.in_session(late))
        self.assertAlmostEqual(e.session_end_ts(inside), late)

    def test_utc_mapping(self):
        # Slides: 03:05–09:11 NY = 07:05–13:11 UTC in October
        start = datetime(2026, 10, 7, 7, 5, tzinfo=e.UTC).timestamp()
        end = datetime(2026, 10, 7, 13, 10, 59, tzinfo=e.UTC).timestamp()
        self.assertTrue(e.in_session(start))
        self.assertTrue(e.in_session(end))


class FvgTests(unittest.TestCase):
    def test_bearish_three_candle_gap(self):
        c1 = bar(H, 100, 101, 99.5, 100.2)
        c2 = bar(H + 60, 100.2, 100.4, 97.0, 97.2)
        c3 = bar(H + 120, 97.2, 97.4, 96.8, 97.0)
        self.assertTrue(e.bearish_fvg(c1, c2, c3))
        self.assertFalse(e.bullish_fvg(c1, c2, c3))

    def test_no_gap_when_wicks_overlap(self):
        c1 = bar(H, 100, 101, 98, 99)
        c2 = bar(H + 60, 99, 100, 97, 97.5)
        c3 = bar(H + 120, 97.5, 98.5, 97, 97.8)
        self.assertFalse(e.bearish_fvg(c1, c2, c3))

    def test_bullish_mirror(self):
        c1 = bar(H, 100, 100.2, 99.0, 99.8)
        c2 = bar(H + 60, 99.8, 103.0, 99.7, 102.8)
        c3 = bar(H + 120, 102.8, 103.2, 102.5, 103.0)
        self.assertTrue(e.bullish_fvg(c1, c2, c3))


class SweepTests(unittest.TestCase):
    def test_short_needs_bullish_prev_and_high_taken(self):
        prev = bar(PREV, 100, 101, 99.5, 100.8)
        self.assertTrue(e.is_bullish(prev))
        accum = m1_flat(H - 20 * 60, 20, 100.4)
        self.assertTrue(e.accum_held(accum, H, prev[2], "SHORT"))
        sweep = bar(H + 120, 100.9, 101.4, 100.8, 101.2)
        self.assertEqual(e.first_sweep_bar(accum + [sweep], H, prev[2], "SHORT", H), sweep)

    def test_accum_fails_if_already_swept(self):
        prev_high = 101.0
        leak = [bar(H - 5 * 60, 100.8, 101.2, 100.7, 100.9)]
        self.assertFalse(e.accum_held(leak, H, prev_high, "SHORT"))

    def test_long_sweeps_prev_low(self):
        prev = bar(PREV, 100.8, 101.2, 99.5, 99.7)
        self.assertTrue(e.is_bearish(prev))
        accum = m1_flat(H - 20 * 60, 20, 100.0)
        self.assertTrue(e.accum_held(accum, H, prev[3], "LONG"))
        sweep = bar(H + 60, 99.8, 99.9, 99.2, 99.3)
        self.assertEqual(e.first_sweep_bar(accum + [sweep], H, prev[3], "LONG", H), sweep)


class RRTests(unittest.TestCase):
    def test_skip_when_opposite_closer_than_2r(self):
        # entry 100, sweep 101 → stop ~101.05, R~1.05, 2R~2.10, opp at 99 is only 1.00 away
        stop, target, rr, reason = e.size_trade("SHORT", 100.0, 101.0, 99.0)
        self.assertIsNone(stop)
        self.assertEqual(reason, "opp_closer_than_2r")

    def test_skip_stop_over_two_percent(self):
        stop, target, rr, reason = e.size_trade("SHORT", 100.0, 103.0, 90.0)
        self.assertEqual(reason, "stop_over_2pct")

    def test_caps_target_at_4r(self):
        stop, target, rr, reason = e.size_trade("SHORT", 100.0, 101.0, 80.0)
        self.assertIsNone(reason)
        self.assertAlmostEqual(rr, 4.0)
        risk = stop - 100.0
        self.assertAlmostEqual(100.0 - target, 4.0 * risk, places=6)

    def test_uses_2r_when_room_is_exactly_2r(self):
        entry, sweep = 100.0, 101.0
        stop = e.stop_beyond_sweep("SHORT", sweep)
        risk = stop - entry
        opp = entry - 2.0 * risk
        s, t, rr, reason = e.size_trade("SHORT", entry, sweep, opp)
        self.assertIsNone(reason)
        self.assertAlmostEqual(rr, 2.0)
        self.assertAlmostEqual(t, opp)

    def test_long_mirror(self):
        stop, target, rr, reason = e.size_trade("LONG", 100.0, 99.0, 110.0)
        self.assertIsNone(reason)
        self.assertLess(stop, 100.0)
        self.assertGreater(target, 100.0)
        self.assertAlmostEqual(rr, 4.0)

    def test_fee_and_notional(self):
        self.assertEqual(e.NOTIONAL, 1000.0)
        self.assertAlmostEqual(e.FEE_USDT, 1.20)
        pnl = e.net_pnl("SHORT", 100.0, 98.0)
        self.assertAlmostEqual(pnl, 20.0 - 1.20)


class ManageTests(unittest.TestCase):
    def test_stop_first_same_bar(self):
        trade = {
            "direction": "SHORT",
            "entry": 100.0,
            "stop": 101.0,
            "target": 97.0,
            "opened_at": H + 600,
            "session_end": H + 5 * 3600,
        }
        bars = [bar(H + 600, 100.0, 101.2, 96.5, 99.0)]
        out = e.manage_open(trade, bars, H + 700)
        self.assertEqual(out["exit_reason"], "STOP")
        self.assertEqual(out["exit_price"], 101.0)

    def test_session_mark(self):
        end = e.session_end_ts(H + 60)
        trade = {
            "direction": "SHORT",
            "entry": 100.0,
            "stop": 104.0,
            "target": 90.0,
            "opened_at": H,
            "session_end": end,
        }
        bars = [bar(int(end) - 60, 99.5, 99.7, 99.4, 99.6)]
        out = e.manage_open(trade, bars, end + 10)
        self.assertEqual(out["exit_reason"], "SESSION")
        self.assertEqual(out["exit_price"], 99.6)


class DetectTests(unittest.TestCase):
    def test_short_setup_then_tap(self):
        prev = bar(PREV, 100, 101, 99.6, 100.7)
        h1 = [prev, bar(H, 100.7, 101.5, 97.0, 98.0)]
        accum = m1_flat(H - 20 * 60, 20, 100.5)
        sweep = bar(H + 60, 100.8, 101.3, 100.6, 101.1)
        c1 = bar(H + 180, 101.0, 101.1, 100.4, 100.5)
        c2 = bar(H + 240, 100.5, 100.6, 96.0, 97.5)
        c3 = bar(H + 300, 97.5, 99.8, 97.2, 98.0)  # 99.8 < 100.4
        tap = bar(H + 420, 99.2, 100.1, 99.0, 99.5)
        m1 = accum + [sweep, c1, c2, c3, tap]
        now = H + 500
        setups = e.detect_setups(h1, m1, now, "BTCUSDT")
        self.assertTrue(setups)
        fill = e.fill_from_setup(setups[0], m1, now + 60)
        self.assertIsNotNone(fill)
        self.assertNotIn("skip", fill or {})
        self.assertEqual(fill["direction"], "SHORT")
        self.assertGreater(fill["stop"], fill["entry"])
        self.assertLess(fill["target"], fill["entry"])
        self.assertGreaterEqual(fill["rr"], 2.0)

    def test_long_setup_then_tap(self):
        prev = bar(PREV, 100.8, 101.2, 99.2, 99.6)
        h1 = [prev, bar(H, 99.6, 104.0, 98.8, 103.0)]
        accum = m1_flat(H - 20 * 60, 20, 99.8)
        sweep = bar(H + 60, 99.5, 99.6, 98.9, 99.1)
        c1 = bar(H + 180, 99.2, 99.4, 99.0, 99.3)
        c2 = bar(H + 240, 99.3, 103.5, 99.2, 103.0)
        c3 = bar(H + 300, 103.0, 103.4, 100.2, 100.8)  # 100.2 > 99.4
        tap = bar(H + 420, 100.6, 100.8, 99.9, 100.3)
        m1 = accum + [sweep, c1, c2, c3, tap]
        now = H + 500
        setups = e.detect_setups(h1, m1, now, "ETHUSDT")
        self.assertTrue(setups)
        fill = e.fill_from_setup(setups[0], m1, now + 60)
        self.assertIsNotNone(fill)
        self.assertNotIn("skip", fill or {})
        self.assertEqual(fill["direction"], "LONG")
        self.assertLess(fill["stop"], fill["entry"])
        self.assertGreater(fill["target"], fill["entry"])
        self.assertGreaterEqual(fill["rr"], 2.0)


if __name__ == "__main__":
    unittest.main()
