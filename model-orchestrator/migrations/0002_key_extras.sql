-- 0002 key extras. All columns are nullable, so code that names its columns
-- explicitly (the Streamlit admin UI) keeps working unchanged.
--
--   max_concurrency  requests in flight allowed for this key; NULL = no cap
--   owner_email      the person behind the key, for a later self-service login
--   created_by       who created the key in the admin website
--   notes            free text

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS max_concurrency INTEGER;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS owner_email TEXT;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_by TEXT;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS notes TEXT;
