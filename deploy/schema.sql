-- Reference schema for TEAM MODE (Postgres + pgvector).
-- PgStore auto-applies this on first connect; kept here for review/manual setup.
-- Embedding dimension (384) must match LEARNINGS_EMBED_DIM / the embedding model.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS learnings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    tags        JSONB NOT NULL DEFAULT '[]',
    project     TEXT,
    workspace   TEXT NOT NULL DEFAULT 'personal',
    is_core     BOOLEAN NOT NULL DEFAULT false,
    context     TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',
    refs        JSONB NOT NULL DEFAULT '[]',   -- provenance; never embedded/searched
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    hit_count   INT NOT NULL DEFAULT 0,
    last_used   TIMESTAMPTZ,
    embedding   vector(384)
);

CREATE INDEX IF NOT EXISTS learnings_ws_idx ON learnings (workspace);
CREATE INDEX IF NOT EXISTS learnings_embedding_idx
    ON learnings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
