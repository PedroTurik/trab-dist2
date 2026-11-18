# Laboratório: Consistência, Latência e Vazão — etcd × Cassandra

Sistemas: **etcd v3.7.3** (classe forte, Raft, leitura linearizável) e **Cassandra 5.0** (classe eventual,
RF = 3, CL = ONE, tabela `lab.kv (k text PRIMARY KEY, v blob)` usada como chave-valor).
Ambiente baseado no `lab-compose.zip` do apêndice A. As mudanças estão listadas no topo de `etcd.yml`/`cassandra.yml`
e no relatório.

## Estrutura

```
lab/
  etcd.yml, cassandra.yml   clusters de 3 nós (n1, n2, n3) + conteiner gerador
  gerador/                  gerador de carga em Go (loop fechado), adaptadores finos (kv.go)
    main.go                 modos: load | run | stale (coordenador E3) | dump (convergência E5)
    kv.go                   adaptador etcd (clientv3) e Cassandra (gocql), mesma interface Get/Put
    cpu.go                  monitor de CPU do gerador (1 amostra/s)
    bin/gerador             binário estático (montado em /app no conteiner gerador)
  scripts/
    lib.sh                  funções comuns (subir cluster limpo, pré-carga, líder do etcd)
    latencia_on.sh / latencia_off.sh / rtt.sh / particao.sh / cura.sh   (do apêndice)
    e1.sh ... e6.sh         um script por experimento (3 repetições por padrão)
  analise/analise.py        gera figuras e tabelas (relatorio/figs, relatorio/tabelas)
  resultados/               saídas: amostras brutas (.csv.gz, ~42 GB, não incluídas no zip de entrega),
                            resumos (.json), CPU do gerador (.cpu.csv), eventos, pings/tc, logs, dumps do E5
../relatorio/               RELATORIO.md + parte2.md (texto), tabelas/ e figs/ (geradas), build.sh, relatorio.pdf
```

Redes: `lab_cluster` 172.30.0.0/24 (tráfego entre nós; recebe atraso e partição) e
`lab_clientes` 172.31.0.0/24 (gerador ↔ nós; nunca recebe atraso). As sub-redes do apêndice
(172.20/172.21) colidiam com redes já existentes na máquina e foram trocadas.

## Pré-requisitos

Linux com Docker Engine + Compose v2, `jq`, Go ≥ 1.22 (só para recompilar o gerador) e Python 3 (análise).

```bash
cd lab
(cd gerador && CGO_ENABLED=0 go build -o bin/gerador .)      # recompilar o gerador (opcional)
python3 -m venv .venv && .venv/bin/pip install pandas matplotlib numpy
```

## Execução dos experimentos

Cada script sobe um cluster limpo (`down -v` + `up`), pré-carrega as 10.000 chaves e repete 3 vezes.
Os valores de N vêm do E1 (N ótimo).

```bash
./scripts/e1.sh etcd;            ./scripts/e1.sh cassandra            # saturação (N = 1..16384, 2 min/ponto)
./scripts/e2.sh etcd 1024;       ./scripts/e2.sh cassandra 64         # baseline (3 min)
./scripts/e3.sh etcd;            ./scripts/e3.sh cassandra            # stale reads (10.000 pares, rede original e +50 ms)
./scripts/e4.sh etcd 1024;       ./scripts/e4.sh cassandra 64         # latência elevada (netem 50 ms/sentido)
./scripts/e5.sh etcd 1024;       ./scripts/e5.sh cassandra 64         # partição de n1 por 60 s
./scripts/e6.sh etcd 1024 lider; ./scripts/e6.sh etcd 1024 seguidor  # docker kill (failover)
./scripts/e6.sh cassandra 64 lider; ./scripts/e6.sh cassandra 64 seguidor
.venv/bin/python analise/analise.py                                  # figuras + tabelas
../relatorio/build.sh                                                # relatorio.pdf (pandoc/latex em Docker)
```

Diagnóstico do colapso de vazão do etcd (seção 4.6 do relatório; fora das médias):
`E2DIR=e2_diag ./scripts/e2.sh etcd 1024 3` (guarda os logs dos nós em `resultados/etcd/e2_diag/logs_rep*`).

O driver `./scripts/run_resto.sh 1024 64` executa E5, E6, E3, E2 e E4 dos dois sistemas em sequência.

### Demonstração rápida do E2 (apresentação)

```bash
./scripts/e2.sh etcd 1024 1          # 1 repetição (~4 min)
./scripts/e2.sh cassandra 64 1       # 1 repetição (~8 min, a subida do Cassandra leva ~4 min)
.venv/bin/python analise/analise.py e2
```

O resumo (vazão efetiva, p50/p95/p99 de GET e PUT, erros, CPU do gerador) é impresso ao final de cada execução
e salvo em `resultados/<sistema>/e2/rep<k>.json`.

## Gerador de carga

`gerador run -sys etcd|cassandra -threads N -dur 120s -warmup 30s -seed 42 -timeout 2s -out <prefixo>`

* N goroutines em loop fechado; cada uma sorteia 80% GET / 20% PUT e uma chave uniforme em 10.000
  (`k00000`…`k09999`), valor de 256 B; mede t0/t1 e reinicia sem espera.
* Semente fixa por thread (`PCG(seed, thread)`), logo a mesma sequência de operações nos dois sistemas.
* Timeout de 2 s por requisição: a amostra é registrada como erro, sem latência.
* Saídas: `<prefixo>.csv.gz` (uma linha por requisição: `start_ns` absoluto, `lat_us`, op, ok, nó, chave,
  id do valor, tipo de erro), `<prefixo>.json` (resumo após o aquecimento) e `<prefixo>.cpu.csv` (CPU do gerador).
* Por padrão a thread i usa sempre o nó i mod 3 como coordenador (`-pin`); no E6 usa-se `-pin=false`, em que o
  cliente conhece os três nós e o driver redireciona as requisições após a falha.
