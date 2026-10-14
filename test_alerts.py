"""
Tests for alerts.py
===================
Run with:  python -m pytest test_alerts.py -v
"""

import json
import os
import tempfile
import textwrap
from datetime import date

import pytest

# Import the module under test
import alerts as A


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_prices_csv(rows: list[tuple[str, str, str | float]]) -> str:
    """Write a temporary CSV and return its path."""
    content = "date,instrument,close\n"
    for d, inst, close in rows:
        content += f"{d},{inst},{close}\n"
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.write(fd, content.encode())
    os.close(fd)
    return path


def make_rules_json(rules: list[dict]) -> str:
    """Write a temporary JSON rules file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.write(fd, json.dumps(rules).encode())
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# load_prices
# ---------------------------------------------------------------------------

class TestLoadPrices:
    def test_basic_load(self):
        path = make_prices_csv([
            ("2026-09-01", "INFY", 1520.50),
            ("2026-09-02", "INFY", 1534.10),
        ])
        data = A.load_prices(path)
        assert "INFY" in data
        assert data["INFY"] == [(date(2026, 9, 1), 1520.50), (date(2026, 9, 2), 1534.10)]

    def test_out_of_order_dates_sorted(self):
        """Prices out of order in the file must be sorted ascending."""
        path = make_prices_csv([
            ("2026-09-03", "TCS", 3920.0),
            ("2026-09-01", "TCS", 3890.0),
            ("2026-09-02", "TCS", 3902.25),
        ])
        data = A.load_prices(path)
        dates = [d for d, _ in data["TCS"]]
        assert dates == sorted(dates)

    def test_multiple_instruments(self):
        path = make_prices_csv([
            ("2026-09-01", "INFY", 1520.0),
            ("2026-09-01", "TCS", 3890.0),
        ])
        data = A.load_prices(path)
        assert set(data.keys()) == {"INFY", "TCS"}

    def test_bad_close_raises(self):
        path = make_prices_csv([("2026-09-01", "INFY", "not_a_number")])
        with pytest.raises(ValueError, match="Bad row"):
            A.load_prices(path)

    def test_missing_column_raises(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.write(fd, b"date,instrument\n2026-09-01,INFY\n")
        os.close(fd)
        with pytest.raises((ValueError, KeyError)):
            A.load_prices(path)

    def test_whitespace_stripped(self):
        """Extra whitespace in cells must be stripped."""
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.write(fd, b"date,instrument,close\n 2026-09-01 , INFY , 1500.0 \n")
        os.close(fd)
        data = A.load_prices(path)
        assert "INFY" in data
        assert data["INFY"][0] == (date(2026, 9, 1), 1500.0)


# ---------------------------------------------------------------------------
# _sma helper
# ---------------------------------------------------------------------------

class TestSMA:
    def test_window_1(self):
        result = A._sma([1.0, 2.0, 3.0], 1)
        assert result == [1.0, 2.0, 3.0]

    def test_window_3(self):
        result = A._sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
        assert result[:2] == [None, None]
        assert result[2] == pytest.approx(2.0)
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(4.0)

    def test_window_equal_length(self):
        result = A._sma([10.0, 20.0, 30.0], 3)
        assert result[:2] == [None, None]
        assert result[2] == pytest.approx(20.0)

    def test_window_larger_than_series(self):
        result = A._sma([1.0, 2.0], 5)
        assert all(v is None for v in result)

    def test_empty_series(self):
        assert A._sma([], 3) == []


# ---------------------------------------------------------------------------
# Level rule
# ---------------------------------------------------------------------------

class TestLevelRule:
    def _series(self, closes: list[float]) -> list[tuple[date, float]]:
        base = date(2026, 9, 1)
        from datetime import timedelta
        return [(base + timedelta(days=i), c) for i, c in enumerate(closes)]

    def _rule(self, direction: str, level: float) -> dict:
        return {"id": "r1", "instrument": "INFY", "type": "level",
                "direction": direction, "level": level}

    def test_fires_below_on_crossing(self):
        series = self._series([1510, 1510, 1490])   # crosses below 1500 on day 3
        alerts = A.evaluate_level(self._rule("below", 1500), series)
        assert len(alerts) == 1
        assert alerts[0][0] == date(2026, 9, 3)

    def test_fires_above_on_crossing(self):
        series = self._series([1490, 1490, 1510])
        alerts = A.evaluate_level(self._rule("above", 1500), series)
        assert len(alerts) == 1
        assert alerts[0][0] == date(2026, 9, 3)

    def test_no_fire_if_already_below_on_day_one(self):
        """Starts already below level — no previous day to compare against index 0."""
        series = self._series([1490, 1480])
        alerts = A.evaluate_level(self._rule("below", 1500), series)
        assert alerts == []

    def test_fires_only_once(self):
        """Level rule fires only on the first crossing, not subsequent ones."""
        series = self._series([1510, 1490, 1510, 1490])
        alerts = A.evaluate_level(self._rule("below", 1500), series)
        assert len(alerts) == 1

    def test_no_crossing_no_alert(self):
        series = self._series([1510, 1520, 1530])
        alerts = A.evaluate_level(self._rule("below", 1500), series)
        assert alerts == []

    def test_exact_level_not_below(self):
        """Close exactly AT the level does not count as crossing below."""
        series = self._series([1510, 1500])
        alerts = A.evaluate_level(self._rule("below", 1500), series)
        assert alerts == []

    def test_exact_level_not_above(self):
        """Close exactly AT the level does not count as crossing above."""
        series = self._series([1490, 1500])
        alerts = A.evaluate_level(self._rule("above", 1500), series)
        assert alerts == []

    def test_single_row_no_alert(self):
        series = self._series([1490])
        alerts = A.evaluate_level(self._rule("below", 1500), series)
        assert alerts == []


# ---------------------------------------------------------------------------
# MA-cross rule
# ---------------------------------------------------------------------------

class TestMACrossRule:
    def _rule(self, direction: str, short: int = 3, long: int = 5) -> dict:
        return {"id": "r2", "instrument": "TCS", "type": "ma_cross",
                "direction": direction, "short": short, "long": long}

    def _series(self, closes: list[float]) -> list[tuple[date, float]]:
        base = date(2026, 9, 1)
        from datetime import timedelta
        return [(base + timedelta(days=i), c) for i, c in enumerate(closes)]

    def test_cross_above_fires(self):
        # Short (3) crosses above long (5) → upward bullish cross
        closes = [10, 10, 10, 10, 10, 20, 30, 40]
        series = self._series(closes)
        alerts = A.evaluate_ma_cross(self._rule("above"), series)
        assert len(alerts) >= 1

    def test_insufficient_data_no_alert(self):
        """Fewer rows than the long window → no MAs defined → no alert."""
        closes = [100, 101, 102, 103]   # long window = 5, only 4 rows
        series = self._series(closes)
        alerts = A.evaluate_ma_cross(self._rule("above", 3, 5), series)
        assert alerts == []

    def test_short_ge_long_raises(self):
        with pytest.raises(ValueError, match="short.*less than.*long|long.*short"):
            rule = self._rule("above", short=5, long=5)
            A.evaluate_ma_cross(rule, self._series([1, 2, 3, 4, 5, 6]))

    def test_no_cross_no_alert(self):
        """Monotonically increasing series: short always above long after warmup → no cross."""
        closes = [i * 10.0 for i in range(1, 30)]
        series = self._series(closes)
        alerts = A.evaluate_ma_cross(self._rule("below"), series)  # looking for downward cross
        assert alerts == []

    def test_cross_below_fires(self):
        # Price drops sharply → short MA drops below long MA
        closes = [50, 50, 50, 50, 50, 10, 10, 10]
        series = self._series(closes)
        alerts = A.evaluate_ma_cross(self._rule("below"), series)
        assert len(alerts) >= 1

    def test_window_exactly_met(self):
        """Only 'long' rows available: one crossing point at most can exist."""
        closes = [10] * 4 + [20]   # long=5, short=3; 5 rows total
        series = self._series(closes)
        # With 5 rows, long MA is defined only at index 4.
        # Need index i and i-1 both defined for cross detection → need >=6 rows.
        alerts = A.evaluate_ma_cross(self._rule("above", 3, 5), series)
        assert alerts == []


# ---------------------------------------------------------------------------
# Integration tests (full run)
# ---------------------------------------------------------------------------

class TestIntegration:
    def _run(self, csv_rows, rules):
        prices_path = make_prices_csv(csv_rows)
        rules_path = make_rules_json(rules)
        return A.run(prices_path, rules_path)

    def test_brief_example_level(self):
        """The sample from the brief: INFY crosses below 1500 on 2026-09-03."""
        rows = [
            ("2026-09-01", "INFY", 1520.50),
            ("2026-09-02", "INFY", 1534.10),
            ("2026-09-03", "INFY", 1498.00),
        ]
        rule = {"id": "r1", "instrument": "INFY", "type": "level",
                "direction": "below", "level": 1500}
        lines = self._run(rows, [rule])
        assert len(lines) == 1
        assert "2026-09-03" in lines[0]
        assert "r1" in lines[0]
        assert "INFY" in lines[0]
        assert "level" in lines[0]
        assert "below" in lines[0]

    def test_output_ordered_by_date_then_rule_id(self):
        """Alerts from the same day must be sorted by rule id."""
        rows = [
            ("2026-09-01", "INFY", 1510.0),
            ("2026-09-01", "TCS", 3900.0),
            ("2026-09-02", "INFY", 1490.0),  # r1 fires
            ("2026-09-02", "TCS", 3800.0),   # r2 fires (below 3850)
        ]
        rules = [
            {"id": "r1", "instrument": "INFY", "type": "level",
             "direction": "below", "level": 1500},
            {"id": "r2", "instrument": "TCS", "type": "level",
             "direction": "below", "level": 3850},
        ]
        lines = self._run(rows, rules)
        assert len(lines) == 2
        assert "r1" in lines[0]
        assert "r2" in lines[1]

    def test_unknown_instrument_in_rules_silently_skipped(self):
        """A rule for an instrument with no prices produces no alert and no crash."""
        rows = [("2026-09-01", "INFY", 1490.0)]
        rules = [{"id": "r1", "instrument": "UNKNOWN", "type": "level",
                  "direction": "below", "level": 1000}]
        lines = self._run(rows, rules)
        assert lines == []

    def test_empty_prices_no_alerts(self):
        """An empty prices file (header only) → no alerts."""
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.write(fd, b"date,instrument,close\n")
        os.close(fd)
        rules_path = make_rules_json([
            {"id": "r1", "instrument": "INFY", "type": "level",
             "direction": "below", "level": 1500}
        ])
        lines = A.run(path, rules_path)
        assert lines == []

    def test_empty_rules_no_alerts(self):
        rows = [("2026-09-01", "INFY", 1490.0)]
        lines = self._run(rows, [])
        assert lines == []

    def test_out_of_order_prices_correct_alert(self):
        """CSV rows out of order must produce the same result as sorted input."""
        rows_sorted = [
            ("2026-09-01", "INFY", 1520.0),
            ("2026-09-02", "INFY", 1534.0),
            ("2026-09-03", "INFY", 1495.0),
        ]
        rows_shuffled = [rows_sorted[2], rows_sorted[0], rows_sorted[1]]
        rule = [{"id": "r1", "instrument": "INFY", "type": "level",
                 "direction": "below", "level": 1500}]
        assert self._run(rows_sorted, rule) == self._run(rows_shuffled, rule)

    def test_ma_cross_needs_enough_data(self):
        """MA-cross rule with short=5 long=20 needs at least 21 rows to fire."""
        rows = [("2026-09-{:02d}".format(i), "TCS", 3890.0 + i)
                for i in range(1, 15)]   # only 14 rows
        rules = [{"id": "r2", "instrument": "TCS", "type": "ma_cross",
                  "direction": "above", "short": 5, "long": 20}]
        lines = self._run(rows, rules)
        assert lines == []

    def test_level_rule_direction_above(self):
        """Price crosses above a level → alert fires."""
        rows = [
            ("2026-09-01", "INFY", 1480.0),
            ("2026-09-02", "INFY", 1490.0),
            ("2026-09-03", "INFY", 1510.0),  # crosses above 1500
        ]
        rule = [{"id": "r1", "instrument": "INFY", "type": "level",
                 "direction": "above", "level": 1500}]
        lines = self._run(rows, rule)
        assert len(lines) == 1
        assert "2026-09-03" in lines[0]

    def test_bad_rules_file_raises(self):
        rows = [("2026-09-01", "INFY", 1490.0)]
        prices_path = make_prices_csv(rows)
        fd, rules_path = tempfile.mkstemp(suffix=".json")
        os.write(fd, b'[{"id":"r1","instrument":"INFY","type":"UNKNOWN","direction":"below"}]')
        os.close(fd)
        with pytest.raises(ValueError, match="Unknown rule type"):
            A.run(prices_path, rules_path)

    def test_multiple_rules_multiple_instruments(self):
        """Two rules on two different instruments both firing."""
        rows = [
            ("2026-09-01", "INFY", 1510.0),
            ("2026-09-02", "INFY", 1490.0),  # r1: below 1500
            ("2026-09-01", "TCS", 3910.0),
            ("2026-09-02", "TCS", 3880.0),   # r2: below 3900
        ]
        rules = [
            {"id": "r1", "instrument": "INFY", "type": "level",
             "direction": "below", "level": 1500},
            {"id": "r2", "instrument": "TCS", "type": "level",
             "direction": "below", "level": 3900},
        ]
        lines = self._run(rows, rules)
        assert len(lines) == 2
