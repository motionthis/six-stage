"""Backtest of the six-stage model on monthly data.

1. What each sector and asset class did in each stage (sector returns are
   measured against the S&P 500).
2. A strategy test: a portfolio that follows Pring's allocation tilt for the
   current stage, compared with simple benchmarks, after trading costs.
3. A stability check: everything is also computed separately for
   1993-2009 and 2010-today.

Method (no look-ahead): at each month end the stage is worked out from prices
up to that month only, using one of the signal rules in signals.py; the result
is the return over the FOLLOWING month. "Per year" values in the tables are the
average monthly figure x 12 (simple, not compounded); strategy returns are
compounded.

Long-history stand-ins, so the test starts in the 1990s:
  bonds = VUSTX (Vanguard long-term Treasury fund, since 1986)
  stocks = SPY (since 1993)
  commodities = ^SPGSCI (S&P GSCI commodity index)
  cash = ^IRX (13-week Treasury bill yield)
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import signals  # noqa: E402

CORE = {
    "bonds": ["VUSTX", "TLT"],
    "stocks": ["SPY"],
    "commodities": ["^SPGSCI", "GSG", "DBC"],
}
SECTORS = ["XLK", "XLC", "XLY", "XLF", "XLI", "XLB", "XLE", "XLP", "XLV", "XLU", "XLRE"]
ASSET_CLASSES = {
    "bonds": ["VUSTX", "TLT"],
    "stocks": ["SPY"],
    "commodities": ["^SPGSCI", "GSG", "DBC"],
    "gold": ["GC=F", "GLD"],
    "cash": ["^IRX"],          # yield in % per year, turned into a monthly return
}
STAGES = [(1, -1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1), (-1, -1, 1), (-1, -1, -1)]
PERIODS = (3, 6, 12, 18)
MIN_MONTHS = 24          # cells with fewer months are flagged as a small sample
SPLIT = "2010-01"        # stability check: before / from this month
COST = 0.001             # 0.1% trading cost per unit of portfolio turned over

# Pring's allocation tilt per stage (ow = overweight, n = neutral, uw = underweight),
# turned into weights: ow counts 2, neutral 1, underweight 0, then normalised.
TILT = [
    {"cash": "n",  "bonds": "ow", "stocks": "uw", "commodities": "uw"},
    {"cash": "uw", "bonds": "ow", "stocks": "ow", "commodities": "uw"},
    {"cash": "uw", "bonds": "n",  "stocks": "ow", "commodities": "ow"},
    {"cash": "uw", "bonds": "uw", "stocks": "ow", "commodities": "ow"},
    {"cash": "ow", "bonds": "uw", "stocks": "uw", "commodities": "ow"},
    {"cash": "ow", "bonds": "n",  "stocks": "uw", "commodities": "uw"},
]
SCORE = {"ow": 2, "n": 1, "uw": 0}
POOL = ("cash", "bonds", "stocks", "commodities")


def _weights(tilt):
    total = sum(SCORE[tilt[a]] for a in POOL)
    return {a: SCORE[tilt[a]] / total for a in POOL}


WEIGHTS = [_weights(t) for t in TILT]
EQUAL = {a: 0.25 for a in POOL}


def fetch_monthly(symbol, retries=3):
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1=0&period2={int(time.time())}&interval=1mo&includeAdjustedClose=true"
    )
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
    ind = result["indicators"]
    closes = (ind.get("adjclose") or [{}])[0].get("adjclose") or ind["quote"][0]["close"]
    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    out = {}
    for t, c in zip(result["timestamp"], closes):
        m = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m")
        if c is not None and m != this_month:   # drop the unfinished month
            out[m] = c                           # later bars overwrite duplicates
    return out


def first_available(candidates, log):
    for sym in candidates:
        try:
            data = fetch_monthly(sym)
            if len(data) > 36:
                return sym, data
        except Exception as exc:
            log(f"  {sym}: {exc}")
        time.sleep(0.4)
    return None, {}


def gold_history(first, log):
    """Gold: GLD where it exists (no missing months), gold futures (GC=F) only before it.

    Yahoo's monthly GC=F series skips dozens of months (May 2022 among them), which left
    rare stages without enough gold returns. The futures part is rescaled to join GLD smoothly.
    """
    try:
        gld = fetch_monthly("GLD")
    except Exception as exc:
        log(f"  GLD: {exc}")
        return first
    sym, fut = first
    if sym != "GC=F":
        try:
            fut = fetch_monthly("GC=F")
        except Exception:
            fut = {}
    if not gld:
        return first
    join = min(gld)
    if join in fut and fut[join]:
        scale = gld[join] / fut[join]
        out = {m: v * scale for m, v in fut.items() if m < join}
    else:
        out = {}
    out.update(gld)
    return ("GC=F+GLD" if out.keys() - gld.keys() else "GLD"), out


def stage_of(dirs):
    for i, s in enumerate(STAGES):
        if s == dirs:
            return i + 1
    return None


def summarise(vals):
    """Average (as % per year), share of positive months, count and t-statistic."""
    if not vals:
        return None
    n = len(vals)
    avg = sum(vals) / n
    wins = sum(1 for v in vals if v > 0) / n
    t = 0.0
    if n > 2:
        var = sum((v - avg) ** 2 for v in vals) / (n - 1)
        t = avg / (var ** 0.5 / n ** 0.5) if var > 0 else 0.0
    return {
        "avg": round(avg * 12 * 100, 1),
        "hit": round(wins * 100),
        "n": n,
        "t": round(t, 2),
        "small": n < MIN_MONTHS,
    }


def perf(rets, cash):
    """Compounded performance statistics for a list of monthly returns."""
    n = len(rets)
    if n < 12:
        return None
    growth, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        growth *= 1 + r
        peak = max(peak, growth)
        mdd = min(mdd, growth / peak - 1)
    mean = sum(rets) / n
    sd = (sum((r - mean) ** 2 for r in rets) / (n - 1)) ** 0.5
    ex = [r - c for r, c in zip(rets, cash)]
    mex = sum(ex) / n
    sdex = (sum((e - mex) ** 2 for e in ex) / (n - 1)) ** 0.5
    return {
        "cagr": round((growth ** (12 / n) - 1) * 100, 1),
        "vol": round(sd * 12 ** 0.5 * 100, 1),
        "mdd": round(mdd * 100, 1),
        "sharpe": round(mex / sdex * 12 ** 0.5, 2) if sdex > 0 else 0.0,
        "growth": round(growth, 2),
        "months": n,
    }


def stage_series(core, months, n, rule):
    """Stage at each month end for one moving-average length and signal rule."""
    per_asset = []
    for key in ("bonds", "stocks", "commodities"):
        closes = [core[key][1][m] for m in months]
        ma = signals.moving_average(closes, n)
        per_asset.append(signals.directions(closes, ma, rule, signals.HOLD_MONTHS))
    stage_at = {}
    stock_dir = {}
    for i, m in enumerate(months):
        dirs = tuple(d[i] for d in per_asset)
        if None in dirs:
            continue
        stage_at[m] = stage_of(dirs)
        stock_dir[m] = dirs[1]
    return stage_at, stock_dir


def simulate(stage_at, stock_dir, core, cash_yield):
    """Follow Pring's tilt month by month and compare with simple benchmarks."""
    bonds, spy, comm = core["bonds"][1], core["stocks"][1], core["commodities"][1]
    tested = sorted(stage_at)
    names = ("pring", "trend", "spy", "sixty", "equal")
    rets = {k: [] for k in names}
    cash_r, dates = [], []
    prev = None
    turnover = 0.0

    def ret(s, a, b):
        return s[b] / s[a] - 1 if a in s and b in s and s[a] else None

    for a, b in zip(tested, tested[1:]):
        r = {
            "cash": cash_yield[a] / 100 / 12 if a in cash_yield else None,
            "bonds": ret(bonds, a, b),
            "stocks": ret(spy, a, b),
            "commodities": ret(comm, a, b),
        }
        if None in r.values():
            continue
        st = stage_at[a]
        w = WEIGHTS[st - 1] if st else (prev or EQUAL)   # mixed signal: keep last allocation
        moved = sum(abs(w[x] - (prev or w)[x]) for x in POOL)
        turnover += moved
        prev = w
        rets["pring"].append(sum(w[x] * r[x] for x in POOL) - COST * moved)
        # Simple trend rule for comparison: stocks when stocks are rising, otherwise cash
        rets["trend"].append(r["stocks"] if stock_dir[a] > 0 else r["cash"])
        rets["spy"].append(r["stocks"])
        rets["sixty"].append(0.6 * r["stocks"] + 0.4 * r["bonds"])
        rets["equal"].append(sum(0.25 * r[x] for x in POOL))
        cash_r.append(r["cash"])
        dates.append(b)
    if not dates:
        return None

    def curve(rs):
        g, out = 1.0, []
        for x in rs:
            g *= 1 + x
            out.append(round(g, 4))
        return out

    split = next((i for i, d in enumerate(dates) if d >= SPLIT), len(dates))
    return {
        "start": dates[0],
        "end": dates[-1],
        "turnover": round(turnover / len(dates) * 12 * 100),   # % of portfolio per year
        "curves": {"dates": dates, **{k: curve(v) for k, v in rets.items()}},
        "stats": {k: perf(v, cash_r) for k, v in rets.items()},
        "halves": {
            "a": {k: perf(v[:split], cash_r[:split]) for k, v in rets.items()},
            "b": {k: perf(v[split:], cash_r[split:]) for k, v in rets.items()},
        },
        "weights": WEIGHTS,
    }


