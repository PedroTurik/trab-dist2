#!/usr/bin/env bash
# E4 - Rede com latencia elevada. Uso: ./scripts/e4.sh etcd|cassandra N [atraso] [reps]
# tc netem delay na saida de cada no, so na interface da rede entre nos (lab_cluster).
# 50 ms por sentido => RTT de 100 ms entre nos; trafego gerador<->nos sem atraso.
source "$(dirname "$0")/lib.sh"
SYS="$1"; N="$2"; DELAY="${3:-50ms}"; REPS="${4:-3}"
for rep in $(seq 1 "$REPS"); do
  cluster_up "$SYS"
  [ "$SYS" = etcd ] && etcd_move_leader n1
  out="$RES/$SYS/e4"; mkdir -p "$out"
  "$SCRIPTS/latencia_on.sh" "$DELAY" n1 n2 n3 | tee "$out/netem_rep$rep.txt"
  docker run --rm --net container:n1 nicolaka/netshoot tc qdisc show >> "$out/netem_rep$rep.txt"
  { "$SCRIPTS/rtt.sh" n1 172.30.0.12; "$SCRIPTS/rtt.sh" n2 172.30.0.13
    docker run --rm --net container:gerador nicolaka/netshoot ping -c 3 172.31.0.11 || true; } > "$out/rtt_rep$rep.txt" 2>&1
  grep -E "rtt|round-trip" "$out/rtt_rep$rep.txt" >&2 || true
  log "E4 $SYS rep$rep N=$N atraso=$DELAY"
  gen run -sys "$SYS" -threads "$N" -dur 180s -warmup 30s -seed 42 -out "/resultados/$SYS/e4/rep$rep" \
    | jq -c '{throughput_ok, get: .get, put: .put, errors, timeouts, gen_cpus_util_pct_avg}'
  mkdir -p "$RES/$SYS/e4/logs_rep$rep"; save_logs "$RES/$SYS/e4/logs_rep$rep"
  "$SCRIPTS/latencia_off.sh" n1 n2 n3
done
cluster_down "$SYS"
