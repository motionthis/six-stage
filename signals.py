"""Shared trend-signal rules used by the live page and the backtest.

An asset is "rising" when its price is above its moving average. The raw rule
flips on every crossing, which causes false alarms when the price hovers near
the average. Two confirmation rules make the signal steadier:

  band  - only flip once the price is more than BAND above/below the average
  hold  - only flip after the price has stayed on the other side for a while
          (HOLD_DAYS trading days on daily data, HOLD_MONTHS month-ends on
          monthly data)
"""

RULES = ("raw", "band", "hold")
DEFAULT_RULE = "raw"
BAND = 0.02          # 2%
HOLD_DAYS = 21       # about one month of trading days
HOLD_MONTHS = 2      # two month-ends in a row


def key(period, rule):
    """Data key for a period/rule pair: '12' for raw, '12:band', '12:hold'."""
    return str(period) if rule == "raw" else f"{period}:{rule}"


def directions(closes, averages, rule, hold_obs):
    """Return +1/-1 (or None before the average exists) for every observation."""
    out = []
    cur = None
    streak = 0
    for c, m in zip(closes, averages):
        if m is None:
            out.append(None)
            continue
        raw = 1 if c > m else -1
        if cur is None or rule == "raw":
            cur = raw
        elif rule == "band":
            if cur < 0 and c > m * (1 + BAND):
                cur = 1
            elif cur > 0 and c < m * (1 - BAND):
                cur = -1
        elif rule == "hold":
            if raw != cur:
                streak += 1
                if streak >= hold_obs:
                    cur, streak = raw, 0
            else:
                streak = 0
        out.append(cur)
    return out


def moving_average(values, n):
    out = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out
