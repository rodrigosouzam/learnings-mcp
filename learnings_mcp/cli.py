"""
CLI for driving the store directly — seeding, inspection, core management, and the
SessionStart hook. Handy without going through an MCP client.

    learnings add    -t "Title" -c "Content" [--tags a,b] [--project p] [--workspace w] [--core] [--force]
    learnings search "query" [--workspace w] [--project p] [--limit 5]
    learnings list   [--workspace w] [--project p] [--tags a,b] [--limit 10]
    learnings enrich <id> -c "extra context"
    learnings core   [--workspace w]                 # list always-on learnings
    learnings core-set   <id>                        # mark a learning as core
    learnings core-unset <id>                        # unmark
    learnings rm     <id>
    learnings hook   [--workspace w]                 # emit SessionStart JSON (used by hook)
"""

from __future__ import annotations

import argparse
import json
import sys

from .store import Store
from .workspace import resolve_workspace


def _tags(val):
    if not val:
        return None
    return [t.strip() for t in val.split(",") if t.strip()]


def _core_block(store, workspace):
    items = store.core(workspace=workspace)
    if not items:
        return None
    ws = resolve_workspace(workspace)
    lines = [f"# Core learnings for workspace: {ws}", ""]
    for r in items:
        tagstr = ", ".join(r["tags"])
        proj = f" ({r['project']})" if r.get("project") else ""
        lines.append(f"- **{r['title']}**{proj} [{tagstr}]: {r['content']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="learnings", description="Personal learnings store")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="add a learning")
    p_add.add_argument("-t", "--title", required=True)
    p_add.add_argument("-c", "--content", required=True)
    p_add.add_argument("--tags")
    p_add.add_argument("--project")
    p_add.add_argument("--workspace")
    p_add.add_argument("--source", default="manual")
    p_add.add_argument("--core", action="store_true", help="mark as always-on core learning")
    p_add.add_argument("--force", action="store_true", help="create even if a near-duplicate exists")
    p_add.add_argument("--reference", help="provenance note (e.g. ticket, incident, what prompted it)")

    p_search = sub.add_parser("search", help="semantic search")
    p_search.add_argument("query")
    p_search.add_argument("--workspace")
    p_search.add_argument("--project")
    p_search.add_argument("--limit", type=int, default=5)

    p_list = sub.add_parser("list", help="list learnings")
    p_list.add_argument("--workspace")
    p_list.add_argument("--project")
    p_list.add_argument("--tags")
    p_list.add_argument("--limit", type=int, default=10)

    p_enrich = sub.add_parser("enrich", help="append context to a learning")
    p_enrich.add_argument("id")
    p_enrich.add_argument("-c", "--content", required=True)
    p_enrich.add_argument("--reference", help="provenance note for this enrichment")

    p_core = sub.add_parser("core", help="list always-on core learnings")
    p_core.add_argument("--workspace")

    p_cset = sub.add_parser("core-set", help="mark a learning as core")
    p_cset.add_argument("id")
    p_cunset = sub.add_parser("core-unset", help="unmark a core learning")
    p_cunset.add_argument("id")

    p_rm = sub.add_parser("rm", help="delete a learning")
    p_rm.add_argument("id")

    p_rev = sub.add_parser("review", help="show curation candidates (stale / never-used)")
    p_rev.add_argument("--workspace")

    p_ver = sub.add_parser("verify", help="mark a learning re-verified now")
    p_ver.add_argument("id")

    p_bak = sub.add_parser("backup", help="write a timestamped copy of the database")
    p_bak.add_argument("--dir")

    p_hook = sub.add_parser("hook", help="emit SessionStart JSON with core learnings")
    p_hook.add_argument("--workspace")

    p_scan = sub.add_parser("scan", help="find candidate knowledge sources under a path")
    p_scan.add_argument("path")
    p_scan.add_argument("--limit", type=int, default=200)

    p_ing = sub.add_parser("ingest", help="batch-add proposed learnings from JSON (dry-run by default)")
    p_ing.add_argument("file")
    p_ing.add_argument("--workspace", help="default workspace for entries that omit one")
    p_ing.add_argument("--apply", action="store_true", help="actually write the NEW ones")

    p_eval = sub.add_parser("eval", help="run the retrieval eval harness")
    p_eval.add_argument("file", nargs="?")

    p_exp = sub.add_parser("export", help="export learnings to a git repo of markdown files")
    p_exp.add_argument("dir", nargs="?")
    p_imp = sub.add_parser("import", help="import/upsert learnings from the repo")
    p_imp.add_argument("dir", nargs="?")
    p_syn = sub.add_parser("sync", help="export → commit → pull --rebase → import → push")
    p_syn.add_argument("dir", nargs="?")
    p_syn.add_argument("-m", "--message", default="learnings sync")

    p_ui = sub.add_parser("ui", help="launch the local web UI")
    p_ui.add_argument("--port", type=int, default=8765)
    p_ui.add_argument("--host", default="127.0.0.1",
                      help="bind address; use 0.0.0.0 to reach it from your LAN (a token is auto-required)")
    p_ui.add_argument("--token", help="require this access token (auto-generated for non-loopback binds)")
    p_ui.add_argument("--no-browser", action="store_true")

    args = parser.parse_args(argv)

    # The hook must never break a session: swallow everything and exit 0.
    if args.cmd == "hook":
        # Capture this session's provenance from the SessionStart payload on stdin.
        try:
            if not sys.stdin.isatty():
                from .store import write_session
                payload = json.loads(sys.stdin.read() or "{}")
                write_session({
                    "session": payload.get("session_id"),
                    "transcript": payload.get("transcript_path"),
                    "cwd": payload.get("cwd"),
                })
        except Exception:
            pass
        try:
            store = Store()
            block = _core_block(store, args.workspace)
            if block:
                print(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": block,
                    }
                }))
        except Exception:
            pass
        return 0

    if args.cmd == "ui":
        from .ui import serve
        serve(port=args.port, host=args.host, token=args.token,
              open_browser=not args.no_browser)
        return 0

    if args.cmd == "scan":
        from .ingest import scan
        found = scan(args.path, limit=args.limit)
        for f in found:
            print(f"{f['kb']:>7} KB  {f['path']}\n            {f['head']}")
        print(f"\n{len(found)} candidate source documents")
        return 0

    if args.cmd == "ingest":
        from .ingest import load_batch, plan, apply
        planned = plan(load_batch(args.file), workspace=args.workspace)
        for it in planned:
            r, m = it["record"], it["match"]
            if it["status"] == "new":
                print(f"  NEW  [{r['workspace']}] {r['title']}")
            else:
                print(f"  DUP  [{r['workspace']}] {r['title']}\n       ↳ matches: {m['title']} (d={m['distance']:.3f}, id={m['id']})")
        n_new = sum(1 for i in planned if i["status"] == "new")
        print(f"\n{n_new} new, {len(planned) - n_new} duplicate(s)")
        if args.apply:
            print("applied:", apply(planned))
        else:
            print("dry run — re-run with --apply to write the NEW ones")
        return 0

    if args.cmd == "eval":
        from .evaluate import load_cases, run, format_report
        print(format_report(run(load_cases(args.file))))
        return 0

    if args.cmd == "export":
        from .sync import export_to_dir, sync_dir
        n = export_to_dir(args.dir)
        print(f"exported {n} learnings to {sync_dir(args.dir)}")
        return 0
    if args.cmd == "import":
        from .sync import import_from_dir
        print(import_from_dir(args.dir))
        return 0
    if args.cmd == "sync":
        from .sync import sync
        print(sync(args.dir, args.message))
        return 0

    store = Store()

    if args.cmd == "add":
        res = store.create(
            title=args.title, content=args.content, tags=_tags(args.tags),
            project=args.project, workspace=args.workspace, source=args.source,
            is_core=args.core, force=args.force, reference=args.reference,
        )
        if res["status"] == "duplicate":
            m = res["match"]
            print(f"near-duplicate exists (distance {m['distance']:.3f}): {m['id']} — {m['title']}")
            print("use `enrich` to extend it, or re-run with --force")
        else:
            r = res["learning"]
            print(f"created {r['id']} in {r['workspace']}{' [core]' if r['is_core'] else ''}: {r['title']}")

    elif args.cmd == "search":
        results = store.search(args.query, project=args.project, workspace=args.workspace, limit=args.limit)
        if not results:
            print(f"No relevant learnings found in '{resolve_workspace(args.workspace)}' (or base).")
        for i, r in enumerate(results):
            d = r.get("distance")
            tag = f"{d:.3f}" if d is not None else "kw"
            print(f"{i + 1}. [{tag}] {r['title']}  ({r['workspace']}/{r.get('project') or 'general'})  id={r['id']}")

    elif args.cmd == "list":
        results = store.list(tags=_tags(args.tags), project=args.project, workspace=args.workspace, limit=args.limit)
        for i, r in enumerate(results):
            core = " ★" if r["is_core"] else ""
            print(f"{i + 1}. {r['title']}{core}  [{', '.join(r['tags'])}]  ({r['workspace']}/{r.get('project') or 'general'})  id={r['id']}")

    elif args.cmd == "enrich":
        r = store.enrich(args.id, args.content, reference=args.reference)
        print(json.dumps(r, indent=2) if r else f"no learning with id {args.id}")

    elif args.cmd == "core":
        block = _core_block(store, args.workspace)
        print(block if block else f"No core learnings for '{resolve_workspace(args.workspace)}'.")

    elif args.cmd in ("core-set", "core-unset"):
        try:
            r = store.set_core(args.id, args.cmd == "core-set")
        except ValueError as e:
            print(f"error: {e}")
            return 1
        print(f"{'cored' if args.cmd == 'core-set' else 'un-cored'}: {r['title']}" if r else f"no learning with id {args.id}")

    elif args.cmd == "rm":
        print("deleted" if store.remove(args.id) else "not found")

    elif args.cmd == "review":
        r = store.review(workspace=args.workspace)
        print(f"STALE ({len(r['stale'])}) — verified long ago, may be outdated:")
        for x in r["stale"]:
            print(f"  · {x['title']}  ({x['workspace']}, verified {x['verified_at'][:10]})  id={x['id']}")
        print(f"\nCOLD ({len(r['cold'])}) — never surfaced by search & not new:")
        for x in r["cold"]:
            print(f"  · {x['title']}  ({x['workspace']}, {x['created_at'][:10]})  id={x['id']}")

    elif args.cmd == "verify":
        r = store.verify(args.id)
        print(f"verified: {r['title']}" if r else f"no learning with id {args.id}")

    elif args.cmd == "backup":
        b = store.backup(args.dir)
        print(f"backup written: {b['path']} ({b['bytes'] // 1024} KB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
