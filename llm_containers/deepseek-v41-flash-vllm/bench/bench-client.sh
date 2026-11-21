#!/bin/bash
# Runs INSIDE a throwaway client container (vllm image, --network host, /models ro, /bench ro, /results rw).
# Usage: bench-client.sh <out dir> "<scenarios>"   scenarios: sweep prefill warm needle agents burst (needle includes the repetition check)
set -u
OUT=$1; SC=${2:-"sweep prefill warm needle"}
U=${BENCH_URL:-http://127.0.0.1:8000}
M=$(curl -s $U/v1/models | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])')
MAXLEN=$(curl -s $U/v1/models | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0].get("max_model_len", 0))')
TOK=/models/DeepSeek-V4.1-Flash
# vocab_size sits under text_config for this model; fall back to the tokenizer file.
VOCAB=$(python3 -c "
import json
c = json.load(open('$TOK/config.json'))
v = c.get('vocab_size') or (c.get('text_config') or {}).get('vocab_size')
if not v:
    t = json.load(open('$TOK/tokenizer.json'))
    v = len(t['model']['vocab']) + len(t.get('added_tokens', []))
print(int(v))")
[ -n "$VOCAB" ] || { echo "could not determine the vocabulary size"; exit 1; }
echo "model=$M max_model_len=$MAXLEN vocab=$VOCAB kv_tokens=${KV_TOKENS:-unknown}"
mkdir -p $OUT/json
COMMON="--backend vllm --base-url $U --endpoint /v1/completions --model $M --tokenizer $TOK --trust-remote-code
 --dataset-name random --random-range-ratio 0 --ignore-eos --seed 42 --request-rate inf
 --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,90,99
 --save-result --save-detailed --result-dir $OUT/json --disable-tqdm --ready-check-timeout-sec 600"
run() { # name input_len output_len concurrency num_prompts warmups
  echo "=== $(date -u +%FT%TZ) START $1 in=$2 out=$3 conc=$4 n=$5 warmups=$6"
  vllm bench serve $COMMON --random-input-len "$2" --random-output-len "$3" --max-concurrency "$4" \
    --num-prompts "$5" --num-warmups "$6" --result-filename "$1.json" --label "$1"
  local rc=$?; echo "=== $(date -u +%FT%TZ) END $1 rc=$rc"
}
for s in $SC; do case $s in
  sweep)   # same shape as the 19 Nov baseline (scenario A), plus 8 and 24 streams
    run A-c01 1024 512 1 8 1; run A-c04 1024 512 4 16 4; run A-c08 1024 512 8 32 4
    run A-c16 1024 512 16 64 4; run A-c24 1024 512 24 96 4 ;;
  prefill) # cold time to first token for one long prompt at a time (random tokens: no prefix-cache hits)
    run P-100k 100000 64 1 2 0
    [ "$MAXLEN" -ge 399100 ] && run P-400k 399000 64 1 2 0 ;;
  warm)    # how many ~390k sessions stay warm: N = floor(KV / session) and N+1
    L=390000; S=$((L + 512 + 16))
    if [ -n "${KV_TOKENS:-}" ] && [ "$MAXLEN" -ge $S ]; then N=$(( KV_TOKENS / S )); else N=4; fi
    for n in $N $((N + 1)); do
      [ $n -ge 1 ] || continue
      echo "=== $(date -u +%FT%TZ) START W-$n sessions=$n prefix=$L"
      python3 /bench/warm.py --url $U --model "$M" --vocab $VOCAB --sessions $n --prefix $L --turns 3 --out $OUT/warm-$n.json
      rc=$?; echo "=== $(date -u +%FT%TZ) END W-$n rc=$rc"
    done ;;
  agents)  # N agents, each with its own cached context of real code (agentbench.py)
    [ -f /corpus/extra.jsonl ] || { echo "=== no corpus at /corpus: agents skipped"; continue; }
    for L in ${AGENT_CONTEXTS:-118000 385000 1000000}; do
      [ "$MAXLEN" -ge $(( L + 12000 )) ] || { echo "=== G-ctx$L skipped: max_model_len $MAXLEN leaves no room for the turns"; continue; }
      echo "=== $(date -u +%FT%TZ) START G-ctx$L agents=${AGENT_COUNTS:-1,5,8}"
      python3 /bench/agentbench.py --url $U --model "$M" --corpus /corpus --context $L --agents "${AGENT_COUNTS:-1,5,8}" \
        --turns "${AGENT_TURNS:-3}" --repeats "${AGENT_REPEATS:-1}" --out $OUT/agents-$L.json
      rc=$?; echo "=== $(date -u +%FT%TZ) END G-ctx$L rc=$rc"
    done ;;
  burst)   # N agents start a new context at the same moment (agentbench.py --burst)
    [ -f /corpus/extra.jsonl ] || { echo "=== no corpus at /corpus: burst skipped"; continue; }
    L=${BURST_CONTEXT:-385000}; n=${BURST_AGENTS:-8}
    echo "=== $(date -u +%FT%TZ) START B-ctx$L agents=$n"
    python3 /bench/agentbench.py --url $U --model "$M" --corpus /corpus --context $L --agents "$n" --burst --out $OUT/burst-$L.json
    rc=$?; echo "=== $(date -u +%FT%TZ) END B-ctx$L rc=$rc" ;;
  needle)
    L2=390000; [ "$MAXLEN" -lt 391000 ] && L2=$(( MAXLEN - 2000 ))
    echo "=== $(date -u +%FT%TZ) START N-needle lengths=100000,$L2"
    python3 /bench/needle.py --url $U --model "$M" --tokenizer $TOK --lengths 100000,$L2 --out $OUT/needle.json
    rc=$?; echo "=== $(date -u +%FT%TZ) END N-needle rc=$rc" ;;
esac; done
