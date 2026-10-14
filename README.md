# Price Alert Tool

## How to Run

**Requirements:** Python 3.9+. No third-party libraries are needed to run the tool.
Tests need `pytest` (install once with `pip install pytest`).

```bash
# Run the tool
python alerts.py prices.csv rules.json

# Run the tests
python -m pytest test_alerts.py -v
```

**Output** – one line per fired alert, ordered by date then rule id:

```
2026-09-03        r1    INFY     level below 1500
2026-09-24        r2    TCS      ma_cross 5/20 above
```

---

## Files

| File | Purpose |
|---|---|
| `alerts.py` | Main tool – load prices/rules, evaluate, print output |
| `test_alerts.py` | 35 pytest tests covering unit + integration cases |
| `prices.csv` | Sample prices (two instruments, ~5 weeks) |
| `rules.json` | Two sample rules matching the brief exactly |

---

## How I Used the AI Assistant

**I used Antigravity (Google Deepmind's coding assistant) with the following prompts:**

1. *Initial prompt:* Provided the full brief verbatim and asked it to build the tool, tests, sample data, and a README.

2. *Verification prompt (price data):* I noticed the sample `prices.csv` initially had only 19 TCS rows, which is fewer than the `long=20` window. I asked it to verify the MA-cross computation by printing intermediate SMA values, then extend the data to cover the full warm-up period.

---

## What the Assistant Got Wrong

**The assistant initially generated only 19 rows of TCS data** — one fewer than the `long=20` SMA window. This meant the 20-day moving average was never defined and the `ma_cross` rule could never fire on the sample data. The tool silently produced no MA-cross alert.

**How I caught it:** I ran a diagnostic script that printed the SMA columns row by row and saw every entry in the SMA20 column was `--` (undefined). I then asked the assistant to extend the price history.

**What I changed:** Added 20 extra TCS rows in August 2026 so the long MA becomes defined by 2026-09-01, giving enough history for the crossover to occur and be detected.

---

## Design Decisions

- **Level rule fires once** – the brief says "first day the close crosses". The code breaks after the first crossing.
- **MA-cross fires on every crossing** – the brief says "the day the short moving average crosses"; it doesn't restrict to the first, so each crossing is reported (consistent with how traders use the signal).
- **Strict boundary conditions** – close *exactly* at the level is not a crossing (uses strict `<` / `>`), matching the mathematical definition of "crosses below/above".
- **Dates may be out of order** – prices are sorted by date per instrument before evaluation, as the brief warns.
- **No external dependencies** – only stdlib (`csv`, `json`, `datetime`, `collections`) so the tool runs from a clean download with no `pip install`.
