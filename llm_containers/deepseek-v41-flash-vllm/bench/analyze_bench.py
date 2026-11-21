#!/usr/bin/env python3
"""Summarise a load-test directory: bench JSON per run + GPU/vLLM telemetry in each run's time window.
Usage: python3 analyze.py <loadtest dir>   -> prints a table, writes summary.csv and summary.md into the dir."""
import csv, glob, json, os, re, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

D = sys.argv[1]
LOCAL = ZoneInfo("Europe/Berlin")

def utc(s):  # 2026-11-19T20:36:29Z
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

# run windows from bench.log
win = {}
for line in open(os.path.join(D, "bench.log"), errors="replace"):
    m = re.match(r"=== (\S+) (START|END) (\S+)", line)
    if m:
        win.setdefault(m.group(3), {})[m.group(2)] = utc(m.group(1))

# GPU telemetry (local-time timestamps)
gpu = []
with open(os.path.join(D, "gpu.csv")) as f:
    for row in csv.reader(f):
        if not row or row[0].startswith("timestamp"):
            continue
        try:
            ts = datetime.strptime(row[0].strip(), "%Y/%m/%d %H:%M:%S.%f").replace(tzinfo=LOCAL).astimezone(timezone.utc)
            num = lambda x: float(re.sub(r"[^0-9.]", "", x) or "nan")
            gpu.append(dict(ts=ts, w=num(row[1]), t=num(row[2]), tm=num(row[3]), util=num(row[4]),
                            sm=num(row[7]), thr=row[9].strip()))
        except (ValueError, IndexError):
            pass

# vLLM metrics
met = []
for line in open(os.path.join(D, "vllm-metrics.log"), errors="replace"):
    parts = line.split()
    if not parts:
        continue
    try:
        ts = utc(parts[0])
    except ValueError:
        continue
    vals = {}
    for k, v in zip(parts[1::2], parts[2::2]):
        try:
            vals[k] = max(vals.get(k, 0.0), float(v))
        except ValueError:
            pass
    met.append(dict(ts=ts, **vals))

def window(series, name):
    w = win.get(name, {})
    a, b = w.get("START"), w.get("END")
    return [x for x in series if a and b and a <= x["ts"] <= b]

def idle(series, first=True):
    starts = sorted(w["START"] for w in win.values() if "START" in w)
    ends = sorted(w["END"] for w in win.values() if "END" in w)
    if first:
        return [x for x in series if starts and x["ts"] < starts[0]]
    return [x for x in series if ends and x["ts"] > ends[-1]]

rows = []
for path in sorted(glob.glob(os.path.join(D, "json", "*.json"))):
    name = os.path.basename(path)[:-5]
    d = json.load(open(path))
    g = [x for x in window(gpu, name) if x["util"] > 0] or window(gpu, name)
    m = window(met, name)
    kv = [x.get("vllm:kv_cache_usage_perc", x.get("vllm:gpu_cache_usage_perc", 0.0)) for x in m]
    pre = [x.get("vllm:num_preemptions_total", 0.0) for x in m]
    thr = sorted({x["thr"] for x in g if x["thr"] not in ("0x0000000000000000", "")})
    in_len = round(d.get("total_input_tokens", 0) / max(d.get("completed", 1), 1))
    out_len = round(d.get("total_output_tokens", 0) / max(d.get("completed", 1), 1))
    rows.append({
        "run": name, "in": in_len, "out": out_len, "conc": d.get("max_concurrency"),
        "ok": d.get("completed"), "fail": d.get("failed", 0), "dur_s": round(d.get("duration", 0), 1),
        "out_tok_s": round(d.get("output_throughput", 0), 1), "total_tok_s": round(d.get("total_token_throughput", 0), 1),
        "per_user_tok_s": round(1000 / d["mean_tpot_ms"], 1) if d.get("mean_tpot_ms") else None,
        "ttft_mean_ms": round(d.get("mean_ttft_ms", 0)), "ttft_p99_ms": round(d.get("p99_ttft_ms", 0)),
        "tpot_mean_ms": round(d.get("mean_tpot_ms", 0), 1), "tpot_p99_ms": round(d.get("p99_tpot_ms", 0), 1),
        "e2e_mean_s": round(d.get("mean_e2el_ms", 0) / 1000, 1),
        "gpu_w_mean": round(sum(x["w"] for x in g) / len(g)) if g else None,
        "gpu_w_max": round(max(x["w"] for x in g)) if g else None,
        "gpu_c_max": max(x["t"] for x in g) if g else None,
        "hbm_c_max": max(x["tm"] for x in g) if g else None,
        "sm_mhz_min": min(x["sm"] for x in g) if g else None,
        "kv_max_pct": round(100 * max(kv), 1) if kv else None,
        "preempt": int(max(pre) - min(pre)) if pre else None,
        "throttle": " ".join(thr) or "none",
    })

cols = list(rows[0].keys()) if rows else []
with open(os.path.join(D, "summary.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

def idle_line(label, first):
    g = idle(gpu, first)
    if not g:
        return f"{label}: no samples"
    return (f"{label}: {len(g)} s, GPU {sum(x['w'] for x in g)/len(g):.0f} W mean, "
            f"{max(x['t'] for x in g):.0f} °C max, util {sum(x['util'] for x in g)/len(g):.0f} % mean")

lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
lines += ["| " + " | ".join("" if r[c] is None else str(r[c]) for c in cols) + " |" for r in rows]
lines += ["", idle_line("Idle before", True), idle_line("Idle after", False)]
foreign = os.path.join(D, "foreign-requests.log")
if os.path.exists(foreign):
    lines.append(f"Requests from other clients during the test: {sum(1 for _ in open(foreign))}")
open(os.path.join(D, "summary.md"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
