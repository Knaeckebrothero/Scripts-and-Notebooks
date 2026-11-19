"""Usage numbers with and without ``usage_hourly`` rows, and the CSV export.

The CSV carries exact numbers, so most assertions read it rather than HTML.
"""
from __future__ import annotations

import re
from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from .helpers import add_daily, add_hourly, add_key, add_route, read_csv, today


def export(admin: TestClient, **params) -> dict[str, dict[str, str]]:
    response = admin.get("/usage.csv", params=params)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    group = params.get("group", "key")
    return {row[group]: row for row in read_csv(response.text)}


@pytest.fixture()
def daily_only(db) -> dict[str, int]:
    """An install whose router has never written usage_hourly."""
    add_route(db, "Gemma", "chat")
    add_route(db, "Embed", "embedding")
    rag = add_key(db, name="rag", owner="Mira")
    lab = add_key(db, name="lab", owner="Teaching Lab")
    add_daily(db, rag, "Gemma", 300, 6, days_ago=0, owner="Mira")
    add_daily(db, rag, "Gemma", 200, 4, days_ago=3, owner="Mira")
    add_daily(db, rag, "Embed", 1000, 0, days_ago=3, owner="Mira")
    add_daily(db, lab, "Gemma", 50, 5, days_ago=1, owner="Teaching Lab")
    add_daily(db, lab, "Gemma", 7777, 0, days_ago=40, owner="Teaching Lab")  # outside 30 days
    add_daily(db, 4242, "Embed", 20, 1, days_ago=2, owner="Gone")           # a deleted key
    return {"rag": rag, "lab": lab}


@pytest.fixture()
def with_hourly(db, daily_only) -> dict[str, int]:
    """The same install after the router started writing usage_hourly.

    The hourly request counts deliberately differ from the daily ones, so a
    test can tell which table a number came from.
    """
    rag, lab = daily_only["rag"], daily_only["lab"]
    add_hourly(db, rag, "Gemma", "gemma-4", "ok", 280, prompt=560_000, completion=140_000, duration_ms=1_400_000, days_ago=0, owner="Mira")
    add_hourly(db, rag, "Gemma", "google/gemma-4-it", "ok", 10, prompt=20_000, completion=5_000, duration_ms=60_000, days_ago=0, owner="Mira")
    add_hourly(db, rag, "Gemma", "gemma-4", "backend_error", 4, duration_ms=8_000, days_ago=0, owner="Mira")
    add_hourly(db, rag, "Gemma", "gemma-4", "client_closed", 3, prompt=6_000, duration_ms=3_000, days_ago=0, owner="Mira")
    add_hourly(db, rag, "Gemma", "gemma-4", "rate_limited", 25, days_ago=0, owner="Mira")
    add_hourly(db, rag, "Gemma", "gemma-4", "unavailable", 5, days_ago=0, owner="Mira")
    add_hourly(db, lab, "Gemma", "gemma-4", "timeout", 2, duration_ms=1_200_000, days_ago=1, owner="Teaching Lab")
    add_hourly(db, lab, "Gemma", "gemma-4", "ok", 40, prompt=80_000, completion=20_000, duration_ms=200_000, days_ago=1, owner="Teaching Lab")
    return daily_only


# ------------------------------------------------------- without usage_hourly
def test_daily_only_totals_by_key(admin: TestClient, daily_only):
    rows = export(admin, group="key")
    assert set(rows) == {"rag", "lab", "Deleted key #4242"}
    assert (rows["rag"]["requests"], rows["rag"]["errors"], rows["rag"]["error_rate"]) == ("1500", "10", "0.0067")
    assert (rows["lab"]["requests"], rows["lab"]["errors"]) == ("50", "5"), "the 40-day-old row is outside the default 30 days"
    assert rows["Deleted key #4242"]["key_id"] == "4242" and rows["Deleted key #4242"]["key_prefix"] == ""
    assert rows["rag"]["owner"] == "Mira" and rows["rag"]["key_prefix"].startswith("sk-mo-")
    for row in rows.values():
        assert (row["prompt_tokens"], row["completion_tokens"], row["total_tokens"]) == ("0", "0", "0")
        assert row["avg_duration_ms"] == "" and row["rejected"] == "0"


