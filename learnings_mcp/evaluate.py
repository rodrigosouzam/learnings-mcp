"""
Retrieval eval harness — measures whether real questions surface the right learning.

Run it after tuning search (RRF weights, dup threshold) or swapping the embedding model
to catch regressions:

    learnings eval                 # uses evals/retrieval.json
    learnings eval mycases.json

Case format: [{"query": "...", "expect": "<substring of the expected title>",
               "workspace": "project-a"}]
Reports hit@1, hit@3 and MRR.
"""

from __future__ import annotations

import json
from pathlib import Path

from .store import Store

_EVALS = Path(__file__).resolve().parent.parent / "evals"
# Prefer your local (gitignored) cases; fall back to the committed generic example.
DEFAULT_CASES = _EVALS / "retrieval.json"
EXAMPLE_CASES = _EVALS / "retrieval.example.json"


def load_cases(file=None) -> list[dict]:
    if file:
        p = Path(file).expanduser()
    else:
        p = DEFAULT_CASES if DEFAULT_CASES.exists() else EXAMPLE_CASES
    if not p.exists():
        raise FileNotFoundError(f"no eval cases at {p}")
    return json.loads(p.read_text())


def run(cases, limit=5) -> dict:
    store = Store()
    rows = []
    for c in cases:
        res = store.search(c["query"], workspace=c.get("workspace"), limit=limit)
        titles = [r["title"] for r in res]
        exp = c["expect"].lower()
        rank = next((i + 1 for i, t in enumerate(titles) if exp in t.lower()), None)
        rows.append({"query": c["query"], "expect": c["expect"], "rank": rank,
                     "top": titles[0] if titles else None})
    store.close()
    n = len(rows) or 1
    return {
        "cases": rows,
        "n": len(rows),
        "hit@1": round(sum(1 for r in rows if r["rank"] == 1) / n, 3),
        "hit@3": round(sum(1 for r in rows if r["rank"] and r["rank"] <= 3) / n, 3),
        "mrr": round(sum(1 / r["rank"] for r in rows if r["rank"]) / n, 3),
    }


def format_report(res: dict) -> str:
    lines = []
    for c in res["cases"]:
        mark = "✓" if c["rank"] == 1 else ("~" if c["rank"] and c["rank"] <= 3 else "✗")
        pos = f"#{c['rank']}" if c["rank"] else "miss"
        lines.append(f"  {mark} [{pos:>4}] {c['query'][:52]:52}  → {c['top'] or '-'}")
    lines.append("")
    lines.append(f"  {res['n']} cases | hit@1 {res['hit@1']:.0%} | hit@3 {res['hit@3']:.0%} | MRR {res['mrr']}")
    return "\n".join(lines)
