#!/usr/bin/env python3
"""ds-forwarder: the only door to the engine container.

The engine container sits on an internal Docker network that has no route out and is not reachable from other
machines. This program runs on the host, accepts HTTP on the addresses in DSF_LISTEN and passes requests on to
the engine. Python standard library only.

What it does:
  * only known paths pass (the OpenAI-compatible endpoints, /health, /metrics); everything else is 404
  * source addresses: DSF_ALLOW_API may use everything, DSF_ALLOW_METRICS only GET /metrics and /health,
    all others get 403
  * at most DSF_MAX_ACTIVE generation requests reach the engine at once; further ones wait in arrival order
    (at most DSF_MAX_WAITING, then 503)
  * when a client goes away, the engine's request is closed too, so the engine stops generating
  * GET /metrics returns the engine's metrics plus its own (dsfwd_*)
It logs one line per request (never the content) and keeps nothing.

Settings (environment):
  DSF_UPSTREAM=172.31.250.2:8112   DSF_LISTEN=127.0.0.1:8010[,addr:port...]
  DSF_MAX_ACTIVE=8   DSF_MAX_WAITING=64   DSF_MAX_BODY_MB=32
  DSF_ALLOW_API=127.0.0.0/8[,cidr...]   DSF_ALLOW_METRICS=[cidr,...]   DSF_LOG_PROBES=0
One request per connection: the forwarder asks the engine to close after each answer and passes that on.
"""
import asyncio, ipaddress, json, os, signal, socket, sys, time


def _nets(s):
    return [ipaddress.ip_network(x.strip(), strict=False) for x in s.split(",") if x.strip()]


def _addrs(s):
    return [(x.strip().rsplit(":", 1)[0], int(x.strip().rsplit(":", 1)[1])) for x in s.split(",") if x.strip()]


UP_HOST, UP_PORT = _addrs(os.environ.get("DSF_UPSTREAM", "172.31.250.2:8112"))[0]
LISTEN = _addrs(os.environ.get("DSF_LISTEN", "127.0.0.1:8010"))
MAX_ACTIVE = int(os.environ.get("DSF_MAX_ACTIVE", "8"))
MAX_WAITING = int(os.environ.get("DSF_MAX_WAITING", "64"))
MAX_BODY = int(os.environ.get("DSF_MAX_BODY_MB", "32")) * 2 ** 20
ALLOW_API = _nets(os.environ.get("DSF_ALLOW_API", "127.0.0.0/8"))
ALLOW_METRICS = _nets(os.environ.get("DSF_ALLOW_METRICS", ""))
LOG_PROBES = os.environ.get("DSF_LOG_PROBES", "0") == "1"

SLOT = {"/v1/chat/completions", "/v1/completions", "/v1/responses", "/v1/messages"}     # these take a slot
FREE = {("GET", "/health"), ("GET", "/v1/models"), ("GET", "/version"), ("GET", "/metrics"),
        ("POST", "/tokenize"), ("POST", "/detokenize")}
METRICS_ONLY = {("GET", "/metrics"), ("GET", "/health")}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te", "trailer",
       "upgrade", "expect", "host", "content-length", "x-forwarded-for"}
REASON = {400: "Bad Request", 403: "Forbidden", 404: "Not Found", 411: "Length Required", 413: "Payload Too Large",
          502: "Bad Gateway", 503: "Service Unavailable", 200: "OK"}


class S:   # counters
    active = 0; waiting = 0; total = 0; ok = 0; client_errors = 0; server_errors = 0
    rejected_queue = 0; denied = 0; upstream_down = 0; client_aborts = 0
    wait_sum = 0.0; wait_count = 0; max_active = 0; max_waiting = 0
    sem = None


def log(msg):
    print(msg, flush=True)


def reply(writer, code, msg, body=None, ctype="application/json", extra=""):
    if body is None:
        body = json.dumps({"error": {"message": msg, "type": "forwarder_error", "code": code}}).encode()
    writer.write((f"HTTP/1.1 {code} {REASON.get(code, 'Error')}\r\ncontent-type: {ctype}\r\ncontent-length: {len(body)}\r\n"
                  f"{extra}connection: close\r\n\r\n").encode() + body)


async def upstream():
    return await asyncio.wait_for(asyncio.open_connection(UP_HOST, UP_PORT, limit=1 << 20), 5)


def dechunk(b):
    out = bytearray(); i = 0
    while True:
        j = b.find(b"\r\n", i)
        if j < 0:
            break
        n = int(b[i:j].split(b";")[0] or b"0", 16)
        if n == 0:
            break
        out += b[j + 2:j + 2 + n]; i = j + 2 + n + 2
    return bytes(out)