def test_daily_only_by_owner_and_route(admin: TestClient, daily_only):
    owners = export(admin, group="owner")
    assert {k: v["requests"] for k, v in owners.items()} == {"Mira": "1500", "Teaching Lab": "50", "Gone": "20"}
    routes = export(admin, group="route")
    assert {k: (v["requests"], v["errors"]) for k, v in routes.items()} == {"Gemma": ("550", "15"), "Embed": ("1020", "1")}


def test_daily_only_has_nothing_by_model_or_outcome(admin: TestClient, daily_only):
    assert export(admin, group="model") == {}
    assert export(admin, group="outcome") == {}
    page = admin.get("/usage?group=model").text
    assert "Nothing to group by model here" in page and "none has been recorded yet" in page
    assert 'href="/usage?' in page and "group=route" in page


def test_daily_only_pages_look_right(admin: TestClient, daily_only):
    home = admin.get("/?range=7d").text
    assert re.search(r'<dd class="value">1,570</dd>', home), "300 + 200 + 1000 + 50 + 20 requests in 7 days"
    assert "No hourly usage recorded yet" in home
    assert "Deleted key #4242" in home and ">rag<" in home
    assert "1.0%" in home  # 16 errors of 1,570

    day = admin.get("/?range=24h")
    assert day.status_code == 200
    assert "Hourly usage has not been recorded yet" in day.text
    assert re.search(r'<dd class="value">350</dd>', day.text), "today and yesterday from the daily table"

    assert admin.get("/?range=30d").status_code == 200
    usage_page = admin.get("/usage").text
    assert "Tokens and durations appear once hourly usage is recorded" in usage_page
    detail = admin.get(f"/keys/{daily_only['rag']}").text
    assert re.search(r'<dd class="value">1,500</dd>', detail)
    assert "none has been recorded yet" in detail
    models = admin.get("/models").text
    assert "Gemma" in models and "Embed" in models


def test_date_range(admin: TestClient, daily_only):
    start = (today() - timedelta(days=45)).isoformat()
    rows = export(admin, group="key", **{"from": start, "to": today().isoformat()})
    assert rows["lab"]["requests"] == "7827", "a longer range reaches the old row"
    single = export(admin, group="route", **{"from": today().isoformat(), "to": today().isoformat()})
    assert {k: v["requests"] for k, v in single.items()} == {"Gemma": "300"}
    swapped = admin.get("/usage", params={"from": today().isoformat(), "to": start})
    assert "have been swapped" in swapped.text
    huge = admin.get("/usage", params={"from": "2001-01-01", "to": today().isoformat()})
    assert "at most 366 days" in huge.text
    assert admin.get("/usage", params={"from": "yesterday", "to": "31/12/2026", "group": "planet"}).status_code == 200


# ---------------------------------------------------------- with usage_hourly
def test_requests_stay_daily_and_tokens_come_from_hourly(admin: TestClient, with_hourly):
    rows = export(admin, group="key")
    rag = rows["rag"]
    assert (rag["requests"], rag["errors"]) == ("1500", "10"), "requests and errors are still the usage_daily numbers"
    assert (rag["prompt_tokens"], rag["completion_tokens"], rag["total_tokens"]) == ("586000", "145000", "731000")
    assert rag["rejected"] == "30", "rate_limited + unavailable, counted apart from requests"
    assert rag["avg_duration_ms"] == "4953", "(1,400,000 + 60,000 + 8,000 + 3,000) ms over 297 forwarded requests"
    lab = rows["lab"]
    assert (lab["requests"], lab["errors"], lab["total_tokens"], lab["rejected"]) == ("50", "5", "100000", "0")
    assert rows["Deleted key #4242"]["total_tokens"] == "0"


def test_by_model_uses_the_hourly_table_with_daily_definitions(admin: TestClient, with_hourly):
    rows = export(admin, group="model")
    assert set(rows) == {"gemma-4", "google/gemma-4-it"}
    main = rows["gemma-4"]
    # forwarded: ok 280 + backend_error 4 + client_closed 3 + timeout 2 + ok 40; refused ones are not requests
    assert main["requests"] == "329"
    assert main["errors"] == "6", "backend_error and timeout; client_closed is not an error"
    assert main["rejected"] == "30"
    assert rows["google/gemma-4-it"]["requests"] == "10" and rows["google/gemma-4-it"]["total_tokens"] == "25000"


