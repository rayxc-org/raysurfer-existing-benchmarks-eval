#!/usr/bin/env python3
"""Run rotating benchmark trials with Claude SDK baseline vs Raysurfer drop-in client."""

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
from raysurfer import RaysurferClient

from common import BenchmarkTask, TaskRunResult, build_query, load_tasks, now_utc_iso, write_runs


Mode = Literal["baseline", "raysurfer"]


ROTATING_VARIANTS = [
    "Variant focus: implement direct, deterministic logic with clear helper structure.",
    "Variant focus: prioritize input validation paths before core algorithm.",
    "Variant focus: keep implementation concise but include edge-case guards.",
    "Variant focus: maintain stable naming and deterministic branch ordering.",
]


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


def extract_python_source(text: str) -> str | None:
    """Extract Python source from markdown fenced text if present."""
    python_block = re.search(r"```python\\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if python_block:
        source = python_block.group(1).strip()
        return source if source else None

    generic_block = re.search(r"```\\s*(.*?)```", text, flags=re.DOTALL)
    if generic_block:
        source = generic_block.group(1).strip()
        return source if source else None

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
    """Validate candidate source against benchmark task tests."""
    candidate_path = workdir / "candidate.py"
    runner_path = workdir / "runner.py"

    candidate_path.write_text(source, encoding="utf-8")
    runner_path.write_text(f"from candidate import *\n{task.test_code}\n", encoding="utf-8")

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


def build_rotating_prompt(task: BenchmarkTask, round_index: int, position_index: int) -> tuple[str, str]:
    """Build a rotated variant prompt for the same benchmark task."""
    variant_index = (round_index + position_index) % len(ROTATING_VARIANTS)
    variant_label = f"round_{round_index + 1:02d}_variant_{variant_index + 1:02d}"
    variant_note = ROTATING_VARIANTS[variant_index]

    prompt = (
        f"{build_query(task)}\n"
        f"Rotation label: {variant_label}\n"
        f"{variant_note}\n\n"
        "Execution requirements:\n"
        "- Produce one complete Python file at solution.py.\n"
        "- Preserve the exact required function signature and behavior.\n"
        "- If .raysurfer_code exists and contains files, inspect cache files first and bias toward high-reputation reuse.\n"
        "- If cache files are present, write cache_review.json with keys: reviewed_files, selected_file, rationale.\n"
        "- Run python -m py_compile solution.py before stopping.\n"
        "- Return DONE when finished.\n"
    )
    return prompt, variant_label


def build_baseline_options(task_workdir: Path, model: str, max_turns: int) -> ClaudeAgentOptions:
    """Build baseline options for direct Claude SDK runs."""
    return ClaudeAgentOptions(
        tools={"type": "preset", "preset": "claude_code"},
        sandbox={"enabled": True, "autoAllowBashIfSandboxed": True},
        permission_mode="bypassPermissions",
        model=model,
        max_turns=max_turns,
        cwd=str(task_workdir),
        system_prompt=(
            "You are a benchmark coding agent. Use tools efficiently, compile-check your output, and stop."
        ),
    )


def build_raysurfer_options(task_workdir: Path, model: str, max_turns: int) -> ClaudeAgentOptions:
    """Build options for Raysurfer drop-in runs (defaults fill tools+sandbox)."""
    return ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=model,
        max_turns=max_turns,
        cwd=str(task_workdir),
        system_prompt=(
            "You are a benchmark coding agent. Review cached code and reputation hints before coding when available."
        ),
    )


async def stream_messages(mode: Mode, client: ClaudeSDKClient | RaysurferClient):
    """Yield SDK messages for either baseline or Raysurfer client mode."""
    if mode == "baseline":
        assert isinstance(client, ClaudeSDKClient)
        async for message in client.receive_response():
            yield message
        return

    assert isinstance(client, RaysurferClient)
    async for message in client.response():
        yield message