async def fetch(target):
    """one whole GET from the engine: (status, content type, body)"""
    r, w = await upstream()
    try:
        w.write(f"GET {target} HTTP/1.1\r\nHost: {UP_HOST}:{UP_PORT}\r\nConnection: close\r\n\r\n".encode())
        await w.drain()
        raw = await asyncio.wait_for(r.read(-1), 20)
    finally:
        w.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    hl = head.decode("latin-1").lower()
    if "transfer-encoding: chunked" in hl:
        body = dechunk(body)
    ctype = next((l.split(":", 1)[1].strip() for l in hl.split("\r\n") if l.startswith("content-type:")), "text/plain")
    return int(head.split(b" ", 2)[1]), ctype, body


def own_metrics(up):
    g = [("dsfwd_upstream_up", "gauge", "1 if the engine answered this scrape", up),
         ("dsfwd_slots", "gauge", "generation requests the forwarder lets through at once", MAX_ACTIVE),
         ("dsfwd_requests_active", "gauge", "generation requests at the engine now", S.active),
         ("dsfwd_requests_waiting", "gauge", "generation requests waiting for a slot now", S.waiting),
         ("dsfwd_requests_total", "counter", "requests accepted and passed on", S.total),
         ("dsfwd_responses_ok_total", "counter", "answers with a status below 400", S.ok),
         ("dsfwd_responses_client_error_total", "counter", "answers with status 400-499 from the engine", S.client_errors),
         ("dsfwd_responses_server_error_total", "counter", "answers with status 500 and above, or none", S.server_errors),
         ("dsfwd_rejected_queue_full_total", "counter", "requests refused because the waiting line was full", S.rejected_queue),
         ("dsfwd_denied_total", "counter", "requests refused by address or path", S.denied),
         ("dsfwd_upstream_unreachable_total", "counter", "requests that found the engine unreachable", S.upstream_down),
         ("dsfwd_client_aborts_total", "counter", "requests whose client went away before the answer ended", S.client_aborts),
         ("dsfwd_slot_wait_seconds_sum", "counter", "seconds requests spent waiting for a slot", round(S.wait_sum, 3)),
         ("dsfwd_slot_wait_seconds_count", "counter", "requests that asked for a slot", S.wait_count)]
    return "".join(f"# HELP {n} {h}\n# TYPE {n} {t}\n{n} {v}\n" for n, t, h, v in g).encode()


async def relay(up_r, writer, sent):
    """engine -> client, byte for byte, until the engine closes. Returns the status; sent[0] counts the bytes."""
    code = 0
    while True:
        chunk = await up_r.read(65536)
        if not chunk:
            return code
        if sent[0] == 0:
            try:
                code = int(chunk.split(b" ", 2)[1])
            except (IndexError, ValueError):
                code = 0
        writer.write(chunk)
        await writer.drain()
        sent[0] += len(chunk)


