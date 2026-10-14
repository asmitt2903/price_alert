"""
Price Alert Tool
================
Reads daily closing prices from a CSV and alert rules from a JSON file.
Prints one line per fired alert, ordered by date then rule id.

Usage:
    python alerts.py prices.csv rules.json
"""

import sys
import csv
import json
import math
from collections import defaultdict
from datetime import date


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_prices(path: str) -> dict[str, list[tuple[date, float]]]:
    """Return {instrument: [(date, close), ...]} sorted ascending by date."""
    rows = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, start=2):          # line 2 = first data row
            try:
                d = date.fromisoformat(row["date"].strip())
                instrument = row["instrument"].strip()
                close = float(row["close"].strip())
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Bad row at line {i} in prices file: {exc}") from exc
            rows[instrument].append((d, close))

    # Sort each instrument's series by date
    return {inst: sorted(series) for inst, series in rows.items()}


def load_rules(path: str) -> list[dict]:
    """Return the list of rule dicts exactly as stored in the JSON file."""
    with open(path, encoding="utf-8") as fh:
        rules = json.load(fh)
    if not isinstance(rules, list):
        raise ValueError("Rules file must contain a JSON array.")

    required_keys = {"id", "instrument", "type", "direction"}
    for rule in rules:
        missing = required_keys - rule.keys()
        if missing:
            raise ValueError(f"Rule {rule!r} is missing keys: {missing}")
        if rule["type"] not in ("level", "ma_cross"):
            raise ValueError(f"Unknown rule type: {rule['type']!r}")
    return rules


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
                (curr_date, f"{curr_date}        {rule_id}    {instrument}     {label}")
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

    if short_w <= 0 or long_w <= 0:
        raise ValueError(f"Rule {rule_id!r}: window sizes must be positive integers.")
    if short_w >= long_w:
        raise ValueError(
            f"Rule {rule_id!r}: 'short' window ({short_w}) must be less than 'long' ({long_w})."
        )

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
                (curr_date, f"{curr_date}        {rule_id}    {instrument}     {label}")
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
            # No price data for this instrument — rule can never fire
            continue

        rule_type = rule["type"]
        if rule_type == "level":
            fired = evaluate_level(rule, series)
        elif rule_type == "ma_cross":
            fired = evaluate_ma_cross(rule, series)
        else:
            raise ValueError(f"Unknown rule type: {rule_type!r}")

        for alert_date, line in fired:
            all_alerts.append((alert_date, rule["id"], line))

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
