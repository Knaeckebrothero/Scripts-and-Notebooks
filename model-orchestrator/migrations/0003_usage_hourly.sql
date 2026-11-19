-- 0003 hourly usage with token counts and outcomes.
--
--   hour     start of the hour, UTC
--   model    the model id exactly as the client sent it (alias or backend id)
--   route    the route name that served it
--   outcome  ok | client_error | backend_error | unreachable | timeout |
--            midstream_error | client_closed | unavailable | rate_limited |
--            concurrency_limited
--
-- No foreign key to api_keys, like usage_daily: history survives key deletion.

CREATE TABLE IF NOT EXISTS usage_hourly (
    hour              TIMESTAMPTZ NOT NULL,
    api_key_id        INTEGER NOT NULL,
    owner             TEXT NOT NULL,
    model             TEXT NOT NULL,
    route             TEXT NOT NULL,
    outcome           TEXT NOT NULL,
    requests          BIGINT NOT NULL DEFAULT 0,
    prompt_tokens     BIGINT NOT NULL DEFAULT 0,
    completion_tokens BIGINT NOT NULL DEFAULT 0,
    duration_ms_sum   BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, api_key_id, model, route, outcome)
);

CREATE INDEX IF NOT EXISTS usage_hourly_key_hour ON usage_hourly (api_key_id, hour);