async def handle(reader, writer):
    peer = (writer.get_extra_info("peername") or ("?", 0))[0]
    sock = writer.get_extra_info("socket")
    if sock is not None:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    t0 = time.monotonic(); method = path = "-"; code = 0; waited = 0.0; nin = nout = 0
    slot = False; note = ""; up_w = None; watch = None
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
            lines = head[:-4].decode("latin-1").split("\r\n")
            method, target, _version = lines[0].split(" ", 2)
            method = method.upper()
            headers = [(k.strip(), v.strip()) for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)]
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, asyncio.LimitOverrunError, ValueError):
            code = 400; note = "bad or no request"; reply(writer, 400, "bad request"); return
        path = target.split("?", 1)[0]
        try:
            ip = ipaddress.ip_address(peer)
        except ValueError:
            ip = None
        key = (method, path)
        if ip is not None and any(ip in n for n in ALLOW_API):
            allowed = key in FREE or (method == "POST" and path in SLOT)
        elif ip is not None and any(ip in n for n in ALLOW_METRICS):
            if key not in METRICS_ONLY:
                code = 403; S.denied += 1; reply(writer, 403, "this address may only read /metrics and /health"); return
            allowed = True
        else:
            code = 403; S.denied += 1; reply(writer, 403, "address not allowed"); return
        if not allowed:
            code = 404; S.denied += 1; reply(writer, 404, f"{method} {path} is not served here"); return
        h = {k.lower(): v for k, v in headers}
        if "transfer-encoding" in h:
            code = 411; reply(writer, 411, "send the body with Content-Length"); return
        try:
            n = int(h.get("content-length") or 0)
        except ValueError:
            code = 400; reply(writer, 400, "bad Content-Length"); return
        if n > MAX_BODY:
            code = 413; reply(writer, 413, f"body larger than {MAX_BODY >> 20} MiB"); return
        if h.get("expect", "").lower() == "100-continue":
            writer.write(b"HTTP/1.1 100 Continue\r\n\r\n"); await writer.drain()
        body = await asyncio.wait_for(reader.readexactly(n), 600) if n else b""
        nin = n

        if key == ("GET", "/metrics"):
            try:
                st, ctype, mbody = await fetch(target)
                up = 1 if st == 200 else 0
            except (OSError, asyncio.TimeoutError, ValueError, IndexError):
                up = 0; mbody = b""; ctype = "text/plain; version=0.0.4; charset=utf-8"
            if mbody and not mbody.endswith(b"\n"):
                mbody += b"\n"
            code = 200; out = (mbody if up else b"") + own_metrics(up); nout = len(out)
            reply(writer, 200, "", body=out, ctype=ctype); return

        watch = asyncio.ensure_future(reader.read(1))      # completes when the client goes away
        if path in SLOT:
            if S.waiting >= MAX_WAITING:
                code = 503; S.rejected_queue += 1
                reply(writer, 503, "all slots busy and the waiting line is full", extra="retry-after: 30\r\n"); return
            S.waiting += 1; S.max_waiting = max(S.max_waiting, S.waiting); tw = time.monotonic()
            acq = asyncio.ensure_future(S.sem.acquire())
            try:
                await asyncio.wait({acq, watch}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                S.waiting -= 1
            if not acq.done():
                acq.cancel()
                try:
                    await acq
                    S.sem.release()                         # it was granted in the same moment
                except asyncio.CancelledError:
                    pass
                code = 499; note = "client left while waiting"; S.client_aborts += 1; return
            slot = True; S.active += 1; S.max_active = max(S.max_active, S.active)
            waited = time.monotonic() - tw; S.wait_sum += waited; S.wait_count += 1
            if watch.done():
                code = 499; note = "client left while waiting"; S.client_aborts += 1; return
        S.total += 1
        try:
            up_r, up_w = await upstream()
        except (OSError, asyncio.TimeoutError):
            code = 503; S.upstream_down += 1; note = "engine unreachable"
            reply(writer, 503, "the engine is not reachable (starting or stopped)", extra="retry-after: 30\r\n"); return
        out = [f"{method} {target} HTTP/1.1", f"Host: {UP_HOST}:{UP_PORT}", "Connection: close", f"X-Forwarded-For: {peer}"]
        out += [f"{k}: {v}" for k, v in headers if k.lower() not in HOP]
        if n or method == "POST":
            out.append(f"Content-Length: {n}")
        up_w.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1") + body)
        await up_w.drain()
        sent = [0]
        pump = asyncio.ensure_future(relay(up_r, writer, sent))
        await asyncio.wait({pump, watch}, return_when=asyncio.FIRST_COMPLETED)
        nout = sent[0]
        if pump.done():
            code = pump.result(); nout = sent[0]
            if nout == 0:
                code = 502; note = "engine closed without an answer"; reply(writer, 502, "the engine closed the connection")
        else:
            pump.cancel(); code = 499; note = "client left"; S.client_aborts += 1
    except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError) as e:
        if not code:
            code = 499; S.client_aborts += 1
        note = note or type(e).__name__
    except Exception as e:                                   # never let one request take the server down
        code = code or 500; note = f"forwarder error: {e!r}"
    finally:
        if watch is not None:
            watch.cancel()
        if up_w is not None:
            up_w.close()
        if slot:
            S.active -= 1; S.sem.release()
        if code and code != 499 and path != "-" and (path in SLOT or code >= 400):
            if code < 400:
                S.ok += 1
            elif code < 500:
                S.client_errors += 1
            else:
                S.server_errors += 1
        try:
            await writer.drain()
        except Exception:
            pass
        writer.close()
        probe = method == "GET" and code == 200
        if LOG_PROBES or not probe:
            log(f"{peer} {method} {path} {code} in={nin} out={nout} wait={waited:.2f}s dur={time.monotonic() - t0:.2f}s "
                f"active={S.active} waiting={S.waiting}" + (f" ({note})" if note else ""))


def listen_socket(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.setsockopt(socket.IPPROTO_IP, getattr(socket, "IP_FREEBIND", 15), 1)   # the address may come up later
    except OSError:
        pass
    s.bind((host, port)); s.listen(256); s.setblocking(False)
    return s


async def main():
    S.sem = asyncio.Semaphore(MAX_ACTIVE)
    servers = [await asyncio.start_server(handle, sock=listen_socket(h, p), limit=65536) for h, p in LISTEN]
    log(f"ds-forwarder: listening on {', '.join(f'{h}:{p}' for h, p in LISTEN)} -> {UP_HOST}:{UP_PORT}; slots {MAX_ACTIVE}, "
        f"waiting line {MAX_WAITING}, body limit {MAX_BODY >> 20} MiB; api from {[str(n) for n in ALLOW_API]}, "
        f"metrics only from {[str(n) for n in ALLOW_METRICS]}")
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    for s in servers:
        s.close()
    log(f"ds-forwarder: stopping (active {S.active}, waiting {S.waiting}; peaks: active {S.max_active}, waiting {S.max_waiting})")


if __name__ == "__main__":
    asyncio.run(main())
