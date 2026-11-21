#!/usr/bin/env python3
"""Checks that the built image holds what the tested derpseek container saw, and starts the same command.

Usage: verify-image.py <image tag> <docker-inspect.json of a tested run> [engine.env]
1. Files: every file under /engine, /derpseek, /fastpath, /w and the files patched into vLLM must have the same
   sha256 as its source in /srv/src (the launcher's bind mounts, applied in the launcher's order).
2. Command and environment: the entrypoint's dry run with engine.env is compared with the tested container's
   arguments and environment. Differences are listed; the expected ones are named as such.
Needs no GPU. Exit code 0 = no unexpected difference."""
import hashlib, json, os, subprocess, sys

IMAGE, INSPECT = sys.argv[1], sys.argv[2]
ENVFILE = sys.argv[3] if len(sys.argv) > 3 else None
SRC = "/srv/src"
V = "/usr/local/lib/python3.12/dist-packages/vllm"
SKIP_DIRS = {".git", "__pycache__"}
bad = 0


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


LEFT_OUT = "kernels/vendor/ds41/peer3/prebuilt/"   # dsv41's two prebuilt sidecar binaries: not used, not in the image


def tree(root, prefix, skip=()):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root)
            if rel in skip or f.endswith(".pyc") or os.path.islink(p):   # find -type f in the image skips links too
                continue
            if prefix == "/engine" and rel.startswith(LEFT_OUT):
                continue
            out[prefix + "/" + rel] = sha(p)
    return out


# what the launcher mounts, in its order (later mounts cover earlier ones)
exp = {}
exp.update(tree(f"{SRC}/dsv41", "/engine"))
exp.update(tree(f"{SRC}/derpseek/engine", "/derpseek", skip={"derpseek-isolated.sh"}))
exp.update(tree(f"{SRC}/dsv41/runtime/hook", "/w"))
exp["/w/mega_peer_hook.py"] = sha(f"{SRC}/derpseek/engine/hook/mega_peer_hook.py")
fp = tree(f"{SRC}/dsv41/runtime/fastpath", "/fastpath")
exp.update({k: v for k, v in fp.items() if not k.startswith("/fastpath/megamoe_fork/")})
exp.update(tree(f"{SRC}/derpseek/engine/overlay/megamoe_fork", "/fastpath/megamoe_fork"))
exp["/fastpath/fastpath_boundary.py"] = sha(f"{SRC}/derpseek/engine/overlay/fastpath_boundary.py")
PATCHED = {"gumbel.py": "v1/worker/gpu/sample/gumbel.py",
           "rejection_sampler_utils.py": "v1/worker/gpu/spec_decode/rejection_sampler_utils.py",
           "deepseek_v41_encoding.py": "tokenizers/deepseek_v41_encoding.py",
           "sampling_params.py": "sampling_params.py",
           "deepseek_v41.py": "tokenizers/deepseek_v41.py",
           "detokenizer.py": "v1/engine/detokenizer.py",
           "parser_deepseek_v41.py": "parser/deepseek_v41.py"}   # the last one is our addition
for src, dst in PATCHED.items():
    exp[f"{V}/{dst}"] = sha(f"{SRC}/dsv41/patches/vllm/{src}")
exp["/etc/python3.12/sitecustomize.py"] = sha(f"{SRC}/dsv41/runtime/fastpath/sitecustomize.py")

cmd = ("find /engine /derpseek /fastpath /w -type f -not -path '*/__pycache__/*' -not -name '*.pyc' -print0 | sort -z | xargs -0 sha256sum; "
       "sha256sum " + " ".join(f"{V}/{d}" for d in PATCHED.values()) + " /etc/python3.12/sitecustomize.py")
r = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "bash", IMAGE, "-c", cmd],
                   capture_output=True, text=True)
if r.returncode:
    print("could not list the image:", r.stderr[-400:]); sys.exit(2)
