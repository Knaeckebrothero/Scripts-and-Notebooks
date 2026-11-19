-- 0004 audit log for the admin website: who changed what, and when.
-- Written by the admin website only; the router never reads it.

CREATE TABLE IF NOT EXISTS admin_audit (
    id      BIGSERIAL PRIMARY KEY,
    at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    target  TEXT,
    detail  JSONB
);

CREATE INDEX IF NOT EXISTS admin_audit_at ON admin_audit (at DESC);
