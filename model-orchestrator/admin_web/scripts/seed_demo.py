#!/usr/bin/env python3
"""Fill a DEVELOPMENT database with believable demo data.

    uv run python scripts/seed_demo.py --migrate --reset

Writes a dozen keys, nine routes, 60 days of ``usage_daily``, 14 days of
``usage_hourly`` and a short audit history, so every page of the console has
something to show. All names are invented.

Safety: the script refuses to touch a database that already holds keys
unless ``--reset`` is given, and ``--reset`` empties the five tables first.
Never point it at a live router database.

``--migrate`` applies ``../migrations/*.sql`` in order. That is a development
convenience for a database no router has started against; in a real
installation the router applies the migrations.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mo_admin.keys import KEY_PREFIX_LEN, generate_token, hash_token  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent.parent.parent / "migrations"
TABLES = ("api_keys", "usage_daily", "usage_hourly", "admin_audit", "routes", "admin_revoked_sessions")

# route name, type, model ids clients send, relative weight, error rate,
# (prompt tokens, completion tokens) per request, typical duration in ms
ROUTES = [
    ("Gemma-4-26B-A4B", "chat", ["gemma-4-26b", "google/gemma-4-26B-A4B-it"], 30, 0.012, (1900, 520), 6800),
    ("Qwen3-Embedding-8B", "embedding", ["qwen3-embedding-8b"], 24, 0.004, (410, 0), 140),
    ("GPT-OSS-20B", "chat", ["gpt-oss-20b", "openai/gpt-oss-20b"], 16, 0.015, (1400, 610), 5200),
    ("Muse-Glimmer-30B", "chat", ["muse-glimmer-30b"], 11, 0.021, (2600, 880), 9400),
    ("Qwen3-Reranker-8B", "reranker", ["qwen3-reranker-8b"], 8, 0.005, (720, 0), 210),
    ("Pixtral-12B-2409", "vision", ["pixtral-12b"], 5, 0.018, (2300, 340), 7600),
    ("Qwen3-Embedding-0.6B", "embedding", ["qwen3-embedding-0.6b"], 4, 0.003, (380, 0), 60),
    ("Whisper-large-v3-turbo", "stt", ["whisper-large-v3-turbo"], 1.6, 0.010, (0, 0), 11800),
    ("Kokoro-82M", "tts", ["kokoro-82m"], 0.9, 0.008, (0, 0), 2400),
]

# name, owner, email, rpm, concurrency, allowed routes, share of traffic,
# days since creation, expiry in days from today (None = never), enabled, notes
KEYS = [
    ("rag-pipeline-prod", "Mira Castellan", "mira.castellan@example.org", 600, 16, None, 26, 58, None, True,
     "Retrieval service of the library assistant. Page Mira before lowering the limit."),
    ("course-assistant", "Teaching Lab", "teaching-lab@example.org", 300, 8,
     ["Gemma-4-26B-A4B", "GPT-OSS-20B", "Qwen3-Embedding-8B"], 17, 51, 9, True,
     "Renewed every semester."),
    ("thesis-eval-harness", "Jonas Feldkamp", "jonas.feldkamp@example.org", 120, 4, None, 13, 44, 120, True, None),
    ("doc-search-indexer", "Mira Castellan", "mira.castellan@example.org", 900, None,
     ["Qwen3-Embedding-8B", "Qwen3-Embedding-0.6B", "Qwen3-Reranker-8B"], 15, 57, None, True,
     "Nightly re-index, bursts after midnight UTC."),
    ("speech-notes", "Lab 4 Speech", "lab4@example.org", 60, 2,
     ["Whisper-large-v3-turbo", "Kokoro-82M"], 3, 37, 200, True, None),
    ("vision-triage", "Ines Oduya", "ines.oduya@example.org", 90, 4,
     ["Pixtral-12B-2409", "Gemma-4-26B-A4B"], 7, 29, 4, True, "Pilot, ends with the month."),
    ("notebook-sandbox", "Student Pool", None, 30, 2, None, 6, 40, None, True, "Shared by the seminar group."),
    ("agent-playground", "Tomas Reller", "tomas.reller@example.org", 240, 6, None, 9, 21, 45, True, None),
    ("summer-school-2026", "Teaching Lab", "teaching-lab@example.org", 200, 8, None, 4, 55, -18, True,
     "Event is over; kept for the usage report."),
    ("legacy-batch-import", "Platform Team", "platform@example.org", 100, None, None, 0, 59, None, False,
     "Replaced by doc-search-indexer."),
    ("partner-demo", "Ines Oduya", "ines.oduya@example.org", 60, 2, ["Gemma-4-26B-A4B"], 0, 33, 60, True,
     "Handed out, apparently never tried."),
    ("ci-smoke-test", "Platform Team", "platform@example.org", 20, 1, None, 0, 2, None, True, None),
]
DELETED_KEY_ID = 9001  # usage rows without a key: history survives deletion
DELETED_KEY_OWNER = "Former Intern"

# How the hours of a day share the day's traffic (UTC): quiet night, two humps.
HOUR_SHAPE = [0.6, 0.45, 0.4, 0.35, 0.35, 0.5, 0.8, 1.4, 2.2, 2.9, 3.1, 3.0,
              2.4, 2.8, 3.2, 3.1, 2.7, 2.1, 1.6, 1.3, 1.1, 1.0, 0.9, 0.7]
FAILURES = ["backend_error", "timeout", "unreachable", "midstream_error", "client_error"]


def day_volume(rng: random.Random, day: date, today: date, base: float) -> float:
    """Requests on one day: weekly rhythm, slow growth, a little noise."""
    weekday = (1.0, 1.08, 1.12, 1.05, 0.92, 0.34, 0.27)[day.weekday()]
    age = (today - day).days
    growth = 1.0 - 0.0065 * age
    return max(0.0, base * weekday * growth * rng.uniform(0.82, 1.2))


def split_outcomes(rng: random.Random, forwarded: int, error_rate: float) -> dict[str, int]:
    errors = sum(1 for _ in range(forwarded) if rng.random() < error_rate) if forwarded < 400 else round(
        forwarded * error_rate * rng.uniform(0.6, 1.5)
    )
    errors = min(errors, forwarded)
    closed = round((forwarded - errors) * rng.uniform(0.0, 0.012))
    out = {"ok": forwarded - errors - closed}
    if closed:
        out["client_closed"] = closed
    for _ in range(errors):
        kind = rng.choices(FAILURES, weights=[5, 3, 1, 1, 4])[0]
        out[kind] = out.get(kind, 0) + 1
    return {k: v for k, v in out.items() if v > 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("ADMIN_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    parser.add_argument("--migrate", action="store_true", help="apply ../migrations/*.sql first (development only)")
    parser.add_argument("--reset", action="store_true", help="empty the tables before seeding")
    parser.add_argument("--no-hourly", action="store_true", help="leave usage_hourly empty, like a router before 3.0")
    parser.add_argument("--seed", type=int, default=20261118)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("set ADMIN_DATABASE_URL or pass --database-url")

    rng = random.Random(args.seed)
    now = datetime.now(timezone.utc)
    today = now.date()

    with psycopg.connect(args.database_url) as conn:
        if args.migrate:
            for path in sorted(MIGRATIONS.glob("*.sql")):
                conn.execute(path.read_text(encoding="utf-8"))
                print(f"applied {path.name}")
        existing = conn.execute("SELECT count(*) FROM api_keys").fetchone()[0]
        if existing and not args.reset:
            print(f"api_keys already holds {existing} keys; pass --reset to replace them", file=sys.stderr)
            return 1
        if args.reset:
            conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")

        for name, kind, *_ in ROUTES:
            conn.execute("INSERT INTO routes (name, type) VALUES (%s, %s)", (name, kind))

        # ---------------------------------------------------------------- keys
        key_rows = []
        audit_rows: list[tuple[datetime, str, str, str | None, dict]] = []
        for name, owner, email, rpm, conc, allowed, share, age, expiry, enabled, notes in KEYS:
            token = generate_token()  # never printed: demo keys are not meant to be used
            created = now - timedelta(days=age, hours=rng.randint(0, 9), minutes=rng.randint(0, 59))
            expires = (
                datetime.combine(today + timedelta(days=expiry), time(23, 59, 59), tzinfo=timezone.utc)
                if expiry is not None else None
            )
            key_id = conn.execute(
                """
                INSERT INTO api_keys (key_hash, key_prefix, name, owner, owner_email, rate_limit_rpm,
                                      max_concurrency, allowed_models, enabled, expires_at, created_at,
                                      created_by, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (hash_token(token), token[:KEY_PREFIX_LEN], name, owner, email, rpm, conc, allowed, enabled,
                 expires, created, "local:demo-admin", notes),
            ).fetchone()[0]
            key_rows.append(
                {"id": key_id, "name": name, "owner": owner, "allowed": allowed, "share": share, "age": age,
                 "expiry": expiry, "enabled": enabled, "prefix": token[:KEY_PREFIX_LEN], "created": created,
                 "rpm": rpm}
            )
            audit_rows.append(
                (created, "local:demo-admin", "key.create", f"key:{key_id}",
                 {"name": name, "owner": owner, "owner_email": email, "key_prefix": token[:KEY_PREFIX_LEN],
                  "rate_limit_rpm": rpm, "max_concurrency": conc, "allowed_models": allowed,
                  "expires_at": expires.isoformat() if expires else None, "has_notes": bool(notes)})
            )
        key_rows.append({"id": DELETED_KEY_ID, "name": "(deleted)", "owner": DELETED_KEY_OWNER, "allowed": None,
                         "share": 2.5, "age": 60, "expiry": -30, "enabled": True, "prefix": "", "created": now,
                         "rpm": 60})

        # --------------------------------------------------------------- usage
        daily: dict[tuple[date, int, str], list[int]] = {}
        hourly: dict[tuple[datetime, int, str, str, str], list[int]] = {}
        owners = {k["id"]: k["owner"] for k in key_rows}
        hourly_days = 0 if args.no_hourly else 14
        total_weight = sum(route[3] for route in ROUTES)
        last_used: dict[int, datetime] = {}

        for offset in range(59, -1, -1):
            day = today - timedelta(days=offset)
            day_fraction = (now.hour + now.minute / 60) / 24 if offset == 0 else 1.0
            for key in key_rows:
                if not key["share"]:
                    continue
                start_age = key["age"]
                # a key stops when it expires, the deleted one a month ago
                ended = key["expiry"] is not None and key["expiry"] < -offset
                if offset > start_age or ended:
                    continue
                for route_name, _kind, model_ids, weight, error_rate, tokens, duration in ROUTES:
                    if key["allowed"] is not None and route_name not in key["allowed"]:
                        continue
                    base = key["share"] * 38 * weight / total_weight
                    if key["allowed"] is not None:
                        base *= 2.2  # restricted keys concentrate on their few routes
                    volume = day_volume(rng, day, today, base)
                    # one bad afternoon on one route, eleven days ago
                    incident = offset == 11 and route_name == "Muse-Glimmer-30B"
                    rate = 0.34 if incident else error_rate
                    if offset < hourly_days:
                        shape = sum(HOUR_SHAPE)
                        for hour in range(24):
                            if offset == 0 and hour > now.hour:
                                break
                            expected = volume * HOUR_SHAPE[hour] / shape
                            if offset == 0 and hour == now.hour:
                                expected *= now.minute / 60
                            forwarded = int(expected) + (1 if rng.random() < expected % 1 else 0)
                            stamp = datetime.combine(day, time(hour), tzinfo=timezone.utc)
                            model = rng.choices(model_ids, weights=[4] + [1] * (len(model_ids) - 1))[0]
                            if forwarded:
                                for outcome, count in split_outcomes(rng, forwarded, rate).items():
                                    cell = hourly.setdefault((stamp, key["id"], model, route_name, outcome), [0, 0, 0, 0])
                                    cell[0] += count
                                    if outcome in ("ok", "client_closed", "midstream_error"):
                                        cell[1] += int(count * tokens[0] * rng.uniform(0.7, 1.35))
                                        cell[2] += int(count * tokens[1] * rng.uniform(0.6, 1.4))
                                    slow = 4.5 if outcome == "timeout" else 0.25 if outcome == "client_error" else 1.0
                                    cell[3] += int(count * duration * slow * rng.uniform(0.75, 1.3))
                                    entry = daily.setdefault((day, key["id"], route_name), [0, 0])
                                    entry[0] += count
                                    if outcome not in ("ok", "client_closed"):
                                        entry[1] += count
                                last_used[key["id"]] = max(last_used.get(key["id"], stamp), stamp)
                            # refused before forwarding: only in usage_hourly
                            if forwarded > key["rpm"] * 0.5 and rng.random() < 0.5:
                                cell = hourly.setdefault((stamp, key["id"], model, route_name, "rate_limited"), [0, 0, 0, 0])
                                cell[0] += rng.randint(1, max(2, forwarded // 12))
                            if rng.random() < 0.004 and forwarded:
                                cell = hourly.setdefault((stamp, key["id"], model, route_name, "concurrency_limited"), [0, 0, 0, 0])
                                cell[0] += rng.randint(1, 3)
                            if incident and 13 <= hour <= 15 and forwarded:
                                cell = hourly.setdefault((stamp, key["id"], model, route_name, "unavailable"), [0, 0, 0, 0])
                                cell[0] += rng.randint(2, 9)
                    else:
                        requests = round(volume * day_fraction)
                        if requests:
                            errors = min(requests, round(requests * rate * rng.uniform(0.5, 1.6)))
                            daily[(day, key["id"], route_name)] = [requests, errors]
                            stamp = datetime.combine(day, time(rng.randint(8, 20)), tzinfo=timezone.utc)
                            last_used[key["id"]] = max(last_used.get(key["id"], stamp), stamp)

        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO usage_daily (day, api_key_id, owner, model, request_count, error_count) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [(d, k, owners[k], route, n, e) for (d, k, route), (n, e) in daily.items()],
            )
            cur.executemany(
                "INSERT INTO usage_hourly (hour, api_key_id, owner, model, route, outcome, requests, "
                "prompt_tokens, completion_tokens, duration_ms_sum) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(h, k, owners[k], model, route, outcome, *cell) for (h, k, model, route, outcome), cell in hourly.items()],
            )
        for key_id, stamp in last_used.items():
            jitter = timedelta(minutes=rng.randint(1, 50))
            conn.execute("UPDATE api_keys SET last_used_at = %s WHERE id = %s", (min(now, stamp + jitter), key_id))

        # --------------------------------------------------------------- audit
        by_name = {k["name"]: k for k in key_rows}

        def audit(days_ago: float, actor: str, action: str, key_name: str | None, detail: dict) -> None:
            key = by_name.get(key_name) if key_name else None
            if key:
                detail = {"name": key["name"], "key_prefix": key["prefix"], **detail}
            target = f"key:{key['id']}" if key else None
            if action == "key.delete":
                target = f"key:{DELETED_KEY_ID}"
            audit_rows.append((now - timedelta(days=days_ago), actor, action, target, detail))

        audit(19.2, "oidc:mira.castellan", "auth.login", None, {"method": "oidc", "subject": "demo-user-0001"})
        audit(19.1, "oidc:mira.castellan", "key.update", "rag-pipeline-prod",
              {"changes": {"rate_limit_rpm": {"from": 300, "to": 600}, "max_concurrency": {"from": 8, "to": 16}}})
        audit(12.4, "local:demo-admin", "key.disable", "legacy-batch-import", {"owner": "Platform Team"})
        audit(6.3, "oidc:tomas.reller", "auth.denied", None,
              {"method": "oidc", "reason": "missing role", "required_role": "router-admin"})
        audit(3.1, "oidc:mira.castellan", "auth.login", None, {"method": "oidc", "subject": "demo-user-0001"})
        audit(3.05, "oidc:mira.castellan", "key.update", "course-assistant",
              {"changes": {"allowed_models": {"from": None, "to": ["Gemma-4-26B-A4B", "GPT-OSS-20B", "Qwen3-Embedding-8B"]},
                           "notes": "changed"}})
        audit(1.2, "local:demo-admin", "auth.login", None, {"method": "local"})
        audit(1.19, "local:demo-admin", "key.delete", None,
              {"name": "intern-experiments", "owner": DELETED_KEY_OWNER, "key_prefix": "sk-mo-4c81e0",
               "was_enabled": True})
        # ids must follow time: the audit page pages by id
        for at, actor, action, target, detail in sorted(audit_rows, key=lambda row: row[0]):
            conn.execute(
                "INSERT INTO admin_audit (at, actor, action, target, detail) VALUES (%s, %s, %s, %s, %s)",
                (at, actor, action, target, Jsonb(detail)),
            )

    print(
        f"seeded {len(KEYS)} keys, {len(ROUTES)} routes, {len(daily)} usage_daily rows, "
        f"{len(hourly)} usage_hourly rows"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