got = {}
for line in r.stdout.splitlines():
    h, p = line.split(None, 1)
    got[p.strip()] = h
missing = sorted(set(exp) - set(got)); extra = sorted(set(got) - set(exp)); diff = sorted(k for k in exp if k in got and exp[k] != got[k])
print(f"files: {len(exp)} expected, {len(got)} in the image; missing {len(missing)}, extra {len(extra)}, different {len(diff)}"
      f" (left out on purpose: /engine/{LEFT_OUT}*)")
pre = [p for p in got if p.endswith(".so") and "prebuilt" in p]
if pre:
    print("  prebuilt binaries in the image:", pre); bad += len(pre)
for name, lst in (("missing", missing), ("extra", extra), ("different", diff)):
    for p in lst[:15]:
        print(f"  {name}: {p}")
bad += len(missing) + len(extra) + len(diff)

# command and environment
t = json.load(open(INSPECT))[0]
t_args = [t["Path"]] + t["Args"]
t_env = dict(e.split("=", 1) for e in t["Config"]["Env"])
run = ["docker", "run", "--rm", "--network", "none", "-e", "DS_DRY_RUN=1"]
if ENVFILE:
    run += ["--env-file", ENVFILE]
r = subprocess.run(run + [IMAGE], capture_output=True, text=True)
if r.returncode:
    print("dry run failed:", (r.stdout + r.stderr)[-400:]); sys.exit(2)
n_args = [l.split("\t", 1)[1] for l in r.stdout.splitlines() if l.startswith("ARG\t")]
n_env = dict(l.split("\t", 1)[1].split("=", 1) for l in r.stdout.splitlines() if l.startswith("ENV\t") and "=" in l)


def opts(a):
    """{flag: [values]} for a vllm serve command line"""
    out = {}; cur = None
    for x in a:
        if x.startswith("--") and not x[2:3].isdigit():
            cur = x; out[cur] = []
        elif cur:
            out[cur].append(x)
    return out


EXPECTED_ARGS = {"--served-model-name", "--kv-cache-memory-bytes", "--max-model-len", "--max-num-seqs"}
to, no = opts(t_args), opts(n_args)
head_t, head_n = t_args[:4], n_args[:4]
print(f"command head: tested {head_t} | image {head_n}" + ("" if head_t == head_n else "   <-- DIFFERENT"))
bad += head_t != head_n
for k in sorted(set(to) | set(no)):
    if to.get(k) != no.get(k):
        ok = k in EXPECTED_ARGS
        print(f"  argument {k}: tested {to.get(k)} | image {no.get(k)}" + ("   (a setting of ours)" if ok else "   <-- UNEXPECTED"))
        bad += not ok
PREF = ("DSV41_", "DERPSEEK_", "DS41_", "MEGA_", "PYTORCH_", "PYTHONPATH", "HF_HUB_OFFLINE", "VLLM_LOGGING_LEVEL", "CUDA_LOG_FILE")
OURS = {"VLLM_NO_USAGE_STATS", "DO_NOT_TRACK", "VLLM_DSV41_STRUCTURAL_DSML"}
SHELL = {"PWD", "SHLVL", "HOME", "HOSTNAME", "_", "OLDPWD", "TERM"}
for k in sorted(set(t_env) | set(n_env)):
    a, b = t_env.get(k), n_env.get(k)
    if a == b or k in SHELL:
        continue
    if k.startswith(PREF):
        print(f"  environment {k}: tested {a!r} | image {b!r}   <-- UNEXPECTED"); bad += 1
    elif k in OURS:
        print(f"  environment {k}: tested {a!r} | image {b!r}   (ours)")
    else:
        print(f"  environment {k}: tested {a!r} | image {b!r}   <-- not expected (base image variable?)"); bad += 1
print("RESULT:", "no unexpected difference" if not bad else f"{bad} unexpected difference(s)")
sys.exit(1 if bad else 0)
