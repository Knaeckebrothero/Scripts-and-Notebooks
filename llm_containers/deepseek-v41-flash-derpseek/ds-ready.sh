#!/bin/bash
# ExecStartPost of deepseek-derpseek.service: waits until the engine answers /health, then warms it up with the
# dsv41 project's warm.py (from the image, in a throwaway container on the isolated network), so the first real
# requests do not pay for the one-time capture of the step graphs. The unit is "active" only after this.
set -u
URL=http://${ENGINE_IP:-172.31.250.2}:${ENGINE_PORT:-8112}
ok=0
for i in $(seq 1 480); do                      # up to 80 minutes
  if [ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$URL/health")" = 200 ]; then ok=1; break; fi
  if [ "$i" -gt 6 ] && [ "$(docker inspect -f '{{.State.Status}}' deepseek-derpseek 2>/dev/null)" != running ]; then
    echo "ds-ready: the engine container is not running"; exit 1
  fi
  sleep 10
done
[ $ok = 1 ] || { echo "ds-ready: no /health after $SECONDS s"; exit 1; }
echo "ds-ready: ready on :${ENGINE_PORT:-8112} after $SECONDS s"
docker rm -f deepseek-derpseek-warm >/dev/null 2>&1
# -I -S: plain Python, without the engine's start-up hook that the image installs for every Python process.
if timeout 900 docker run --rm --name deepseek-derpseek-warm --network ds-isolated --entrypoint python3 "${IMAGE}" \
     -I -S /engine/bench/warm.py "$URL" >/dev/null 2>&1; then
  echo "ds-ready: warmed after $SECONDS s"
else
  echo "ds-ready: warm-up failed (serving anyway)"
fi
exit 0
