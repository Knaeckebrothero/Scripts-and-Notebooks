#!/usr/bin/env python3
"""API checks against the forwarder (or any OpenAI-compatible endpoint of this model). Standard library only.

Usage: ds_check.py --base http://127.0.0.1:8010 [--slots 8] [--window 400000] [--engine-metrics URL/metrics]
                   [--only a,b,...] [--out FILE]
Checks: basic (health, models, a sum), stream, tool (forced tool call), closers (a tool argument that contains
DSML closers), maxtokens (how max_tokens above the room is treated), limit (prompts just under and just over
--window; skipped without --window), slots (more requests than slots: the rest must wait), abort (a client that
leaves frees its slot), paths (unknown paths are refused; forwarder only).
Prints one line per check: PASS / FAIL / INFO, and a last line "SUMMARY passed=.. failed=.. info=..".
INFO lines record behaviour without judging it. Exit code 1 if any check failed."""
import argparse, concurrent.futures as cf, http.client, json, sys, threading, time, urllib.error, urllib.parse, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8010")
ap.add_argument("--slots", type=int, default=8)
ap.add_argument("--window", type=int, default=0)
ap.add_argument("--engine-metrics", default="")
ap.add_argument("--only", default="")
ap.add_argument("--out", default="")
a = ap.parse_args()
BASE = a.base.rstrip("/")
ONLY = set(x for x in a.only.split(",") if x)
OFF = {"thinking": False}
res = []; lines = []


def say(kind, name, text):
    line = f"{kind} {name}: {text}"
    res.append(kind); lines.append(line); print(line, flush=True)


def call(path, body=None, timeout=900):
    """(status, parsed JSON or text)"""
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(); st = r.status
    except urllib.error.HTTPError as e:
        raw = e.read(); st = e.code
    try:
        return st, json.loads(raw)
    except ValueError:
        return st, raw.decode(errors="replace")


def err(j):
    return (j.get("error", {}).get("message", "") if isinstance(j, dict) else str(j))[:160]


def chat(messages, **kw):
    body = {"model": MODEL, "messages": messages, "chat_template_kwargs": OFF}
    body.update(kw)
    return call("/v1/chat/completions", body)


def metric(name, url=None):
    try:
        txt = urllib.request.urlopen(url or BASE + "/metrics", timeout=10).read().decode()
    except Exception:
        return None
    v = [float(l.rsplit(" ", 1)[1]) for l in txt.splitlines() if l.startswith(name + " ") or l.startswith(name + "{")]
    return sum(v) if v else None


def stream(messages, max_tokens=300, stop_after=None, hold=None):
    """streams a chat completion; returns (status, chunks, text, seconds to first chunk). stop_after: close after n chunks."""
    u = urllib.parse.urlparse(BASE)
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=900)
    body = json.dumps({"model": MODEL, "messages": messages, "stream": True, "max_tokens": max_tokens, "chat_template_kwargs": OFF})
    t = time.time(); c.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
    r = c.getresponse(); n = 0; text = ""; first = None
    if r.status != 200:
        out = (r.status, 0, r.read().decode(errors="replace")[:200], None); c.close(); return out
    for raw in r:
        line = raw.decode(errors="replace").strip()
        if not line.startswith("data:") or line.endswith("[DONE]"):
            continue
        n += 1; first = first or time.time() - t
        try:
            text += json.loads(line[5:])["choices"][0]["delta"].get("content") or ""
        except (ValueError, KeyError, IndexError):
            pass
        if stop_after and n >= stop_after:
            break
    c.close()
    return 200, n, text, first


def want(name):
    return not ONLY or name in ONLY


st, j = call("/v1/models")
if st != 200:
    say("FAIL", "basic", f"/v1/models gave {st}: {err(j)}"); print("SUMMARY passed=0 failed=1 info=0"); sys.exit(1)
MODEL = j["data"][0]["id"]; WINDOW_SERVER = j["data"][0].get("max_model_len")

