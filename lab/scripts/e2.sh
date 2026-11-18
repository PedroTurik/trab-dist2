#!/usr/bin/env bash
# E2 - Baseline. Uso: ./scripts/e2.sh etcd|cassandra N [reps]   (E2DIR=nome muda o diretorio de saida)
# N = N otimo do E1. 3 min sem falhas (30 s de aquecimento descartados).
source "$(dirname "$0")/lib.sh"
SYS="$1"; N="$2"; REPS="${3:-3}"
for rep in $(seq 1 "$REPS"); do
  cluster_up "$SYS"
  [ "$SYS" = etcd ] && etcd_move_leader n1
  mkdir -p "$RES/$SYS/${E2DIR:-e2}"
  log "E2 $SYS rep$rep N=$N"
  gen run -sys "$SYS" -threads "$N" -dur 180s -warmup 30s -seed 42 -out "/resultados/$SYS/${E2DIR:-e2}/rep$rep" \
    | jq -c '{throughput_ok, get: .get, put: .put, errors, timeouts, gen_cpus_util_pct_avg}'
  mkdir -p "$RES/$SYS/${E2DIR:-e2}/logs_rep$rep"; save_logs "$RES/$SYS/${E2DIR:-e2}/logs_rep$rep"
done
cluster_down "$SYS"
