"""
Local store for learnings: SQLite for records + sqlite-vec (vec0) for vector KNN.

Single-file database, no server. Default location ~/.learnings/learnings.db,
overridable with LEARNINGS_DB_PATH.

v2: workspace isolation (per project) + a shared "base" workspace mixed into every
search, "core" learnings (always-on, capped), and near-duplicate detection on create.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import sqlite_vec

from .embeddings import EMBED_DIM, embed, learning_text
from .redact import redact_text
from .workspace import BASE_WORKSPACE, resolve_workspace

# A learning matching the active `project` gets this subtracted from its distance,
# nudging same-project knowledge up the ranking (a fine-grained boost within a workspace).
PROJECT_BOOST = 0.05

# Two learnings closer than this (L2 on normalized vectors) are treated as near-duplicates.
DUP_DISTANCE = float(os.environ.get("LEARNINGS_DUP_DISTANCE", "0.5"))

# Reciprocal Rank Fusion constant (standard 60) + a small nudge for same-project hits.
RRF_K = 60
PROJECT_RRF_BONUS = 0.01

# Max always-on "core" learnings per workspace (excludes base). Keeps session context lean.
CORE_CAP = int(os.environ.get("LEARNINGS_CORE_CAP", "15"))

_COLS = (
    "id, title, content, tags, project, workspace, is_core, context, "
    "source, created_at, updated_at, hit_count, last_used, verified_at, refs"
)

# A learning whose verified_at is older than this is flagged "stale" for review.
STALE_DAYS = int(os.environ.get("LEARNINGS_STALE_DAYS", "180"))


def _db_path() -> Path:
    raw = os.environ.get("LEARNINGS_DB_PATH")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".learnings" / "learnings.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_file() -> Path:
    return _db_path().parent / "session.json"


def write_session(data: dict):
    """Persist the current session's provenance (called by the SessionStart hook)."""
    try:
        sf = _session_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps({k: v for k, v in data.items() if v}))
    except Exception:
        pass


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    if "is_core" in d:
        d["is_core"] = bool(d["is_core"])
    if "refs" in d:
        d["references"] = json.loads(d.pop("refs") or "[]")
    return d


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or _db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self._migrate()

    def close(self):
        self.db.close()

    def _migrate(self):
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS learnings (
                rowid      INTEGER PRIMARY KEY,
                id         TEXT UNIQUE NOT NULL,
                title      TEXT NOT NULL,
                content    TEXT NOT NULL,
                tags       TEXT NOT NULL DEFAULT '[]',
                project    TEXT,
                workspace  TEXT NOT NULL DEFAULT 'personal',
                is_core    INTEGER NOT NULL DEFAULT 0,
                context    TEXT,
                source     TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Idempotent upgrade for pre-v2 databases created without these columns.
        existing = {r["name"] for r in self.db.execute("PRAGMA table_info(learnings)")}
        if "workspace" not in existing:
            self.db.execute(
                "ALTER TABLE learnings ADD COLUMN workspace TEXT NOT NULL DEFAULT 'personal'"
            )
        if "is_core" not in existing:
            self.db.execute("ALTER TABLE learnings ADD COLUMN is_core INTEGER NOT NULL DEFAULT 0")
        if "hit_count" not in existing:
            self.db.execute("ALTER TABLE learnings ADD COLUMN hit_count INTEGER NOT NULL DEFAULT 0")
        if "last_used" not in existing:
            self.db.execute("ALTER TABLE learnings ADD COLUMN last_used TEXT")
        if "verified_at" not in existing:
            self.db.execute("ALTER TABLE learnings ADD COLUMN verified_at TEXT")
        if "refs" not in existing:
            self.db.execute("ALTER TABLE learnings ADD COLUMN refs TEXT NOT NULL DEFAULT '[]'")
        # Backfill: treat existing rows as verified at creation time.
        self.db.execute("UPDATE learnings SET verified_at = created_at WHERE verified_at IS NULL")

        self.db.execute("CREATE INDEX IF NOT EXISTS idx_learnings_workspace ON learnings(workspace)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_learnings_project ON learnings(project)")
        self.db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_learnings USING vec0(embedding float[{EMBED_DIM}])"
        )
        self._migrate_fts()
        self.db.commit()

    def _session_ref(self, op: str, note=None) -> dict:
        """A provenance entry: which session/op produced this learning (+ optional note).
        Never embedded or indexed — traceability only."""
        ref = {"op": op, "at": _now()}
        try:
            sf = _session_file()
            if sf.exists():
                d = json.loads(sf.read_text())
                for k in ("session", "transcript", "cwd"):
                    if d.get(k):
                        ref[k] = d[k]
        except Exception:
            pass
        if note:
            ref["note"] = note
        return ref

    def _migrate_fts(self):
        """Standard FTS5 keyword index (stores its own copy; ref = learnings.rowid),
        maintained explicitly by the write methods — no triggers, no external-content
        (which is fragile on delete). Degrades to vector-only if FTS5 is unavailable."""
        self.has_fts = False
        try:
            # Remove any legacy external-content index + its triggers.
            self.db.executescript(
                "DROP TRIGGER IF EXISTS learnings_ai;"
                "DROP TRIGGER IF EXISTS learnings_ad;"
                "DROP TRIGGER IF EXISTS learnings_au;"
            )
            row = self.db.execute("SELECT sql FROM sqlite_master WHERE name='fts_learnings'").fetchone()
            recreate = True
            if row:
                # 'porter' marks the current schema (stemming: onboard ~ onboarding).
                ok = "porter" in (row["sql"] or "")
                try:
                    self.db.execute("SELECT ref FROM fts_learnings LIMIT 1").fetchone()
                except sqlite3.DatabaseError:
                    ok = False
                if ok:
                    recreate = False
                else:
                    self.db.execute("DROP TABLE IF EXISTS fts_learnings")
            if recreate:
                self.db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS fts_learnings "
                    "USING fts5(ref UNINDEXED, title, content, tags, context, "
                    "tokenize='porter unicode61')"
                )
            if self.db.execute("SELECT count(*) c FROM fts_learnings").fetchone()["c"] == 0:
                self.db.execute(
                    "INSERT INTO fts_learnings(ref, title, content, tags, context) "
                    "SELECT rowid, title, content, tags, COALESCE(context, '') FROM learnings"
                )
            self.has_fts = True
        except sqlite3.DatabaseError:
            self.has_fts = False

    def _fts_put(self, rowid, title, content, tags_json, context):
        if not getattr(self, "has_fts", False):
            return
        try:
            self.db.execute("DELETE FROM fts_learnings WHERE ref = ?", (rowid,))
            self.db.execute(
                "INSERT INTO fts_learnings(ref, title, content, tags, context) VALUES (?, ?, ?, ?, ?)",
                (rowid, title, content, tags_json, context or ""),
            )
        except sqlite3.DatabaseError:
            pass

    def _fts_del(self, rowid):
        if not getattr(self, "has_fts", False):
            return
        try:
            self.db.execute("DELETE FROM fts_learnings WHERE ref = ?", (rowid,))
        except sqlite3.DatabaseError:
            pass

    # ---- writes -------------------------------------------------------------

    def create(
        self,
        *,
        title,
        content,
        tags=None,
        project=None,
        workspace=None,
        source="manual",
        context=None,
        is_core=False,
        force=False,
        reference=None,
    ) -> dict:
        """Create a learning. Returns {"status": "created"|"duplicate", ...}.

        If a near-duplicate already exists in the same workspace and force is False,
        nothing is written and the existing match is returned so the caller can enrich it.
        """
        ws = resolve_workspace(workspace)
        tags = tags or []
        title = redact_text(title)
        content = redact_text(content)
        context = redact_text(context)
        vec = embed(learning_text(title, content, tags))

        if not force:
            dup = self._nearest_in_workspace(vec, ws)
            if dup and dup["distance"] <= DUP_DISTANCE:
                return {"status": "duplicate", "match": dup}

        if is_core:
            self._assert_core_capacity(ws)

        lid = str(uuid.uuid4())
        now = _now()
        refs = [self._session_ref("created", reference)]
        cur = self.db.execute(
            """
            INSERT INTO learnings
                (id, title, content, tags, project, workspace, is_core, context, source,
                 created_at, updated_at, verified_at, refs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (lid, title, content, json.dumps(tags), project, ws, 1 if is_core else 0,
             context, source, now, now, now, json.dumps(refs)),
        )
        rowid = cur.lastrowid
        self.db.execute(
            "INSERT INTO vec_learnings (rowid, embedding) VALUES (?, ?)",
            (rowid, sqlite_vec.serialize_float32(vec)),
        )
        self._fts_put(rowid, title, content, json.dumps(tags), context)
        self.db.commit()
        return {"status": "created", "learning": self.get(lid)}

    def enrich(self, lid: str, context: str, reference=None) -> dict | None:
        existing = self.get(lid)
        if not existing:
            return None
        context = redact_text(context)
        new_context = (
            f"{existing['context']}\n\n---\n\n{context}" if existing.get("context") else context
        )
        enriched_content = f"{existing['content']}\n\nAdditional context: {new_context}"
        vec = embed(learning_text(existing["title"], enriched_content, existing["tags"]))

        refs = (existing.get("references") or []) + [self._session_ref("enriched", reference)]
        rowid = self._rowid(lid)
        self.db.execute(
            "UPDATE learnings SET context = ?, refs = ?, updated_at = ? WHERE id = ?",
            (new_context, json.dumps(refs), _now(), lid),
        )
        self.db.execute(
            "UPDATE vec_learnings SET embedding = ? WHERE rowid = ?",
            (sqlite_vec.serialize_float32(vec), rowid),
        )
        self._fts_put(rowid, existing["title"], existing["content"], json.dumps(existing["tags"]), new_context)
        self.db.commit()
        return self.get(lid)

    def set_core(self, lid: str, value: bool) -> dict | None:
        rec = self.get(lid)
        if not rec:
            return None
        if value and not rec["is_core"]:
            self._assert_core_capacity(rec["workspace"])
        self.db.execute(
            "UPDATE learnings SET is_core = ?, updated_at = ? WHERE id = ?",
            (1 if value else 0, _now(), lid),
        )
        self.db.commit()
        return self.get(lid)

    def remove(self, lid: str) -> bool:
        rowid = self._rowid(lid)
        if rowid is None:
            return False
        self.db.execute("DELETE FROM learnings WHERE id = ?", (lid,))
        self.db.execute("DELETE FROM vec_learnings WHERE rowid = ?", (rowid,))
        self._fts_del(rowid)
        self.db.commit()
        return True

    # ---- reads --------------------------------------------------------------

    def get(self, lid: str) -> dict | None:
        row = self.db.execute(f"SELECT {_COLS} FROM learnings WHERE id = ?", (lid,)).fetchone()
        return _row_to_dict(row) if row else None

    def _rowid(self, lid: str) -> int | None:
        row = self.db.execute("SELECT rowid FROM learnings WHERE id = ?", (lid,)).fetchone()
        return row["rowid"] if row else None

    def list(self, *, tags=None, project=None, workspace=None, limit=10, offset=0) -> list[dict]:
        ws = resolve_workspace(workspace)
        clauses = ["workspace IN (?, ?)"]
        params: list = [ws, BASE_WORKSPACE]
        if project:
            clauses.append("project = ?")
            params.append(project)
        where = f"WHERE {' AND '.join(clauses)}"
        params.extend([int(limit), int(offset)])
        rows = self.db.execute(
            f"SELECT {_COLS} FROM learnings {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        results = [_row_to_dict(r) for r in rows]
        if tags:
            wanted = set(tags)
            results = [r for r in results if wanted & set(r["tags"])]
        return results

    def core(self, *, workspace=None, include_base=True, limit=CORE_CAP) -> list[dict]:
        """Always-on learnings for a workspace (+ base), capped. No embedding needed."""
        ws = resolve_workspace(workspace)
        spaces = [ws, BASE_WORKSPACE] if include_base else [ws]
        placeholders = ",".join("?" for _ in spaces)
        rows = self.db.execute(
            f"SELECT {_COLS} FROM learnings "
            f"WHERE is_core = 1 AND workspace IN ({placeholders}) "
            f"ORDER BY workspace = ? DESC, created_at ASC LIMIT ?",
            [*spaces, ws, int(limit)],
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def _fts_rowids(self, query: str, limit: int) -> list[int]:
        """Keyword-match rowids (best first), or [] if FTS unavailable / no usable terms."""
        if not getattr(self, "has_fts", False):
            return []
        terms = [t for t in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        try:
            rows = self.db.execute(
                "SELECT ref FROM fts_learnings WHERE fts_learnings MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [int(r["ref"]) for r in rows]

    def search(self, query: str, *, project=None, workspace=None, limit=5) -> list[dict]:
        # workspace='*' searches across every workspace (used by the local UI).
        ws = "*" if workspace == "*" else resolve_workspace(workspace)
        if self.db.execute("SELECT COUNT(*) AS n FROM learnings").fetchone()["n"] == 0:
            return []
        k = max(limit * 10, 50)

        # 1) semantic ranking (vector KNN)
        qvec = embed(query)
        vrows = self.db.execute(
            """
            SELECT v.rowid AS rowid, v.distance AS distance
            FROM vec_learnings v
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (sqlite_vec.serialize_float32(qvec), k),
        ).fetchall()
        vec_order = [r["rowid"] for r in vrows]
        vec_dist = {r["rowid"]: r["distance"] for r in vrows}

        # 2) keyword ranking (FTS5) — catches exact identifiers the vector fuzzes
        fts_order = self._fts_rowids(query, k)

        # 3) Reciprocal Rank Fusion of the two rankings
        rrf: dict[int, float] = {}
        for rank, rid in enumerate(vec_order):
            rrf[rid] = rrf.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)
        for rank, rid in enumerate(fts_order):
            rrf[rid] = rrf.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)

        scored = []
        for rid, score in rrf.items():
            rec = self.db.execute(
                f"SELECT {_COLS} FROM learnings WHERE rowid = ?", (rid,)
            ).fetchone()
            if not rec:
                continue
            rec = _row_to_dict(rec)
            if ws != "*" and rec["workspace"] not in (ws, BASE_WORKSPACE):
                continue  # workspace isolation — never leak across projects
            if project and rec["project"] == project:
                score += PROJECT_RRF_BONUS
            rec["distance"] = vec_dist.get(rid)  # None if only the keyword search matched
            rec["score"] = round(score, 5)
            scored.append((score, rec))

        scored.sort(key=lambda t: -t[0])
        results = [rec for _s, rec in scored[:limit]]
        # Usage signal: bump hit_count / last_used for what we actually surfaced.
        if results:
            now = _now()
            self.db.executemany(
                "UPDATE learnings SET hit_count = hit_count + 1, last_used = ? WHERE id = ?",
                [(now, r["id"]) for r in results],
            )
            self.db.commit()
        return results

    def backup(self, dest_dir=None) -> dict:
        """Write a consistent timestamped copy of the database (online-backup API)."""
        dest_dir = Path(dest_dir).expanduser() if dest_dir else (self.path.parent / "backups")
        dest_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = dest_dir / f"learnings-{ts}.db"
        out = sqlite3.connect(str(dest))
        try:
            self.db.backup(out)
        finally:
            out.close()
        return {"path": str(dest), "bytes": dest.stat().st_size}

    def verify(self, lid: str) -> dict | None:
        """Mark a learning re-verified now (resets its staleness clock)."""
        now = _now()
        cur = self.db.execute(
            "UPDATE learnings SET verified_at = ?, updated_at = ? WHERE id = ?", (now, now, lid)
        )
        self.db.commit()
        return self.get(lid) if cur.rowcount else None

    def review(self, *, workspace=None, stale_days=STALE_DAYS, cold_days=30) -> dict:
        """Curation candidates: stale (old verified_at) and cold (never surfaced & not new)."""
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        stale_cut = (now - timedelta(days=stale_days)).isoformat()
        cold_cut = (now - timedelta(days=cold_days)).isoformat()
        where, params = "", []
        if workspace and workspace != "*":
            where = "AND workspace IN (?, ?)"
            params = [resolve_workspace(workspace), BASE_WORKSPACE]
        stale = self.db.execute(
            f"SELECT {_COLS} FROM learnings WHERE verified_at < ? {where} ORDER BY verified_at ASC",
            [stale_cut, *params],
        ).fetchall()
        cold = self.db.execute(
            f"SELECT {_COLS} FROM learnings WHERE hit_count = 0 AND created_at < ? {where} "
            f"ORDER BY created_at ASC",
            [cold_cut, *params],
        ).fetchall()
        return {"stale": [_row_to_dict(r) for r in stale], "cold": [_row_to_dict(r) for r in cold]}

    def find_duplicate(self, *, title, content, tags=None, workspace=None) -> dict | None:
        """Dry-run near-duplicate check (no write) — used by batch ingest planning."""
        ws = resolve_workspace(workspace)
        vec = embed(learning_text(title, content, tags or []))
        dup = self._nearest_in_workspace(vec, ws)
        return dup if dup and dup["distance"] <= DUP_DISTANCE else None

    # ---- sync (git interchange) ---------------------------------------------

    def all_records(self) -> list[dict]:
        """Every learning (all workspaces) — for export."""
        rows = self.db.execute(
            f"SELECT {_COLS} FROM learnings ORDER BY workspace, created_at"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def upsert(self, rec: dict) -> str:
        """Insert or update a learning by its id (used on import). Re-embeds only when
        the text (title/content/tags) is new or changed. Returns created/updated/unchanged."""
        lid = rec["id"]
        title, content = rec["title"], rec["content"]
        tags = rec.get("tags") or []
        ws = resolve_workspace(rec.get("workspace"))
        now = _now()
        existing = self.get(lid)

        refs = json.dumps(rec.get("references") or [])
        if existing is None:
            vec = embed(learning_text(title, content, tags))
            cur = self.db.execute(
                """
                INSERT INTO learnings
                    (id, title, content, tags, project, workspace, is_core, context, source,
                     created_at, updated_at, verified_at, refs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (lid, title, content, json.dumps(tags), rec.get("project"), ws,
                 1 if rec.get("is_core") else 0, rec.get("context"), rec.get("source") or "import",
                 rec.get("created_at") or now, now, rec.get("verified_at") or rec.get("created_at") or now,
                 refs),
            )
            self.db.execute(
                "INSERT INTO vec_learnings (rowid, embedding) VALUES (?, ?)",
                (cur.lastrowid, sqlite_vec.serialize_float32(vec)),
            )
            self._fts_put(cur.lastrowid, title, content, json.dumps(tags), rec.get("context"))
            self.db.commit()
            return "created"

        text_changed = (existing["title"], existing["content"], existing["tags"]) != (title, content, tags)
        self.db.execute(
            """
            UPDATE learnings SET title=?, content=?, tags=?, project=?, workspace=?, is_core=?,
                   context=?, source=?, verified_at=?, refs=?, updated_at=? WHERE id=?
            """,
            (title, content, json.dumps(tags), rec.get("project"), ws,
             1 if rec.get("is_core") else 0, rec.get("context"),
             rec.get("source") or existing["source"],
             rec.get("verified_at") or existing.get("verified_at"),
             refs if rec.get("references") is not None else json.dumps(existing.get("references") or []),
             now, lid),
        )
        if text_changed:
            vec = embed(learning_text(title, content, tags))
            self.db.execute(
                "UPDATE vec_learnings SET embedding=? WHERE rowid=?",
                (sqlite_vec.serialize_float32(vec), self._rowid(lid)),
            )
        self._fts_put(self._rowid(lid), title, content, json.dumps(tags), rec.get("context"))
        self.db.commit()
        return "updated" if text_changed else "unchanged"

    # ---- internals ----------------------------------------------------------

    def _nearest_in_workspace(self, vec, workspace: str) -> dict | None:
        rows = self.db.execute(
            """
            SELECT v.rowid AS rowid, v.distance AS distance
            FROM vec_learnings v
            WHERE v.embedding MATCH ? AND k = 20
            ORDER BY v.distance
            """,
            (sqlite_vec.serialize_float32(vec),),
        ).fetchall()
        for r in rows:
            rec = self.db.execute(
                f"SELECT {_COLS} FROM learnings WHERE rowid = ?", (r["rowid"],)
            ).fetchone()
            if not rec:
                continue
            rec = _row_to_dict(rec)
            if rec["workspace"] == workspace:
                rec["distance"] = r["distance"]
                return rec
        return None

    def _assert_core_capacity(self, workspace: str):
        n = self.db.execute(
            "SELECT COUNT(*) AS n FROM learnings WHERE is_core = 1 AND workspace = ?",
            (workspace,),
        ).fetchone()["n"]
        if n >= CORE_CAP:
            raise ValueError(
                f"Workspace '{workspace}' already has {n} core learnings "
                f"(cap {CORE_CAP}). Un-core one first to keep session context lean."
            )
