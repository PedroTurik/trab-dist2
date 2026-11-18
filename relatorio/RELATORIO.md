---
title: "Laboratório: Consistência, Latência e Vazão em Armazenamento Replicado"
subtitle: "etcd (Raft, linearizável) × Cassandra (RF = 3, CL = ONE)"
author: "Pedro Turik Firmino"
date: "Novembro de 2026"
lang: pt-BR
---

# 1. Objetivo e resumo

Medimos o custo da consistência forte (consenso Raft no **etcd**) em relação à consistência eventual
(**Cassandra** com RF = 3 e CL = ONE) em operação normal (E1, E2), com latência elevada entre os nós (E3, E4),
em partição de rede (E5) e em falha de nó (E6). Os dois clusters têm 3 nós em Docker Compose e são exercitados
pelo mesmo gerador de carga em loop fechado. O código, os scripts e os dados brutos estão em `lab/`
(ver `lab/README.md`).

**Principais resultados** (mediana de 3 repetições):

| | etcd (forte) | Cassandra (eventual) |
|---|---|---|
| E1: N ótimo / vazão no ótimo / p99 | 1024 / 114,5 mil req/s / 17,8 ms | 64 / 154 mil req/s / 0,82 ms |
| E1: vazão máxima sustentada (platô) | ~196 mil req/s (CPU do líder) | ~460 mil req/s (CPU dos 3 nós) |
| E2 → E4 (RTT 100 ms): PUT p99 | 24 → 219 ms | 0,58 → 1,28 ms |
| E2 → E4: vazão (N fixo) | 109 mil → 3,7 mil req/s (−97%) | 160 mil → 185 mil req/s |
| E3: stale reads (rede original / +50 ms) | 0% / 0% (serializable: 62% / 99,9%) | 0,55% / 99,3% |
| E5: lado minoritário (n1) | indisponível (timeouts) — **CP** | aceita leituras e escritas, ~64% stale — **AP** |
| E5: escritas confirmadas perdidas | 0 | 0 (LWW, relógio único) |
| E6: escritas interrompidas (líder / seguidor) | 1,4 s / 0 s | 0 s / 0 s |

O consenso cobra latência proporcional a rounds até um quórum, que fica evidente quando o RTT entre nós cresce (E4),
e concentra a vazão de escrita num único líder (E1). Em troca, não há stale reads (E3) nem perda de escritas, e o
sistema fica indisponível só do lado minoritário de uma partição (E5). O Cassandra com CL = ONE responde sem esperar a
rede, escala com os nós e fica disponível na partição e na falha, pagando com leituras desatualizadas sempre que a
replicação assíncrona se atrasa.

# 2. Ambiente e configuração

## 2.1 Hardware e software

| Item | Valor |
|---|---|
| CPU | Intel Core i7-14700 (8 núcleos P com HT + 12 núcleos E = 28 CPUs lógicas), até 5,4 GHz |
| Memória | 32 GB |
| Disco | NVMe Kingston SFYRS1000G (1 TB), ext4 (`rw,relatime`), Docker overlay2 |
| SO | Arch Linux, kernel 7.1.8, Docker Engine 29.7.2, Docker Compose 5.5.0 |
| etcd | `quay.io/coreos/etcd:v3.7.3` (cliente Go `go.etcd.io/etcd/client/v3` v3.7.3) |
| Cassandra | `cassandra:5.0` (5.0.9) (cliente Go `github.com/gocql/gocql` v1.7.0) |
| Gerador | Go 1.26 (binário estático em `alpine:3.22`) |
| Emulação de rede | `nicolaka/netshoot` (tc netem / ping), `docker network disconnect` |

**Isolamento de CPU.** Para que gerador e nós não disputem CPU, cada contêiner tem `cpuset` fixo:
n1 = CPUs 0-3, n2 = 4-7, n3 = 8-11 (2 núcleos P com HT cada) e gerador = CPUs 12-27 (2 núcleos P + 12 núcleos E).
Todos os nós e o gerador estão na mesma máquina; a "rede" é a bridge do Docker (RTT entre nós ≈ 0,05-0,1 ms).

