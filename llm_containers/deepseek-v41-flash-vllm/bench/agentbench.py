#!/usr/bin/env python3
"""Agent-style benchmark: N agents, each with its own large cached context, working turn by turn.

Each agent holds one chat conversation: a system prompt, a user message that carries a "repository" of about --context
tokens of real source code plus a task, then turns. The repositories differ per agent (corpus/agent<i>.jsonl), so the
agents share no prefix: each needs its own room in the KV cache, and their text needs different experts.

Phases for one context size:
  cold    every agent's first turn, one agent at a time: how long a new context takes to read (time to first token).
  settle  before each cell, every agent of the cell touches its context once (1 token, not part of the result), so the
          cell starts from whatever the cache can hold.
  steady  for each agent count N in --agents: agents 0..N-1 run --turns turns at once, each sending its next turn as
          soon as the last one finished. A turn adds new files (about --new-tokens tokens) and a question, and
          generates --max-tokens tokens (the answer is cut there, so every turn costs the same).
Per cell: time to first token (median, p90, max), decode speed per stream, total generated tokens per second over the
cell's wall-clock time, and the share of prompt tokens the server took from its prefix cache (from /metrics).

Usage: agentbench.py --model NAME --corpus DIR --context 385000 --agents 1,5,8 --out agents-385000.json
"""
import argparse, asyncio, json, os, random, statistics, time
import aiohttp

p = argparse.ArgumentParser()
p.add_argument("--url", default="http://127.0.0.1:8000")
p.add_argument("--model", required=True)
p.add_argument("--corpus", required=True)
p.add_argument("--context", type=int, required=True, help="tokens of source code in each agent's first message")
p.add_argument("--agents", default="1,5,8")
p.add_argument("--turns", type=int, default=3)
p.add_argument("--max-tokens", type=int, default=600)
p.add_argument("--new-tokens", type=int, default=1500)
p.add_argument("--temperature", type=float, default=1.0)
p.add_argument("--top-p", type=float, default=0.95)
p.add_argument("--no-thinking", action="store_true")
p.add_argument("--seed", type=int, default=5)
p.add_argument("--repeats", type=int, default=1, help="run the whole list of agent counts this many times")
p.add_argument("--burst", action="store_true",
               help="only this: all agents send their first turn (a new context each) at the same moment")
p.add_argument("--out", required=True)
a = p.parse_args()
COUNTS = sorted({int(x) for x in a.agents.split(",")})
NMAX = max(COUNTS)
RUN = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
T0 = time.perf_counter()
WRAP = 16   # tokens the "### File: path" line and the code fence add per file, roughly
FALLBACK = "I need to look at more of the code before I can give the final answer."
COUNTERS = ("vllm:prefix_cache_hits", "vllm:prefix_cache_queries", "vllm:num_preemptions", "vllm:prompt_tokens",
            "vllm:generation_tokens")
GAUGES = ("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc")   # the name differs between vLLM versions


SPECIAL = "<｜"   # the model's special tokens are spelled <|name|> with full-width bars; the server answers
                      # HTTP 400 to message text that holds its image token, so files that spell one are left out


def load(path):
    with open(path) as f:
        return [e for e in map(json.loads, f) if SPECIAL not in e["text"]]


EXTRA = load(os.path.join(a.corpus, "extra.jsonl"))


def block(e, head="File"):
    return f"### {head}: {e['path']}\n```python\n{e['text']}\n```"


class Agent:
    def __init__(self, i):
        self.i = i
        self.rng = random.Random(a.seed * 1000 + i)
        files, total = [], 0
        for e in load(os.path.join(a.corpus, f"agent{i}.jsonl")):
            if total + e["ntok"] + WRAP > a.context:
                continue   # does not fit any more; a smaller file further down may
            files.append(e); total += e["ntok"] + WRAP
        self.files, self.ctx_tokens = files, total
        target = self.rng.choice(files)["path"]
        # The session id comes first, so nothing is shared between agents or with an earlier run on the same server.
        system = (f"Session {RUN}-{i}. You are a coding agent working in the repository the user shows you. "
                  "Answer precisely and refer to files by their path.")
        first = (f"This is the repository you are working in ({len(files)} files).\n\n"
                 + "\n\n".join(block(e) for e in files)
                 + f"\n\nTask: write a detailed technical overview of `{target}`: its purpose, its main classes and "
                   "functions, and how the rest of the repository uses it. Be thorough and concrete.")
        self.base = [{"role": "system", "content": system}, {"role": "user", "content": first}]

    def next_user(self):
        parts, n = [], 0
        while n < a.new_tokens:
            e = self.rng.choice(EXTRA); parts.append(block(e, "Result of read_file")); n += e["ntok"] + WRAP
        other = self.rng.choice(self.files)["path"]
        return ("\n\n".join(parts) + f"\n\nExplain in detail how the code above relates to `{other}` from the "
                "repository, then propose two concrete refactorings and write the code for them.")


