#!/usr/bin/env python3
"""Warm-session capacity test.

N sessions, each with its own fixed random prefix of L tokens. Turns run in round-robin order
(s1t1, s2t1, ..., sNt1, s1t2, ...), one request at a time. Turn 1 of a session is a cold prefill;
a later turn is fast only if that session's prefix is still in vLLM's prefix cache. If N sessions fit
in the KV pool, every later turn is a hit; one session more and LRU eviction turns them into misses.

Prompts are sent as token ids (/v1/completions accepts a list of ids), so lengths are exact and the
client needs no tokenizer. Usage:
  warm.py --model NAME --vocab 129280 --sessions 7 --prefix 390000 --turns 3 --out warm-7.json
"""
import argparse, asyncio, json, random, statistics, time
import aiohttp

p = argparse.ArgumentParser()
p.add_argument("--url", default="http://127.0.0.1:8000")
p.add_argument("--model", required=True)
p.add_argument("--vocab", type=int, required=True)
p.add_argument("--sessions", type=int, required=True)
p.add_argument("--prefix", type=int, required=True)
p.add_argument("--suffix", type=int, default=512)
p.add_argument("--turns", type=int, default=3)
p.add_argument("--max-tokens", type=int, default=16)
p.add_argument("--seed", type=int, default=11)
p.add_argument("--out", required=True)
a = p.parse_args()

rng = random.Random(a.seed)
lo, hi = 1000, a.vocab - 1000          # stay clear of special tokens at either end
prefixes = [[rng.randrange(lo, hi) for _ in range(a.prefix)] for _ in range(a.sessions)]


async def one(http, sid, turn):
    ids = prefixes[sid] + [rng.randrange(lo, hi) for _ in range(a.suffix)]
    body = {"model": a.model, "prompt": ids, "max_tokens": a.max_tokens, "temperature": 0,
            "ignore_eos": True, "stream": True, "stream_options": {"include_usage": True}}
    t0 = time.perf_counter(); ttft = None; usage = None
    async with http.post(a.url + "/v1/completions", json=body) as r:
        if r.status != 200:
            return {"sid": sid, "turn": turn, "error": f"http {r.status}: {(await r.text())[:300]}"}
        # Parse the SSE stream by hand: aiohttp's line iterator refuses lines over 512 KB, and the server
        # can send one that large for a 390k-token prompt.
        buf = b""; done = False; big = None
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
                if len(data) > 100_000 and big is None:
                    big = {"bytes": len(data), "keys": sorted(ch.keys()),
                           "choice_keys": sorted(ch["choices"][0].keys()) if ch.get("choices") else []}
                if ch.get("usage"):
                    usage = ch["usage"]
                if ttft is None and ch.get("choices") and ch["choices"][0].get("text"):
                    ttft = time.perf_counter() - t0
            if done:
                break
    out = {"sid": sid, "turn": turn, "ttft_s": ttft, "e2e_s": time.perf_counter() - t0, "usage": usage}
    if big:
        out["big_chunk"] = big
    return out


async def main():
    res = []
    to = aiohttp.ClientTimeout(total=3600)
    async with aiohttp.ClientSession(timeout=to) as http:
        for turn in range(1, a.turns + 1):
            for sid in range(a.sessions):
                r = await one(http, sid, turn)
                res.append(r)
                print(json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items() if k != "usage"}), flush=True)
    cold = [r["ttft_s"] for r in res if r.get("turn") == 1 and r.get("ttft_s")]
    later = [r for r in res if r.get("turn", 0) > 1]
    cold_med = statistics.median(cold) if cold else None
    hits = [r for r in later if r.get("ttft_s") and cold_med and r["ttft_s"] < 0.25 * cold_med]
    summary = {"sessions": a.sessions, "prefix": a.prefix, "suffix": a.suffix, "turns": a.turns,
               "cold_ttft_median_s": cold_med,
               "later_ttft_median_s": statistics.median([r["ttft_s"] for r in later if r.get("ttft_s")]) if later else None,
               "later_hits": len(hits), "later_total": len(later),
               "errors": sum(1 for r in res if "error" in r)}
    json.dump({"summary": summary, "requests": res}, open(a.out, "w"), indent=1)
    print("SUMMARY", json.dumps(summary))

asyncio.run(main())
