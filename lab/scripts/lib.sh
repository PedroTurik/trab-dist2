#!/usr/bin/env bash
# Funcoes comuns aos scripts de experimento. Uso: source scripts/lib.sh
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS="$LAB/scripts"
RES="$LAB/resultados"
ETCD_EPS="http://172.31.0.11:2379,http://172.31.0.12:2379,http://172.31.0.13:2379"

log() { echo "[$(date +%T)] $*" >&2; }
now_ns() { date +%s%N; }

# registra um evento (instante absoluto) no arquivo de eventos do experimento
event() { echo "$(now_ns),$2" >> "$1"; log "evento: $2"; }

# executa o gerador dentro do conteiner gerador (rede lab_clientes)
gen() { docker exec gerador /app/bin/gerador "$@"; }

cluster_down() {
  docker rm -f netshoot-tmp >/dev/null 2>&1 || true
  docker compose -f "$LAB/$1.yml" down -v --remove-orphans >/dev/null 2>&1 || true
}

# sobe um cluster limpo, espera ficar saudavel e pre-carrega as 10.000 chaves
cluster_up() {
  local sys="$1"
  cluster_down etcd; cluster_down cassandra
  mkdir -p "$RES"
  log "subindo cluster $sys"
  docker compose -f "$LAB/$sys.yml" up -d --wait >/dev/null
  if [ "$sys" = etcd ]; then
    until docker exec n1 etcdctl --endpoints="$ETCD_EPS" endpoint health >/dev/null 2>&1; do sleep 1; done
  else
    until [ "$(docker exec n1 nodetool status 2>/dev/null | grep -c '^UN')" = 3 ]; do sleep 3; done
    docker exec n1 cqlsh -e "CREATE KEYSPACE IF NOT EXISTS lab WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 3};
      CREATE TABLE IF NOT EXISTS lab.kv (k text PRIMARY KEY, v blob);" >/dev/null
    sleep 5
  fi
  gen load -sys "$sys"
  log "cluster $sys pronto"
}

# ---- etcd: lider ----
etcd_status_json() { docker exec n2 etcdctl --endpoints="$ETCD_EPS" endpoint status -w json 2>/dev/null; }

# imprime n1|n2|n3 do lider atual
etcd_leader() {
  etcd_status_json | jq -r '.[] | select(.Status.header.member_id == .Status.leader) | .Endpoint' \
    | sed -E 's#http://172.31.0.1([1-3]):2379#n\1#' | head -1
}

# transfere a lideranca para o no indicado (n1|n2|n3)
etcd_move_leader() {
  local target="$1" cur ip tid
  cur="$(etcd_leader)"
  [ "$cur" = "$target" ] && return 0
  ip="172.31.0.1${target#n}"
  tid=$(etcd_status_json | jq -r --arg e "http://$ip:2379" '.[] | select(.Endpoint == $e) | .Status.header.member_id' | xargs printf '%x')
  docker exec n2 etcdctl --endpoints="http://172.31.0.1${cur#n}:2379" move-leader "$tid" >/dev/null
  sleep 2
  log "lider do etcd: $(etcd_leader)"
}

# registra configuracao do ambiente (versoes, hardware) uma vez
record_env() {
  local f="$RES/ambiente.txt"
  {
    echo "data: $(date -Is)"
    echo "kernel: $(uname -r)"; grep PRETTY /etc/os-release
    lscpu | grep -E 'Model name|^CPU\(s\)'
    free -h | head -2
    findmnt -T /var/lib/docker -o SOURCE,FSTYPE,OPTIONS | tail -1
    lsblk -d -o NAME,MODEL,ROTA,SIZE | grep -v loop
    docker version --format 'docker {{.Server.Version}}'
    docker compose version
    docker image inspect --format '{{index .RepoTags 0}} {{.Id}}' quay.io/coreos/etcd:v3.7.3 cassandra:5.0 alpine:3.22 nicolaka/netshoot:latest 2>/dev/null
    go version 2>/dev/null || true
  } > "$f"
}

# salva os logs dos nos (diagnostico)
save_logs() { for c in n1 n2 n3; do docker logs "$c" 2>&1 | gzip > "$1/log_$c.txt.gz" || true; done; }
