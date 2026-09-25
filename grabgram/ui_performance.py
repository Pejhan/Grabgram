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
RECOMMENDED_DURATION_SECONDS = 300


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
        "sum": round(sum(values), 3),
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
        duration = max(0.0, self.clock() - self.started_clock)
        observed_minutes = duration / 60
        stall_counts = {
            str(threshold): sum(lag >= threshold for lag in self.heartbeat_lag_ms)
            for threshold in STALL_THRESHOLDS_MS
        }
        total_lag_ms = sum(self.heartbeat_lag_ms)
        return {
            "format_version": 2,
            "label": self.label,
            "started_at_utc": self.started_at.isoformat(),
            "duration_seconds": round(duration, 3),
            "heartbeat_interval_ms": HEARTBEAT_INTERVAL_MS,
            "event_loop_lag_ms": summarize(self.heartbeat_lag_ms),
            "stall_lag_ms": summarize([
                lag for lag in self.heartbeat_lag_ms if lag >= STALL_THRESHOLDS_MS[0]
            ]),
            "stall_counts": stall_counts,
            "normalized": {
                "observed_minutes": round(observed_minutes, 3),
                "stall_rates_per_minute": {
                    threshold: round(count / observed_minutes, 3) if observed_minutes else 0.0
                    for threshold, count in stall_counts.items()
                },
                "estimated_ui_delayed_seconds": round(total_lag_ms / 1000, 3),
                "estimated_ui_delayed_percent": (
                    round(min(100.0, total_lag_ms / (duration * 10)), 3) if duration else 0.0
                ),
                "heartbeat_samples_per_second": (
                    round(len(self.heartbeat_lag_ms) / duration, 3) if duration else 0.0
                ),
            },
            "measurement_quality": {
                "recommended_duration_seconds": RECOMMENDED_DURATION_SECONDS,
                "duration_is_representative": duration >= RECOMMENDED_DURATION_SECONDS,
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


def _stall_rate(report: dict[str, Any], threshold: str) -> float:
    normalized = report.get("normalized", {})
    stored = normalized.get("stall_rates_per_minute", {}).get(threshold)
    if stored is not None:
        return float(stored)
    minutes = float(report.get("duration_seconds", 0)) / 60
    return float(report.get("stall_counts", {}).get(threshold, 0)) / minutes if minutes else 0.0


def _delayed_percent(report: dict[str, Any]) -> float:
    stored = report.get("normalized", {}).get("estimated_ui_delayed_percent")
    if stored is not None:
        return float(stored)
    duration = float(report.get("duration_seconds", 0))
    lag = report.get("event_loop_lag_ms", {})
    total_lag_ms = float(lag.get("sum", float(lag.get("mean", 0)) * int(lag.get("count", 0))))
    return min(100.0, total_lag_ms / (duration * 10)) if duration else 0.0


def comparison_rows(before: dict[str, Any], after: dict[str, Any]) -> list[tuple[str, float, float]]:
    rows = [
        ("Duration (minutes)", float(before.get("duration_seconds", 0)) / 60,
         float(after.get("duration_seconds", 0)) / 60),
        ("Estimated UI delayed (%)", _delayed_percent(before), _delayed_percent(after)),
        ("Stalls >= 50 ms / minute", _stall_rate(before, "50"), _stall_rate(after, "50")),
        ("Stalls >= 250 ms / minute", _stall_rate(before, "250"), _stall_rate(after, "250")),
        ("Stalls >= 1000 ms / minute", _stall_rate(before, "1000"), _stall_rate(after, "1000")),
    ]
    metrics = (
        ("Event-loop lag p99 (ms)", ("event_loop_lag_ms", "p99")),
        ("Worst observed lag (ms)", ("event_loop_lag_ms", "max")),
        ("Poll total p95 (ms)", ("poll", "total_ms", "p95")),
        ("Poll events p95 (ms)", ("poll", "events_ms", "p95")),
        ("Poll cards p95 (ms)", ("poll", "cards_ms", "p95")),
        ("Poll positions p95 (ms)", ("poll", "positions_ms", "p95")),
        ("Poll panel p95 (ms)", ("poll", "panel_ms", "p95")),
        ("Queued UI events p95", ("poll", "queue_before", "p95")),
    )
    rows.extend((label, _value(before, path), _value(after, path)) for label, path in metrics)
    return rows


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
    short = [
        str(report.get("label", "unnamed")) for report in (before, after)
        if float(report.get("duration_seconds", 0)) < RECOMMENDED_DURATION_SECONDS
    ]
    if short:
        print(
            f"\nWarning: {', '.join(short)} ran for less than "
            f"{RECOMMENDED_DURATION_SECONDS // 60} minutes; tail statistics may be unstable."
        )


if __name__ == "__main__":
    main()
