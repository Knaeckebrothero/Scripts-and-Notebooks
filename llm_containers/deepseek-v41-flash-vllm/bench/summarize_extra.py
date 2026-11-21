#!/usr/bin/env python3
"""Appends the warm-session, needle and repetition results of a bench-step run to its summary (stdout).
Usage: summarize_extra.py <run dir>"""
import glob, json, os, re, sys

D = sys.argv[1]
out = []
facts = open(os.path.join(D, "server-facts.txt"), errors="replace").read() if os.path.exists(os.path.join(D, "server-facts.txt")) else ""
m = re.search(r"GPU KV cache size: ([\d,]+) tokens", facts)
args = [l.strip() for l in open(os.path.join(D, "serve.args")) if l.strip() and not l.startswith("#")] if os.path.exists(os.path.join(D, "serve.args")) else []
def arg(flag):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else "default"
out.append("")
out.append("## Step facts")
out.append(f"- KV pool: {m.group(1) if m else 'unknown'} tokens"
           + (f" = {int(m.group(1).replace(',', '')) / 400_000:.1f} x 400k" if m else ""))
for f in ["--max-model-len", "--max-num-seqs", "--gpu-memory-utilization", "--cpu-offload-gb", "--kv-cache-dtype"]:
    out.append(f"- {f} {arg(f)}")
mm = re.search(r'CoherentGPUMemoryMode: "?([^"\n]*)"?', facts)
out.append(f"- memory mode: {(mm.group(1) or 'NUMA (empty)') if mm else 'unknown'}")

warm = sorted(glob.glob(os.path.join(D, "warm-*.json")), key=lambda p: int(re.search(r"warm-(\d+)", p).group(1)))
if warm:
    out += ["", "## Warm sessions (round-robin, 3 turns, ~390k-token prefix each)",
            "| sessions | cold TTFT median (s) | later-turn TTFT median (s) | later turns that hit the cache | errors |", "|---|---|---|---|---|"]
    for p in warm:
        s = json.load(open(p))["summary"]
        f = lambda v: "" if v is None else f"{v:.2f}"
        out.append(f"| {s['sessions']} | {f(s['cold_ttft_median_s'])} | {f(s['later_ttft_median_s'])} | {s['later_hits']}/{s['later_total']} | {s['errors']} |")

ag = sorted(glob.glob(os.path.join(D, "agents-*.json")), key=lambda p: int(re.search(r"agents-(\d+)", p).group(1)))
if ag:
    f = lambda v, d=1: "" if v is None else f"{v:.{d}f}"
    out += ["", "## Agents, each with its own context of source code (agentbench: thinking on, 600 new tokens per turn)",
            "", "Reading a new context (one agent at a time):", "",
            "| context (prompt tokens) | time to first token, median (s) | reading speed (tokens/s) | errors |", "|---|---|---|---|"]
    runs = [json.load(open(p)) for p in ag]
    for d in runs:
        c = d["cold_summary"]
        out.append(f"| {f(c['prompt_tokens_median'], 0)} | {f(c['ttft_median_s'])} | {f(c['read_tps_median'], 0)} | {c['errors']} |")
    out += ["", "Working turns with the context already read:", "",
            "| context (prompt tokens) | agents at once | time to first token median / p90 / max (s) | tokens/s per agent | tokens/s total | prompt tokens from cache | preemptions | errors |",
            "|---|---|---|---|---|---|---|---|"]
    for d in runs:
        for c in d["cells"]:
            share = "" if c.get("cache_share") is None else f"{100 * c['cache_share']:.1f}%"
            out.append(f"| {f(c['prompt_tokens_median'], 0)} | {c['agents']} | {f(c['ttft_median_s'], 2)} / {f(c['ttft_p90_s'], 2)} / {f(c['ttft_max_s'], 2)} "
                       f"| {f(c['decode_tps_per_stream_median'])} | {f(c['total_tps'])} | {share} | {f(c.get('preemptions'), 0)} | {c['errors']}/{c['requests']} |")

np_ = os.path.join(D, "needle.json")
if os.path.exists(np_):
    d = json.load(open(np_))
    out += ["", "## Long-context checks", "| check | prompt tokens | depth | result | seconds |", "|---|---|---|---|---|"]
    for r in d.get("needle", []):
        out.append(f"| needle | {r.get('tokens')} | {r.get('depth')} | {'pass' if r.get('pass') else 'FAIL ' + str(r.get('answer', r.get('error', '')))[:40]} | {r.get('seconds', '')} |")
    for r in d.get("rep", []):
        res = r.get("error") or (f"max 12-gram repeat {r.get('max_repeat_12gram')}, {'LOOP?' if r.get('loop_suspected') else 'ok'}; "
                                 f"{r.get('completion_tokens')} tokens, finish={r.get('finish')}, reasoning field {r.get('reasoning_field')}")
        out.append(f"| repetition | - | - | {res} | {r.get('seconds', '')} |")
print("\n".join(out))
