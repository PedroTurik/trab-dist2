#!/usr/bin/env bash
# E3 - Stale reads. Uso: ./scripts/e3.sh etcd|cassandra [reps]
# Coordenador: PUT(k, v crescente) no n1; apos o retorno com sucesso, GET(k) no n3.
# Condicoes: latencia original e latencia elevada (50 ms por sentido, como no E4).
# No etcd roda tambem a variante serializable (leitura local no n3, sem ReadIndex).
source "$(dirname "$0")/lib.sh"
SYS="$1"; REPS="${2:-3}"
variants=("linearizable:false"); [ "$SYS" = etcd ] && variants+=("serializable:true")
[ "$SYS" = cassandra ] && variants=("one:false")
for rep in $(seq 1 "$REPS"); do
  cluster_up "$SYS"
  [ "$SYS" = etcd ] && etcd_move_leader n1
  for cond in normal atraso; do
    if [ "$cond" = atraso ]; then
      "$SCRIPTS/latencia_on.sh" 50ms n1 n2 n3
      "$SCRIPTS/rtt.sh" n1 172.30.0.13 > "$RES/$SYS/e3/rtt_rep$rep.txt"
    fi
    for v in "${variants[@]}"; do
      name="${v%%:*}"; ser="${v##*:}"
      mkdir -p "$RES/$SYS/e3/$name/$cond"
      log "E3 $SYS rep$rep $cond $name"
      gen stale -sys "$SYS" -pairs 10000 -workers 8 -serializable="$ser" \
        -out "/resultados/$SYS/e3/$name/$cond/rep$rep" | jq -c '{pairs, stale, stale_rate, max_lag_versions, put_errors, get_errors, elapsed_s}'
    done
  done
  "$SCRIPTS/latencia_off.sh" n1 n2 n3
done
cluster_down "$SYS"