HORIZONS = (3, 6, 12)    # months ahead for "what usually happens next"
MIN_OBS = 6              # fewer observations than this are left out


def after_stats(stage_at, series):
    """What usually followed each stage: forward returns, how long the stage lasted, what came next.

    Forward returns are measured from every month-end spent in the stage, so the windows
    overlap; "episodes" counts separate visits to the stage.
    """
    tested = sorted(stage_at)
    pos_of = {m: i for i, m in enumerate(tested)}

    def fwd(months):
        out = {}
        for name, s in series.items():
            out[name] = {}
            for hz in HORIZONS:
                vals = []
                for m in months:
                    j = pos_of[m] + hz
                    if j < len(tested):
                        b = tested[j]
                        if m in s and b in s and s[m]:
                            vals.append(s[b] / s[m] - 1)
                if len(vals) < MIN_OBS:
                    out[name][str(hz)] = None
                    continue
                vals.sort()
                mid = len(vals) // 2
                med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
                out[name][str(hz)] = {
                    "avg": round(sum(vals) / len(vals) * 100, 1),
                    "med": round(med * 100, 1),
                    "pos": round(sum(1 for v in vals if v > 0) / len(vals) * 100),
                    "n": len(vals),
                }
        return out

    forward = {str(st): fwd([m for m in tested if stage_at[m] == st]) for st in range(1, 7)}
    forward["all"] = fwd(tested)

    # Runs of consecutive months in the same stage (None = mixed signal)
    runs, cur, length = [], "start", 0
    for m in tested:
        s = stage_at[m]
        if s == cur:
            length += 1
        else:
            if cur != "start":
                runs.append((cur, length))
            cur, length = s, 1
    finished = runs                      # the last, ongoing run is not counted
    durations, transitions = {}, {}
    for st in range(1, 7):
        lens = sorted(l for s, l in finished if s == st)
        if lens:
            mid = len(lens) // 2
            durations[str(st)] = {
                "avg": round(sum(lens) / len(lens), 1),
                "med": lens[mid] if len(lens) % 2 else round((lens[mid - 1] + lens[mid]) / 2, 1),
                "n": len(lens),
            }
        nxt = {}
        for i, (s, _) in enumerate(finished):
            if s != st:
                continue
            for s2, _ in finished[i + 1:] + [(cur, 0)]:
                if s2 is not None and s2 != st:
                    nxt[str(s2)] = nxt.get(str(s2), 0) + 1
                    break
        if nxt:
            transitions[str(st)] = nxt
    return {"forward": forward, "durations": durations, "transitions": transitions,
            "horizons": list(HORIZONS)}


