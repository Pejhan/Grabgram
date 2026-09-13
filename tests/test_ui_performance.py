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
        self.assertEqual(2.5, result["p50"])
        self.assertEqual(100.0, result["max"])

    def test_recorder_measures_heartbeat_delay_and_writes_report(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            recorder = UiPerformanceRecorder(path, "baseline", clock)
            clock.value += 0.075
            recorder.record_heartbeat()
            recorder.record_poll(total_ms=12, events_ms=2, events_handled=4, queue_before=5)
            recorder.write_report()
            report = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual("baseline", report["label"])
        self.assertAlmostEqual(25.0, report["event_loop_lag_ms"]["max"])
        self.assertEqual(12.0, report["poll"]["total_ms"]["max"])
        self.assertEqual(5.0, report["poll"]["queue_before"]["max"])

    def test_comparison_rows_include_before_and_after_values(self) -> None:
        def report(value: float) -> dict:
            summary = {"p95": value, "p99": value, "max": value}
            poll = {name: summary for name in (
                "total_ms", "events_ms", "cards_ms", "positions_ms", "panel_ms",
                "queue_before",
            )}
            return {
                "event_loop_lag_ms": summary,
                "stall_counts": {"250": value, "1000": value},
                "poll": poll,
            }

        rows = comparison_rows(report(10), report(4))
        self.assertEqual(("Event-loop lag p95 (ms)", 10.0, 4.0), rows[0])


if __name__ == "__main__":
    unittest.main()
