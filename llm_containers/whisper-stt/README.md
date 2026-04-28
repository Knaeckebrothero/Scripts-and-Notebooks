# whisper-stt

Minimal wrapper that adds a clean OpenAI-compatible
`POST /v1/audio/transcriptions` endpoint on top of
[`lewangdev/faster-whisper`](https://hub.docker.com/r/lewangdev/faster-whisper).

The upstream image bundles its own `openaiapi.py`, but its form-field handling
and response shape drift from OpenAI's transcription contract. This replaces
it with a 60-line FastAPI app that does the right thing.

## Build

```bash
podman build -t faster-whisper-fixed:latest .
```

## Run

The image listens on **port 80** (uvicorn default in the upstream CMD). Pin
to one GPU and pass the model size via env:

```bash
podman run --rm \
    --device nvidia.com/gpu=0 \
    -p 8087:80 \
    -e MODEL_SIZE=large-v3 \
    -e COMPUTE_TYPE=float16 \
    localhost/faster-whisper-fixed:latest
```

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `MODEL_SIZE` | `tiny` | `tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3` |
| `DEVICE_TYPE` | `cuda` | |
| `COMPUTE_TYPE` | `float16` | `int8_float16` for tighter VRAM |
| `BEAM_SIZE` | `5` | |
| `VAD_FILTER` | `true` | Silero VAD pre-filter |
| `MIN_SILENCE_DURATION_MS` | `50` | VAD min-silence threshold |

## Endpoint

```bash
curl -X POST http://localhost:8087/v1/audio/transcriptions \
    -F "file=@audio.wav" \
    -F "model=whisper-1" \
    -F "language=en"
```

Response: `{"text": "..."}`. Matches OpenAI's transcription contract.

## VRAM footprint (large-v3, float16)

~4 GB. Co-tenants happily on a shared L40S with TEI / vLLM workloads.
