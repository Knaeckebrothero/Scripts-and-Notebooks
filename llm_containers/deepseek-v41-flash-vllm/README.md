# DeepSeek-V4.1-Flash on one DGX Station GB300 (vLLM)

**Status (2026-11-21):** this image runs the vLLM recipe for this box unchanged (step 0), and that is measured:
one agent gets about 100 tokens/s, eight agents about 220 in total, whatever the context size. The tuning
steps further down were not pursued. For serving we moved to the derpseek engine, which is about four times
as fast with eight agents: see [`../deepseek-v41-flash-derpseek`](../deepseek-v41-flash-derpseek). This image
stays as the fallback and as the baseline for comparisons.

The Station runs one model and nothing else: DeepSeek-V4.1-Flash on vLLM 0.34.0. The goal is a 400k-token
context with as many concurrent sessions and as much KV cache as the box gives.

## Why it is set up this way

The Station has two memory pools: 252 GB of HBM on the GPU and about 496 GB of LPDDR5X on the Grace CPU,
linked by NVLink-C2C. The checkpoint is about 476 GiB and does not fit in the HBM, so the recipe puts:

- the two Engram lookup tables (2 × 94.4 GiB) in pinned Grace memory (`--engram-config '{"cpu_offload":true}'`)
- 60 GiB of routed-expert weights in Grace memory (`--offload-backend uva --cpu-offload-gb 60 --cpu-offload-params ...`)
- everything else, plus the KV cache, in HBM

That uses about 430 of the 494 GiB Grace pool, so the host has little memory to spare.

### NUMA mode vs. CDMM (check this first)

The NVIDIA driver can expose the HBM to Linux as a CPU-less NUMA node (**NUMA mode**, the default before
R615) or keep it driver-managed (**CDMM**). In NUMA mode the kernel can fill the HBM with page cache. We saw
this happen: copying the 476 GiB checkpoint left 35.5 GiB of page cache in the HBM, and `nvidia-smi` showed
36 GiB used with no GPU process running. That lost memory is what makes vLLM fail at start-up with "No
available memory for the cache blocks".

```bash
grep Coherent /proc/driver/nvidia/params      # "" = NUMA mode, "driver" = CDMM
# Switch to CDMM (takes effect at the next boot; the driver is not needed to boot):
echo 'options nvidia NVreg_CoherentGPUMemoryMode=driver' | sudo tee /etc/modprobe.d/nvidia-coherent-memory.conf
sudo update-initramfs -u -k "$(uname -r)" && sudo reboot
```

The unit also drops the page cache before every start (`ExecStartPre`), which helps in either mode.

## Host layout

| Path | What |
|---|---|
| `/srv` | RAID0 of the two spare NVMe drives (ext4, `nofail` in fstab). Weights and caches can all be re-created, so redundancy is not needed. |
| `/srv/models/DeepSeek-V4.1-Flash/` | weights, copied out of the HF cache (`cp -rL` of the snapshot), checked by sha256 against the blob names |
| `/srv/vllm/cache` → `/root/.cache` | torch.compile cache and FlashInfer autotune cache. **Must persist:** the first start runs a 75-90 min autotune; later starts take minutes. |
| `/srv/vllm/triton` → `/root/.triton` | Triton cache |
| `/etc/deepseek-vllm/` | `serve.args` (flags, one argument per line), `env` (container env), `unit.env` (image tag, GPU UUID) |
| `/opt/deepseek-vllm/` | build context (this folder), `tools/` (cache guard, memguard), `bench/` |

## Build and deploy

```bash
sudo mkdir -p /opt/deepseek-vllm/tools /etc/deepseek-vllm /srv/vllm/cache /srv/vllm/triton
sudo cp Dockerfile entrypoint.sh /opt/deepseek-vllm/
sudo cp scripts/*.sh /opt/deepseek-vllm/tools/ && sudo chmod +x /opt/deepseek-vllm/tools/*.sh
sudo cp config/serve.args config/env config/unit.env /etc/deepseek-vllm/     # then set GPU= in unit.env
sudo cp deepseek-vllm.service /etc/systemd/system/
cd /opt/deepseek-vllm && sudo docker build -t deepseek-v41-flash-vllm:0.34.0-1 .
sudo systemctl daemon-reload && sudo systemctl start deepseek-vllm
journalctl -fu deepseek-vllm
```