**Manutenção.** Ambos os sistemas e clientes são mantidos: etcd é projeto graduado da CNCF (v3.7.3 é a série
estável atual); `cassandra:5.0` é imagem oficial do Docker Hub (Apache Cassandra 5.0.9); o driver `gocql` é
mantido pela comunidade e foi doado à Apache (`apache/cassandra-gocql-driver`).

## 2.2 Topologia (apêndice A, com ajustes)

Mantivemos a topologia do `lab-compose.zip`: contêineres `n1`, `n2`, `n3` e `gerador`; duas redes Docker, uma
entre nós (`lab_cluster`, onde se aplicam atraso e partição) e uma de clientes (`lab_clientes`, nunca afetada).
Mudanças em relação ao pacote (registradas também nos `.yml`):

* sub-redes trocadas de 172.20/172.21 para **172.30.0.0/24** (entre nós) e **172.31.0.0/24** (clientes), pois as
  originais colidiam com redes já existentes na máquina (problema previsto na seção A.12);
* etcd v3.5.14 → v3.7.3 e Cassandra 4.1 → 5.0 (versões estáveis atuais);
* heap do Cassandra 512 MB → 2 GB por nó (a máquina tem 32 GB; evita GC excessivo na saturação);
* `cpuset` por contêiner (acima);
* etcd: compactação automática periódica (`--auto-compaction-mode=periodic --auto-compaction-retention=1m`) e quota
  de 8 GB, para o histórico MVCC não esgotar a quota de 2 GB nas rodadas longas do E1 (sem efeito sobre a semântica);
* gerador em Go (em vez de Python) para sustentar milhares de clientes concorrentes sem saturar a CPU (seção 3).

## 2.3 Parâmetros de replicação, consistência e durabilidade

| | etcd (classe forte) | Cassandra (classe eventual) |
|---|---|---|
| Replicação | Raft, 3 membros, quórum = 2; líder único ordena todas as escritas | RF = 3 (`SimpleStrategy`), todos os nós têm todas as chaves; sem líder |
| Escrita (PUT) | `Put`: proposta ao líder, *commit* quando gravada (com fsync) na maioria (2/3), aplicada e respondida | `INSERT ... CL=ONE`: coordenador envia aos 3 réplicas e responde após **1** confirmação |
| Leitura (GET) | `Get` **linearizável** (padrão): ReadIndex — o líder confirma a liderança com um round de heartbeat a um quórum e o nó espera aplicar até esse índice. Variante: `WithSerializable()` (leitura local) | `SELECT ... CL=ONE`: responde com a réplica mais próxima segundo o *snitch* |
| Conflitos | não há (ordem total do log) | *last-write-wins* por timestamp da célula (timestamp do cliente, gerado pelo gocql) |
| Durabilidade | WAL com **fsync** antes de confirmar (`--unsafe-no-fsync=false`, padrão) | commit log `periodic`, fsync a cada **10 s** (padrão): a escrita é confirmada antes do fsync |
| Detecção de falha / eleição | heartbeat 100 ms, election timeout 1000 ms (padrões), PreVote e CheckQuorum ligados | gossip + detector φ-accrual (`phi_convict_threshold` = 8, padrão) |
| Reparo | não se aplica (log replicado) | hinted handoff (janela 3 h, throttle 1024 KiB/s), read repair só para CL > ONE, `nodetool repair` manual (não usado) |
| Timeouts do servidor | — | `write_request_timeout` 2 s, `read_request_timeout` 5 s |

## 2.4 Verificação da semântica na documentação

