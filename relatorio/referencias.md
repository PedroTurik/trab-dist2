# Referências

[1] etcd — *API guarantees* (v3.6/v3.7). <https://etcd.io/docs/v3.6/learning/api_guarantees/>

[2] etcd — *Tuning* (heartbeat interval, election timeout) e *FAQ*. <https://etcd.io/docs/v3.6/tuning/>,
<https://etcd.io/docs/v3.6/faq/>

[3] D. Ongaro. *Consensus: Bridging Theory and Practice*. Tese de doutorado, Stanford, 2014 (§6.4, leituras
linearizáveis com ReadIndex). D. Ongaro, J. Ousterhout. *In Search of an Understandable Consensus Algorithm*, USENIX ATC 2014.

[4] Apache Cassandra 5.0 — *Dynamo* (Tunable Consistency, Hinted Handoff, Replica synchronization).
<https://cassandra.apache.org/doc/5.0/cassandra/architecture/dynamo.html>

[5] Apache Cassandra 5.0 — *Hints* e *Read repair*.
<https://cassandra.apache.org/doc/5.0/cassandra/managing/operating/hints.html>,
<https://cassandra.apache.org/doc/5.0/cassandra/managing/operating/read_repair.html>; `cassandra.yaml`
(`commitlog_sync`, `hinted_handoff_*`): <https://cassandra.apache.org/doc/5.0/cassandra/managing/configuration/cass_yaml_file.html>

[6] S. Gilbert, N. Lynch. *Brewer's conjecture and the feasibility of consistent, available, partition-tolerant web
services*. ACM SIGACT News 33(2), 2002.

[7] C. Cachin, R. Guerraoui, L. Rodrigues (CGR). *Introduction to Reliable and Secure Distributed Programming*,
2ª ed., Springer, 2011.
