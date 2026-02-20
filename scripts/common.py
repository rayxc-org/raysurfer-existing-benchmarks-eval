#!/usr/bin/env python3
"""Shared utilities for existing-benchmark Raysurfer eval scripts."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


BENCHMARK_KEY = "RS_EXISTING_BENCH_2026_02_20_V1"


@dataclass(frozen=True)
class BenchmarkTask:
    """Normalized task format used by eval scripts."""

    task_id: str
    benchmark: str
    source_task_id: str
    prompt: str
    entry_point: str
    reference_source: str
    test_code: str


@dataclass
class TaskRunResult:
    """A measured run result for one benchmark task."""

    task_id: str
    trial: int
    completed: bool
    elapsed_seconds: float
    timestamp_utc: str
    details: str


def now_utc_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_tasks(path: Path) -> list[BenchmarkTask]:
    """Load benchmark tasks from JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list):
        raise ValueError("tasks file must contain a 'tasks' array")

    tasks: list[BenchmarkTask] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(tasks_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"task #{index} is not an object")

        task_id = item.get("task_id")
        benchmark = item.get("benchmark")
        source_task_id = item.get("source_task_id")
        prompt = item.get("prompt")
        entry_point = item.get("entry_point")
        reference_source = item.get("reference_source")
        test_code = item.get("test_code")

        if not isinstance(task_id, str) or not task_id:
            raise ValueError(f"task #{index} missing string task_id")
        if task_id in seen_ids:
            raise ValueError(f"duplicate task_id: {task_id}")
        seen_ids.add(task_id)

        if not isinstance(benchmark, str) or not benchmark:
            raise ValueError(f"{task_id} missing string benchmark")
        if not isinstance(source_task_id, str) or not source_task_id:
            raise ValueError(f"{task_id} missing string source_task_id")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError(f"{task_id} missing string prompt")
        if not isinstance(entry_point, str):
            raise ValueError(f"{task_id} missing string entry_point")
        if not isinstance(reference_source, str) or not reference_source:
            raise ValueError(f"{task_id} missing string reference_source")
        if not isinstance(test_code, str) or not test_code:
            raise ValueError(f"{task_id} missing string test_code")

        tasks.append(
            BenchmarkTask(
                task_id=task_id,
                benchmark=benchmark,
                source_task_id=source_task_id,
                prompt=prompt,
                entry_point=entry_point,
                reference_source=reference_source,
                test_code=test_code,
            )
        )

    if not tasks:
        raise ValueError("tasks file contains no tasks")

    return tasks


def build_query(task: BenchmarkTask) -> str:
    """Build a deterministic retrieval query for this benchmark task."""
    entry_line = f"Entry point: {task.entry_point}\n" if task.entry_point else ""
    return (
        f"Benchmark key: {BENCHMARK_KEY}\n"
        f"Benchmark: {task.benchmark}\n"
        f"Source task id: {task.source_task_id}\n"
        f"Task id: {task.task_id}\n"
        f"{entry_line}\n"
        "Solve this Python coding benchmark task exactly:\n"
        f"{task.prompt}\n"
    )


def write_runs(path: Path, label: str, runs: list[TaskRunResult], notes: str) -> None:
    """Write run results in scorer-compatible shape."""
    payload = {
        "label": label,
        "notes": notes,
        "runs": [asdict(run) for run in runs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
