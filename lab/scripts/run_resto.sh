#!/usr/bin/env bash
# Executa E2-E6 para os dois sistemas com o N otimo de cada um (obtido no E1).
# Uso: ./scripts/run_resto.sh <N_etcd> <N_cassandra>
set -euo pipefail
D="$(dirname "$0")"
NE="$1"; NC="$2"
for s in "etcd:$NE" "cassandra:$NC"; do
  sys="${s%%:*}"; n="${s##*:}"
  "$D/e5.sh" "$sys" "$n"
  "$D/e6.sh" "$sys" "$n" lider
  "$D/e6.sh" "$sys" "$n" seguidor
  "$D/e3.sh" "$sys"
  "$D/e2.sh" "$sys" "$n"
  "$D/e4.sh" "$sys" "$n"
done
