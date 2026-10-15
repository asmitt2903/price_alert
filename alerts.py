"""
Price Alert Tool
================
Reads daily closing prices from a CSV and alert rules from a JSON file.
Prints one line per fired alert, ordered by date then rule id.
 
Dirty data policy: bad price rows and bad rules are skipped with a warning
on stderr, and processing continues. Only problems that make it impossible
to continue at all (missing file, unparseable JSON, rules file not an array,
price file without the required columns) are fatal.
 
Usage:
    python alerts.py prices.csv rules.json
"""
 
import sys
import csv
import json
import math
from collections import defaultdict
from datetime import date
 
 
def warn(msg: str) -> None:
    print(f"Warning: {msg}", file=sys.stderr)
 
 
# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
 
def load_prices(path: str) -> dict[str, list[tuple[date, float]]]:
    """Return {instrument: [(date, close), ...]} sorted ascending by date.
 
    Bad rows are skipped with a warning. Duplicate (instrument, date) rows
    keep the last occurrence, with a warning.
    """
    rows: dict[str, dict[date, float]] = defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        needed = {"date", "instrument", "close"}
        missing_cols = needed - set(reader.fieldnames or [])
        if missing_cols:
            raise ValueError(
                f"Prices file is missing required column(s): {sorted(missing_cols)}"
            )
 
        for i, row in enumerate(reader, start=2):          # line 2 = first data row
            try:
                d = date.fromisoformat(row["date"].strip())
                instrument = row["instrument"].strip()
                close = float(row["close"].strip())
                if not instrument:
                    raise ValueError("empty instrument")
                if not math.isfinite(close):
                    raise ValueError(f"non-finite close {close!r}")
            except (KeyError, ValueError, AttributeError) as exc:
                # AttributeError: a short row yields None for missing fields
                warn(f"skipping bad row at line {i} in prices file: {exc}")
                continue
 
            if d in rows[instrument]:
                warn(
                    f"duplicate row for {instrument} on {d} at line {i}; "
                    f"keeping the later value"
                )
            rows[instrument][d] = close
 
    return {inst: sorted(by_date.items()) for inst, by_date in rows.items()}
 
 
def _validate_rule(rule) -> None:
    """Raise ValueError if the rule is unusable."""
    if not isinstance(rule, dict):
        raise ValueError("rule is not a JSON object")
 
    required_keys = {"id", "instrument", "type", "direction"}
    missing = required_keys - rule.keys()
    if missing:
        raise ValueError(f"missing keys: {sorted(missing)}")
 
    if rule["type"] not in ("level", "ma_cross"):
        raise ValueError(f"unknown rule type: {rule['type']!r}")
    if rule["direction"] not in ("above", "below"):
        raise ValueError(f"unknown direction: {rule['direction']!r}")
 
    if rule["type"] == "level":
        if "level" not in rule:
            raise ValueError("missing key: 'level'")
        try:
            level = float(rule["level"])
        except (TypeError, ValueError):
            raise ValueError(f"level {rule['level']!r} is not a number")
        if not math.isfinite(level):
            raise ValueError(f"level {rule['level']!r} is not finite")
    else:
        for key in ("short", "long"):
            if key not in rule:
                raise ValueError(f"missing key: {key!r}")
        try:
            short_w, long_w = int(rule["short"]), int(rule["long"])
        except (TypeError, ValueError):
            raise ValueError("'short' and 'long' must be integers")
        if short_w <= 0 or long_w <= 0:
            raise ValueError("window sizes must be positive integers")
        if short_w >= long_w:
            raise ValueError(
                f"'short' window ({short_w}) must be less than 'long' ({long_w})"
            )
 
 
def load_rules(path: str) -> list[dict]:
    """Return the valid rule dicts from the JSON file; invalid ones are
    skipped with a warning."""
    with open(path, encoding="utf-8") as fh:
        rules = json.load(fh)
    if not isinstance(rules, list):
        raise ValueError("Rules file must contain a JSON array.")
 
    valid: list[dict] = []
    seen_ids: set = set()
    for idx, rule in enumerate(rules):
        try:
            _validate_rule(rule)
        except ValueError as exc:
            ident = rule.get("id", f"#{idx}") if isinstance(rule, dict) else f"#{idx}"
            warn(f"skipping rule {ident!r}: {exc}")
            continue
        if rule["id"] in seen_ids:
            warn(f"duplicate rule id {rule['id']!r}; keeping both")
        seen_ids.add(rule["id"])
        valid.append(rule)
    return valid
 
 
# ---------------------------------------------------------------------------
# Moving-average helper
# ---------------------------------------------------------------------------
 
