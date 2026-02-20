#!/usr/bin/env python3
"""Score benchmark eval logs for 3-minute consistency."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from common import BenchmarkTask, load_tasks


@dataclass(frozen=True)
class RunRecord:
    """Single run record from output logs."""

    task_id: str
    completed: bool
    elapsed_seconds: float


@dataclass
class TaskAggregate:
    """Aggregated metrics for one task."""

    attempts: int = 0
    completed: int = 0
    completed_within_sla: int = 0

    def consistency(self) -> float:
        """Return completed-within-SLA ratio."""
        if self.attempts == 0:
            return 0.0
        return self.completed_within_sla / self.attempts


@dataclass
class ScoreReport:
    """Complete report metrics for one mode."""

    label: str
    per_task: dict[str, TaskAggregate]
    total_attempts: int
    total_completed: int
    total_completed_within_sla: int

    def overall_consistency(self) -> float:
        """Weighted consistency across all attempts."""
        if self.total_attempts == 0:
            return 0.0
        return self.total_completed_within_sla / self.total_attempts


def load_run_records(path: Path, valid_task_ids: set[str]) -> tuple[str, list[RunRecord]]:
    """Load run log and validate its shape."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    label = raw.get("label")
    runs = raw.get("runs")

    if not isinstance(label, str) or not label:
        label = path.stem
    if not isinstance(runs, list):
        raise ValueError(f"{path}: expected list at key 'runs'")

    records: list[RunRecord] = []
    for index, item in enumerate(runs, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: run #{index} is not an object")

        task_id = item.get("task_id")
        completed = item.get("completed")
        elapsed_seconds = item.get("elapsed_seconds")

        if not isinstance(task_id, str) or task_id not in valid_task_ids:
            raise ValueError(f"{path}: invalid task_id at run #{index}")
        if not isinstance(completed, bool):
            raise ValueError(f"{path}: run #{index} missing boolean completed")
        if not isinstance(elapsed_seconds, (int, float)):
            raise ValueError(f"{path}: run #{index} missing numeric elapsed_seconds")

        records.append(
            RunRecord(
                task_id=task_id,
                completed=completed,
                elapsed_seconds=float(elapsed_seconds),
            )
        )

    return label, records


def compute_report(
    label: str,
    tasks: list[BenchmarkTask],
    runs: list[RunRecord],
    sla_seconds: float,
) -> ScoreReport:
    """Aggregate run records into score report."""
    per_task: dict[str, TaskAggregate] = {task.task_id: TaskAggregate() for task in tasks}

    for run in runs:
        aggregate = per_task[run.task_id]
        aggregate.attempts += 1
        if run.completed:
            aggregate.completed += 1
            if run.elapsed_seconds <= sla_seconds:
                aggregate.completed_within_sla += 1

    total_attempts = sum(item.attempts for item in per_task.values())
    total_completed = sum(item.completed for item in per_task.values())
    total_completed_within_sla = sum(item.completed_within_sla for item in per_task.values())

    return ScoreReport(
        label=label,
        per_task=per_task,
        total_attempts=total_attempts,
        total_completed=total_completed,
        total_completed_within_sla=total_completed_within_sla,
    )


def print_report(report: ScoreReport, tasks: list[BenchmarkTask], sla_seconds: float) -> None:
    """Print readable summary for a mode."""
    task_by_id = {task.task_id: task for task in tasks}
    print(f"\n=== {report.label} ===")
    print(f"overall_consistency_within_{int(sla_seconds)}s: {report.overall_consistency():.2%}")
    print(
        "completed_within_sla/attempts: "
        f"{report.total_completed_within_sla}/{report.total_attempts}"
    )

    print("\nPer-task consistency:")
    print("task_id | benchmark | within_sla | attempts | consistency | source_task_id")
    print("--- | --- | ---: | ---: | ---: | ---")

    for task in tasks:
        aggregate = report.per_task[task.task_id]
        print(
            f"{task.task_id} | {task.benchmark} | {aggregate.completed_within_sla} | "
            f"{aggregate.attempts} | {aggregate.consistency():.2%} | {task.source_task_id}"
        )


def print_comparison(
    baseline: ScoreReport,
    raysurfer: ScoreReport,
    tasks: list[BenchmarkTask],
    sla_seconds: float,
) -> None:
    """Print baseline-vs-Raysurfer deltas."""
    baseline_overall = baseline.overall_consistency()
    raysurfer_overall = raysurfer.overall_consistency()
    delta = raysurfer_overall - baseline_overall

    print("\n=== Comparison ===")
    print(f"baseline_consistency_within_{int(sla_seconds)}s: {baseline_overall:.2%}")
    print(f"raysurfer_consistency_within_{int(sla_seconds)}s: {raysurfer_overall:.2%}")
    print(f"delta: {delta:+.2%}")

    print("\nPer-task delta (Raysurfer - Baseline):")
    print("task_id | benchmark | baseline | raysurfer | delta")
    print("--- | --- | ---: | ---: | ---: ")

    for task in tasks:
        base_score = baseline.per_task[task.task_id].consistency()
        rs_score = raysurfer.per_task[task.task_id].consistency()
        print(
            f"{task.task_id} | {task.benchmark} | {base_score:.2%} | "
            f"{rs_score:.2%} | {rs_score - base_score:+.2%}"
        )


def write_json_summary(
    *,
    output_path: Path,
    tasks: list[BenchmarkTask],
    raysurfer: ScoreReport,
    baseline: ScoreReport | None,
    sla_seconds: float,
) -> None:
    """Write machine-readable summary output."""
    payload: dict[str, object] = {
        "sla_seconds": sla_seconds,
        "task_count": len(tasks),
        "raysurfer": {
            "label": raysurfer.label,
            "overall_consistency": raysurfer.overall_consistency(),
            "total_attempts": raysurfer.total_attempts,
            "total_completed": raysurfer.total_completed,
            "total_completed_within_sla": raysurfer.total_completed_within_sla,
        },
        "per_task": {},
    }

    per_task: dict[str, object] = {}
    for task in tasks:
        rs = raysurfer.per_task[task.task_id]
        item: dict[str, object] = {
            "benchmark": task.benchmark,
            "source_task_id": task.source_task_id,
            "raysurfer_consistency": rs.consistency(),
            "raysurfer_attempts": rs.attempts,
            "raysurfer_within_sla": rs.completed_within_sla,
        }
        if baseline is not None:
            base = baseline.per_task[task.task_id]
            item["baseline_consistency"] = base.consistency()
            item["baseline_attempts"] = base.attempts
            item["baseline_within_sla"] = base.completed_within_sla
            item["delta"] = rs.consistency() - base.consistency()
        per_task[task.task_id] = item

    payload["per_task"] = per_task

    if baseline is not None:
        payload["baseline"] = {
            "label": baseline.label,
            "overall_consistency": baseline.overall_consistency(),
            "total_attempts": baseline.total_attempts,
            "total_completed": baseline.total_completed,
            "total_completed_within_sla": baseline.total_completed_within_sla,
            "delta": raysurfer.overall_consistency() - baseline.overall_consistency(),
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Score benchmark run logs")
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--raysurfer-runs", type=Path, required=True)
    parser.add_argument("--baseline-runs", type=Path)
    parser.add_argument("--sla-seconds", type=float, default=180.0)
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()

    tasks = load_tasks(args.tasks)
    task_ids = {task.task_id for task in tasks}

    raysurfer_label, raysurfer_runs = load_run_records(args.raysurfer_runs, task_ids)
    raysurfer_report = compute_report(raysurfer_label, tasks, raysurfer_runs, args.sla_seconds)

    baseline_report: ScoreReport | None = None
    if args.baseline_runs is not None:
        baseline_label, baseline_runs = load_run_records(args.baseline_runs, task_ids)
        baseline_report = compute_report(baseline_label, tasks, baseline_runs, args.sla_seconds)

    print_report(raysurfer_report, tasks, args.sla_seconds)
    if baseline_report is not None:
        print_report(baseline_report, tasks, args.sla_seconds)
        print_comparison(baseline_report, raysurfer_report, tasks, args.sla_seconds)

    if args.json_out is not None:
        write_json_summary(
            output_path=args.json_out,
            tasks=tasks,
            raysurfer=raysurfer_report,
            baseline=baseline_report,
            sla_seconds=args.sla_seconds,
        )
        print(f"\nWrote JSON summary to {args.json_out}")


if __name__ == "__main__":
    main()