def emit(rec):
    print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in rec.items()}), flush=True)


async def metrics(http):
    """Sums of the server's counters we compare before and after a cell (label sets added up)."""
    out = dict.fromkeys(COUNTERS, 0.0)
    try:
        async with http.get(a.url + "/metrics") as r:
            for line in (await r.text()).splitlines():
                if line.startswith("#"):
                    continue
                name = line.split("{", 1)[0].split(" ", 1)[0]
                for c in COUNTERS:
                    if name in (c, c + "_total"):
                        out[c] += float(line.rsplit(" ", 1)[1])
                if name in GAUGES:   # share of the KV cache in use, 0..1
                    out["kv_usage"] = max(out.get("kv_usage", 0.0), float(line.rsplit(" ", 1)[1]))
    except Exception as e:  # noqa: BLE001 - the benchmark goes on without the counters
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


async def kv_peak(http, stop):
    """Highest share of the KV cache in use while a cell runs, sampled once a second. The gauge counts the blocks that
    running requests hold, so it is about zero between cells and only the peak says how full the cache gets."""
    peak = None
    while not stop.is_set():
        v = (await metrics(http)).get("kv_usage")
        if v is not None:
            peak = max(peak or 0.0, v)
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
    return peak


async def one(http, ag, msgs, phase, cell, turn, max_tokens=None):
    body = {"model": a.model, "messages": msgs, "max_tokens": max_tokens or a.max_tokens,
            "temperature": a.temperature, "top_p": a.top_p, "seed": a.seed * 100000 + ag.i * 1000 + cell * 10 + turn,
            "stream": True, "stream_options": {"include_usage": True}}
    if a.no_thinking:
        body["chat_template_kwargs"] = {"thinking": False}
    rec = {"agent": ag.i, "phase": phase, "cell": cell, "turn": turn, "start_s": time.perf_counter() - T0}
    t0 = time.perf_counter(); first = last = None; usage = None; finish = None; content = []; reasoning = 0
    try:
        async with http.post(a.url + "/v1/chat/completions", json=body) as r:
            if r.status != 200:
                rec["error"] = f"http {r.status}: {(await r.text())[:300]}"
                return rec, ""
            buf = b""; done = False   # SSE by hand: aiohttp's line iterator refuses very long lines
            async for part in r.content.iter_any():
                buf += part
                while b"\n" in buf and not done:
                    raw, buf = buf.split(b"\n", 1)
                    raw = raw.strip()
                    if not raw.startswith(b"data:"):
                        continue
                    data = raw[5:].strip()
                    if data == b"[DONE]":
                        done = True
                        break
                    ch = json.loads(data)
                    if ch.get("error"):
                        rec["error"] = f"error event: {json.dumps(ch['error'])[:300]}"
                    if ch.get("usage"):
                        usage = ch["usage"]
                    for c in ch.get("choices") or []:
                        d = c.get("delta") or {}
                        txt = d.get("content"); rs = d.get("reasoning") or d.get("reasoning_content")
                        if txt or rs:
                            last = time.perf_counter()
                            if first is None:
                                first = last
                            if txt:
                                content.append(txt)
                            if rs:
                                reasoning += len(rs)
                        if c.get("finish_reason"):
                            finish = c["finish_reason"]
                if done:
                    break
    except Exception as e:  # noqa: BLE001 - transport errors are a result
        rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return rec, ""
    u = usage or {}
    ct = u.get("completion_tokens")
    rec.update(ttft_s=(first - t0) if first else None, decode_s=(last - first) if first else None,
               e2e_s=time.perf_counter() - t0, prompt_tokens=u.get("prompt_tokens"), completion_tokens=ct,
               cached_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens"), finish=finish,
               content_chars=sum(map(len, content)), reasoning_chars=reasoning)
    if ct and ct > 1 and first and last > first:
        rec["decode_tps"] = (ct - 1) / (last - first)
    return rec, "".join(content)


async def agent_loop(http, ag, cell, rep):
    msgs = list(ag.base); out = []
    for turn in range(1, a.turns + 1):
        msgs.append({"role": "user", "content": ag.next_user()})
        rec, text = await one(http, ag, msgs, "steady", cell, turn)
        rec["rep"] = rep
        emit(rec); out.append(rec)
        if "error" in rec:
            break
        msgs.append({"role": "assistant", "content": text or FALLBACK})
    return out


def med(v):
    return statistics.median(v) if v else None


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))] if v else None


