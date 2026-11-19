-- 0001 baseline: the schema as the router has created it since 2026-06-12.
-- Written with IF NOT EXISTS so an existing database adopts it unchanged.

CREATE TABLE IF NOT EXISTS routes (
    name        TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id              SERIAL PRIMARY KEY,
    key_hash        TEXT NOT NULL UNIQUE,
    key_prefix      TEXT NOT NULL,
    name            TEXT NOT NULL,
    owner           TEXT NOT NULL,
    rate_limit_rpm  INTEGER NOT NULL DEFAULT 100,
    allowed_models  TEXT[],
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS usage_daily (
    day             DATE NOT NULL,
    api_key_id      INTEGER NOT NULL,
    owner           TEXT NOT NULL,
    model           TEXT NOT NULL,
    request_count   BIGINT NOT NULL DEFAULT 0,
    error_count     BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (day, api_key_id, model)
);
