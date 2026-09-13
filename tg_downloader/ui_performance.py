from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


HEARTBEAT_INTERVAL_MS = 50
STALL_THRESHOLDS_MS = (50, 100, 250, 500, 1000)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def summarize(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 3) if values else 0.0,
        "p50": round(_percentile(values, 50), 3),
        "p95": round(_percentile(values, 95), 3),
        "p99": round(_percentile(values, 99), 3),
        "max": round(max(values), 3) if values else 0.0,
    }


class UiPerformanceRecorder:
    """Collect low-overhead Tk responsiveness measurements for one app session."""

    def __init__(
        self, report_path: Path, label: str = "", clock: Callable[[], float] = time.perf_counter,
    ):
        self.report_path = report_path
        self.label = label or report_path.stem
        self.clock = clock
        self.started_at = datetime.now(timezone.utc)
        self.started_clock = clock()
        self.next_heartbeat_due = self.started_clock + HEARTBEAT_INTERVAL_MS / 1000
        self.heartbeat_lag_ms: list[float] = []
        self.poll_metrics: dict[str, list[float]] = {
            "total_ms": [], "events_ms": [], "cards_ms": [], "positions_ms": [],
            "panel_ms": [], "events_handled": [], "queue_before": [], "queue_after": [],
        }

    def record_heartbeat(self) -> None:
        now = self.clock()
        self.heartbeat_lag_ms.append(max(0.0, (now - self.next_heartbeat_due) * 1000))
        self.next_heartbeat_due = now + HEARTBEAT_INTERVAL_MS / 1000

    def record_poll(self, **metrics: float | int) -> None:
        for name, value in metrics.items():
            if name in self.poll_metrics:
                self.poll_metrics[name].append(float(value))

    def build_report(self) -> dict[str, Any]:
        return {
            "format_version": 1,
            "label": self.label,
            "started_at_utc": self.started_at.isoformat(),
            "duration_seconds": round(max(0.0, self.clock() - self.started_clock), 3),
            "heartbeat_interval_ms": HEARTBEAT_INTERVAL_MS,
            "event_loop_lag_ms": summarize(self.heartbeat_lag_ms),
            "stall_counts": {
                str(threshold): sum(lag >= threshold for lag in self.heartbeat_lag_ms)
                for threshold in STALL_THRESHOLDS_MS
            },
            "poll": {name: summarize(values) for name, values in self.poll_metrics.items()},
        }

    def write_report(self) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(
            json.dumps(self.build_report(), indent=2) + "\n", encoding="utf-8",
        )


def _value(report: dict[str, Any], path: tuple[str, ...]) -> float:
    value: Any = report
    for component in path:
        value = value[component]
    return float(value)


def comparison_rows(before: dict[str, Any], after: dict[str, Any]) -> list[tuple[str, float, float]]:
    metrics = (
        ("Event-loop lag p95 (ms)", ("event_loop_lag_ms", "p95")),
        ("Event-loop lag p99 (ms)", ("event_loop_lag_ms", "p99")),
        ("Worst event-loop lag (ms)", ("event_loop_lag_ms", "max")),
        ("Stalls >= 250 ms", ("stall_counts", "250")),
        ("Stalls >= 1000 ms", ("stall_counts", "1000")),
        ("Poll total p95 (ms)", ("poll", "total_ms", "p95")),
        ("Poll events p95 (ms)", ("poll", "events_ms", "p95")),
        ("Poll cards p95 (ms)", ("poll", "cards_ms", "p95")),
        ("Poll positions p95 (ms)", ("poll", "positions_ms", "p95")),
        ("Poll panel p95 (ms)", ("poll", "panel_ms", "p95")),
        ("Peak queued UI events", ("poll", "queue_before", "max")),
    )
    return [(label, _value(before, path), _value(after, path)) for label, path in metrics]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two UI performance reports")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    print(f"{'Metric':32} {'Before':>12} {'After':>12} {'Change':>12}")
    print("-" * 71)
    for label, old, new in comparison_rows(before, after):
        print(f"{label:32} {old:12.1f} {new:12.1f} {new - old:+12.1f}")


if __name__ == "__main__":
    main()
