"""
Git-backed sync: export learnings to a git repo of Markdown files and back.

The local SQLite DB is the source of truth; the repo is the human-readable, versioned
interchange format for moving learnings between machines (and, later, sharing with a
team). One file per learning at <repo>/<workspace>/<id>.md.

Not synced: embeddings (re-derived on import) and usage stats (hit_count/last_used —
machine-local). Synced: the knowledge + curation flags (is_core, verified_at).

    learnings export [dir]
    learnings import [dir]
    learnings sync   [dir] [-m msg]     # export → commit → pull --rebase → import → push
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .store import Store

SCALARS = ["id", "title", "workspace", "project", "source", "created_at", "verified_at"]
CONTEXT_MARKER = "\n<!--CONTEXT-->\n"


def sync_dir(d=None) -> Path:
    raw = d or os.environ.get("LEARNINGS_SYNC_DIR") or (Path.home() / ".learnings" / "repo")
    return Path(raw).expanduser()


def _serialize(r: dict) -> str:
    fm = ["---"]
    for k in SCALARS:
        fm.append(f"{k}: {r.get(k) or ''}")
    fm.append(f"tags: {json.dumps(r.get('tags') or [])}")
    fm.append(f"is_core: {str(bool(r.get('is_core'))).lower()}")
    fm.append(f"references: {json.dumps(r.get('references') or [])}")
    fm.append("---")
    body = r.get("content") or ""
    if r.get("context"):
        body += CONTEXT_MARKER + r["context"]
    return "\n".join(fm) + "\n\n" + body + "\n"


def _parse(text: str) -> dict:
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    _, fm, body = text.split("---\n", 2)
    rec: dict = {}
    for line in fm.strip().splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
        elif line.endswith(":"):
            k, v = line[:-1], ""
        else:
            continue
        rec[k.strip()] = v
    rec["tags"] = json.loads(rec.get("tags") or "[]")
    rec["references"] = json.loads(rec.get("references") or "[]")
    rec["is_core"] = str(rec.get("is_core", "")).strip().lower() == "true"
    for k in ("project", "source", "verified_at"):
        if rec.get(k) == "":
            rec[k] = None
    body = body.lstrip("\n")
    if CONTEXT_MARKER.strip() in body:
        content, ctx = body.split(CONTEXT_MARKER, 1)
        rec["content"] = content.rstrip("\n")
        rec["context"] = ctx.strip()
    else:
        rec["content"] = body.rstrip("\n")
        rec["context"] = None
    return rec


def export_to_dir(d=None) -> int:
    root = sync_dir(d)
    root.mkdir(parents=True, exist_ok=True)
    store = Store()
    recs = store.all_records()
    written = set()
    for r in recs:
        wsdir = root / r["workspace"]
        wsdir.mkdir(parents=True, exist_ok=True)
        (wsdir / f"{r['id']}.md").write_text(_serialize(r))
        written.add(r["id"])
    # reflect local deletions: drop files whose learning no longer exists
    for p in root.glob("*/*.md"):
        if p.stem not in written:
            p.unlink()
    store.close()
    return len(recs)


def import_from_dir(d=None) -> dict:
    root = sync_dir(d)
    store = Store()
    stats = {"created": 0, "updated": 0, "unchanged": 0, "error": 0}
    for p in sorted(root.glob("*/*.md")):
        try:
            stats[store.upsert(_parse(p.read_text()))] += 1
        except Exception:
            stats["error"] += 1
    store.close()
    return stats


def _git(root: Path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=check
    )


def sync(d=None, message="learnings sync") -> str:
    root = sync_dir(d)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        _git(root, "init")
        _git(root, "add", "-A", check=False)

    n = export_to_dir(root)
    _git(root, "add", "-A", check=False)
    dirty = _git(root, "status", "--porcelain", check=False).stdout.strip()
    if dirty:
        _git(root, "commit", "-m", message, check=False)

    has_remote = bool(_git(root, "remote", check=False).stdout.strip())
    out = [f"exported {n} learnings"]
    if has_remote:
        pull = _git(root, "pull", "--rebase", check=False)
        if pull.returncode != 0:
            return (
                f"exported {n}, committed locally, but `git pull --rebase` failed:\n"
                f"{pull.stderr.strip()}\n\nResolve conflicts in {root}, then run "
                "`learnings import` and `git -C <dir> push`."
            )
        out.append("pulled remote")

    stats = import_from_dir(root)
    out.append(f"imported (created {stats['created']}, updated {stats['updated']}, "
               f"unchanged {stats['unchanged']}, errors {stats['error']})")

    if has_remote:
        push = _git(root, "push", check=False)
        out.append("pushed" if push.returncode == 0 else f"push failed: {push.stderr.strip()}")
    else:
        out.append("no git remote configured (local versioning only)")
    return f"{root}: " + "; ".join(out)
