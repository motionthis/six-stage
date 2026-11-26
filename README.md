# Six-Stage Business Cycle

A live reading of Martin Pring's six-stage business cycle for the US market, in Persian and English.

- **Stage** from bond, stock and commodity prices (IEF, SPY, DBC) against their 3, 6, 12 or 18-month moving averages, with optional confirmation rules (2% band, or hold for a month).
- **Economic check** from four FRED indicators: yield curve, unemployment (Sahm rule), credit spread, industrial production.
- **Asset table** for 24 ETFs (sectors, bonds, metals, commodities, dollar) — edit `watchlist.json`.
- **Backtest** since the 1990s: sector and asset-class returns by stage, a strategy test against simple benchmarks, and a stability check across 1993–2009 and 2010–today.

Not investment advice.

## Files

| File | What it does |
|---|---|
| `cycle_live.py` | Fetches prices, builds the page. `--out site/index.html` sets where it goes. |
| `signals.py` | Trend rules shared by the page and the backtest. |
| `backtest.py` | Monthly backtest; rebuilt weekly (or with `--backtest`). |
| `macro.py` | FRED economic indicators. |
| `template.html` | The page design. |
| `watchlist.json` | Assets in the asset table. |
| `web/` | App icon, manifest and offline support for installing on a phone. |
| `last_data.json`, `backtest.json`, `macro.json` | Last good data, used if a source is down. |

Standard library Python 3.9+ only; no packages to install.

## Run locally

```bash
python3 cycle_live.py
```

## Online (GitHub Pages)

`.github/workflows/update.yml` rebuilds the page every weekday at 21:30 UTC (after the US close) and publishes it with GitHub Pages. In the repository's **Settings → Pages**, set **Source** to **GitHub Actions**. To refresh by hand: **Actions → Update page → Run workflow**.

## Install as an app

Open the site on a phone:
- **iPhone (Safari):** Share → **Add to Home Screen**.
- **Android (Chrome):** menu → **Install app** (or **Add to Home screen**).
