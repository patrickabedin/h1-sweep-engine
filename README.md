# H1 sweep paper engine

Standalone paper book for the H1 liquidity-sweep + M1 fair-value-gap setup. Not Trend Continuation. No exchange keys. No live orders.

- Engine: this repo, deployed to `/opt/engines/h1-sweep` on grok-engines-ams3
- Desk: [h1-sweep-desk.vercel.app](https://h1-sweep-desk.vercel.app/calendar/)
- Size: $100 × 10x = $1,000 notional, $1.20 round-trip fee
- Session: 03:05–09:11 America/New_York
- Reward: 2R minimum, 4R cap; skip if the opposite dealing-range side is closer than 2R

```
python3 -m unittest tests.test_engine -v
python3 scan.py --once
python3 replay.py --days 5
python3 publish.py --write
```

`engine-trend-continuation.service` is a different unit. Do not restart it from here.
