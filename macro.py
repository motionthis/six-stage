"""Economic indicators from FRED (US Federal Reserve Bank of St. Louis).

Each indicator votes on whether the US economy is expanding (+1), at a
turning point / unclear (0) or contracting (-1):

  Yield curve (T10Y2Y, 10-year minus 2-year Treasury yield)
      above 0.25 = +1, 0 to 0.25 = 0 (flat), below 0 = -1 (inverted: recession risk)
  Unemployment (UNRATE), Sahm-rule style
      3-month average minus its lowest level of the prior 12 months:
      below 0.3 = +1, 0.3 to 0.5 = 0, 0.5 or more = -1 (recession signal)
  Credit spread (BAA10Y, Baa corporate yield minus 10-year Treasury)
      more than 0.25 points below its 12-month average = +1, more than 0.25
      above it = -1 (lenders getting nervous), otherwise 0
  Industrial production (INDPRO), change over 12 months
      above +1% = +1, -1% to +1% = 0, below -1% = -1

The score is the sum (-4..+4). Positive scores fit the expansion side of
Pring's cycle (stages 3-5); negative scores fit the contraction side
(stages 6, 1 and 2).
"""
import csv
import io
import time
import urllib.request

INDICATORS = ("curve", "unemployment", "credit", "production")
SERIES = {"curve": "T10Y2Y", "unemployment": "UNRATE", "credit": "BAA10Y", "production": "INDPRO"}
HISTORY_MONTHS = 60


def fetch_fred(series_id, start="1990-01-01", retries=3):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    last = None
    for attempt in range(retries):
        try:
            # FRED stalls requests that send a bare browser User-Agent; Python's default works
            with urllib.request.urlopen(url, timeout=30) as resp:
                text = resp.read().decode("utf-8")
            break
        except Exception as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise last
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or row[0].startswith("observation") or row[0] == "DATE":
            continue
        try:
            rows.append((row[0], float(row[1])))
        except ValueError:       # FRED uses "." for missing values
            continue
    return rows


def month_end(rows):
    """Last value in each month, as an ordered list of (YYYY-MM, value, date)."""
    out = {}
    for d, v in rows:
        out[d[:7]] = (v, d)
    return [(m, v, d) for m, (v, d) in sorted(out.items())]


def vote_curve(v):
    return 1 if v > 0.25 else (0 if v >= 0 else -1)


def signals_for(monthly):
    """Return {month: (vote, value, detail)} for every indicator month."""
    res = {}
    for m, v, _ in monthly["curve"]:
        res.setdefault(m, {})["curve"] = (vote_curve(v), round(v, 2), None)

    u = monthly["unemployment"]
    avg3 = [None if i < 2 else sum(x[1] for x in u[i - 2 : i + 1]) / 3 for i in range(len(u))]
    for i, (m, v, _) in enumerate(u):
        prior = [a for a in avg3[max(0, i - 12) : i] if a is not None]
        if avg3[i] is None or not prior:
            continue
        sahm = avg3[i] - min(prior)
        vote = 1 if sahm < 0.3 else (0 if sahm < 0.5 else -1)
        res.setdefault(m, {})["unemployment"] = (vote, round(v, 1), round(sahm, 2))

    c = monthly["credit"]
    for i, (m, v, _) in enumerate(c):
        if i < 12:
            continue
        avg = sum(x[1] for x in c[i - 12 : i]) / 12
        diff = v - avg
        vote = 1 if diff < -0.25 else (-1 if diff > 0.25 else 0)
        res.setdefault(m, {})["credit"] = (vote, round(v, 2), round(diff, 2))

    p = monthly["production"]
    for i, (m, v, _) in enumerate(p):
        if i < 12:
            continue
        yoy = (v / p[i - 12][1] - 1) * 100
        vote = 1 if yoy > 1 else (0 if yoy >= -1 else -1)
        res.setdefault(m, {})["production"] = (vote, round(v, 1), round(yoy, 2))
    return res


def run(log=print):
    log("Fetching economic indicators from FRED...")
    monthly, raw = {}, {}
    for key, sid in SERIES.items():
        rows = fetch_fred(sid)
        raw[key] = rows
        monthly[key] = month_end(rows)
        time.sleep(0.3)
    by_month = signals_for(monthly)

    # Latest reading of each indicator (they are released on different dates)
    latest = {}
    for key in INDICATORS:
        for m in sorted(by_month, reverse=True):
            if key in by_month[m]:
                vote, value, detail = by_month[m][key]
                last_date = next(d for mm, _, d in reversed(monthly[key]) if mm == m)
                latest[key] = {"vote": vote, "value": value, "detail": detail, "date": last_date, "series": SERIES[key]}
                break

    # Monthly score history, carrying each indicator's last known vote forward
    history, last = [], {}
    for m in sorted(by_month):
        for key, (vote, _, _) in by_month[m].items():
            last[key] = vote
        if len(last) == len(INDICATORS):
            history.append({"month": m, "score": sum(last.values()), "votes": dict(last)})
    history = history[-HISTORY_MONTHS:]

    # Small charts: month-end values over the last five years
    spark = {
        key: {"months": [m for m, _, _ in monthly[key][-HISTORY_MONTHS:]],
              "values": [round(v, 2) for _, v, _ in monthly[key][-HISTORY_MONTHS:]]}
        for key in INDICATORS
    }
    score = sum(x["vote"] for x in latest.values())
    return {"indicators": latest, "score": score, "history": history, "spark": spark}


if __name__ == "__main__":
    out = run()
    for k, v in out["indicators"].items():
        print(f"{k:<13} {v['series']:<7} {v['date']}  value {v['value']}  detail {v['detail']}  vote {v['vote']:+d}")
    print("score", out["score"], " last months:", [h["score"] for h in out["history"][-6:]])
