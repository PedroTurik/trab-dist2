| Sistema | N ótimo | vazão no ótimo (req/s) | p99 no ótimo (ms) | vazão/p99 (req/s por ms) | vazão máx. sustentada (req/s) | N no platô (≥95% do máx.) | CPU máx. do gerador (%) |
|---|---|---|---|---|---|---|---|
| etcd | 1024 | 114.547 | 17,77 | 6.446 | 196.620 (N=8192) | 8192; 16384 | 50 |
| cassandra | 64 | 154.106 | 0,82 | 188.164 | 462.431 (N=2048) | 1024; 2048; 4096 | 63 |
