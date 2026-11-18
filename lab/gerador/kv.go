package main

// Adaptadores finos: a mesma interface KV para etcd e Cassandra.
// Toda a logica de carga (operacoes, chaves, valores, tempos) fica em main.go
// e e identica para os dois sistemas.

import (
	"context"
	"errors"
	"time"

	"github.com/gocql/gocql"
	clientv3 "go.etcd.io/etcd/client/v3"
	"go.uber.org/zap"
)

type KV interface {
	Get(ctx context.Context, key string) ([]byte, error) // nil, nil se a chave nao existe
	Put(ctx context.Context, key string, val []byte) error
	Close()
}

// Enderecos dos nos na rede de clientes (lab_clientes).
var nodeIPs = []string{"172.31.0.11", "172.31.0.12", "172.31.0.13"}

// newKV cria um cliente. Com pinned >= 0 o cliente fala apenas com o no indicado
// (coordenador fixo); com pinned < 0 o cliente conhece os tres nos e o proprio
// driver balanceia e redireciona em caso de falha.
func newKV(sys string, pinned int, serializable bool, conns int) (KV, error) {
	ips := nodeIPs
	if pinned >= 0 {
		ips = []string{nodeIPs[pinned]}
	}
	switch sys {
	case "etcd":
		eps := make([]string, len(ips))
		for i, ip := range ips {
			eps[i] = "http://" + ip + ":2379"
		}
		c, err := clientv3.New(clientv3.Config{
			Endpoints:            eps,
			DialTimeout:          2 * time.Second,
			DialKeepAliveTime:    time.Second,
			DialKeepAliveTimeout: time.Second,
			Logger:               zap.NewNop(),
		})
		if err != nil {
			return nil, err
		}
		return &etcdKV{c: c, ser: serializable}, nil
	case "cassandra":
		cl := gocql.NewCluster(ips...)
		cl.Keyspace = "lab"
		cl.Consistency = gocql.One // CL=ONE para leitura e escrita
		cl.Timeout = 2 * time.Second
		cl.ConnectTimeout = 2 * time.Second
		cl.NumConns = conns
		cl.RetryPolicy = nil // sem retentativas: o gerador observa o erro
		cl.PoolConfig.HostSelectionPolicy = gocql.RoundRobinHostPolicy()
		cl.ReconnectInterval = time.Second
		if pinned >= 0 {
			cl.HostFilter = gocql.WhiteListHostFilter(ips[0])
		}
		s, err := cl.CreateSession()
		if err != nil {
			return nil, err
		}
		return &cassKV{s: s}, nil
	}
	return nil, errors.New("sistema desconhecido: " + sys)
}

// ---------- etcd ----------
type etcdKV struct {
	c   *clientv3.Client
	ser bool
}

func (e *etcdKV) Get(ctx context.Context, key string) ([]byte, error) {
	var opts []clientv3.OpOption
	if e.ser {
		opts = append(opts, clientv3.WithSerializable()) // variante: leitura local
	}
	r, err := e.c.Get(ctx, key, opts...) // padrao: linearizavel (ReadIndex)
	if err != nil {
		return nil, err
	}
	if len(r.Kvs) == 0 {
		return nil, nil
	}
	return r.Kvs[0].Value, nil
}

func (e *etcdKV) Put(ctx context.Context, key string, val []byte) error {
	_, err := e.c.Put(ctx, key, string(val))
	return err
}

func (e *etcdKV) Close() { e.c.Close() }

// ---------- Cassandra ----------
type cassKV struct{ s *gocql.Session }

func (c *cassKV) Get(ctx context.Context, key string) ([]byte, error) {
	var v []byte
	err := c.s.Query("SELECT v FROM kv WHERE k = ?", key).WithContext(ctx).Scan(&v)
	if errors.Is(err, gocql.ErrNotFound) {
		return nil, nil
	}
	return v, err
}

func (c *cassKV) Put(ctx context.Context, key string, val []byte) error {
	return c.s.Query("INSERT INTO kv (k, v) VALUES (?, ?)", key, val).WithContext(ctx).Exec()
}

func (c *cassKV) Close() { c.s.Close() }
