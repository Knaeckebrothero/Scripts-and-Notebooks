#!/usr/bin/env python3
"""Builds the fixed text corpus for agentbench.py from the Python source files of this container image.

The files are dealt to --agents agents so that each gets about --tokens tokens of distinct code (agent<i>.jsonl), plus
a pool of small files (extra.jsonl) that later turns use as new material. Built once from one pinned image, so every
engine is tested on the same text. Each line: {"path": ..., "ntok": ..., "text": ...}.

Usage: make_corpus.py --tokenizer /models/DeepSeek-V4.1-Flash --out /corpus [--agents 10 --tokens 1050000]
"""
import argparse, hashlib, json, os, random, time
from transformers import AutoTokenizer

p = argparse.ArgumentParser()
p.add_argument("--tokenizer", required=True)
p.add_argument("--out", required=True)
p.add_argument("--agents", type=int, default=10)
p.add_argument("--tokens", type=int, default=1_050_000)
p.add_argument("--extra", type=int, default=600)
p.add_argument("--roots", default="/usr/local/lib/python3.12/dist-packages,/usr/lib/python3.12")
p.add_argument("--seed", type=int, default=7)
a = p.parse_args()

tok = AutoTokenizer.from_pretrained(a.tokenizer, trust_remote_code=True)
paths = []
for root in a.roots.split(","):
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x != "__pycache__")
        for f in sorted(files):
            q = os.path.join(d, f)
            if f.endswith(".py") and not os.path.islink(q) and 3_000 <= os.path.getsize(q) <= 60_000:
                paths.append((os.path.relpath(q, root), q))
random.Random(a.seed).shuffle(paths)
print(f"{len(paths)} candidate files", flush=True)

agents = [[] for _ in range(a.agents)]
have = [0] * a.agents
extra = []
t0 = time.time(); used = 0
for rel, q in paths:
    if min(have) >= a.tokens and len(extra) >= a.extra:
        break
    try:
        text = open(q, encoding="utf-8").read()
    except (UnicodeDecodeError, OSError):
        continue
    if "\x00" in text or max(map(len, text.splitlines() or [""])) > 2_000:   # skip generated tables and blobs
        continue
    n = len(tok.encode(text, add_special_tokens=False))
    e = {"path": rel, "ntok": n, "text": text}
    used += 1
    if 800 <= n <= 2_500 and len(extra) < a.extra and used % 4 == 0:
        extra.append(e)
        continue
    i = have.index(min(have))
    if have[i] < a.tokens:
        agents[i].append(e); have[i] += n
print(f"tokenized {used} files in {time.time() - t0:.0f} s; tokens per agent {have}; extra files {len(extra)}", flush=True)

os.makedirs(a.out, exist_ok=True)
manifest = {"built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "image": os.environ.get("IMAGE_REF", "unknown"),
            "tokenizer": a.tokenizer, "seed": a.seed, "roots": a.roots, "files": {}}


def write(name, entries):
    path = os.path.join(a.out, name)
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    manifest["files"][name] = {"entries": len(entries), "tokens": sum(e["ntok"] for e in entries),
                               "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest()}


for i, entries in enumerate(agents):
    write(f"agent{i}.jsonl", entries)
write("extra.jsonl", extra)
with open(os.path.join(a.out, "MANIFEST.json"), "w") as f:
    json.dump(manifest, f, indent=1)
short = [i for i, h in enumerate(have) if h < a.tokens]
print("DONE" + (f" (agents short of the target: {short})" if short else ""), flush=True)
