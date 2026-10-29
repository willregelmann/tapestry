-- The schema of a version 2 mind, kept to test bringing one forward.
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- A memory in no namespace lives in the default namespace, which is always searched.
CREATE TABLE IF NOT EXISTS namespaces (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id              INTEGER PRIMARY KEY,
    content         TEXT NOT NULL,
    key             TEXT NOT NULL UNIQUE,      -- idempotency key
    created_at      REAL NOT NULL,             -- when it was learned
    superseded_by   INTEGER REFERENCES memories(id),
    embedding       BLOB,
    embedding_model TEXT,
    volatility      TEXT,                      -- Stage 2
    belief_L        REAL,                      -- Stage 2: cached log-odds
    belief_t        REAL                       -- Stage 2: time of last evidence
);
CREATE TABLE IF NOT EXISTS memory_namespaces (
    memory_id    INTEGER NOT NULL REFERENCES memories(id),
    namespace_id INTEGER NOT NULL REFERENCES namespaces(id),
    PRIMARY KEY (memory_id, namespace_id)
);
CREATE INDEX IF NOT EXISTS memory_namespaces_ns ON memory_namespaces(namespace_id);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(content, content=memories, content_rowid=id);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TABLE IF NOT EXISTS associations (
    src          INTEGER NOT NULL REFERENCES memories(id),
    dst          INTEGER NOT NULL REFERENCES memories(id),
    strength     REAL NOT NULL CHECK (strength BETWEEN 0 AND 1),
    last_used_at REAL,
    PRIMARY KEY (src, dst)
);
-- Which memories a memory was inferred from (docs/primitives/MEMORY.md): forgetting
-- the last of them takes the inference with it.
CREATE TABLE IF NOT EXISTS derivations (
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    from_id   INTEGER NOT NULL REFERENCES memories(id),
    PRIMARY KEY (memory_id, from_id)
);
-- What a forgotten memory leaves: that it existed, never what it held. The key stops
-- the same moment being recorded again (a transcript is ingested more than once).
CREATE TABLE IF NOT EXISTS forgotten (
    id  INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    at  REAL NOT NULL
);
-- What a model was asked about a pair of memories, who answered, and what it said, in coarse
-- categories (docs/primitives/EVIDENCE.md). `judged` marks a memory whose pairs are all done.
CREATE TABLE IF NOT EXISTS judgments (
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    other_id  INTEGER NOT NULL REFERENCES memories(id),
    prompt    TEXT NOT NULL,
    model     TEXT NOT NULL,
    category  TEXT NOT NULL CHECK (category IN ('conflict','maybe','no conflict')),
    at        REAL NOT NULL,
    PRIMARY KEY (memory_id, other_id, prompt)
);
CREATE TABLE IF NOT EXISTS judged (
    memory_id INTEGER PRIMARY KEY REFERENCES memories(id),
    prompt    TEXT NOT NULL,
    at        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id          INTEGER PRIMARY KEY,
    memory_id   INTEGER NOT NULL REFERENCES memories(id),
    kind        TEXT NOT NULL CHECK (kind IN
                  ('observe','confirm','contradict','supersede','file','unfile')),
    source      TEXT NOT NULL,
    reliability REAL,
    strength    REAL NOT NULL DEFAULT 1.0,
    episode     TEXT,
    trigger     TEXT,
    reasoner    TEXT,
    note        TEXT,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_memory ON evidence(memory_id);
-- Content is never edited and evidence is never rewritten.
CREATE TRIGGER IF NOT EXISTS memories_no_edit BEFORE UPDATE OF content ON memories
BEGIN SELECT RAISE(ABORT, 'memory content is never edited; supersede it'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_edit BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is never edited'); END;
