#!/usr/bin/env python3
"""Generate SVG chart from benchmark comparison summary JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Generate consistency comparison SVG")
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def format_pct(value: float) -> str:
    """Format fractional value as percent string."""
    return f"{value * 100:.1f}%"


def main() -> None:
    """Build SVG chart from scorer output."""
    args = parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))

    raysurfer = summary.get("raysurfer")
    baseline = summary.get("baseline")
    if not isinstance(raysurfer, dict) or not isinstance(baseline, dict):
        raise ValueError("summary must include both 'raysurfer' and 'baseline'")

    rs_consistency = float(raysurfer["overall_consistency"])
    base_consistency = float(baseline["overall_consistency"])
    delta = float(baseline.get("delta", rs_consistency - base_consistency))
    task_count = int(summary.get("task_count", 0))

    width = 980
    height = 560
    chart_x = 120
    chart_y = 120
    chart_w = 740
    chart_h = 300
    bar_w = 220

    base_bar_h = int(chart_h * base_consistency)
    rs_bar_h = int(chart_h * rs_consistency)

    base_x = chart_x + 110
    rs_x = chart_x + 410
    base_y = chart_y + chart_h - base_bar_h
    rs_y = chart_y + chart_h - rs_bar_h

    title = "Existing Benchmarks: 3-Minute Consistency"
    subtitle = f"{task_count}-task run from public benchmark datasets"

    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>
  <defs>
    <linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>
      <stop offset='0%' stop-color='#f8fafc'/>
      <stop offset='100%' stop-color='#ecfeff'/>
    </linearGradient>
    <linearGradient id='barRs' x1='0' y1='0' x2='0' y2='1'>
      <stop offset='0%' stop-color='#0ea5e9'/>
      <stop offset='100%' stop-color='#0284c7'/>
    </linearGradient>
    <linearGradient id='barBase' x1='0' y1='0' x2='0' y2='1'>
      <stop offset='0%' stop-color='#a8a29e'/>
      <stop offset='100%' stop-color='#78716c'/>
    </linearGradient>
  </defs>

  <rect x='0' y='0' width='{width}' height='{height}' fill='url(#bg)'/>

  <text x='60' y='58' font-family='Helvetica, Arial, sans-serif' font-size='34' font-weight='700' fill='#0f172a'>{title}</text>
  <text x='60' y='92' font-family='Helvetica, Arial, sans-serif' font-size='18' fill='#334155'>{subtitle}</text>

  <line x1='{chart_x}' y1='{chart_y + chart_h}' x2='{chart_x + chart_w}' y2='{chart_y + chart_h}' stroke='#334155' stroke-width='2'/>
  <line x1='{chart_x}' y1='{chart_y}' x2='{chart_x}' y2='{chart_y + chart_h}' stroke='#334155' stroke-width='2'/>

  <text x='{chart_x - 40}' y='{chart_y + chart_h + 6}' font-family='Helvetica, Arial, sans-serif' font-size='14' fill='#475569'>0%</text>
  <text x='{chart_x - 48}' y='{chart_y + chart_h * 0.75 + 6}' font-family='Helvetica, Arial, sans-serif' font-size='14' fill='#475569'>25%</text>
  <text x='{chart_x - 48}' y='{chart_y + chart_h * 0.5 + 6}' font-family='Helvetica, Arial, sans-serif' font-size='14' fill='#475569'>50%</text>
  <text x='{chart_x - 48}' y='{chart_y + chart_h * 0.25 + 6}' font-family='Helvetica, Arial, sans-serif' font-size='14' fill='#475569'>75%</text>
  <text x='{chart_x - 55}' y='{chart_y + 6}' font-family='Helvetica, Arial, sans-serif' font-size='14' fill='#475569'>100%</text>

  <rect x='{base_x}' y='{base_y}' width='{bar_w}' height='{base_bar_h}' rx='12' fill='url(#barBase)'/>
  <rect x='{rs_x}' y='{rs_y}' width='{bar_w}' height='{rs_bar_h}' rx='12' fill='url(#barRs)'/>

  <text x='{base_x + bar_w / 2}' y='{base_y - 12}' text-anchor='middle' font-family='Helvetica, Arial, sans-serif' font-size='22' font-weight='700' fill='#1e293b'>{format_pct(base_consistency)}</text>
  <text x='{rs_x + bar_w / 2}' y='{rs_y - 12}' text-anchor='middle' font-family='Helvetica, Arial, sans-serif' font-size='22' font-weight='700' fill='#0c4a6e'>{format_pct(rs_consistency)}</text>

  <text x='{base_x + bar_w / 2}' y='{chart_y + chart_h + 34}' text-anchor='middle' font-family='Helvetica, Arial, sans-serif' font-size='18' fill='#334155'>Baseline</text>
  <text x='{rs_x + bar_w / 2}' y='{chart_y + chart_h + 34}' text-anchor='middle' font-family='Helvetica, Arial, sans-serif' font-size='18' fill='#0c4a6e'>With Raysurfer</text>

  <rect x='60' y='470' width='860' height='58' rx='12' fill='#082f49'/>
  <text x='86' y='506' font-family='Helvetica, Arial, sans-serif' font-size='23' font-weight='700' fill='#f8fafc'>Consistency uplift within 3 minutes: {format_pct(delta)}</text>
</svg>
"""

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(svg, encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
