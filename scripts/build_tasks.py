#!/usr/bin/env python3
"""Build normalized task files from existing public benchmark datasets."""

from __future__ import annotations

import argparse
import gzip
import json
import urllib.request
from pathlib import Path

from common import BenchmarkTask, now_utc_iso


HUMANEVAL_URL = "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"
MBPP_URL = "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/mbpp.jsonl"


def fetch_bytes(url: str) -> bytes:
    """Download bytes from a URL with a deterministic user agent."""
    request = urllib.request.Request(url, headers={"User-Agent": "raysurfer-benchmark-eval/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def parse_jsonl(text: str) -> list[dict[str, object]]:
    """Parse JSONL text into object records."""
    records: list[dict[str, object]] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError(f"line {line_no} is not a JSON object")
        records.append(parsed)
    return records


def build_humaneval_tasks(limit: int) -> list[BenchmarkTask]:
    """Build normalized tasks from HumanEval."""
    raw_bytes = fetch_bytes(HUMANEVAL_URL)
    decompressed = gzip.decompress(raw_bytes).decode("utf-8")
    records = parse_jsonl(decompressed)

    tasks: list[BenchmarkTask] = []
    for idx, item in enumerate(records[:limit]):
        source_task_id = item.get("task_id")
        prompt = item.get("prompt")
        entry_point = item.get("entry_point")
        canonical_solution = item.get("canonical_solution")
        test_block = item.get("test")

        if not isinstance(source_task_id, str):
            raise ValueError("HumanEval task missing task_id")
        if not isinstance(prompt, str):
            raise ValueError(f"{source_task_id} missing prompt")
        if not isinstance(entry_point, str):
            raise ValueError(f"{source_task_id} missing entry_point")
        if not isinstance(canonical_solution, str):
            raise ValueError(f"{source_task_id} missing canonical_solution")
        if not isinstance(test_block, str):
            raise ValueError(f"{source_task_id} missing test")

        task_id = f"HE-{idx:03d}"
        reference_source = f"{prompt}{canonical_solution}".replace("\r\n", "\n")
        test_code = f"{test_block}\n\ncheck({entry_point})\n".replace("\r\n", "\n")

        tasks.append(
            BenchmarkTask(
                task_id=task_id,
                benchmark="humaneval",
                source_task_id=source_task_id,
                prompt=prompt,
                entry_point=entry_point,
                reference_source=reference_source,
                test_code=test_code,
            )
        )

    return tasks


def build_mbpp_tasks(limit: int) -> list[BenchmarkTask]:
    """Build normalized tasks from MBPP."""
    raw_text = fetch_bytes(MBPP_URL).decode("utf-8")
    records = parse_jsonl(raw_text)

    tasks: list[BenchmarkTask] = []
    for idx, item in enumerate(records[:limit]):
        source_task_id = item.get("task_id")
        prompt = item.get("text")
        reference_source = item.get("code")
        setup = item.get("test_setup_code")
        tests = item.get("test_list")

        if not isinstance(source_task_id, int):
            raise ValueError("MBPP task missing integer task_id")
        if not isinstance(prompt, str):
            raise ValueError(f"MBPP/{source_task_id} missing text")
        if not isinstance(reference_source, str):
            raise ValueError(f"MBPP/{source_task_id} missing code")
        if not isinstance(setup, str):
            raise ValueError(f"MBPP/{source_task_id} missing test_setup_code")
        if not isinstance(tests, list):
            raise ValueError(f"MBPP/{source_task_id} missing test_list")

        checks: list[str] = []
        for test_item in tests:
            if isinstance(test_item, str):
                checks.append(test_item)

        if not checks:
            raise ValueError(f"MBPP/{source_task_id} has no tests")

        task_id = f"MB-{idx:03d}"
        merged_test = "\n".join([setup, *checks])

        tasks.append(
            BenchmarkTask(
                task_id=task_id,
                benchmark="mbpp",
                source_task_id=f"MBPP/{source_task_id}",
                prompt=prompt,
                entry_point="",
                reference_source=reference_source.replace("\r\n", "\n"),
                test_code=merged_test.replace("\r\n", "\n"),
            )
        )

    return tasks


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Build tasks from public benchmark datasets")
    parser.add_argument("--out", type=Path, default=Path("tasks/existing_benchmarks_20.json"))
    parser.add_argument("--humaneval-limit", type=int, default=10)
    parser.add_argument("--mbpp-limit", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    """Build and write benchmark task file."""
    args = parse_args()
    humaneval_tasks = build_humaneval_tasks(args.humaneval_limit)
    mbpp_tasks = build_mbpp_tasks(args.mbpp_limit)
    tasks = [*humaneval_tasks, *mbpp_tasks]

    payload = {
        "version": "1.0.0",
        "built_at_utc": now_utc_iso(),
        "sources": {
            "humaneval_url": HUMANEVAL_URL,
            "mbpp_url": MBPP_URL,
        },
        "task_count": len(tasks),
        "tasks": [task.__dict__ for task in tasks],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {args.out} with {len(tasks)} tasks")


if __name__ == "__main__":
    main()
