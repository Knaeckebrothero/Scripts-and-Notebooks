# qwen3-reranker-vllm

Podman quadlet for **`Qwen/Qwen3-Reranker-8B`** on vLLM, including the score
template that makes the model actually work over the `/v1/rerank` and `/score`
APIs. Deployed on the university server (`embeddingprod`, port 8082) behind the
model-orchestrator.

No Dockerfile here — it runs the stock `vllm/vllm-openai` image; the quadlet
plus the Jinja template are the whole deployment.

## Why vLLM (and not TEI like the embedding models)

Qwen3-Reranker is not a standard cross-encoder. It is a causal LM judged on the
logits of the literal tokens `yes`/`no`, served via vLLM's
`Qwen3ForSequenceClassification` conversion (`--hf_overrides` with
`is_original_qwen3_reranker: true`). TEI (#643, #691, #763), Infinity (#642)
and SGLang (#7949) all reject this architecture.

## Why the score template is load-bearing

vLLM (0.16) does **not** apply any prompt formatting to score/rerank requests
for this model by default — `get_score_prompt` falls back to raw
`query + document` string concatenation. The yes/no head then judges a
malformed prompt and returns noise: on a trivial 4-question retrieval battery
the route scored 1/4, e.g. ranking "user enjoys hiking" above "user adopted a
corgi named Pixel" for *"What is my dog's name?"*.

`--chat-template qwen3_reranker_score.jinja` fixes this. vLLM renders the
template with `[{role: "query", ...}, {role: "document", ...}]`, producing the
official Qwen3-Reranker format (system prompt + `<Instruct>/<Query>/<Document>`
+ empty `<think>` block). Same battery after the fix: 4/4, relevant documents
0.74–0.99, distractors ≈ 0.000.

The template must render **byte-exactly** — the classifier reads the position
after the final `</think>\n\n`, which is why the file ends in an explicit
`{{ "\n\n" }}` expression and has no trailing newline.

## Deployment

```bash
# as embeddingprod on the host
cp qwen3_reranker_score.jinja ~/.config/vllm/
cp vllm-reranker.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start vllm-reranker.service
curl localhost:8082/health   # ready when 200, ~1 min with warm HF cache
```

## Verification

```bash
curl -s localhost:8082/v1/rerank -H 'Content-Type: application/json' -d '{
  "model": "Qwen/Qwen3-Reranker-8B",
  "query": "What is my dog'\''s name?",
  "documents": ["user enjoys hiking", "user adopted a corgi named Pixel"]
}'
```

Expected: the corgi document wins by orders of magnitude (≈0.004 vs ≈0.000).
If both scores land in the 0.2–0.8 noise band, the template is not being
applied — check `podman logs vllm-reranker | grep chat_template`.

## Caveats

- Clients must send **raw** query/document strings. Pre-templating on the
  client (the old workaround) now double-wraps the prompt.
- The instruction line is baked into the template (Qwen's default web-search
  instruct). Per-request instructions are not supported on this route.
- Image is `:latest` with `AutoUpdate=registry`; the score-template plumbing
  is vLLM-version-sensitive (verified on 0.16.0), so re-run the verification
  after image updates — or pin the image.
