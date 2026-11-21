# derpseek baked into an image: DeepSeek-V4.1-Flash-1017 on one DGX Station GB300 (arm64).
#
# What is in it: the stock vLLM 0.32 image (pinned by digest) plus the files that the derpseek launcher
# (github.com/chronarion/derpseek, engine/derpseek.sh) bind-mounts into that image at run time, copied to the
# same paths. Nothing is compiled or downloaded here: the kernels in engine/runtime were built once on the host
# with dsv41's build.sh, and the build runs with --network none.
#
# The build context is assembled by build-image.sh:
#   engine/     the dsv41 checkout (github.com/chronarion/dsv41) with its built runtime/, without .git and
#               without its two prebuilt sidecar binaries (not used here)
#   derpseek/   the derpseek checkout's engine/ directory
#   MANIFEST.sha256, BUILD-INFO
#
# One addition to what the launcher mounts: dsv41's patches/vllm/parser_deepseek_v41.py (tool arguments that
# contain a DSML closer). VLLM_DSV41_STRUCTURAL_DSML=0 switches it back to the stock parser.
#
# No weights and no settings in the image: the weights are mounted read-only at /model, and the entrypoint
# reads its settings from the environment (see entrypoint.sh).
ARG BASE=vllm/vllm-openai@sha256:6ff5ff0ddf3658d2c2d677dac5ca2d4726ce0156b4371166d918181b210bd4bc
FROM ${BASE}
ARG BASE
ARG DERPSEEK_COMMIT=unknown
ARG DSV41_COMMIT=unknown
LABEL org.opencontainers.image.title="deepseek-v41-flash-derpseek" \
      org.opencontainers.image.base.name="${BASE}" \
      org.opencontainers.image.description="vLLM 0.32 + dsv41 engine + derpseek overlay for DeepSeek-V4.1-Flash-1017 on one DGX Station GB300" \
      derpseek.commit="${DERPSEEK_COMMIT}" \
      dsv41.commit="${DSV41_COMMIT}"

COPY engine/ /engine/
COPY derpseek/ /derpseek/

# The launcher's bind mounts, as copies. A mount of a directory replaces the directory, so megamoe_fork is
# removed before the overlay's copy goes in. /usr/lib/python3.12/sitecustomize.py is a symlink into /etc; cp
# follows it, as the mount does.
RUN set -eu; V=/usr/local/lib/python3.12/dist-packages/vllm; P=/engine/patches/vllm; \
    put() { test -f "$V/$2" || { echo "not in the base image: $V/$2"; exit 1; }; cp "$P/$1" "$V/$2"; }; \
    put gumbel.py v1/worker/gpu/sample/gumbel.py; \
    put rejection_sampler_utils.py v1/worker/gpu/spec_decode/rejection_sampler_utils.py; \
    put deepseek_v41_encoding.py tokenizers/deepseek_v41_encoding.py; \
    put sampling_params.py sampling_params.py; \
    put deepseek_v41.py tokenizers/deepseek_v41.py; \
    put detokenizer.py v1/engine/detokenizer.py; \
    put parser_deepseek_v41.py parser/deepseek_v41.py; \
    cp -a /engine/runtime/hook /w; \
    cp /derpseek/hook/mega_peer_hook.py /w/mega_peer_hook.py; \
    cp -a /engine/runtime/fastpath /fastpath; \
    cp /derpseek/overlay/fastpath_boundary.py /fastpath/fastpath_boundary.py; \
    rm -rf /fastpath/megamoe_fork; \
    cp -a /derpseek/overlay/megamoe_fork /fastpath/megamoe_fork; \
    test -f /usr/lib/python3.12/sitecustomize.py; \
    cp /engine/runtime/fastpath/sitecustomize.py /usr/lib/python3.12/sitecustomize.py

# The launcher's fixed environment. The two last lines are ours: no usage statistics.
ENV PYTHONPATH=/engine:/derpseek \
    HF_HUB_OFFLINE=1 \
    VLLM_LOGGING_LEVEL=INFO \
    CUDA_LOG_FILE=stderr \
    PYTORCH_CUDA_ALLOC_CONF=pinned_max_round_threshold_mb:512 \
    DS41_GPU_RESERVE_MIB=0 \
    DERPSEEK_SOLO=1 \
    VLLM_NO_USAGE_STATS=1 \
    DO_NOT_TRACK=1

COPY MANIFEST.sha256 BUILD-INFO /opt/ds/
COPY entrypoint.sh /usr/local/bin/ds-entrypoint
RUN chmod 0755 /usr/local/bin/ds-entrypoint

# The base image ships ENTRYPOINT ["vllm","serve"]; override it.
ENTRYPOINT ["/usr/local/bin/ds-entrypoint"]
