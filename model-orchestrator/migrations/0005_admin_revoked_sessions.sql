-- 0005 revoked sessions of the admin website. Signing out records the session
-- id here, so a copied session cookie stays refused across restarts and
-- across processes. A row is useless once expires_at has passed, which is
-- when the session would have ended anyway; the website deletes such rows.
-- Written and read by the admin website only; the router never reads it.

CREATE TABLE IF NOT EXISTS admin_revoked_sessions (
    session_id  TEXT PRIMARY KEY,
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS admin_revoked_sessions_expires ON admin_revoked_sessions (expires_at);