- **Docker plus a plain unit, not a Quadlet.** The Station ships Docker and the NVIDIA Container Toolkit. Docker's
  restart policy stays off and systemd owns restarts, with a start limit (3 tries in 6 h) so a broken start
  does not loop for hours.
- **Host networking.** Ports published by Docker bypass the host firewall; with `--network host`, ufw rules apply.
- **`--oom-score-adj 1000`.** If memory runs out, the kernel kills vLLM, not sshd.
- **Cache guard.** `scripts/vllm-cache-guard.sh` wipes the compile cache when the image id changes. It is
  the Docker variant of the one in `muse-glimmer-vllm`.
- **First unattended start.** Run `scripts/memguard.sh 6` next to it as a transient unit. It stops the
  server if the Grace node runs out of free and reclaimable memory.

## Benchmark harness (`bench/`)

`sudo bench/bench-step.sh <label> ["sweep prefill warm needle"]` runs against the live server. It
records the GPU and vLLM metrics and writes `/srv/vllm/runs/<UTC>-bench-<label>/summary.md`. Every run
uses the same scenarios, so the steps can be compared:

| Scenario | What it measures |
|---|---|
| `sweep` | 1024 in / 512 out at 1, 4, 8, 16 and 24 streams: throughput, TTFT, TPOT, power |
| `prefill` | cold TTFT for one 100k and one 399k-token prompt |
| `warm` | how many ~390k-token sessions stay in the prefix cache. N sessions are revisited round-robin at N = floor(KV / session) and N + 1; a later turn is fast only if its prefix survived (`warm.py`, token-id prompts) |
| `needle` | a passkey at 10, 50 and 90 % depth of 100k and ~390k-token prompts (thinking off), plus a repetition-loop check on two long answers with thinking on (`needle.py`) |
| `agents` | N agents, each with its own context of real source code (`AGENT_CONTEXTS`, `AGENT_COUNTS`, `AGENT_REPEATS`): time to first token, tokens/s per agent and in total, preemptions, share of prompt tokens from the cache, peak KV use (`agentbench.py`; the corpus is built once by `make_corpus.py`) |
| `burst` | `BURST_AGENTS` agents start a new `BURST_CONTEXT`-token context at the same moment |

`BENCH_URL` and `SERVER_CONTAINER` point the harness at another server, for example the derpseek engine.

## Tuning plan (not pursued; one change at a time, against step 0)

1. `--max-model-len` 1M → 400k (smaller start-up buffers)
2. `VLLM_WEIGHT_OFFLOADING_DISABLE_PIN_MEMORY=1` (the recipe reports about 66 GB of Grace memory freed and about 7 % slower single-stream decode)
3. more expert offload (`--cpu-offload-gb 70`, ...): more KV at lower decode speed
4. NVFP4 KV cache (`--kv-cache-dtype nvfp4_ds_mla`), if 0.34.0 supports it on this backend; check quality with `needle`
5. KV offload to Grace memory (`--kv-offloading-size`), if steps 2-3 free enough memory
6. `--max-num-seqs`, sized to the real agent load

Speculative decoding (DSpark) stays off because of an open crash report for requests over 100k tokens.
Open vLLM issues to watch at long context: #62811, #62935, #64584.

## Client notes

- Thinking is on by default. Switch it with `chat_template_kwargs: {"thinking": false}` (or `enable_thinking`).
- `reasoning_effort` accepts `low`, `high`, `xhigh`, `max` or 1-100; `medium` is rejected.
- Recommended sampling: `temperature 1.0`, `top_p 1.0`.
- `/metrics` is served on the API port.
