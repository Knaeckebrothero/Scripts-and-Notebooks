# whisper-stt — OpenAI-compatible faster-whisper wrapper
#
# Adds a clean /v1/audio/transcriptions endpoint on top of the upstream
# lewangdev/faster-whisper image. The upstream image's API quirks (form-field
# names, response shape) don't match OpenAI's transcription contract, so we
# replace its openaiapi.py with a minimal FastAPI app that does.
#
# Build: podman build -t faster-whisper-fixed:latest .
# Run:   see ../../[deployment-quadlet] — typically pinned to one L40S GPU
#        with `MODEL_SIZE=large-v3` and `COMPUTE_TYPE=float16`.

FROM docker.io/lewangdev/faster-whisper:latest

COPY openaiapi.py /src/openaiapi.py
