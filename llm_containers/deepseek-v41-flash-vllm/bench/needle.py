#!/usr/bin/env python3
"""Long-context correctness checks against the chat endpoint.

needle: a passkey hidden at 10 / 50 / 90 % depth of a filler text of ~100k and ~390k tokens; thinking off,
        greedy; pass = the passkey appears in the answer.
rep:    two long free-form answers (thinking on, recommended sampling); flags repetition loops by counting
        how often the most frequent 12-word sequence repeats (a loop shows dozens).
Usage: needle.py --model NAME --tokenizer DIR --out needle.json
"""
import argparse, collections, json, random, time, urllib.request
from transformers import AutoTokenizer

p = argparse.ArgumentParser()
p.add_argument("--url", default="http://127.0.0.1:8000")
p.add_argument("--model", required=True)
p.add_argument("--tokenizer", required=True)
p.add_argument("--lengths", default="100000,390000")
p.add_argument("--depths", default="0.1,0.5,0.9")
p.add_argument("--out", required=True)
p.add_argument("--skip-rep", action="store_true")
a = p.parse_args()

tok = AutoTokenizer.from_pretrained(a.tokenizer, trust_remote_code=True)
rng = random.Random(11)
FILLER = [
    "The river widened as it reached the plain, and the boats slowed to a crawl.",
    "Most of the town's bakeries opened before dawn to serve the market traders.",
    "A survey of the old library found several maps that nobody had catalogued.",
    "In winter the mountain pass closed for weeks, and supplies came by sled.",
    "The committee met every Thursday to review the budget for the coming season.",
    "Engineers replaced the bridge's bearings after a routine inspection in spring.",
    "Farmers in the valley rotated barley, clover and potatoes over three years.",
    "The museum's new wing displays tools used by the first settlers of the region.",
    "Rain arrived late that year, and the reservoirs fell to their lowest level.",
    "A small orchestra played in the square on the first Sunday of every month.",
    "The ferry schedule changed twice before the harbour was finally dredged.",
    "Students compared the two translations line by line and noted every difference.",
    "The lighthouse keeper logged the weather at six in the morning and at six at night.",
    "Traders brought salt, wool and dried fish along the coastal road each autumn.",
    "The observatory's telescope was cleaned and recalibrated after the storm.",
    "Volunteers planted a row of lime trees along the avenue to the station.",
]
LEN = [len(tok.encode(" " + s, add_special_tokens=False)) for s in FILLER]


def post(path, body, timeout=1800):
    req = urllib.request.Request(a.url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return d, time.perf_counter() - t0


def haystack(n_tokens, depth, key):
    parts, total = [], 0
    while total < n_tokens:
        i = rng.randrange(len(FILLER)); parts.append(FILLER[i]); total += LEN[i]
    needle = f"The secret passkey for the archive is {key}. Remember this passkey."
    parts.insert(int(len(parts) * depth), needle)
    return " ".join(parts)


results = {"needle": [], "rep": []}
for n in [int(x) for x in a.lengths.split(",")]:
    for depth in [float(x) for x in a.depths.split(",")]:
        key = str(rng.randrange(1000000, 9999999))
        text = haystack(n, depth, key) + "\n\nWhat is the secret passkey for the archive? Answer with the number only."
        body = {"model": a.model, "messages": [{"role": "user", "content": text}], "max_tokens": 64,
                "temperature": 0, "chat_template_kwargs": {"thinking": False}}
        try:
            d, dt = post("/v1/chat/completions", body)
            ans = d["choices"][0]["message"].get("content") or ""
            r = {"tokens": d["usage"]["prompt_tokens"], "depth": depth, "pass": key in ans, "answer": ans[:60], "seconds": round(dt, 1)}
        except Exception as e:
            r = {"tokens": n, "depth": depth, "pass": False, "error": str(e)[:200]}
        results["needle"].append(r); print("needle", json.dumps(r), flush=True)

if not a.skip_rep:
    for i in range(2):
        body = {"model": a.model, "max_tokens": 6000, "temperature": 1.0, "top_p": 1.0,
                "messages": [{"role": "user", "content": "Explain in detail how a hash table works: hashing, collisions, "
                              "open addressing versus chaining, resizing, and performance. Give code examples. At least 1500 words."}]}
        try:
            d, dt = post("/v1/chat/completions", body)
            m = d["choices"][0]["message"]
            reasoning = m.get("reasoning_content") or m.get("reasoning") or ""
            text = (reasoning + "\n" + (m.get("content") or "")).split()
            grams = collections.Counter(tuple(text[j:j + 12]) for j in range(max(0, len(text) - 11)))
            top = grams.most_common(1)[0][1] if grams else 0
            r = {"run": i, "completion_tokens": d["usage"]["completion_tokens"], "finish": d["choices"][0]["finish_reason"],
                 "reasoning_field": [k for k in m if "reason" in k], "reasoning_words": len(reasoning.split()),
                 "content_words": len((m.get("content") or "").split()), "max_repeat_12gram": top,
                 "loop_suspected": top >= 8, "seconds": round(dt, 1)}
        except Exception as e:
            r = {"run": i, "error": str(e)[:200]}
        results["rep"].append(r); print("rep", json.dumps(r), flush=True)

json.dump(results, open(a.out, "w"), indent=1)
print("SUMMARY needle", sum(r.get("pass", False) for r in results["needle"]), "/", len(results["needle"]),
      "| rep loops", sum(r.get("loop_suspected", False) for r in results["rep"]), "/", len(results["rep"]))