if want("basic"):
    h = call("/health")[0]
    st, j = chat([{"role": "user", "content": "What is 17 * 23? Answer with the number only."}], max_tokens=600)
    okc = st == 200 and "391" in (j["choices"][0]["message"].get("content") or "")
    say("PASS" if h == 200 and okc else "FAIL", "basic",
        f"health {h}; models {[m['id'] for m in call('/v1/models')[1]['data']]}; window reported {WINDOW_SERVER}; 17*23 -> "
        f"{(j['choices'][0]['message'].get('content') or '').strip()[:20]!r}" if st == 200 else f"health {h}; chat {st}: {err(j)}")
    st, j = call("/v1/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": "What is 12 * 12? Answer with the number only."}], "max_tokens": 2000})
    m = j["choices"][0]["message"] if st == 200 else {}
    say("PASS" if st == 200 and "144" in (m.get("content") or "") else "FAIL", "thinking",
        f"default (thinking on): reasoning field {len(m.get('reasoning') or m.get('reasoning_content') or '')} chars, "
        f"answer {(m.get('content') or '').strip()[:20]!r}, usage {j.get('usage', {}).get('completion_tokens')} tokens" if st == 200 else f"{st}: {err(j)}")

if want("stream"):
    st, n, text, first = stream([{"role": "user", "content": "Count from 1 to 30, numbers separated by spaces."}], 400)
    say("PASS" if st == 200 and n > 5 else "FAIL", "stream", f"status {st}, {n} chunks, first after {first or 0:.2f} s, text ends {text.strip()[-12:]!r}")