**etcd.** A página *API guarantees* afirma que o etcd garante **linearizabilidade** para todas as operações de
chave-valor por padrão (exceto *watch*), e que leituras `serializable` são uma opção explícita que "pode acessar dados
desatualizados em relação ao quórum" em troca de menor latência [1]. A mesma página garante que "operações concluídas
são duráveis" e que uma leitura nunca devolve dado não durável [1]. O mecanismo de leitura linearizável é o ReadIndex
do Raft (tese de Ongaro, §6.4) [3]; a escrita só é confirmada depois de replicada na maioria [2]. Portanto a
configuração usada (padrão, leitura linearizável, fsync ligado) pertence à classe forte.

**Cassandra.** A documentação de arquitetura (*Dynamo — Tunable Consistency*) define `ONE` como "apenas uma réplica
precisa responder" e explica que leituras e escritas só se sobrepõem — garantindo que toda escrita confirmada seja
vista pelas leituras seguintes — quando R + W > RF [4]. Com CL = ONE em ambos, R + W = 2 ≤ RF = 3: **não há
interseção garantida de quóruns**, e a convergência depende de hinted handoff, read repair e anti-entropia [4, 5],
com resolução de conflitos por *last-write-wins* sobre timestamps [4]. A configuração usada pertence, portanto, à
classe eventual. Conforme pedido, **declaramos que a tabela `lab.kv (k text PRIMARY KEY, v blob)` é usada como
armazenamento chave-valor** (GET = `SELECT v FROM kv WHERE k = ?`, PUT = `INSERT INTO kv (k, v) VALUES (?, ?)`).

# 3. Gerador de carga e método

**Gerador** (`lab/gerador`, Go). N goroutines em **loop fechado**; cada uma (1) sorteia a operação — 80% GET, 20% PUT
— e uma chave uniforme entre 10.000 (`k00000`…`k09999`, pré-carregadas), com valor de 256 B; (2) marca t0, envia a
requisição e espera a resposta ou o **timeout de 2 s**; (3) marca t1 e registra a amostra (início absoluto em ns,
latência, operação, sucesso/erro, nó, chave, id do valor); (4) recomeça imediatamente. Em timeout a amostra é
registrada como erro, **sem latência**. A **semente é fixa** (PCG(42, thread)): a sequência de operações e chaves de
cada thread é idêntica nos dois sistemas. A lógica é única; cada sistema tem apenas um **adaptador fino** de ~30
linhas (`kv.go`) com `Get`/`Put`. Os primeiros 30 s de cada execução são descartados nos resumos.

**Clientes.** Em E1-E5 a thread i usa sempre o nó i mod 3 como ponto de contato (um terço da carga por nó), o que
permite separar os lados da partição no E5. No E6 cada cliente conhece os 3 nós (`-pin=false`) e o próprio driver
redireciona após a falha (gRPC round-robin com keepalive de 1 s no etcd; `RoundRobinHostPolicy` + eventos de topologia
no gocql). O gocql foi configurado **sem retentativas** (`RetryPolicy = nil`), para que o gerador observe cada erro.

**Valores e verificação.** Os primeiros 16 bytes de cada valor contêm um identificador único da escrita
(thread, sequência). Isso permite saber exatamente qual escrita cada GET leu (stale reads no E5) e qual escrita
sobreviveu após a reconciliação (escritas perdidas no E5).

**Métricas.** Vazão efetiva = requisições concluídas com sucesso por segundo (na janela após o aquecimento).
Percentis (p50, p95, p99) só de requisições com sucesso. Cada configuração foi repetida **3 vezes**, cada repetição
em um **cluster novo** (`docker compose down -v` + `up` + pré-carga); as tabelas mostram **mediana [mín-máx]**.

**CPU do gerador.** O gerador amostra a cada 1 s a própria CPU (`getrusage`) e a utilização das 16 CPUs do seu
`cpuset` (`/proc/stat`). O valor máximo ficou em 50% (etcd) e 63% (Cassandra) no E1, mesmo com N = 16384, e abaixo de 30% nos demais experimentos: **o gerador nunca saturou**. Como ele roda em CPUs separadas das dos nós, não há disputa de CPU com o cluster.

