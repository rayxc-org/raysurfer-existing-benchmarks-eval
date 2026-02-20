#!/usr/bin/env python3
"""Seed Raysurfer with reference solutions from existing benchmark tasks."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from raysurfer import AsyncRaySurfer, FileWritten

from common import build_query, load_tasks


def load_env_from_file(path: Path) -> None:
    """Load dotenv-style variables from a file if it exists."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def load_env() -> None:
    """Load local and workspace-level env files."""
    script_path = Path(__file__).resolve()
    load_env_from_file(script_path.parents[1] / ".env")
    load_env_from_file(script_path.parents[3] / ".env")


async def seed(tasks_path: Path, limit: int | None) -> None:
    """Upload reference code snippets to Raysurfer."""
    tasks = load_tasks(tasks_path)
    if limit is not None:
        tasks = tasks[:limit]

    api_key = os.getenv("RAYSURFER_API_KEY")
    base_url = os.getenv("RAYSURFER_BASE_URL", "https://api.raysurfer.com")

    if not api_key:
        raise RuntimeError("RAYSURFER_API_KEY is missing")

    async with AsyncRaySurfer(api_key=api_key, base_url=base_url) as rs:
        for idx, task in enumerate(tasks, start=1):
            query = build_query(task)
            path = f"{task.task_id.lower()}_reference.py"

            print(f"[{idx}/{len(tasks)}] upload {task.task_id} ({task.benchmark})", flush=True)
            await rs.upload_new_code_snip(
                task=query,
                file_written=FileWritten(path=path, content=task.reference_source),
                succeeded=True,
                use_raysurfer_ai_voting=False,
                tags=["existing-benchmark", task.benchmark, "reference"],
            )


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Seed reference benchmark solutions into Raysurfer")
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    load_env()
    asyncio.run(seed(args.tasks, args.limit))


if __name__ == "__main__":
    main()
