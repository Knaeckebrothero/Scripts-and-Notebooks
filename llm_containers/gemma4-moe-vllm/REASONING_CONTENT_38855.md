# Gemma 4 reasoning output — RESOLVED (vLLM #38855 was a false alarm)

**Status:** ✅ RESOLVED 2026-06-29. There is **no vLLM bug and no container fix
needed.** Gemma 4 reasoning works out of the box on vLLM 0.22.0. The entire
"#38855" investigation was a **response-field-name mistake.**
**Applies to:** both `gemma4-moe-vllm` and `gemma4-dense-vllm` (same built-in
`--reasoning-parser gemma4`).

---

## The real story

The chat-completion response returns the chain-of-thought in the **`reasoning`**
field, **not** `reasoning_content`. vLLM deprecated/renamed `reasoning_content`
→ `reasoning` (see `vllm/entrypoints/openai/chat_completion/protocol.py`), so
`reasoning_content` is **always empty** — but the data is right there in
`reasoning`.

The `Gemma4ReasoningParser` is fully functional:
- Its `adjust_request` **auto-sets `skip_special_tokens=False`**, so the
  `<|channel>…<channel|>` thinking markers survive into `output.text`.
- The stock text-based `extract_reasoning` then splits them correctly
  (`reasoning` = thinking, `content` = answer).
- The streaming path is token-based and **also works**.

The 2026-06-22 investigation (and a validation harness built from it) checked
`reasoning_content` — found it empty — and concluded "the reasoning is dropped."
It wasn't dropped; it was in `reasoning` the whole time.

## Evidence (A100 pod, 2026-06-29)

- **Plain** chat request (no special flags) → `reasoning` = 634 chars,
  `content` = clean answer.
- Full agent-readiness gate **6/6 GO**: reasoning non-streaming + **streaming**
  (265 reasoning deltas), tool calling (thinking on/off), structured output
  (thinking on/off).
- Ran the real model output through **stock** vs a candidate parser patch — byte
  identical. The patch is a **no-op**.

## The fix

**Read the `reasoning` field of the response message.** Nothing else.

- The `model-orchestrator` already injects `skip_special_tokens:false` (harmless,
  and not even required since `adjust_request` does it) — it just needs to read
  `message.reasoning` instead of `message.reasoning_content`. That code lives on
  the uni server, not in this repo.
- **No container rebuild** for either dense or MoE.
- `skip_special_tokens:false` from the client is **optional** (auto-handled).
- Streaming reasoning is **fine** — read `delta.reasoning`.

## Abandoned work

A fail-safe parser monkeypatch was built on branch
`fix/gemma4-dense-reasoning-vllm-0.23.0` (and a vLLM 0.22.0→0.23.0 bump tried
first). Both were chasing the phantom bug; proven unnecessary and **not merged**.
Branch deleted.

## References
- vLLM Issue #38855 — <https://github.com/vllm-project/vllm/issues/38855> (not an actual defect for our usage)
- `Gemma4ReasoningParser` / `gemma4_utils.parse_thinking_output`
- vLLM Gemma 4 recipe — <https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html>
