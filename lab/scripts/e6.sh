#!/usr/bin/env bash
# E6 - Falha de no (failover). Uso: ./scripts/e6.sh etcd|cassandra N lider|seguidor [reps]
# Carga do E2, mas cada cliente conhece os 3 nos (-pin=false), para que o driver possa
# redirecionar as requisicoes apos a falha. Linha do tempo (240 s):
#   t=0 inicio | t=60 s docker kill <vitima> | t=120 s docker start <vitima> | t=240 s fim.
# etcd: lider = n1 (lideranca transferida antes), seguidor = n3.
# Cassandra (sem lider): "lider" = n1 (seed e primeiro contact point), "seguidor" = n3.
source "$(dirname "$0")/lib.sh"
SYS="$1"; N="$2"; VAR="$3"; REPS="${4:-3}"
VICTIM=n1; [ "$VAR" = seguidor ] && VICTIM=n3
for rep in $(seq 1 "$REPS"); do
  cluster_up "$SYS"
  [ "$SYS" = etcd ] && etcd_move_leader n1
  out="$RES/$SYS/e6/$VAR/rep$rep"; mkdir -p "$out"; ev="$out/events.csv"
  echo "t_ns,event" > "$ev"
  [ "$SYS" = etcd ] && etcd_status_json > "$out/etcd_status_antes.json"

  gen run -sys "$SYS" -threads "$N" -pin=false -dur 240s -warmup 30s -seed 42 \
    -out "/resultados/$SYS/e6/$VAR/rep$rep/load" > "$out/load_summary.txt" &
  GP=$!
  event "$ev" load_start
  sleep 60
  docker kill "$VICTIM" >/dev/null
  event "$ev" "kill_$VICTIM"
  sleep 10
  [ "$SYS" = etcd ] && { etcd_status_json > "$out/etcd_status_falha.json" || true; log "novo lider: $(etcd_leader || true)"; }
  [ "$SYS" = cassandra ] && docker exec n2 nodetool status > "$out/nodetool_falha.txt" 2>&1 || true
  sleep 50
  docker start "$VICTIM" >/dev/null
  event "$ev" "start_$VICTIM"
  wait $GP
  event "$ev" load_end
  save_logs "$out"
  [ "$SYS" = etcd ] && etcd_status_json > "$out/etcd_status_depois.json" || true
  [ "$SYS" = cassandra ] && docker exec n2 nodetool status > "$out/nodetool_depois.txt" 2>&1 || true
done
cluster_down "$SYS"
