import json
import tempfile
import unittest
from pathlib import Path

from tg_downloader.ui_performance import UiPerformanceRecorder, comparison_rows, summarize


class FakeClock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value


class UiPerformanceTests(unittest.TestCase):
    def test_summary_reports_interpolated_percentiles(self) -> None:
        result = summarize([1.0, 2.0, 3.0, 100.0])
        self.assertEqual(4, result["count"])
        self.assertEqual(106.0, result["sum"])
        self.assertEqual(2.5, result["p50"])
        self.assertEqual(100.0, result["max"])

    def test_recorder_measures_heartbeat_delay_and_writes_report(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            recorder = UiPerformanceRecorder(path, "baseline", clock)
            clock.value += 0.075
            recorder.record_heartbeat()
            clock.value += 0.2
            recorder.record_heartbeat()
            recorder.record_poll(total_ms=12, events_ms=2, events_handled=4, queue_before=5)
            clock.value = 70.0
            recorder.write_report()
            report = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual("baseline", report["label"])
        self.assertAlmostEqual(150.0, report["event_loop_lag_ms"]["max"])
        self.assertEqual(1.0, report["normalized"]["stall_rates_per_minute"]["50"])
        self.assertAlmostEqual(0.292, report["normalized"]["estimated_ui_delayed_percent"])
        self.assertEqual(12.0, report["poll"]["total_ms"]["max"])
        self.assertEqual(5.0, report["poll"]["queue_before"]["max"])

    def test_comparison_rows_include_before_and_after_values(self) -> None:
        def report(value: float, duration: float = 60) -> dict:
            summary = {"count": 10, "mean": value, "p95": value, "p99": value, "max": value}
            poll = {name: summary for name in (
                "total_ms", "events_ms", "cards_ms", "positions_ms", "panel_ms",
                "queue_before",
            )}
            return {
                "duration_seconds": duration,
                "event_loop_lag_ms": summary,
                "stall_counts": {"50": value, "250": value, "1000": value},
                "poll": poll,
            }

        rows = comparison_rows(report(10), report(4))
        self.assertEqual(("Duration (minutes)", 1.0, 1.0), rows[0])
        self.assertIn(("Stalls >= 50 ms / minute", 10.0, 4.0), rows)

    def test_stall_rates_are_comparable_across_different_run_lengths(self) -> None:
        def report(duration: float, stalls: int) -> dict:
            summary = {"count": 1, "mean": 0, "p95": 0, "p99": 0, "max": 0}
            return {
                "duration_seconds": duration,
                "event_loop_lag_ms": summary,
                "stall_counts": {"50": stalls, "250": 0, "1000": 0},
                "poll": {name: summary for name in (
                    "total_ms", "events_ms", "cards_ms", "positions_ms", "panel_ms",
                    "queue_before",
                )},
            }

        rows = comparison_rows(report(60, 10), report(120, 20))
        rates = next(row for row in rows if row[0] == "Stalls >= 50 ms / minute")
        self.assertEqual(("Stalls >= 50 ms / minute", 10.0, 10.0), rates)


if __name__ == "__main__":
    unittest.main()
