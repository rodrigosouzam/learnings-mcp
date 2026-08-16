"""
PreToolUse guard — a deterministic safety net under the traffic-light rule.

Reads the Bash tool call on stdin; if the command matches a genuinely dangerous
pattern (destructive, or mutating a production target), it returns permissionDecision
"ask" so Claude Code prompts the user to confirm before running. Everything else falls
through untouched (normal permission flow).

FAIL-OPEN: any error → no output, exit 0. A broken guard must never block your work.

Wire via ~/.claude/settings.json:
    "PreToolUse": [{ "matcher": "Bash", "hooks": [
        { "type": "command",
          "command": ".../.venv/bin/python -m learnings_mcp.guard" } ] }]
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def _load_config():
    """Prod-target + destructive-script patterns. Generic defaults; override per-machine
    via <db-dir>/guard.json  {"prod_pattern": "...", "danger_scripts": "..."}  so no
    project-specific identifiers ever live in this code."""
    prod = r"\bprod\b|\bprod-|\bproduction\b|--context[=\s]+[\"']?prod"
    danger = ""
    try:
        raw = os.environ.get("LEARNINGS_DB_PATH")
        base = Path(raw).expanduser().parent if raw else (Path.home() / ".learnings")
        f = base / "guard.json"
        if f.exists():
            d = json.loads(f.read_text())
            prod = d.get("prod_pattern") or prod
            danger = d.get("danger_scripts") or danger
    except Exception:
        pass
    return re.compile(prod, re.I), (re.compile(danger, re.I) if danger else None)


# A production target anywhere in the command escalates mutating ops.
PROD, DANGER_SCRIPTS = _load_config()

KUBECTL = re.compile(r"\bkubectl\b", re.I)
KUBECTL_MUTATE = re.compile(
    r"\b(apply|patch|replace|edit|scale|drain|cordon|uncordon|rollout|set|annotate|label|create)\b",
    re.I,
)
FORCE_DELETE = re.compile(r"--force\b.*--grace-period[=\s]*0|--grace-period[=\s]*0.*--force", re.I | re.S)


def _reason(cmd: str) -> str | None:
    """Return a confirmation reason if the command is high-risk, else None."""
    low = cmd.lower()
    prod = bool(PROD.search(cmd))
    hits: list[str] = []

    # kubectl
    if KUBECTL.search(cmd):
        if re.search(r"\bdelete\b", low):
            hits.append("kubectl delete" + (" on PROD" if prod else ""))
        elif prod and KUBECTL_MUTATE.search(cmd):
            hits.append("mutating kubectl on PROD")
        if FORCE_DELETE.search(cmd):
            hits.append("force delete (--grace-period=0) — can wedge the node")

    # terraform / terragrunt
    if re.search(r"\b(terraform|terragrunt)\b", low):
        if re.search(r"\bdestroy\b", low):
            hits.append("terraform/terragrunt destroy")
        elif re.search(r"\bapply\b", low) and prod:
            hits.append("terraform/terragrunt apply on PROD")

    # helm
    if re.search(r"\bhelm\b", low):
        if re.search(r"\b(uninstall|delete|rollback)\b", low):
            hits.append("helm uninstall/rollback")
        elif re.search(r"\bupgrade\b", low) and prod:
            hits.append("helm upgrade on PROD")

    # azure destructive
    if re.search(r"\baz\b", low) and re.search(r"\b(delete|purge)\b", low):
        hits.append("az delete/purge")
    if re.search(r"\baz\b.*flexible-server\b.*\bstop\b", low):
        hits.append("stopping an Azure Postgres server")

    # raw shell / db / git
    if re.search(r"\brm\s+-\S*(rf|fr)\b|\brm\s+-[a-z]*r[a-z]*\s+-[a-z]*f|\brm\s+.*--force.*-r", low):
        hits.append("rm -rf")
    if re.search(r"\bdropdb\b|drop\s+database\b|drop\s+table\b", low):
        hits.append("dropping a database/table")
    if re.search(r"git\s+push\b", low) and (
        re.search(r"--force\b|\s-f\b|\+\w", low) or re.search(r"\b(main|master)\b", low)
    ):
        hits.append("git push to main / force push")

    # project-specific destructive scripts (configured per-machine via guard.json)
    if DANGER_SCRIPTS and DANGER_SCRIPTS.search(cmd):
        hits.append("known-destructive project script")

    if not hits:
        return None
    return "🔴 " + "; ".join(dict.fromkeys(hits)) + ". Confirm before running."


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        if data.get("tool_name") != "Bash":
            return 0
        cmd = (data.get("tool_input") or {}).get("command", "")
        if not cmd:
            return 0
        reason = _reason(cmd)
        if reason:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                }
            }))
    except Exception:
        return 0  # fail-open
    return 0


if __name__ == "__main__":
    sys.exit(main())
