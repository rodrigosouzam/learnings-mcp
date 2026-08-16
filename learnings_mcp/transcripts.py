"""
Locate and condense Claude Code session transcripts for a directory.

Claude Code stores each session at ~/.claude/projects/<cwd-with-slashes-as-dashes>/
<session-id>.jsonl. This maps a project directory to its past conversations and
condenses the noisy JSONL into a readable narrative (your messages + Claude's replies +
summarized tool calls + truncated tool results) so a session can be mined for learnings.

No LLM here — this is the deterministic feeder for the `/mine-project` flow.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"


def _folder(dir_path) -> Path | None:
    encoded = str(Path(dir_path).expanduser().resolve()).replace("/", "-")
    f = PROJECTS / encoded
    if f.exists():
        return f
    # Fallback: some encodings also replace '.'/'_' — match by suffix.
    if PROJECTS.exists():
        for cand in PROJECTS.iterdir():
            if cand.is_dir() and cand.name.replace("_", "-").replace(".", "-") == encoded.replace("_", "-").replace(".", "-"):
                return cand
    return None


def list_sessions(dir_path) -> list[dict]:
    folder = _folder(dir_path)
    if not folder:
        return []
    out = []
    for jf in sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        first_user, nmsg = "", 0
        try:
            with jf.open(errors="replace") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    t = d.get("type")
                    if t in ("user", "assistant"):
                        nmsg += 1
                    if t == "user" and not first_user:
                        c = (d.get("message") or {}).get("content")
                        if isinstance(c, str) and not c.startswith("<"):
                            first_user = c.strip().replace("\n", " ")[:80]
        except OSError:
            continue
        st = jf.stat()
        out.append({
            "session": jf.stem,
            "when": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "kb": st.st_size // 1024,
            "messages": nmsg,
            "title": first_user or "(no user text)",
        })
    return out


def condense(dir_path, session, max_result=160) -> str | None:
    folder = _folder(dir_path)
    if not folder:
        return None
    jf = folder / f"{session}.jsonl"
    if not jf.exists():
        return None
    lines = []
    with jf.open(errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type")
            c = (d.get("message") or {}).get("content")
            if t == "user":
                if isinstance(c, str):
                    if c.startswith("<"):  # skip system reminders / meta
                        continue
                    lines.append(f"USER: {c.strip()}")
                elif isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            rc = b.get("content")
                            txt = rc if isinstance(rc, str) else json.dumps(rc)
                            txt = (txt or "").replace("\n", " ")[:max_result]
                            lines.append(f"  ⟶ {txt}")
            elif t == "assistant" and isinstance(c, list):
                for b in c:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        tx = b.get("text", "").strip()
                        if tx:
                            lines.append(f"CLAUDE: {tx}")
                    elif b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        arg = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                        lines.append(f"  [{b.get('name')}: {str(arg)[:100]}]")
    return "\n".join(lines)
