#!/usr/bin/env python3
"""Live six-stage business cycle (Martin Pring model).

Fetches daily prices for a bond, stock and commodity fund from Yahoo Finance,
marks each as rising or falling (price vs. its 3, 6, 12 or 18-month moving
average), works out the current stage and the monthly stage history, and
writes business_cycle_live.html next to this script.

Standard library only. Usage:
    python3 cycle_live.py                 # fetch, build, open in browser
    python3 cycle_live.py --no-open       # fetch and build only
    python3 cycle_live.py --bonds TLT --stocks QQQ --commodities GSG
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import signals  # noqa: E402  (shared signal rules)
TEMPLATE = HERE / "template.html"
OUTPUT = HERE / "business_cycle_live.html"
CACHE = HERE / "last_data.json"
WATCHLIST = HERE / "watchlist.json"   # extra assets shown in the page's asset table
BACKTEST = HERE / "backtest.json"     # cached backtest results, rebuilt weekly
BACKTEST_MAX_AGE_DAYS = 7

# Moving-average lookbacks the page can switch between (months -> trading days)
PERIODS = {"3": 63, "6": 126, "12": 252, "18": 378}
DEFAULT_PERIOD = "12"
HISTORY_MONTHS = 60    # stage history shown on the page
SPARK_DAYS = 504       # about 2 years in the price charts

# Pring's six stages: +1 rising, -1 falling (bonds, stocks, commodities)
STAGES = [
    (1, -1, -1),
    (1, 1, -1),
    (1, 1, 1),
    (-1, 1, 1),
    (-1, -1, 1),
    (-1, -1, -1),
]
ASSET_KEYS = ("bonds", "stocks", "commodities")


def fetch_yahoo(symbol, years="10y"):
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={years}&interval=1d&includeAdjustedClose=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(3):                      # retry slow or dropped replies
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                payload = json.load(resp)
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    ind = result["indicators"]
    closes = (ind.get("adjclose") or [{}])[0].get("adjclose") or ind["quote"][0]["close"]
    rows = [
        (datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), c)
        for t, c in zip(stamps, closes)
        if c is not None
    ]
    # Yahoo's long daily history leaves the latest day's close empty for a few hours after the
    # close, while its summary already has the final price. Use that price, but only once the
    # trading session has ended (never a half-day price), scaled like the adjusted series.
    meta = result["meta"]
    last_day = datetime.fromtimestamp(stamps[-1], timezone.utc).strftime("%Y-%m-%d") if stamps else None
    session_end = (meta.get("currentTradingPeriod") or {}).get("regular", {}).get("end")
    price, price_time = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if (rows and last_day and last_day > rows[-1][0] and price and price_time and session_end
            and price_time >= session_end
            and datetime.fromtimestamp(price_time, timezone.utc).strftime("%Y-%m-%d") == last_day):
        raw = ind["quote"][0]["close"]
        ratio = 1.0
        for c_adj, c_raw in zip(reversed(closes), reversed(raw)):
            if c_adj is not None and c_raw:
                ratio = c_adj / c_raw
                break
        rows.append((last_day, price * ratio))
    if len(rows) < max(PERIODS.values()) + 30:
        raise RuntimeError(f"{symbol}: only {len(rows)} days of data")
    return {
        "symbol": symbol,
        "name": result["meta"].get("longName") or result["meta"].get("shortName") or symbol,
        "dates": [d for d, _ in rows],
        "closes": [round(c, 4) for _, c in rows],
    }


moving_average = signals.moving_average


def stage_of(dirs):
    for i, s in enumerate(STAGES):
        if s == dirs:
            return i + 1, []
    nearest = [i + 1 for i, s in enumerate(STAGES) if sum(a != b for a, b in zip(s, dirs)) == 1]
    return None, nearest


def analyse_watch(s):
    """Trend vs. each moving average, plus 3- and 12-month returns, for one asset."""
    closes = s["closes"]

    def ret(n):
        return round((closes[-1] / closes[-1 - n] - 1) * 100, 2) if len(closes) > n else None

    periods = {}
    for months, days in PERIODS.items():
        ma = moving_average(closes, days)
        gap = round((closes[-1] / ma[-1] - 1) * 100, 2)
        for rule in signals.RULES:
            dirs = signals.directions(closes, ma, rule, signals.HOLD_DAYS)
            periods[signals.key(months, rule)] = {"dir": dirs[-1], "gap": gap}
    return {
        "price": round(closes[-1], 2),
        "date": s["dates"][-1],
        "ret": {"m3": ret(63), "m12": ret(252)},
        "periods": periods,
    }


def fetch_watchlist():
    if not WATCHLIST.exists():
        return []
    out = []
    items = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    print(f"Fetching {len(items)} assets for the asset table...")
    for item in items:
        try:
            out.append({**item, **analyse_watch(fetch_yahoo(item["symbol"], "5y"))})
        except Exception as exc:
            print(f"  skipped {item['symbol']}: {exc}", file=sys.stderr)
            out.append({**item, "error": True})
        time.sleep(0.3)  # be gentle with Yahoo
    return out


def load_backtest(force):
    """Return backtest results, rebuilding them when forced or older than a week."""
    # Age comes from the build date stored in the file (file times reset on a fresh checkout)
    cached = json.loads(BACKTEST.read_text()) if BACKTEST.exists() else None
    fresh = False
    if cached and cached.get("built"):
        age = (datetime.now() - datetime.strptime(cached["built"], "%Y-%m-%d")).days
        fresh = age < BACKTEST_MAX_AGE_DAYS and all(":" in k or k.isdigit() for k in cached.get("periods", {}))
        fresh = fresh and any(":" in k for k in cached.get("periods", {}))   # needs the rule-aware format
        fresh = fresh and bool(cached.get("deepbuy"))                         # needs the deep-buy study
        fresh = fresh and bool(cached.get("spx"))                             # needs the price-chart series
    if fresh and not force:
        return cached
    try:
        import backtest
        result = backtest.run()
        BACKTEST.write_text(json.dumps(result))
        return result
    except Exception as exc:
        print(f"Backtest failed: {exc}", file=sys.stderr)
        return json.loads(BACKTEST.read_text()) if BACKTEST.exists() else None


MACRO = HERE / "macro.json"   # last good economic-indicator reading


def load_macro():
    """Fetch FRED indicators; fall back to the last saved reading if FRED is down."""
    try:
        import macro
        result = macro.run()
        MACRO.write_text(json.dumps(result))
        return result
    except Exception as exc:
        print(f"Economic indicators failed: {exc}", file=sys.stderr)
        if MACRO.exists():
            result = json.loads(MACRO.read_text())
            result["stale"] = True
            return result
        return None


def analyse(series, ma_days, rule="raw"):
    assets = {}
    for key, s in series.items():
        ma = moving_average(s["closes"], ma_days)
        dirs = signals.directions(s["closes"], ma, rule, signals.HOLD_DAYS)
        price, avg = s["closes"][-1], ma[-1]
        spark_from = max(ma_days - 1, len(s["closes"]) - SPARK_DAYS)
        step = 3
        idx = list(range(spark_from, len(s["closes"]), step))
        if idx[-1] != len(s["closes"]) - 1:
            idx.append(len(s["closes"]) - 1)
        assets[key] = {
            "symbol": s["symbol"],
            "name": s["name"],
            "date": s["dates"][-1],
            "price": round(price, 2),
            "ma": round(avg, 2),
            "gap": round((price / avg - 1) * 100, 2),
            "dir": dirs[-1],
            "spark": {
                "dates": [s["dates"][i] for i in idx],
                "price": [round(s["closes"][i], 2) for i in idx],
                "ma": [round(ma[i], 2) for i in idx],
            },
            "_dirs": dirs,
        }

    # Monthly history: last trading day of each month, all three assets
    lookup = {}
    for key, s in series.items():
        lookup[key] = {d: v for d, v in zip(s["dates"], assets[key]["_dirs"]) if v is not None}
    common = sorted(set.intersection(*(set(v) for v in lookup.values())))
    month_end = {}
    for d in common:
        month_end[d[:7]] = d
    history = []
    for month in sorted(month_end)[-HISTORY_MONTHS:]:
        d = month_end[month]
        dirs = tuple(lookup[k][d] for k in ASSET_KEYS)
        stage, _ = stage_of(dirs)
        history.append({"month": month, "stage": stage, "dirs": list(dirs)})

    for a in assets.values():
        del a["_dirs"]
    stage, nearest = stage_of(tuple(assets[k]["dir"] for k in ASSET_KEYS))
    return {
        "maDays": ma_days,
        "stage": stage,
        "nearest": nearest,
        "assets": assets,
        "history": history,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bonds", default="IEF", help="bond fund ticker (default IEF)")
    ap.add_argument("--stocks", default="SPY", help="stock fund ticker (default SPY)")
    ap.add_argument("--commodities", default="DBC", help="commodity fund ticker (default DBC)")
    ap.add_argument("--period", default=DEFAULT_PERIOD, choices=list(PERIODS),
                    help="moving average (months) the page opens with (default 12)")
    ap.add_argument("--backtest", action="store_true", help="rebuild the backtest now (otherwise weekly)")
    ap.add_argument("--no-open", action="store_true", help="don't open the page when done")
    ap.add_argument("--out", default=str(OUTPUT), help="where to write the page (default next to this script)")
    ap.add_argument("--template", default=str(TEMPLATE), help="page design to use (default template.html)")
    ap.add_argument("--split-research", action="store_true",
                    help="write the backtest to research.json next to the page instead of inside it (smaller page)")
    ap.add_argument("--no-alerts", action="store_true", help="don't check for or send stage-change alerts")
    ap.add_argument("--alert-lang", default="en", choices=["en", "fa"], help="language of alert notifications")
    args = ap.parse_args()
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    tickers = {"bonds": args.bonds.upper(), "stocks": args.stocks.upper(), "commodities": args.commodities.upper()}

    try:
        series = {}
        for key, sym in tickers.items():
            print(f"Fetching {sym} ({key})...")
            series[key] = fetch_yahoo(sym)
        periods = {
            signals.key(months, rule): analyse(series, days, rule)
            for months, days in PERIODS.items()
            for rule in signals.RULES
        }
        data = {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "asOf": min(a["date"] for a in periods[DEFAULT_PERIOD]["assets"].values()),
            "periods": periods,
            "watch": fetch_watchlist(),
            "stale": False,
        }
        try:                                   # VIX and the S&P 500's drop from its high
            import deepbuy
            print("Fetching VIX and S&P 500 history...")
            data["deep"] = deepbuy.live()
        except Exception as exc:
            print(f"Deep-buy readings failed: {exc}", file=sys.stderr)
            old = json.loads(CACHE.read_text()).get("deep") if CACHE.exists() else None
            if old:
                data["deep"] = dict(old, stale=True)
        CACHE.write_text(json.dumps(data))
    except Exception as exc:  # network down, Yahoo change, bad ticker
        print(f"Couldn't fetch prices: {exc}", file=sys.stderr)
        cached = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        if "periods" not in cached:
            sys.exit(1)
        print("Using the last saved data instead.", file=sys.stderr)
        data = cached
        data["stale"] = True
    data["default"] = args.period
    data["backtest"] = load_backtest(args.backtest)
    data["macro"] = load_macro()

    # Stage-change alerts (skipped when prices couldn't be refreshed)
    if not args.no_alerts and not data.get("stale"):
        try:
            import alerts
            msgs = alerts.check(data, HERE / "alerts_state.json", args.alert_lang)
            if msgs:
                alerts.notify(msgs, args.alert_lang)
            else:
                print("Alerts: no change")
        except Exception as exc:
            print(f"Alerts failed: {exc}", file=sys.stderr)

    page_data = dict(data)
    if args.split_research and page_data.get("backtest"):
        (output.parent / "research.json").write_text(json.dumps(page_data.pop("backtest")), encoding="utf-8")
    html = Path(args.template).read_text(encoding="utf-8").replace("/*__DATA__*/null", json.dumps(page_data))
    output.write_text(html, encoding="utf-8")

    print(f"Prices as of {data['asOf']}")
    for months in PERIODS:
        row = []
        for rule in signals.RULES:
            p = data["periods"].get(signals.key(months, rule))
            if p:
                row.append(f"{rule} {p['stage'] or 'mixed'}")
        print(f"  {months:>2}-month average, stage by rule: " + "  ".join(row))
    watch = data.get("watch", [])
    if watch:
        ok = sum(1 for w in watch if not w.get("error"))
        print(f"Asset table: {ok} of {len(watch)} assets loaded")
    if data.get("backtest"):
        print(f"Backtest: built {data['backtest']['built']}")
    if data.get("macro"):
        print(f"Economic score: {data['macro']['score']:+d} of 4" + (" (saved reading)" if data["macro"].get("stale") else ""))
    print(f"Wrote {output}")
    if not args.no_open:
        subprocess.run(["open", str(output)], check=False)


if __name__ == "__main__":
    main()
