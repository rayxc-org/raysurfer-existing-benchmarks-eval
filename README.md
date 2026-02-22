# Raysurfer Existing Benchmarks Comparison

A public benchmark harness that uses **already existing benchmark tasks** and compares:
- Baseline coding-agent execution (no Raysurfer reuse)
- Raysurfer cached snippet reuse (API mode or local reference-cache mode)

Primary metric is **3-minute consistency**:

`consistency = completed_within_180_seconds / total_attempts`

## Included Benchmark Sources

- HumanEval: `openai/human-eval` (`data/HumanEval.jsonl.gz`)
- MBPP: `google-research/google-research` (`mbpp/mbpp.jsonl`)

## Repo Layout

- `scripts/build_tasks.py`: fetch + normalize benchmark tasks from source datasets
- `scripts/seed_reference_solutions.py`: upload reference solutions into Raysurfer
- `scripts/run_benchmark_eval.py`: run baseline or Raysurfer mode and log results
- `scripts/run_rotating_sdk_eval.py`: rotating prompt-variant eval using Claude SDK baseline vs Raysurfer drop-in
- `scripts/score_eval.py`: score consistency and compute deltas
- `scripts/generate_chart.py`: generate benchmark chart SVG
- `runs/*.json`: run logs and scored summaries

## Quickstart

1. Build a task file from existing benchmark datasets:

```bash
uv run python scripts/build_tasks.py \
  --out tasks/existing_benchmarks_20.json \
  --humaneval-limit 10 \
  --mbpp-limit 10
```

2. Run baseline (strict 180-second SLA per task):

```bash
uv run python scripts/run_benchmark_eval.py \
  --tasks tasks/existing_benchmarks_20.json \
  --mode baseline \
  --out runs/baseline.json \
  --timeout-seconds 180
```

3. Run Raysurfer comparison mode (reference-cache reuse, no backend required):

```bash
uv run python scripts/run_benchmark_eval.py \
  --tasks tasks/existing_benchmarks_20.json \
  --mode raysurfer \
  --raysurfer-source reference \
  --out runs/with_raysurfer.json \
  --timeout-seconds 180
```

4. Score + chart:

```bash
uv run python scripts/score_eval.py \
  --tasks tasks/existing_benchmarks_20.json \
  --raysurfer-runs runs/with_raysurfer.json \
  --baseline-runs runs/baseline.json \
  --json-out runs/summary.json

uv run python scripts/generate_chart.py \
  --summary runs/summary.json \
  --out assets/benchmark_comparison.svg
```

### Optional: Live API-backed Raysurfer mode

If you want to compare against actual API retrieval instead of local reference-cache reuse:

```bash
# Start backend in another shell if needed
cd ../raysurfer-backend
uv run uvicorn app.main:app --port 8000

# Seed cache
cd ../examples/raysurfer-existing-benchmarks-eval
RAYSURFER_BASE_URL=http://127.0.0.1:8000 RAYSURFER_API_KEY=local-dev-key \
uv run python scripts/seed_reference_solutions.py --tasks tasks/existing_benchmarks_20.json

# Run api-backed retrieval
RAYSURFER_BASE_URL=http://127.0.0.1:8000 RAYSURFER_API_KEY=local-dev-key \
uv run python scripts/run_benchmark_eval.py \
  --tasks tasks/existing_benchmarks_20.json \
  --mode raysurfer \
  --raysurfer-source api \
  --out runs/with_raysurfer.json \
  --timeout-seconds 180
```

## Latest Rotating SDK Benchmarks (February 21-22, 2026)

All results below were generated with `scripts/run_rotating_sdk_eval.py` plus `scripts/score_eval.py`.
Each run required cache inspection before execution when `.raysurfer_code` existed, and tracked
`cache_hit` plus `cache_review` in the run details.

| Run Set | Tasks x Rounds | Model | Max Turns | Task Timeout | Baseline Consistency | Raysurfer Consistency | Delta |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Mixed existing benchmarks (10 HumanEval + 10 MBPP) | 20 x 2 (40 attempts/mode) | `claude-haiku-4-5-20251001` | 8 | 120s | 45.0% (18/40) | 77.5% (31/40) | +32.5 pp |
| MBPP showcase (best persistence demo) | 10 x 3 (30 attempts/mode) | `claude-haiku-4-5-20251001` | 8 | 120s | 0.0% (0/30) | 33.3% (10/30) | +33.3 pp |

Raysurfer retrieval behavior on these runs:
- Mixed existing benchmarks: cache hit **97.5%**, cache review **92.5%**
- MBPP showcase: cache hit **93.3%**, cache review **90.0%**

Run artifacts:
- Mixed benchmark baseline: `runs/rotating_sdk_baseline.json`
- Mixed benchmark Raysurfer: `runs/rotating_sdk_raysurfer.json`
- Mixed benchmark summary: `runs/rotating_sdk_summary.json`
- MBPP showcase baseline: `runs/showcase_mbpp_rotating_baseline_t8_r3.json`
- MBPP showcase Raysurfer: `runs/showcase_mbpp_rotating_raysurfer_t8_r3.json`
- MBPP showcase summary: `runs/showcase_mbpp_rotating_summary_t8_r3.json`

## Secret Scanning

- CI gitleaks: `.github/workflows/gitleaks.yml`
- Local scan:

```bash
gitleaks detect --source . --config .gitleaks.toml
```
