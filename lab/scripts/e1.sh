#!/usr/bin/env bash
# E1 - Saturacao. Uso: ./scripts/e1.sh etcd|cassandra [reps] [lista de N]
# Cada repeticao parte de um cluster limpo; cada ponto dura 2 min (30 s de aquecimento descartados).
source "$(dirname "$0")/lib.sh"
SYS="$1"; REPS="${2:-3}"
NS="${3:-1 2 4 8 16 32 64 128 256 512 1024 2048 4096 8192 16384}"
for rep in $(seq 1 "$REPS"); do
  cluster_up "$SYS"
  out="$RES/$SYS/e1/rep$rep"; mkdir -p "$out"
  for n in $NS; do
    log "E1 $SYS rep$rep N=$n"
    gen run -sys "$SYS" -threads "$n" -dur 120s -warmup 30s -seed 42 \
      -out "/resultados/$SYS/e1/rep$rep/n$n" | jq -c '{threads, throughput_ok, p99: .all.p99_ms, errors, timeouts, gen_cpus_util_pct_avg}'
    sleep 5
  done
done
cluster_down "$SYS"
