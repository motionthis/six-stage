"""Deep-buy readings: market fear (VIX) and how far US stocks are below their high.

live()   today's VIX, its recent peak, the last close above 40, and the S&P 500's drop from
         its all-time high. Used by the page's "Deep buy meter".
study()  the history behind the meter (built with the weekly backtest):
           * every first close of VIX above 40, what the S&P 500 did next, how much lower it
             went first, and the six-stage reading at the time,
           * buying when VIX falls back below 30 after a spike above 40,
           * the first month of stage 2,
           * forward returns by how far stocks were below their high.

Standard library only. Data from Yahoo Finance (^VIX, ^GSPC daily since 1990).
"""
import json
import time
import urllib.request
from datetime import datetime, timezone

START = 631152000         # 1990-01-01, when VIX history starts
FEAR = 40.0               # "extreme fear" close
CALM = 30.0               # back-to-calm level after a spike
WATCH = 30.0              # "high fear" level
GAP_DAYS = 126            # a new VIX episode needs ~6 calm months since the last close above 40
HEALTHY = (2, 3, 4)       # stages where a VIX spike was a scare inside a healthy cycle


def fetch_daily(symbol, start=START, retries=3):
    """Daily closes as a sorted list of (YYYY-MM-DD, close)."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(symbol)}"
           f"?period1={start}&period2={int(time.time())}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.load(resp)["chart"]["result"][0]
            break
        except Exception as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise last
    closes = result["indicators"]["quote"][0]["close"]
    out = {}
    for t, c in zip(result["timestamp"], closes):
        if c is not None:
            out[datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")] = c
    meta = result.get("meta", {})
    # Yahoo can leave the latest close empty for a while; use the market price once the day is over
    price, ptime = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    end = (meta.get("currentTradingPeriod") or {}).get("regular", {}).get("end")
    if price and ptime and end and ptime >= end:
        day = datetime.fromtimestamp(ptime, timezone.utc).strftime("%Y-%m-%d")
        if not out or day > max(out):
            out[day] = price
    return sorted(out.items())


def _r(x, d=2):
    return None if x is None else round(x, d)


def live(vix=None, spx=None):
    """Today's fear and drop readings."""
    vix = vix or fetch_daily("^VIX")
    spx = spx or fetch_daily("^GSPC")
    vd, vv = zip(*vix)
    recent = list(zip(vd[-21:], vv[-21:]))                  # about one month
    peak_d, peak_v = max(recent, key=lambda x: x[1])
    above = [d for d, v in vix if v > FEAR]
    year = list(zip(vd[-252:], vv[-252:]))
    step = 2
    spark = year[::step] if (len(year) - 1) % step == 0 else year[::step] + [year[-1]]
    high_d, high_v = max(spx, key=lambda x: x[1])
    now_d, now_v = spx[-1]
    return {
        "vix": {
            "now": _r(vv[-1]), "date": vd[-1],
            "peak": _r(peak_v), "peakDate": peak_d,             # highest close of the last month
            "yearHigh": _r(max(v for _, v in year)),
            "last40": above[-1] if above else None,              # last close above 40
            "spark": {"dates": [d for d, _ in spark], "v": [_r(v, 1) for _, v in spark]},
        },
        "spx": {"now": _r(now_v), "date": now_d, "high": _r(high_v), "highDate": high_d,
                "drop": _r((now_v / high_v - 1) * 100)},
    }


def _prev_month(day):
    y, m = int(day[:4]), int(day[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _summary(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    s = sorted(vals)
    mid = len(s) // 2
    med = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
    return {"avg": _r(sum(s) / len(s), 1), "med": _r(med, 1),
            "pos": round(100 * sum(v > 0 for v in s) / len(s)), "n": len(s)}


def study(stage_at, spy_monthly, vix=None, spx=None):
    """History behind the meter. stage_at: {YYYY-MM: stage or None}, spy_monthly: {YYYY-MM: close}."""
    vix = vix or fetch_daily("^VIX")
    spx = spx or fetch_daily("^GSPC")
    sp = dict(spx)
    days = [d for d, _ in vix if d in sp]
    v = dict(vix)

    def after(i, h):
        return (sp[days[i + h]] / sp[days[i]] - 1) * 100 if i + h < len(days) else None

    def lower(i):
        win = [sp[days[j]] for j in range(i, min(i + 252, len(days)))]
        return (min(win) / sp[days[i]] - 1) * 100

    # First close above 40 in each episode
    events, last_above = [], -10 ** 9
    for i, d in enumerate(days):
        if v[d] > FEAR:
            if i - last_above > GAP_DAYS:
                pm = _prev_month(d)
                peak = max(v[days[j]] for j in range(i, min(i + 63, len(days))))
                events.append({
                    "date": d, "vix": _r(v[d], 1), "peak": _r(peak, 1),
                    "r3": _r(after(i, 63), 1), "r12": _r(after(i, 252), 1), "lower": _r(lower(i), 1),
                    "stage": stage_at.get(pm), "known": pm in stage_at,
                })
            last_above = i

    def group(evs):
        return {"r12": _summary([e["r12"] for e in evs]), "lower": _summary([e["lower"] for e in evs])}

    healthy = [e for e in events if e["stage"] in HEALTHY]
    other = [e for e in events if e["known"] and e["stage"] not in HEALTHY]

    # Buy when VIX falls back under 30 after a close above 40
    calm, armed = [], False
    for i, d in enumerate(days):
        if v[d] > FEAR:
            armed = True
        elif armed and v[d] < CALM:
            armed = False
            calm.append(after(i, 252))

    # Monthly: first month of stage 2, and drop-from-high buckets
    sm = sorted(spy_monthly)
    pos = {m: i for i, m in enumerate(sm)}

    def fwd(m, h):
        i = pos.get(m)
        return None if i is None or i + h >= len(sm) else (spy_monthly[sm[i + h]] / spy_monthly[m] - 1) * 100

    tested = sorted(stage_at)
    first2 = [m for k, m in enumerate(tested) if stage_at[m] == 2 and k and stage_at[tested[k - 1]] != 2]
    peak, drop = 0, {}
    for m in sm:
        peak = max(peak, spy_monthly[m])
        drop[m] = (spy_monthly[m] / peak - 1) * 100
    buckets = []
    for lo, hi in ((0, 10), (10, 20), (20, 30), (30, 100)):
        ms = [m for m in sm if lo <= -drop[m] < hi]
        buckets.append({"lo": lo, "hi": hi, "r12": _summary([fwd(m, 12) for m in ms])})

    return {
        "from": days[0][:4],
        "fear": FEAR, "calm": CALM,
        "events": events,
        "healthy": group(healthy), "other": group(other),
        "allEvents": group(events),
        "calmAfter": _summary(calm),
        "stage2First": {"r12": _summary([fwd(m, 12) for m in first2]), "r24": _summary([fwd(m, 24) for m in first2])},
        "allMonths": _summary([fwd(m, 12) for m in sm]),
        "drops": buckets,
        "stagesFrom": tested[0] if tested else None,
    }


if __name__ == "__main__":
    print(json.dumps(live(), indent=1)[:800])
