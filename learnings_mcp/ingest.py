"""
Batch ingest + source discovery — the onboarding path for a new project workspace.

Typical flow when starting a new project (e.g. project-c):

  1. learnings scan ~/project-c            # find candidate knowledge sources
  2. (Claude reads them and writes drafts.json — a list of proposed learnings)
  3. learnings ingest drafts.json          # DRY RUN: shows NEW vs near-DUPLICATE
  4. learnings ingest drafts.json --apply  # writes only the NEW ones

Dry-run by default so a bad batch can never silently pollute the knowledge base.

drafts.json format — a list of objects:
  [{"title": "...", "content": "...", "tags": ["a"], "workspace": "project-c",
    "project": "repo-name", "is_core": false}]
"""

from __future__ import annotations

import json
from pathlib import Path

from .store import Store
from .workspace import resolve_workspace

# Files worth reading when onboarding a project.
DOC_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".adoc"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".terraform",
             ".terragrunt-cache", "dist", "build", ".cache"}


def scan(path, limit=200) -> list[dict]:
    """Find candidate knowledge sources (runbooks, docs, READMEs) under a path."""
    root = Path(path).expanduser()
    out = []
    for p in sorted(root.rglob("*")):
        if len(out) >= limit:
            break
        if not p.is_file() or p.suffix.lower() not in DOC_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            head = ""
            with p.open("r", errors="replace") as fh:
                for line in fh:
                    if line.strip():
                        head = line.strip()[:90]
                        break
            out.append({"path": str(p), "kb": round(p.stat().st_size / 1024, 1), "head": head})
        except OSError:
            continue
    return out


def load_batch(file) -> list[dict]:
    data = json.loads(Path(file).expanduser().read_text())
    if isinstance(data, dict):
        data = data.get("learnings", [])
    if not isinstance(data, list):
        raise ValueError("expected a JSON list of learnings")
    for r in data:
        if not r.get("title") or not r.get("content"):
            raise ValueError(f"every learning needs title+content; bad entry: {str(r)[:80]}")
    return data


def plan(records, workspace=None) -> list[dict]:
    """Classify each proposed learning as NEW or DUPLICATE (no writes)."""
    store = Store()
    out = []
    for r in records:
        ws = resolve_workspace(r.get("workspace") or workspace)
        dup = store.find_duplicate(
            title=r["title"], content=r["content"], tags=r.get("tags"), workspace=ws
        )
        out.append({"record": {**r, "workspace": ws},
                    "status": "duplicate" if dup else "new",
                    "match": dup})
    store.close()
    return out


def apply(planned) -> dict:
    """Create the NEW ones; skip duplicates (enrich those by hand if wanted)."""
    store = Store()
    stats = {"created": 0, "skipped": 0, "failed": 0}
    for item in planned:
        if item["status"] != "new":
            stats["skipped"] += 1
            continue
        r = item["record"]
        try:
            res = store.create(
                title=r["title"], content=r["content"], tags=r.get("tags"),
                project=r.get("project"), workspace=r.get("workspace"),
                is_core=bool(r.get("is_core")), source=r.get("source") or "ingest",
            )
            stats["created" if res["status"] == "created" else "skipped"] += 1
        except Exception:
            stats["failed"] += 1
    store.close()
    return stats