async def main():
    agents = [Agent(i) for i in range(NMAX)]
    res = {"run": RUN, "args": vars(a), "context_tokens": [ag.ctx_tokens for ag in agents], "cold": [], "cells": []}
    print(f"agentbench run {RUN}: context target {a.context}, built {res['context_tokens']}, agents {COUNTS}", flush=True)
    to = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=3600)
    async with aiohttp.ClientSession(timeout=to, connector=aiohttp.TCPConnector(limit=0)) as http:
        if a.burst:   # every agent starts a new context at the same moment: the worst case after a restart
            m0 = await metrics(http); stop = asyncio.Event(); watch = asyncio.create_task(kv_peak(http, stop))
            t0 = time.perf_counter()
            pairs = await asyncio.gather(*[one(http, ag, ag.base, "burst", NMAX, 0) for ag in agents])
            wall = time.perf_counter() - t0; stop.set(); peak = await watch; m1 = await metrics(http)
            recs = [r for r, _ in pairs]
            for r in recs:
                emit(r)
            ok = [r for r in recs if "error" not in r and r.get("ttft_s") is not None]
            tt = sorted(r["ttft_s"] for r in ok)
            res["burst"] = {"agents": NMAX, "errors": len(recs) - len(ok), "wall_s": wall,
                            "ttft_median_s": med(tt), "ttft_max_s": tt[-1] if tt else None,
                            "prompt_tokens_total": sum(r.get("prompt_tokens") or 0 for r in ok),
                            "read_tps_total": (sum(r.get("prompt_tokens") or 0 for r in ok) / tt[-1]) if tt else None,
                            "decode_tps_per_stream_median": med([r["decode_tps"] for r in ok if r.get("decode_tps")]),
                            "preemptions": m1["vllm:num_preemptions"] - m0["vllm:num_preemptions"],
                            "kv_usage_peak": peak, "requests_detail": recs}
            print("SUMMARY burst", json.dumps({k: (round(v, 3) if isinstance(v, float) else v)
                                               for k, v in res["burst"].items() if k != "requests_detail"}), flush=True)
            with open(a.out, "w") as f:
                json.dump(res, f, indent=1)
            return
        for ag in agents:   # cold: one new context at a time
            rec, text = await one(http, ag, ag.base, "cold", 0, 0)
            emit(rec); res["cold"].append(rec)
            ag.base = ag.base + [{"role": "assistant", "content": text or FALLBACK}]
        cold = [r for r in res["cold"] if r.get("ttft_s")]
        res["cold_summary"] = {"ttft_median_s": med([r["ttft_s"] for r in cold]),
                               "read_tps_median": med([r["prompt_tokens"] / r["ttft_s"] for r in cold if r.get("prompt_tokens")]),
                               "prompt_tokens_median": med([r["prompt_tokens"] for r in cold if r.get("prompt_tokens")]),
                               "errors": len(res["cold"]) - len(cold)}
        print("SUMMARY cold", json.dumps(res["cold_summary"]), flush=True)
        for rep, n in [(rep, n) for rep in range(1, a.repeats + 1) for n in COUNTS]:
            settle = []
            for ag in agents[:n]:
                rec, _ = await one(http, ag, ag.base + [{"role": "user", "content": "Reply with OK."}], "settle", n, 0, max_tokens=1)
                emit(rec); settle.append(rec)
            m0 = await metrics(http); stop = asyncio.Event(); watch = asyncio.create_task(kv_peak(http, stop))
            t0 = time.perf_counter()
            per = await asyncio.gather(*[agent_loop(http, ag, n, rep) for ag in agents[:n]])
            wall = time.perf_counter() - t0; stop.set(); peak = await watch; m1 = await metrics(http)
            recs = [r for rs in per for r in rs]
            ok = [r for r in recs if "error" not in r and r.get("ttft_s") is not None]
            tt = sorted(r["ttft_s"] for r in ok)
            dq = m1["vllm:prefix_cache_queries"] - m0["vllm:prefix_cache_queries"]
            cell = {"agents": n, "rep": rep, "kv_usage_peak": peak,
                    "requests": len(recs), "errors": len(recs) - len(ok), "wall_s": wall,
                    "ttft_median_s": med(tt), "ttft_p90_s": pct(tt, 0.9), "ttft_max_s": tt[-1] if tt else None,
                    "decode_tps_per_stream_median": med([r["decode_tps"] for r in ok if r.get("decode_tps")]),
                    "total_tps": sum(r.get("completion_tokens") or 0 for r in ok) / wall if wall > 0 else None,
                    "prompt_tokens_median": med([r["prompt_tokens"] for r in ok if r.get("prompt_tokens")]),
                    "cache_share": (m1["vllm:prefix_cache_hits"] - m0["vllm:prefix_cache_hits"]) / dq if dq > 0 else None,
                    "preemptions": m1["vllm:num_preemptions"] - m0["vllm:num_preemptions"],
                    "settle_ttft_s": [r.get("ttft_s") for r in settle]}
            print("SUMMARY cell", json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in cell.items()
                                                if k != "settle_ttft_s"}), flush=True)
            cell["requests_detail"] = recs; cell["settle_detail"] = settle
            res["cells"].append(cell)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


asyncio.run(main())
