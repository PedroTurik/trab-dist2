#!/usr/bin/env bash
# E5 - Particao de rede. Uso: ./scripts/e5.sh etcd|cassandra N [reps]
# Carga do E2 com threads fixas nos 3 nos (thread i -> no i%3). Linha do tempo (~150 s):
#   t=0 inicio | t=40 s isola n1 (particao.sh) | t=100 s cura (cura.sh) | t=150 s fim.
# Depois da carga, le todas as chaves em cada no ate os tres concordarem (convergencia).
source "$(dirname "$0")/lib.sh"
SYS="$1"; N="$2"; REPS="${3:-3}"
for rep in $(seq 1 "$REPS"); do
  cluster_up "$SYS"
  [ "$SYS" = etcd ] && etcd_move_leader n1   # n1 (isolado) comeca como lider
  out="$RES/$SYS/e5/rep$rep"; mkdir -p "$out"; ev="$out/events.csv"
  echo "t_ns,event" > "$ev"
  [ "$SYS" = etcd ] && etcd_status_json > "$out/etcd_status_antes.json"

  gen run -sys "$SYS" -threads "$N" -dur 150s -warmup 30s -seed 42 -out "/resultados/$SYS/e5/rep$rep/load" > "$out/load_summary.txt" &
  GP=$!
  event "$ev" load_start
  sleep 40
  event "$ev" partition; TP=$(date +%s)
  "$SCRIPTS/particao.sh"
  # verificacao obrigatoria (apendice A.9)
  { echo "## n2 -> n3 (deve funcionar)"; "$SCRIPTS/rtt.sh" n2 172.30.0.13 2>&1 | tail -2 || true
    echo "## n1 -> n2 (deve falhar)"; timeout 10 docker run --rm --net container:n1 nicolaka/netshoot ping -c 3 -W 1 172.30.0.12 2>&1 | tail -2 || true
    echo "## gerador -> n1 (deve funcionar)"; docker run --rm --net container:gerador nicolaka/netshoot ping -c 3 172.31.0.11 2>&1 | tail -2 || true; } > "$out/verificacao_particao.txt"
  cat "$out/verificacao_particao.txt" >&2
  sleep 15
  [ "$SYS" = etcd ] && { etcd_status_json > "$out/etcd_status_particao.json" || true; }
  [ "$SYS" = cassandra ] && { docker exec n1 nodetool status > "$out/nodetool_n1_particao.txt" 2>&1; docker exec n2 nodetool status > "$out/nodetool_n2_particao.txt" 2>&1; }
  sleep $((60 - ($(date +%s) - TP)))
  event "$ev" heal
  "$SCRIPTS/cura.sh"
  wait $GP
  event "$ev" load_end

  # convergencia: le as 10.000 chaves em cada no ate n1 = n2 = n3
  echo "t_s,diff_n1_n2,diff_n1_n3,diff_n2_n3" > "$out/convergencia.csv"
  t0=$(date +%s)
  while :; do
    for i in 0 1 2; do gen dump -sys "$SYS" -node $i -out "/resultados/$SYS/e5/rep$rep/dump_n$((i+1)).csv"; done
    d12=$(diff "$out/dump_n1.csv" "$out/dump_n2.csv" | grep -c '^<' || true)
    d13=$(diff "$out/dump_n1.csv" "$out/dump_n3.csv" | grep -c '^<' || true)
    d23=$(diff "$out/dump_n2.csv" "$out/dump_n3.csv" | grep -c '^<' || true)
    t=$(( $(date +%s) - t0 ))
    echo "$t,$d12,$d13,$d23" >> "$out/convergencia.csv"
    log "convergencia t=${t}s: n1/n2=$d12 n1/n3=$d13 n2/n3=$d23"
    if [ "$d12$d13$d23" = 000 ] || [ "$t" -ge 900 ]; then break; fi
    sleep 10
  done
  event "$ev" converged
  [ "$SYS" = etcd ] && etcd_status_json > "$out/etcd_status_depois.json"
done
cluster_down "$SYS"
