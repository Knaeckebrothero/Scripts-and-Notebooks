#!/bin/bash
# Builds the engine image from the pinned checkouts in /srv/src (run as root on the Station).
# Usage: build-image.sh [tag, default deepseek-v41-flash-derpseek:1.9-1]
# Nothing is downloaded: the base image is local (pinned by digest) and the build runs with --network none.
# Work folder: /srv/build/ds-image (context in ctx/, log in build.log). Afterwards run verify-image.py.
set -euo pipefail
export LC_ALL=C
HERE=$(cd "$(dirname "$0")" && pwd)
TAG=${1:-deepseek-v41-flash-derpseek:1.9-1}
SRC=/srv/src; W=/srv/build/ds-image
DERPSEEK_COMMIT=8afa27103bc421deca41566830ca10bf43d3574a
DSV41_COMMIT=c542c5b3ef3d1f8a7a48b00d3ec3c856c7811e18
BASE=vllm/vllm-openai@sha256:6ff5ff0ddf3658d2c2d677dac5ca2d4726ce0156b4371166d918181b210bd4bc
g(){ git -C "$1" -c safe.directory='*' "${@:2}"; }

[ "$(g $SRC/derpseek rev-parse HEAD)" = $DERPSEEK_COMMIT ] || { echo "derpseek is not at $DERPSEEK_COMMIT"; exit 1; }
[ "$(g $SRC/dsv41 rev-parse HEAD)" = $DSV41_COMMIT ] || { echo "dsv41 is not at $DSV41_COMMIT"; exit 1; }
[ -z "$(g $SRC/derpseek status --porcelain --untracked-files=no)" ] || { echo "derpseek has changed tracked files"; exit 1; }
[ -z "$(g $SRC/dsv41 status --porcelain --untracked-files=no)" ] || { echo "dsv41 has changed tracked files"; exit 1; }
[ -d $SRC/dsv41/runtime/fastpath ] || { echo "no built runtime in $SRC/dsv41/runtime"; exit 1; }
docker image inspect "$BASE" >/dev/null 2>&1 || { echo "base image $BASE is not local"; exit 1; }

mkdir -p $W; rm -rf $W/ctx; mkdir -p $W/ctx/engine $W/ctx/derpseek
# Left out on purpose: dsv41 ships two prebuilt binaries for its second-GPU sidecar. This setup does not use the
# sidecar, and nothing that was not compiled here from source goes into the image.
tar -C $SRC/dsv41 --exclude=./.git --exclude=__pycache__ --exclude='*.pyc' --exclude=./kernels/vendor/ds41/peer3/prebuilt -cf - . | tar -C $W/ctx/engine -xf -
[ -z "$(find $W/ctx -name '*.so' -path '*prebuilt*')" ] || { echo "a prebuilt binary is still in the context"; exit 1; }
tar -C $SRC/derpseek/engine --exclude=__pycache__ --exclude='*.pyc' --exclude=./derpseek-isolated.sh --exclude=./state -cf - . | tar -C $W/ctx/derpseek -xf -
cp "$HERE/Dockerfile" "$HERE/entrypoint.sh" $W/ctx/
( cd $W/ctx && find engine derpseek -type f -print0 | sort -z | xargs -0 sha256sum ) > $W/ctx/MANIFEST.sha256
{ echo "tag=$TAG"; echo "derpseek=$DERPSEEK_COMMIT"; echo "dsv41=$DSV41_COMMIT"; echo "base=$BASE"; echo "built=$(date -u +%FT%TZ)"; } > $W/ctx/BUILD-INFO
echo "context: $(du -sh $W/ctx | cut -f1), $(wc -l < $W/ctx/MANIFEST.sha256) files"

docker build --network none -t "$TAG" --build-arg BASE="$BASE" \
  --build-arg DERPSEEK_COMMIT=$DERPSEEK_COMMIT --build-arg DSV41_COMMIT=$DSV41_COMMIT $W/ctx > $W/build.log 2>&1 \
  || { echo "BUILD FAILED, last lines of $W/build.log:"; tail -25 $W/build.log; exit 1; }
ID=$(docker image inspect "$TAG" --format '{{.Id}}')
echo "image=$ID" >> $W/ctx/BUILD-INFO
cp $W/ctx/BUILD-INFO $W/ctx/MANIFEST.sha256 $W/
echo "built $TAG = $ID ($(docker image inspect "$TAG" --format '{{.Size}}' | awk '{printf "%.1f GB", $1/1e9}'))"