async def run_task_once(
    *,
    task: BenchmarkTask,
    mode: Mode,
    model: str,
    max_turns: int,
    timeout_seconds: float,
    validation_timeout_seconds: float,
    round_index: int,
    position_index: int,
    work_root: Path,
) -> TaskRunResult:
    """Execute one benchmark task attempt with timeout + validation."""
    task_workdir = work_root / mode / f"round_{round_index + 1:02d}" / task.task_id
    if task_workdir.exists():
        shutil.rmtree(task_workdir)
    task_workdir.mkdir(parents=True, exist_ok=True)

    prompt, variant_label = build_rotating_prompt(task, round_index, position_index)

    options = (
        build_baseline_options(task_workdir, model, max_turns)
        if mode == "baseline"
        else build_raysurfer_options(task_workdir, model, max_turns)
    )

    start = time.perf_counter()
    tool_calls = 0
    tool_names: set[str] = set()
    status = "no_result"
    saw_success = False
    assistant_text_chunks: list[str] = []

    async def _execute() -> tuple[bool, str, bool]:
        nonlocal tool_calls, status, saw_success

        if mode == "baseline":
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                async for msg in stream_messages(mode, client):
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, ToolUseBlock):
                                tool_calls += 1
                                tool_names.add(block.name)
                            elif isinstance(block, TextBlock):
                                assistant_text_chunks.append(block.text)
                    elif isinstance(msg, ResultMessage):
                        status = msg.subtype
                        saw_success = msg.subtype == "success"

                return bool(getattr(client, "_cached_code_blocks", [])), status, saw_success

        async with RaysurferClient(options=options) as client:
            await client.query(prompt)
            async for msg in stream_messages(mode, client):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            tool_calls += 1
                            tool_names.add(block.name)
                        elif isinstance(block, TextBlock):
                            assistant_text_chunks.append(block.text)
                elif isinstance(msg, ResultMessage):
                    status = msg.subtype
                    saw_success = msg.subtype == "success"

            return len(getattr(client, "_cached_code_blocks", [])) > 0, status, saw_success

    try:
        cache_hit, final_status, result_success = await asyncio.wait_for(
            _execute(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        return TaskRunResult(
            task_id=task.task_id,
            trial=round_index + 1,
            completed=False,
            elapsed_seconds=round(elapsed, 3),
            timestamp_utc=now_utc_iso(),
            details=(
                f"status=timeout;mode={mode};benchmark={task.benchmark};"
                f"round={round_index + 1};variant={variant_label}"
            ),
        )

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
                elapsed = time.perf_counter() - start
                return TaskRunResult(
                    task_id=task.task_id,
                    trial=round_index + 1,
                    completed=False,
                    elapsed_seconds=round(elapsed, 3),
                    timestamp_utc=now_utc_iso(),
                    details=(
                        f"status={final_status};mode={mode};validation=missing_solution_file;"
                        f"tools={tool_calls};cache_hit={int(cache_hit)};benchmark={task.benchmark};"
                        f"round={round_index + 1};variant={variant_label}"
                    ),
                )

    source = solution_path.read_text(encoding="utf-8")
    valid, validation_reason = validate_source(
        task=task,
        source=source,
        workdir=task_workdir,
        validation_timeout_seconds=validation_timeout_seconds,
    )
    cache_review_exists = (task_workdir / "cache_review.json").exists()
    completed = result_success and valid
    elapsed = time.perf_counter() - start

    return TaskRunResult(
        task_id=task.task_id,
        trial=round_index + 1,
        completed=completed,
        elapsed_seconds=round(elapsed, 3),
        timestamp_utc=now_utc_iso(),
        details=(
            f"status={final_status};mode={mode};validation={validation_reason};"
            f"tools={tool_calls};tool_names={','.join(sorted(tool_names))};"
            f"cache_hit={int(cache_hit)};cache_review={int(cache_review_exists)};"
            f"benchmark={task.benchmark};round={round_index + 1};variant={variant_label}"
        ),
    )


def rotate_tasks(tasks: list[BenchmarkTask], round_index: int) -> list[BenchmarkTask]:
    """Rotate task order each round for better persistence stress."""
    if not tasks:
        return []
    offset = round_index % len(tasks)
    return tasks[offset:] + tasks[:offset]


async def run_eval(
    *,
    tasks: list[BenchmarkTask],
    mode: Mode,
    model: str,
    max_turns: int,
    timeout_seconds: float,
    validation_timeout_seconds: float,
    rounds: int,
    work_root: Path,
) -> list[TaskRunResult]:
    """Run full rotating evaluation loop."""
    results: list[TaskRunResult] = []

    for round_index in range(rounds):
        ordered_tasks = rotate_tasks(tasks, round_index)
        for position_index, task in enumerate(ordered_tasks):
            result = await run_task_once(
                task=task,
                mode=mode,
                model=model,
                max_turns=max_turns,
                timeout_seconds=timeout_seconds,
                validation_timeout_seconds=validation_timeout_seconds,
                round_index=round_index,
                position_index=position_index,
                work_root=work_root,
            )
            results.append(result)
            print(
                f"[{mode}] round={round_index + 1}/{rounds} task={task.task_id} "
                f"completed={result.completed} elapsed={result.elapsed_seconds}s "
                f"details={result.details}",
                flush=True,
            )

    return results


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Run rotating ClaudeSDK vs Raysurfer drop-in benchmark eval",
    )
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--mode", choices=["baseline", "raysurfer"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--validation-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--work-root", type=Path, default=Path("runs/workdirs_rotating"))
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    load_env()

    tasks = load_tasks(args.tasks)
    if args.limit is not None:
        tasks = tasks[: args.limit]

    if not tasks:
        raise ValueError("No benchmark tasks loaded.")

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required for both baseline and raysurfer modes.")
    if args.mode == "raysurfer" and not os.getenv("RAYSURFER_API_KEY"):
        raise RuntimeError("RAYSURFER_API_KEY is required for raysurfer mode.")

    runs = asyncio.run(
        run_eval(
            tasks=tasks,
            mode=args.mode,
            model=args.model,
            max_turns=args.max_turns,
            timeout_seconds=args.timeout_seconds,
            validation_timeout_seconds=args.validation_timeout_seconds,
            rounds=args.rounds,
            work_root=args.work_root,
        )
    )

    notes = (
        f"mode={args.mode};model={args.model};max_turns={args.max_turns};"
        f"timeout_seconds={args.timeout_seconds};validation_timeout_seconds={args.validation_timeout_seconds};"
        f"rounds={args.rounds};rotation=deterministic;date={now_utc_iso()}"
    )
    write_runs(args.out, args.mode, runs, notes=notes)
    print(f"Wrote {len(runs)} runs to {args.out}", flush=True)


if __name__ == "__main__":
    main()