def test_by_outcome(admin: TestClient, with_hourly):
    rows = export(admin, group="outcome")
    assert {v["outcome_code"]: v["requests"] for v in rows.values()} == {
        "ok": "330", "rate_limited": "25", "unavailable": "5", "backend_error": "4", "client_closed": "3", "timeout": "2",
    }
    assert rows["Completed"]["total_tokens"] == "825000"
    assert rows["Timed out"]["avg_duration_ms"] == "600000"
    assert rows["Rate limited"]["errors"] == "" and rows["Rate limited"]["rejected"] == ""
    page = admin.get("/usage?group=outcome").text
    for label in ("Completed", "Rate limited", "Model unavailable", "Backend error (5xx)", "Client disconnected", "Timed out"):
        assert label in page


def test_per_day_rows(admin: TestClient, with_hourly):
    response = admin.get("/usage.csv", params={"group": "route", "by": "day"})
    rows = read_csv(response.text)
    assert list(rows[0])[:2] == ["day", "route"]
    by_day = {(r["day"], r["route"]): r for r in rows}
    assert by_day[(today().isoformat(), "Gemma")]["requests"] == "300"
    assert by_day[((today() - timedelta(days=3)).isoformat(), "Embed")]["requests"] == "1000"
    assert [r["day"] for r in rows] == sorted(r["day"] for r in rows)
    assert "Per day by route" in admin.get("/usage?group=route&by=day").text


def test_overview_with_hourly(admin: TestClient, with_hourly):
    home = admin.get("/?range=7d").text
    assert re.search(r'<dd class="value">1,570</dd>', home)
    assert re.search(r'<dd class="value">831K</dd>', home), "tokens of the last 7 days"
    assert "No hourly usage recorded yet" not in home

    day = admin.get("/?range=24h").text
    assert "Hourly usage has not been recorded yet" not in day
    assert re.search(r'<dd class="value">297</dd>', day), "forwarded requests of the last 24 hours, from hourly rows"
    assert "per hour, the last 24 hours" in day


def test_token_coverage_note_when_hourly_history_is_shorter(admin: TestClient, with_hourly):
    assert "Counted since" in admin.get("/?range=30d").text
    assert "Tokens and durations counted since" in admin.get("/usage").text


def test_key_detail_with_hourly(admin: TestClient, with_hourly):
    page = admin.get(f"/keys/{with_hourly['rag']}").text
    assert re.search(r'<dd class="value">1,500</dd>', page) and "731K" in page
    for text in ("gemma-4", "google/gemma-4-it", "Rate limited", "Model unavailable", "Tokens per day"):
        assert text in page, text


# ------------------------------------------------------------------------ CSV
def test_csv_download_headers(admin: TestClient, daily_only):
    response = admin.get("/usage.csv?group=owner")
    name = f"usage-by-owner-{(today() - timedelta(days=29)).isoformat()}-to-{today().isoformat()}.csv"
    assert response.headers["content-disposition"] == f'attachment; filename="{name}"'
    assert response.headers["cache-control"] == "no-store"
    lines = response.text.split("\r\n")
    assert lines[0] == "from,to,owner,requests,errors,error_rate,rejected,prompt_tokens,completion_tokens,total_tokens,avg_duration_ms"
    assert lines[1].startswith(f"{(today() - timedelta(days=29)).isoformat()},{today().isoformat()},Mira,1500,10,")


def test_csv_neutralises_spreadsheet_formulas(admin: TestClient, db):
    key = add_key(db, name='=HYPERLINK("http://example.org","x")', owner="+1+1")
    add_daily(db, key, "Gemma", 5, owner="+1+1")
    add_daily(db, key, "@route", 5, owner="+1+1")
    by_key = read_csv(admin.get("/usage.csv?group=key").text)[0]
    assert by_key["key"].startswith("'=HYPERLINK") and by_key["owner"] == "'+1+1"
    assert {r["route"] for r in read_csv(admin.get("/usage.csv?group=route").text)} == {"Gemma", "'@route"}


def test_empty_database_has_good_empty_states(admin: TestClient, db):
    assert read_csv(admin.get("/usage.csv").text) == []
    assert "No usage in this period" in admin.get("/usage").text
    home = admin.get("/").text
    assert "No routes yet" in home and "There are no keys yet." in home and "Nothing needs attention" in home
    assert "The log is empty" not in admin.get("/audit").text, "the sign-in itself is already logged"
    assert "No routes yet" in admin.get("/models").text
