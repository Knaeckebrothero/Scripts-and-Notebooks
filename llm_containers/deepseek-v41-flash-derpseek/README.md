# deepseek-v41-flash-derpseek

DeepSeek-V4.1-Flash-1017 on one NVIDIA DGX Station GB300 (arm64), served by the
[derpseek](https://github.com/chronarion/derpseek) engine as a container image with two systemd units.

This image is **not built by this repository's CI**: it is arm64, and its CUDA kernels are compiled on the
target machine. Build it there with the steps below.

## What is in the image

- the stock `vllm/vllm-openai:v0.32.0-aarch64` image, pinned by digest
- [derpseek](https://github.com/chronarion/derpseek) at commit `8afa271` and
  [dsv41](https://github.com/chronarion/dsv41) at commit `c542c5b`, copied to the paths that derpseek's own
  launcher bind-mounts (so the image runs what `derpseek.sh up exact` runs)
- one addition: dsv41's tool-parser overlay for tool arguments that contain DSML closers
  (`VLLM_DSV41_STRUCTURAL_DSML=0` switches it off)

No weights and no settings are in the image. dsv41's two prebuilt sidecar binaries are left out; everything
binary in the image is either from the vLLM base or compiled in step 2.

Licences: derpseek and dsv41 are MIT, the kernels they vendor are MIT and Apache-2.0, the parser overlay and
vLLM are Apache-2.0. Their licence and notice files are in the image under `/engine` and `/derpseek`.

## Build

```bash
# 1. sources at the pinned commits
git clone https://github.com/chronarion/derpseek /srv/src/derpseek
git -C /srv/src/derpseek checkout 8afa27103bc421deca41566830ca10bf43d3574a
git clone https://github.com/chronarion/dsv41 /srv/src/dsv41
git -C /srv/src/dsv41 checkout c542c5b3ef3d1f8a7a48b00d3ec3c856c7811e18

# 2. base image and kernels (nvcc inside the base image; needs no GPU and no network)
docker pull vllm/vllm-openai:v0.32.0-aarch64   # sha256:6ff5ff0ddf3658d2c2d677dac5ca2d4726ce0156b4371166d918181b210bd4bc
(cd /srv/src/dsv41 && ./build.sh)

# 3. the image (docker build with --network none; context and log in /srv/build/ds-image)
sudo ./build-image.sh          # -> deepseek-v41-flash-derpseek:1.9-1
```

`build-image.sh` refuses to build if a checkout is not at its pinned commit or has changed files.
`verify-image.py <tag> <docker-inspect.json> [engine.env]` compares the image file by file with the sources
and its start command with a container started by derpseek's own launcher (`docker inspect derpseek`).

## Run

Host requirements are derpseek's (see its README): the `-1017` weights
([deepseek-ai/DeepSeek-V4.1-Flash-1017](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017)) and about
350 GB of free host memory.

| File | Install as |
|---|---|
| `entrypoint.sh` | in the image (done by the build) |
| `ds-net.sh`, `ds-ready.sh`, `ds-memguard.sh`, `forwarder.py`, `ds_check.py` | `/opt/deepseek-vllm/derpseek/` |
| `unit.env`, `engine.env`, `forwarder.env` | `/etc/deepseek-derpseek/` (set `GPU` in `unit.env`) |
| `deepseek-derpseek.service`, `deepseek-derpseek-memguard.service`, `deepseek-forwarder.service` | `/etc/systemd/system/` |

```bash
sudo systemctl daemon-reload
sudo systemctl start deepseek-derpseek deepseek-forwarder   # the first start takes about 12 minutes
python3 ds_check.py --base http://127.0.0.1:8010            # API checks through the forwarder
```

How it is laid out:

- **Engine container** (`deepseek-derpseek.service`): on an internal Docker network with no route out, at
  `172.31.250.2:8112`. It cannot open connections to the host or to other machines.
- **Forwarder** (`deepseek-forwarder.service`, `forwarder.py`, Python standard library, runs without root): the
  only door to the engine. It passes only the OpenAI-compatible paths, `/health` and `/metrics`, checks the
  source address, lets `DSF_MAX_ACTIVE` generation requests through at once (further ones wait in line), and
  closes the engine's request when a client leaves. It logs one line per request and never the content.

Settings worth knowing (`engine.env`, `forwarder.env`):

| Setting | Meaning |
|---|---|
| `ROWMAP`, `DERPSEEK_STAGE` | derpseek's split of GPU memory between experts, prompt staging and the KV cache |
| `KV_CACHE_BYTES` | fixed size of the KV cache; without it vLLM takes whatever its start-up trial leaves |
| `MAXLEN` | `--max-model-len` (the context limit, prompt and answer together) |
| `DSF_LISTEN`, `DSF_ALLOW_API`, `DSF_ALLOW_METRICS` | where the forwarder listens and who may use it |
| `DSF_MAX_ACTIVE`, `DSF_MAX_WAITING` | requests at the engine at once, and the length of the waiting line |

`acceptance/` holds the scripts of our acceptance run (API checks, agent benchmark cells, a 12-hour stress
test). They use the benchmark scripts of `../deepseek-v41-flash-vllm`.

## Status (November 2026)

Measured on one machine with this engine and these settings before the image was baked: eight agents with
385k-token contexts get about 950 tokens/s in total (the stock vLLM recipe: about 220), and a new 385k
context is read in about 6 s. The image itself is built and checked file by file against that tested setup;
its own long test run is still to come.