def _sma(series: list[float], window: int) -> list[float | None]:
    """Return a list of SMAs the same length as *series*.
 
    Values are ``None`` until a full *window* of data is available.
    """
    out: list[float | None] = [None] * len(series)
    for i in range(window - 1, len(series)):
        out[i] = sum(series[i - window + 1 : i + 1]) / window
    return out
 
 
# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------
 
def evaluate_level(rule: dict, series: list[tuple[date, float]]) -> list[tuple[date, str]]:
    """Fire on the *first* day the close crosses *level* in *direction*.
 
    'below' → previous close was >= level, current close < level.
    'above' → previous close was <= level, current close > level.
    """
    level = float(rule["level"])
    direction = rule["direction"]
    rule_id = rule["id"]
    instrument = rule["instrument"]
    alerts: list[tuple[date, str]] = []
 
    for i in range(1, len(series)):
        prev_close = series[i - 1][1]
        curr_date, curr_close = series[i]
 
        if direction == "below":
            fired = prev_close >= level and curr_close < level
        elif direction == "above":
            fired = prev_close <= level and curr_close > level
        else:
            raise ValueError(f"Unknown direction {direction!r} for rule {rule_id!r}")
 
        if fired:
            label = f"level {direction} {level:g}"
            alerts.append(
                (curr_date, f"{curr_date}   {rule_id:<4} {instrument:<6} {label}")
            )
            break   # level rule fires only on the *first* crossing
 
    return alerts
 
 
def evaluate_ma_cross(rule: dict, series: list[tuple[date, float]]) -> list[tuple[date, str]]:
    """Fire on each day the short MA crosses the long MA in *direction*.
 
    'above' → short crossed from below to above long.
    'below' → short crossed from above to below long.
 
    Moving averages are undefined until a full window of data is available.
    """
    short_w = int(rule["short"])
    long_w = int(rule["long"])
    direction = rule["direction"]
    rule_id = rule["id"]
    instrument = rule["instrument"]
 
    closes = [c for _, c in series]
    short_ma = _sma(closes, short_w)
    long_ma = _sma(closes, long_w)
 
    alerts: list[tuple[date, str]] = []
 
    for i in range(1, len(series)):
        # Both current and previous SMAs must be defined
        if any(v is None for v in (short_ma[i], long_ma[i], short_ma[i - 1], long_ma[i - 1])):
            continue
 
        s_curr, l_curr = short_ma[i], long_ma[i]        # type: ignore[assignment]
        s_prev, l_prev = short_ma[i - 1], long_ma[i - 1]  # type: ignore[assignment]
        curr_date = series[i][0]
 
        if direction == "above":
            fired = s_prev <= l_prev and s_curr > l_curr
        elif direction == "below":
            fired = s_prev >= l_prev and s_curr < l_curr
        else:
            raise ValueError(f"Unknown direction {direction!r} for rule {rule_id!r}")
 
        if fired:
            label = f"ma_cross {short_w}/{long_w} {direction}"
            alerts.append(
                (curr_date, f"{curr_date}   {rule_id:<4} {instrument:<6} {label}")
            )
 
    return alerts
 
 
# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
 
def run(prices_path: str, rules_path: str) -> list[str]:
    """Return the ordered list of alert output lines (without trailing newline)."""
    prices = load_prices(prices_path)
    rules = load_rules(rules_path)
 
    all_alerts: list[tuple[date, str, str]] = []   # (date, rule_id, line)
 
    for rule in rules:
        instrument = rule["instrument"]
        series = prices.get(instrument)
        if not series:
            warn(f"rule {rule['id']!r}: no valid price data for {instrument!r}; skipped")
            continue
 
        try:
            if rule["type"] == "level":
                fired = evaluate_level(rule, series)
            else:
                fired = evaluate_ma_cross(rule, series)
        except (ValueError, KeyError, TypeError) as exc:
            warn(f"rule {rule['id']!r} failed during evaluation: {exc}; skipped")
            continue
 
        for alert_date, line in fired:
            all_alerts.append((alert_date, str(rule["id"]), line))
 
    # Sort by date, then rule id
    all_alerts.sort(key=lambda x: (x[0], x[1]))
    return [line for _, _, line in all_alerts]
 
 
def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python alerts.py prices.csv rules.json", file=sys.stderr)
        sys.exit(1)
 
    prices_path, rules_path = sys.argv[1], sys.argv[2]
    try:
        lines = run(prices_path, rules_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
 
    for line in lines:
        print(line)
 
 
if __name__ == "__main__":
    main()
