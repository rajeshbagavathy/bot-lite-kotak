"""Unit tests for calm-zone sliding-window math."""
import unittest

from calm_zone_math import compute_calm_metrics, range_max_threshold


def _bar(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}


class TestCalmZoneMath(unittest.TestCase):
    def test_range_thresholds(self) -> None:
        self.assertEqual(range_max_threshold("NIFTY"), 50.0)
        self.assertEqual(range_max_threshold("SENSEX"), 120.0)

    def test_nifty_calm_flat_range_low_ratio(self) -> None:
        # Five flat-ish bars: range small, body small vs range
        bars = [
            _bar(100.0, 101.0, 99.5, 100.2),
            _bar(100.2, 101.0, 99.8, 100.1),
            _bar(100.1, 101.2, 99.9, 100.0),
            _bar(100.0, 101.0, 99.7, 100.1),
            _bar(100.1, 101.0, 99.6, 100.05),
        ]
        m = compute_calm_metrics(bars, "NIFTY")
        assert m is not None
        self.assertLess(m["range_5m"], 50.0)
        self.assertLess(m["body_range_ratio"], 0.25)
        self.assertTrue(m["is_calmzone"])

    def test_nifty_not_calm_high_range(self) -> None:
        # max(high)=150, min(low)=95 → range 55 ≥ 50 (not calm on range alone)
        bars = [
            _bar(100.0, 120.0, 95.0, 100.0),
            _bar(100.0, 130.0, 96.0, 100.0),
            _bar(100.0, 140.0, 97.0, 100.0),
            _bar(100.0, 145.0, 98.0, 100.0),
            _bar(100.0, 150.0, 99.0, 100.0),
        ]
        m = compute_calm_metrics(bars, "NIFTY")
        assert m is not None
        self.assertGreaterEqual(m["range_5m"], 50.0)
        self.assertFalse(m["is_calmzone"])

    def test_sensex_threshold_120(self) -> None:
        # Range 80, ratio low -> calm for SENSEX only if range < 120 and ratio < 0.25
        bars = [
            _bar(25000.0, 25040.0, 24960.0, 25010.0),
            _bar(25010.0, 25050.0, 24970.0, 25005.0),
            _bar(25005.0, 25045.0, 24965.0, 25000.0),
            _bar(25000.0, 25042.0, 24968.0, 25008.0),
            _bar(25008.0, 25038.0, 24972.0, 25002.0),
        ]
        m = compute_calm_metrics(bars, "SENSEX")
        assert m is not None
        self.assertLess(m["range_5m"], 120.0)
        self.assertTrue(m["is_calmzone"])

    def test_range_zero(self) -> None:
        bars = [
            _bar(100.0, 100.0, 100.0, 100.0),
            _bar(100.0, 100.0, 100.0, 100.0),
            _bar(100.0, 100.0, 100.0, 100.0),
            _bar(100.0, 100.0, 100.0, 100.0),
            _bar(100.0, 100.0, 100.0, 100.0),
        ]
        m = compute_calm_metrics(bars, "NIFTY")
        assert m is not None
        self.assertEqual(m["range_5m"], 0.0)
        self.assertIsNone(m["body_range_ratio"])
        self.assertFalse(m["is_calmzone"])

    def test_wrong_window_length(self) -> None:
        self.assertIsNone(compute_calm_metrics([], "NIFTY"))
        self.assertIsNone(compute_calm_metrics([_bar(1, 2, 1, 2)], "NIFTY"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
