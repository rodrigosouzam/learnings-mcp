"""Idempotently wire the learnings hooks + rules into ~/.claude on this machine.
Called by bootstrap.sh. Safe to re-run: never duplicates existing entries.

    python install_hooks.py <repo_dir>
"""
import json
import sys
from pathlib import Path


def main():
    repo = Path(sys.argv[1]).resolve()
    py = str(repo / ".venv" / "bin" / "python")
    cdir = Path.home() / ".claude"
    cdir.mkdir(parents=True, exist_ok=True)

    # ---- settings.json: SessionStart (core inject) + PreToolUse (guard) ----
    sp = cdir / "settings.json"
    cfg = {}
    if sp.exists():
        try:
            cfg = json.loads(sp.read_text())
        except Exception:
            cfg = {}
    hooks = cfg.setdefault("hooks", {})

    def has(event, needle):
        return any(needle in h.get("command", "")
                   for g in hooks.get(event, []) for h in g.get("hooks", []))

    if not has("SessionStart", "learnings_mcp.cli hook"):
        hooks.setdefault("SessionStart", []).append(
            {"hooks": [{"type": "command", "command": f"{py} -m learnings_mcp.cli hook"}]})
        print("+ SessionStart hook")
    if not has("PreToolUse", "learnings_mcp.guard"):
        hooks.setdefault("PreToolUse", []).append(
            {"matcher": "Bash",
             "hooks": [{"type": "command", "command": f"{py} -m learnings_mcp.guard"}]})
        print("+ PreToolUse guard hook")
    sp.write_text(json.dumps(cfg, indent=2))
    print(f"settings.json updated ({sp})")

    # ---- CLAUDE.md: append the rules once ----
    cm = cdir / "CLAUDE.md"
    rules = (repo / "rules" / "CLAUDE.md").read_text()
    existing = cm.read_text() if cm.exists() else ""
    if "# Learnings — institutional memory" not in existing:
        cm.write_text((existing.rstrip() + "\n\n" if existing.strip() else "") + rules)
        print(f"CLAUDE.md rules appended ({cm})")
    else:
        print(f"CLAUDE.md rules already present ({cm})")


if __name__ == "__main__":
    main()
