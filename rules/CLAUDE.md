# Learnings — institutional memory (auto-use)

You have a `learnings` MCP server: a per-project knowledge base of past solutions,
patterns, and lessons. Use it proactively and silently — do not wait to be asked.

## Workspaces (project isolation)

Learnings are isolated per project **workspace**, auto-derived from the working
directory (`~/project-a/...` → `project-a`, `~/project-b/...` → `project-b`,
`~/project-c/...` → `project-c`, else `personal`). Search and list are scoped to the
current workspace plus a shared `base` workspace. **Never** pass a different
workspace to reach another project's learnings — the boundary is intentional
(confidentiality across projects). Only override `workspace` if the user explicitly asks.

Core learnings for the current workspace are injected at session start, so you already
have the ground rules; you rarely need to call `get_core_learnings` yourself.

## Search BEFORE acting

Before any non-trivial action, call `search_learnings` first. Non-trivial includes:
creating/naming branches; architectural or file-layout decisions; anything touching
deploy/CI/infra; debugging an error (search the error text or the technology);
adding a field/feature/endpoint; DB schema or migrations; auth/permissions/security;
setting up tests. When in doubt, search.

- Prefer 2–3 short, targeted queries over one long one, covering different angles.
- Pass `project` (the repo name) to boost same-repo learnings.
- If a learning contradicts your default approach, follow the learning.
- Do not announce that you searched — just apply what you find.

## Suggest saving AFTER solving

After resolving a non-trivial problem, briefly offer to save it as a learning.
Suggest when: a bug took several attempts; you found a workaround for a tool/service
limitation; you discovered undocumented behavior; a non-obvious setup/config step;
a decision future-you would forget; a pattern worth reusing.

**What belongs — the test is "could I get this from a web search or the official docs
right now?"**
- Store what you CAN'T google: environment/architecture facts, decisions and their
  rationale, incidents (symptom → root cause → the fix that worked *here*), non-obvious
  gotchas specific to this setup, and who-owns-what. Incident fixes may include commands
  — that's fine, the value is the environment-specific diagnosis.
- Do NOT store what you CAN google: generic tool syntax, standard procedures, API steps.
  They drift with versions; fetch them fresh instead.
- Prefer the durable insight + a pointer to the living source over pasting perishable
  specifics (e.g. "node-pool sizes live in `terraform-aks-infra/aks-module`") so entries
  don't go stale.

When the user agrees:
1. Call `search_learnings` first to check for a near-duplicate.
2. If a similar one exists → `enrich_learning(id, context)`.
3. Otherwise → `create_learning(title, content, tags, project)`. The tool refuses
   near-duplicates automatically and points you to the one to enrich instead.

Keep it to one short suggestion per solved problem. Don't suggest for trivial fixes
(typos, obvious one-liners). If declined, drop it and move on. Never store secrets or
credentials in a learning (the server also redacts them as a safety net).

# Signal the risk of every command (traffic light)

Before running or proposing a shell/infra command, label its impact so the risk is
visible at a glance. Scale the ceremony to the risk level:

- 🟢 **Safe / read-only** — no state change, trivially reversible (get/list/describe/
  cat/grep, `terraform plan`, `git status`/`diff`, searches, dry-runs). Just run it; a
  🟢 marker is enough — no confirmation needed.
- 🟡 **Caution — may impact something** — mutating but limited/reversible, or the blast
  radius is unclear (restart/scale a pod, edit a ConfigMap, create resources, apply to a
  **dev** env, write a non-trivial file, `git push` a branch). Label 🟡, say what it
  touches and the likely effect, and proceed carefully — if the impact is unclear or the
  target is shared, ask first.
- 🔴 **Important / destructive / production** — hard to reverse or affects prod / project
  data (`kubectl delete`, `terraform apply` on prod, DB drop/restore, a destructive
  setup/reset script, deleting cloud resources, secret rotation, `--force`, pushing to
  `main`). Label 🔴,
  spell out the blast radius, and **get explicit confirmation before executing** — never
  run these unprompted.

Use the learnings to classify: if a learning flags something as destructive, treat it as
🔴. When in doubt, pick the higher risk level.
