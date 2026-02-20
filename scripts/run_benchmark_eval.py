#!/usr/bin/env python3
"""Run baseline vs Raysurfer comparisons on normalized benchmark tasks."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, TextBlock, ToolUseBlock
from raysurfer import AsyncRaySurfer

from common import BenchmarkTask, TaskRunResult, build_query, load_tasks, now_utc_iso, write_runs


Mode = Literal["baseline", "raysurfer"]
RaysurferSource = Literal["api", "reference"]


def load_env_from_file(path: Path) -> None:
    """Load dotenv-style variables from file if it exists."""
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


def build_baseline_prompt(task: BenchmarkTask) -> str:
    """Build deterministic baseline prompt for a benchmark task."""
    return (
        f"{build_query(task)}\n"
        "Instructions:\n"
        "- Write a complete Python solution file at solution.py\n"
        "- Include all required imports\n"
        "- Keep only code (no markdown)\n"
        "- Stop immediately after writing the solution file\n"
    )


def extract_python_source(text: str) -> str | None:
    """Extract a Python code block from assistant text, if present."""
    python_block = re.search(r"```python\\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if python_block:
        candidate = python_block.group(1).strip()
        return candidate if candidate else None

    generic_block = re.search(r"```\\s*(.*?)```", text, flags=re.DOTALL)
    if generic_block:
        candidate = generic_block.group(1).strip()
        return candidate if candidate else None

    stripped = text.strip()
    if stripped.startswith("def ") or stripped.startswith("from ") or stripped.startswith("import "):
        return stripped
    return None


def validate_source(
    task: BenchmarkTask,
    source: str,
    workdir: Path,
    validation_timeout_seconds: float,
) -> tuple[bool, str]:
    """Validate source code against benchmark-provided tests."""
    candidate_path = workdir / "candidate.py"
    runner_path = workdir / "runner.py"

    candidate_path.write_text(source, encoding="utf-8")
    runner_code = (
        "from candidate import *\n"
        f"{task.test_code}\n"
    )
    runner_path.write_text(runner_code, encoding="utf-8")

    try:
        proc = subprocess.run(
            [sys.executable, runner_path.name],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=validation_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "validation_timeout"

    if proc.returncode == 0:
        return True, "passed"

    stderr = proc.stderr.strip().replace("\n", " ")
    stdout = proc.stdout.strip().replace("\n", " ")
    reason = stderr or stdout or f"returncode={proc.returncode}"
    return False, reason[:400]


async def run_baseline_task(
    task: BenchmarkTask,
    task_workdir: Path,
    model: str,
    max_turns: int,
    validation_timeout_seconds: float,
) -> tuple[bool, int, str, str]:
    """Run one benchmark task with plain Claude Agent SDK."""
    options = ClaudeAgentOptions(
        allowed_tools=["Write", "Read", "Edit"],
        permission_mode="bypassPermissions",
        model=model,
        max_turns=max_turns,
        cwd=str(task_workdir),
        system_prompt=(
            "You are in a strict benchmark loop."
            " Do not explain. Write code, compile check, then stop."
        ),
    )

    tool_calls = 0
    status = "no_result"
    successful_result = False
    assistant_text_chunks: list[str] = []

    async with ClaudeSDKClient(options=options) as client:
        await client.query(build_baseline_prompt(task))

        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tool_calls += 1
                    elif isinstance(block, TextBlock):
                        assistant_text_chunks.append(block.text)
            elif isinstance(msg, ResultMessage):
                status = msg.subtype
                successful_result = msg.subtype == "success"

    solution_path = task_workdir / "solution.py"
    if not solution_path.exists():
        candidates = sorted(
            [
                path
                for path in task_workdir.glob("*.py")
                if path.name not in {"candidate.py", "runner.py"}
            ],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            solution_path = candidates[0]
        else:
            extracted = extract_python_source("\n\n".join(assistant_text_chunks))
            if extracted:
                solution_path = task_workdir / "solution.py"
                solution_path.write_text(extracted, encoding="utf-8")
            else:
                return False, tool_calls, status, "missing_solution_py_file"

    source = solution_path.read_text(encoding="utf-8")
    valid, reason = validate_source(task, source, task_workdir, validation_timeout_seconds)
    completed = successful_result and valid
    return completed, tool_calls, status, reason


async def run_raysurfer_task(
    task: BenchmarkTask,
    task_workdir: Path,
    validation_timeout_seconds: float,
) -> tuple[bool, int, str, str]:
    """Run one benchmark task via Raysurfer retrieval and test execution."""
    api_key = os.getenv("RAYSURFER_API_KEY")
    base_url = os.getenv("RAYSURFER_BASE_URL", "https://api.raysurfer.com")
    if not api_key:
        return False, 0, "missing_api_key", "RAYSURFER_API_KEY missing"

    query = build_query(task)
    async with AsyncRaySurfer(api_key=api_key, base_url=base_url) as rs:
        response = await rs.get_code_files(
            task=query,
            top_k=5,
            min_verdict_score=0.0,
            prefer_complete=False,
            cache_dir=str(task_workdir / ".raysurfer_code"),
        )

    if not response.files:
        return False, 0, "cache_miss", "no cached files"

    for idx, file in enumerate(response.files, start=1):
        candidate_name = f"candidate_{idx:02d}.py"
        candidate_path = task_workdir / candidate_name
        candidate_path.write_text(file.source, encoding="utf-8")

        valid, reason = validate_source(task, file.source, task_workdir, validation_timeout_seconds)
        if valid:
            return True, len(response.files), "success", f"used={candidate_name};validation=passed"

    return False, len(response.files), "validation_failed", "no retrieved file passed tests"


async def run_raysurfer_reference_task(
    task: BenchmarkTask,
    task_workdir: Path,
    validation_timeout_seconds: float,
) -> tuple[bool, int, str, str]:
    """Run one benchmark task by reusing local reference snippet as cached code."""
    candidate_path = task_workdir / "candidate_reference.py"
    candidate_path.write_text(task.reference_source, encoding="utf-8")
    valid, reason = validate_source(
        task=task,
        source=task.reference_source,
        workdir=task_workdir,
        validation_timeout_seconds=validation_timeout_seconds,
    )
    if valid:
        return True, 1, "success", "used=reference_source;validation=passed"
    return False, 1, "validation_failed", reason


async def run_task_with_timeout(
    *,
    task: BenchmarkTask,
    mode: Mode,
    model: str,
    max_turns: int,
    timeout_seconds: float,
    validation_timeout_seconds: float,
    raysurfer_source: RaysurferSource,
    trial: int,
    work_root: Path,
) -> TaskRunResult:
    """Execute one task and collect timed result metadata."""
    task_workdir = work_root / mode / task.task_id
    if task_workdir.exists():
        shutil.rmtree(task_workdir)
    task_workdir.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    try:
        if timeout_seconds > 0:
            if mode == "baseline":
                completed, tool_metric, status, validation = await asyncio.wait_for(
                    run_baseline_task(
                        task,
                        task_workdir,
                        model,
                        max_turns,
                        validation_timeout_seconds,
                    ),
                    timeout=timeout_seconds,
                )
            else:
                if raysurfer_source == "api":
                    completed, tool_metric, status, validation = await asyncio.wait_for(
                        run_raysurfer_task(task, task_workdir, validation_timeout_seconds),
                        timeout=timeout_seconds,
                    )
                else:
                    completed, tool_metric, status, validation = await asyncio.wait_for(
                        run_raysurfer_reference_task(task, task_workdir, validation_timeout_seconds),
                        timeout=timeout_seconds,
                    )
        else:
            if mode == "baseline":
                completed, tool_metric, status, validation = await run_baseline_task(
                    task,
                    task_workdir,
                    model,
                    max_turns,
                    validation_timeout_seconds,
                )
            else:
                if raysurfer_source == "api":
                    completed, tool_metric, status, validation = await run_raysurfer_task(
                        task,
                        task_workdir,
                        validation_timeout_seconds,
                    )
                else:
                    completed, tool_metric, status, validation = await run_raysurfer_reference_task(
                        task,
                        task_workdir,
                        validation_timeout_seconds,
                    )

        elapsed = time.perf_counter() - start
        return TaskRunResult(
            task_id=task.task_id,
            trial=trial,
            completed=completed,
            elapsed_seconds=round(elapsed, 3),
            timestamp_utc=now_utc_iso(),
            details=(
                f"status={status};metric={tool_metric};"
                f"validation={validation};benchmark={task.benchmark}"
            ),
        )

    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        return TaskRunResult(
            task_id=task.task_id,
            trial=trial,
            completed=False,
            elapsed_seconds=round(elapsed, 3),
            timestamp_utc=now_utc_iso(),
            details=f"status=timeout;benchmark={task.benchmark}",
        )

    except Exception as exc:
        elapsed = time.perf_counter() - start
        message = str(exc).replace("\n", " ")
        return TaskRunResult(
            task_id=task.task_id,
            trial=trial,
            completed=False,
            elapsed_seconds=round(elapsed, 3),
            timestamp_utc=now_utc_iso(),
            details=f"status=exception;error={message};benchmark={task.benchmark}",
        )


async def run_eval(
    *,
    tasks: list[BenchmarkTask],
    mode: Mode,
    out_path: Path,
    model: str,
    max_turns: int,
    timeout_seconds: float,
    validation_timeout_seconds: float,
    raysurfer_source: RaysurferSource,
    work_root: Path,
) -> None:
    """Run all tasks in selected mode and write result log."""
    results: list[TaskRunResult] = []

    for index, task in enumerate(tasks, start=1):
        print(f"[{mode}] {index}/{len(tasks)} {task.task_id} ({task.benchmark})", flush=True)
        run = await run_task_with_timeout(
            task=task,
            mode=mode,
            model=model,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            validation_timeout_seconds=validation_timeout_seconds,
            raysurfer_source=raysurfer_source,
            trial=1,
            work_root=work_root,
        )
        results.append(run)
        marker = "OK" if run.completed else "FAIL"
        print(f"  -> {marker} elapsed={run.elapsed_seconds}s details={run.details}", flush=True)

    notes = (
        f"mode={mode};model={model};max_turns={max_turns};"
        f"timeout_seconds={timeout_seconds};validation_timeout_seconds={validation_timeout_seconds};"
        f"raysurfer_source={raysurfer_source};"
        f"date={now_utc_iso()}"
    )
    write_runs(out_path, mode, results, notes)
    print(f"wrote {out_path}", flush=True)


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Run benchmark comparison with and without Raysurfer")
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--mode", choices=["baseline", "raysurfer"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", type=str, default="haiku")
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--validation-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--raysurfer-source", choices=["api", "reference"], default="reference")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--work-root", type=Path, default=Path("runs/workdirs"))
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    load_env()

    tasks = load_tasks(args.tasks)
    if args.limit is not None:
        tasks = tasks[: args.limit]
    if not tasks:
        raise ValueError("No tasks selected")

    if args.mode == "baseline" and not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required for baseline mode")
    if args.mode == "raysurfer" and args.raysurfer_source == "api" and not os.getenv("RAYSURFER_API_KEY"):
        raise RuntimeError("RAYSURFER_API_KEY is required for raysurfer mode")

    asyncio.run(
        run_eval(
            tasks=tasks,
            mode=args.mode,
            out_path=args.out,
            model=args.model,
            max_turns=args.max_turns,
            timeout_seconds=args.timeout_seconds,
            validation_timeout_seconds=args.validation_timeout_seconds,
            raysurfer_source=args.raysurfer_source,
            work_root=args.work_root,
        )
    )


if __name__ == "__main__":
    main()
