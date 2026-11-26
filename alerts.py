"""Stage-change alerts for the daily refresh.

Compares today's reading with the last one saved in alerts_state.json and reports:
  * the stage changed,
  * a core asset came within CLOSE_PCT of flipping its trend (which would start a new stage
    or a mixed signal),
  * the economic score moved to the other side of the cycle.

Uses the page's recommended reading: 12-month average with the 2% confirmation band.
On a Mac the alerts are shown as notifications; elsewhere they are only printed.
"""
import json
import subprocess
import sys
from pathlib import Path

KEY = "12:band"          # reading the alerts follow (matches the page default)
BAND = 2.0               # % band of the "band" rule
CLOSE_PCT = 1.0          # warn when a flip is this close
ASSETS = ("bonds", "stocks", "commodities")
STAGES = [(1, -1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1), (-1, -1, 1), (-1, -1, -1)]

TEXT = {
    "en": {
        "title": "Six-Stage",
        "names": ["Early contraction", "Trough", "Early recovery", "Expansion", "Late expansion", "Early downturn"],
        "assets": {"bonds": "Bonds", "stocks": "Stocks", "commodities": "Commodities"},
        "stage": lambda a, b, name: f"Stage changed: {a} → {b} ({name})",
        "mixed": "Signals are now mixed (no single stage)",
        "close": lambda asset, d, target: f"{asset} are {d:.2f}% from flipping, which would start {target}",
        "target_stage": lambda s: f"stage {s}",
        "target_mixed": "a mixed signal",
        "econ": lambda side: f"Economic score moved to the {side} side",
        "sides": {"exp": "expansion", "con": "contraction"},
        "test": "Alerts are working. You'll be notified when the stage changes.",
    },
    "fa": {
        "title": "شش‌مرحله",
        "names": ["ابتدای رکود", "کف رکود", "آغاز بهبود", "رونق", "اواخر رونق", "آغاز افت"],
        "assets": {"bonds": "اوراق قرضه", "stocks": "سهام", "commodities": "کالاها"},
        "stage": lambda a, b, name: f"مرحله عوض شد: {a} ← {b} ({name})",
        "mixed": "سیگنال‌ها مختلط شدند (هیچ مرحله مشخصی نیست)",
        "close": lambda asset, d, target: f"{asset} {d:.2f}٪ تا تغییر روند فاصله دارد؛ یعنی شروع {target}",
        "target_stage": lambda s: f"مرحله {s}",
        "target_mixed": "سیگنال مختلط",
        "econ": lambda side: f"امتیاز اقتصادی به سمت {side} رفت",
        "sides": {"exp": "رونق", "con": "رکود"},
        "test": "هشدارها کار می‌کنند. وقتی مرحله عوض شود خبر می‌دهیم.",
    },
}


def stage_of(dirs):
    for i, s in enumerate(STAGES):
        if s == tuple(dirs):
            return i + 1
    return None


def flip_distance(asset):
    """% move before the asset's trend flips under the 2% band rule."""
    gap = asset["gap"]
    return max(0.0, gap + BAND) if asset["dir"] > 0 else max(0.0, BAND - gap)


def econ_side(macro):
    if not macro:
        return None
    s = macro.get("score", 0)
    return "exp" if s >= 2 else "con" if s <= -2 else None


def check(data, state_path, lang="en"):
    """Return a list of alert messages and save the new state."""
    t = TEXT[lang]
    reading = data["periods"].get(KEY)
    if not reading:
        return []
    a = reading["assets"]
    dirs = [a[k]["dir"] for k in ASSETS]
    stage = reading["stage"]
    close = {}
    for i, k in enumerate(ASSETS):
        d = flip_distance(a[k])
        if d < CLOSE_PCT:
            flipped = list(dirs)
            flipped[i] = -flipped[i]
            close[k] = (round(d, 2), stage_of(flipped))
    side = econ_side(data.get("macro"))

    prev = json.loads(state_path.read_text()) if state_path.exists() else None
    state = {"stage": stage, "close": sorted(close), "econ": side, "asOf": data.get("asOf")}
    state_path.write_text(json.dumps(state))
    if prev is None:
        return []                     # first run: remember today, don't alert

    msgs = []
    if stage != prev.get("stage"):
        if stage:
            msgs.append(t["stage"](prev.get("stage") or "–", stage, t["names"][stage - 1]))
        else:
            msgs.append(t["mixed"])
    for k, (d, target) in close.items():
        if k not in prev.get("close", []):
            tgt = t["target_stage"](target) if target else t["target_mixed"]
            msgs.append(t["close"](t["assets"][k], d, tgt))
    if side and prev.get("econ") and side != prev["econ"]:
        msgs.append(t["econ"](t["sides"][side]))
    return msgs


def notify(msgs, lang="en"):
    """Show each message as a macOS notification; print it everywhere."""
    title = TEXT[lang]["title"]
    for m in msgs:
        print(f"ALERT: {m}")
        if sys.platform == "darwin":
            script = f'display notification {json.dumps(m)} with title {json.dumps(title)} sound name "Glass"'
            subprocess.run(["osascript", "-e", script], check=False)


if __name__ == "__main__":
    # python3 alerts.py --test [fa|en]  → send a test notification
    lang = sys.argv[2] if len(sys.argv) > 2 else "en"
    notify([TEXT[lang]["test"]], lang)
