---
description: Mine a project directory's past Claude sessions for learnings, reviewing each one interactively
argument-hint: [directory]
---

You are mining **past Claude Code sessions** for reusable learnings to add to the
knowledge base. Target directory: **$ARGUMENTS** (if empty, use the current working directory).

Work through these steps:

1. **List sessions.** Run `learnings transcripts <dir>` to list the past sessions for that
   directory (date, id, first message). Show the list and ask the user which to mine — one,
   a few, or "all". Start with one they pick unless they say otherwise.

2. **Read the chosen session.** Run `learnings transcripts <dir> --show <session-id>` to get
   the condensed transcript. If it's very large, read it in parts (grep/head for the
   interesting bits: errors that got resolved, "that worked", decisions, gotchas).

3. **Extract candidates**, applying the test *"could I get this from a web search / the docs
   right now?"*:
   - KEEP: environment & architecture facts, decisions + rationale, incidents
     (symptom → root cause → the fix that worked *here*), non-obvious gotchas specific to
     this setup, who-owns-what.
   - SKIP: generic tool syntax, standard procedures, anything googleable.
   - Prefer a durable insight + a pointer to the source over pasting perishable specifics.

4. **Review each candidate ONE AT A TIME.** For each, present:
   > **Found:** <title>
   > <one–three sentence content>
   > tags: <…> · workspace: <auto-derived from the directory>
   >
   > Include this? (**yes** / **no** / **edit**)

   Then WAIT for the answer before moving on.
   - **yes** → first call `search_learnings` to check for a near-duplicate. If a close match
     exists, offer to `enrich_learning` it instead. Otherwise `create_learning` with
     `reference: "mined from session <session-id>"`.
   - **edit** → apply their changes, confirm, then save.
   - **no** → skip it.

5. **Summary.** Report how many were added, enriched, and skipped.

Rules: never save secrets (the server also redacts as a safety net). Keep each learning
concise. Never create or enrich anything without the user's explicit **yes**.