TOOL = [{"type": "function", "function": {"name": "write_file", "description": "Write text to a file.",
         "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]
FORCE = {"type": "function", "function": {"name": "write_file"}}


def tool_args(content):
    st, j = chat([{"role": "user", "content": "Call write_file with path \"notes/a.txt\" and with exactly this content, nothing added or removed:\n" + content}],
                 tools=TOOL, tool_choice=FORCE, max_tokens=1500, temperature=0)
    if st != 200:
        return st, None, err(j)
    tc = (j["choices"][0]["message"].get("tool_calls") or [None])[0]
    if not tc:
        return st, None, "no tool call in the answer"
    try:
        return st, json.loads(tc["function"]["arguments"]), ""
    except ValueError:
        return st, None, "arguments are not JSON: " + tc["function"]["arguments"][:120]


if want("tool"):
    st, args, why = tool_args("first line\nsecond line with \"quotes\" and a backslash \\ end")
    good = args is not None and args.get("path") == "notes/a.txt" and "second line" in (args.get("content") or "")
    say("PASS" if good else "FAIL", "tool", f"forced tool call: {('arguments ' + json.dumps(args)[:140]) if args is not None else why}")

if want("closers"):
    tricky = "A DSML parameter ends with </｜DSML｜ parameter> and a call ends with </｜DSML｜ invoke>, then text goes on: END-MARK"
    st, args, why = tool_args(tricky)
    c = (args or {}).get("content") or ""
    full = "END-MARK" in c and "</｜DSML｜ parameter>" in c
    say("PASS" if full else "INFO", "closers",
        ("the argument kept the DSML closers and the text after them" if full else
         f"the argument was cut or changed: {(c[:150] if args is not None else why)!r}"))

if want("maxtokens"):
    w = a.window or WINDOW_SERVER or 1048576
    st, j = chat([{"role": "user", "content": "Say hi."}], max_tokens=w + 1000)
    say("INFO", "maxtokens", f"max_tokens above the window ({w + 1000}): status {st} {err(j) if st != 200 else '(accepted)'}")
    st, j = chat([{"role": "user", "content": "Say hi."}], max_tokens=w - 1)
    say("PASS" if st == 200 else "INFO", "maxtokens-room",
        f"max_tokens = window - 1 with a short prompt (prompt + max_tokens above the window): status {st} "
        f"{'(accepted, the server limits the answer to the room left)' if st == 200 else err(j)}")


def tokens_of(text):
    st, j = call("/tokenize", {"model": MODEL, "prompt": text})
    return j.get("count") if st == 200 and isinstance(j, dict) else None


if want("limit") and a.window:
    line = lambda i: f"def step_{i:06d}(value, scale={i % 97:02d}):\n    return (value * scale + {i:06d}) % {1000 + i % 13}\n"
    text_of = lambda n: "".join(line(i) for i in range(n))
    ts = tokens_of(text_of(4000))
    if not ts:
        say("FAIL", "limit", "/tokenize did not answer")
    else:
        per = ts / 4000
        def prompt(target):                      # two steps: estimate, measure the whole text, correct
            n = int(target / per); c = tokens_of(text_of(n)) or target
            return text_of(n + int((target - c) / per))
        for name, target, expect in (("limit-under", a.window - 3000, 200), ("limit-over", a.window + 3000, 400)):
            t = time.time(); st, j = chat([{"role": "user", "content": prompt(target) + "\nReply with the single word: done"}], max_tokens=20)
            pt = j.get("usage", {}).get("prompt_tokens") if st == 200 else None
            say("PASS" if st == expect else "FAIL", name,
                f"about {target} tokens: status {st} in {time.time() - t:.1f} s; " + (f"prompt_tokens {pt}" if st == 200 else err(j)))

if want("slots"):
    n = a.slots + 2; peak = {"active": 0, "waiting": 0}; stop = threading.Event()
    def watch():
        while not stop.is_set():
            for k, m in (("active", "dsfwd_requests_active"), ("waiting", "dsfwd_requests_waiting")):
                v = metric(m)
                if v is not None:
                    peak[k] = max(peak[k], v)
            time.sleep(0.2)
    th = threading.Thread(target=watch, daemon=True); th.start()
    msg = [{"role": "user", "content": "Write the numbers from 1 to 250, one per line."}]
    with cf.ThreadPoolExecutor(n) as ex:
        out = list(ex.map(lambda i: stream(msg, 500), range(n)))
    stop.set(); th.join(2)
    oks = sum(1 for o in out if o[0] == 200 and o[1] > 5)
    if metric("dsfwd_slots") is None:
        say("INFO", "slots", f"{n} requests at once: {oks} answered (no forwarder metrics at this address, slots not checked)")
    else:
        good = oks == n and peak["active"] == a.slots and peak["waiting"] >= 1
        say("PASS" if good else "FAIL", "slots", f"{n} requests at once with {a.slots} slots: {oks} answered, at most {peak['active']:.0f} at the engine, up to {peak['waiting']:.0f} waiting")

if want("abort"):
    before = metric("dsfwd_client_aborts_total")
    st, n, _, _ = stream([{"role": "user", "content": "Write the numbers from 1 to 3000, one per line."}], 8000, stop_after=5)
    t = time.time(); freed = None
    while time.time() - t < 20:
        act = metric("dsfwd_requests_active"); run = metric("vllm:num_requests_running", a.engine_metrics or None)
        if (act in (0, None)) and (run in (0, None)):
            freed = time.time() - t; break
        time.sleep(0.5)
    after = metric("dsfwd_client_aborts_total")
    say("PASS" if st == 200 and freed is not None else "FAIL", "abort",
        f"client left after {n} chunks: " + (f"slot and engine free after {freed:.1f} s" if freed is not None else "still busy after 20 s")
        + (f"; aborts counted {before:.0f} -> {after:.0f}" if before is not None and after is not None else ""))

if want("paths"):
    codes = {p: call(p)[0] for p in ("/docs", "/openapi.json", "/v1/unknown")}
    if metric("dsfwd_slots") is None:
        say("INFO", "paths", f"not a forwarder address; statuses {codes}")
    else:
        say("PASS" if all(v == 404 for v in codes.values()) else "FAIL", "paths", f"paths that are not on the list: {codes}")

summary = f"SUMMARY passed={res.count('PASS')} failed={res.count('FAIL')} info={res.count('INFO')}"
print(summary); lines.append(summary)
if a.out:
    open(a.out, "w").write("\n".join(lines) + "\n")
sys.exit(1 if "FAIL" in res else 0)