def run(log=print):
    log("Backtest: downloading monthly history...")
    cache = {}

    def get(cands):
        key = tuple(cands)
        if key not in cache:
            cache[key] = first_available(cands, log)
        return cache[key]

    core = {k: get(c) for k, c in CORE.items()}
    if not all(sym for sym, _ in core.values()):
        raise RuntimeError("missing core history")
    spy = core["stocks"][1]
    sectors = {}
    for sym in SECTORS:
        _, data = get([sym])
        if data:
            sectors[sym] = data
    classes = {k: get(c) for k, c in ASSET_CLASSES.items()}
    classes["gold"] = gold_history(classes["gold"], log)
    cash_yield = classes["cash"][1]

    months = sorted(set.intersection(*(set(d) for _, d in core.values())))

    def ret(series, a, b):
        if a in series and b in series and series[a]:
            return series[b] / series[a] - 1
        return None

    results = {}
    for n in PERIODS:
        for rule in signals.RULES:
            stage_at, stock_dir = stage_series(core, months, n, rule)
            groups = ("all", "a", "b")
            sec_acc = {g: {s: {st: [] for st in range(1, 7)} for s in sectors} for g in groups}
            cls_acc = {c: {st: [] for st in range(1, 7)} for c in classes}
            counts = {st: 0 for st in range(1, 7)}
            mixed = 0
            tested = sorted(stage_at)
            for a, b in zip(tested, tested[1:]):
                st = stage_at[a]
                if st is None:
                    mixed += 1
                    continue
                counts[st] += 1
                half = "a" if a < SPLIT else "b"
                r_spy = ret(spy, a, b)
                for s, d in sectors.items():
                    r = ret(d, a, b)
                    if r is not None and r_spy is not None:
                        sec_acc["all"][s][st].append(r - r_spy)
                        sec_acc[half][s][st].append(r - r_spy)
                for c, (sym, d) in classes.items():
                    if not sym:
                        continue
                    r = (d[a] / 100 / 12 if a in d else None) if c == "cash" else ret(d, a, b)
                    if r is not None:
                        cls_acc[c][st].append(r)

            table = lambda acc: {s: {st: summarise(v) for st, v in by.items()} for s, by in acc.items()}  # noqa: E731
            results[signals.key(n, rule)] = {
                "start": tested[0] if tested else None,
                "end": tested[-1] if tested else None,
                "stages": [stage_at[m] or 0 for m in tested],   # one per month from start; 0 = mixed
                "counts": counts,
                "mixed": mixed,
                "sectors": table(sec_acc["all"]),
                "halves": {"a": table(sec_acc["a"]), "b": table(sec_acc["b"])},
                "classes": table(cls_acc),
                "strategy": simulate(stage_at, stock_dir, core, cash_yield),
                "after": after_stats(stage_at, {
                    "stocks": spy,
                    "bonds": core["bonds"][1],
                    "commodities": core["commodities"][1],
                    "gold": classes["gold"][1],
                }),
            }

    # S&P 500 index month-ends for the page's stage-colored price chart
    spx = None
    try:
        idx = fetch_monthly("^GSPC")
        first = months[0]
        spx_months = [m for m in months if m >= first]
        spx = {"start": first, "v": [round(idx[m], 2) if m in idx else None for m in spx_months]}
    except Exception as exc:
        log(f"  ^GSPC: {exc}")

    # History behind the page's deep-buy meter (12-month average, 2% band: the page default)
    deep = None
    try:
        import deepbuy
        stage_at, _ = stage_series(core, months, 12, "band")
        deep = deepbuy.study(stage_at, spy)
    except Exception as exc:
        log(f"  deep-buy study failed: {exc}")

    return {
        "built": datetime.now().strftime("%Y-%m-%d"),
        "deepbuy": deep,
        "spx": spx,
        "split": SPLIT,
        "cost": COST,
        "proxies": {k: sym for k, (sym, _) in core.items()},
        "classProxies": {k: sym for k, (sym, _) in classes.items()},
        "sectorStart": {s: min(d) for s, d in sectors.items()},
        "periods": results,
    }


if __name__ == "__main__":
    out = run()
    for k, r in out["periods"].items():
        s = r["strategy"]
        if not s:
            continue
        line = "  ".join(
            f"{name} {v['cagr']:+.1f}%/{v['mdd']:.0f}%dd/S{v['sharpe']}"
            for name, v in s["stats"].items() if v
        )
        print(f"{k:>8}  {s['start']}..{s['end']}  turnover {s['turnover']}%/yr  {line}")
