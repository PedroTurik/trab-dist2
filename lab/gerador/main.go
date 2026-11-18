// Gerador de carga em loop fechado + coordenador de stale reads (E3) + dump (E5).
//
// Modos:
//   gerador load  -sys etcd|cassandra                 pre-carrega as 10.000 chaves
//   gerador run   -sys ... -threads N -dur 120s ...   carga em loop fechado (E1, E2, E4, E5, E6)
//   gerador stale -sys ... -pairs 10000 ...           coordenador PUT(n1) -> GET(n3) (E3)
//   gerador dump  -sys ... -node 0 ...                le todas as chaves de um no (convergencia, E5)
package main

import (
	"bufio"
	"compress/gzip"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"math/rand/v2"
	"os"
	"slices"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

const valSize = 256

func keyName(i int) string { return fmt.Sprintf("k%05d", i) }

// valor de 256 B: 16 digitos hex com o id da escrita + preenchimento
func makeVal(buf []byte, vid uint64) []byte {
	copy(buf, fmt.Sprintf("%016x", vid))
	return buf
}

func parseVid(v []byte) uint64 {
	if len(v) < 16 {
		return 0
	}
	x, _ := strconv.ParseUint(string(v[:16]), 16, 64)
	return x
}

func die(err error) {
	fmt.Fprintln(os.Stderr, "erro:", err)
	os.Exit(1)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("uso: gerador load|run|stale|dump [flags]")
		os.Exit(2)
	}
	mode := os.Args[1]
	fs := flag.NewFlagSet(mode, flag.ExitOnError)
	sys := fs.String("sys", "etcd", "etcd | cassandra")
	threads := fs.Int("threads", 1, "N threads cliente (loop fechado)")
	dur := fs.Duration("dur", 120*time.Second, "duracao total")
	warm := fs.Duration("warmup", 30*time.Second, "aquecimento descartado no resumo")
	seed := fs.Uint64("seed", 42, "semente fixa")
	timeout := fs.Duration("timeout", 2*time.Second, "timeout por requisicao")
	keys := fs.Int("keys", 10000, "numero de chaves")
	getPct := fs.Int("getpct", 80, "% de GET")
	pin := fs.Bool("pin", true, "thread i fixa no no i%3 (senao o cliente conhece os 3 nos)")
	conns := fs.Int("conns", 4, "clientes (conexoes) por no")
	ser := fs.Bool("serializable", false, "etcd: leitura serializable (variante)")
	out := fs.String("out", "/resultados/teste", "prefixo dos arquivos de saida")
	pairs := fs.Int("pairs", 10000, "stale: pares PUT/GET confirmados")
	workers := fs.Int("workers", 4, "stale: coordenadores independentes (uma chave cada)")
	putNode := fs.Int("putnode", 0, "stale: no do PUT (0 = n1)")
	getNode := fs.Int("getnode", 2, "stale: no do GET (2 = n3)")
	node := fs.Int("node", 0, "dump: no a ler")
	fs.Parse(os.Args[2:])

	switch mode {
	case "load":
		doLoad(*sys, *keys)
	case "run":
		doRun(*sys, *threads, *dur, *warm, *seed, *timeout, *keys, *getPct, *pin, *conns, *ser, *out)
	case "stale":
		doStale(*sys, *pairs, *workers, *putNode, *getNode, *timeout, *ser, *out)
	case "dump":
		doDump(*sys, *node, *keys, *timeout, *out)
	default:
		die(fmt.Errorf("modo desconhecido %q", mode))
	}
}

// ---------------------------------------------------------------- load
func doLoad(sys string, keys int) {
	kv, err := newKV(sys, -1, false, 4)
	if err != nil {
		die(err)
	}
	defer kv.Close()
	var wg sync.WaitGroup
	var fail atomic.Int64
	ch := make(chan int)
	for w := 0; w < 64; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			buf := make([]byte, valSize)
			for i := range buf {
				buf[i] = 'x'
			}
			for k := range ch {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				if kv.Put(ctx, keyName(k), makeVal(buf, 0)) != nil {
					fail.Add(1)
				}
				cancel()
			}
		}()
	}
	for k := 0; k < keys; k++ {
		ch <- k
	}
	close(ch)
	wg.Wait()
	fmt.Printf("load: %d chaves, %d falhas\n", keys, fail.Load())
	if fail.Load() > 0 {
		os.Exit(1)
	}
}

// ---------------------------------------------------------------- run
type sample struct {
	start int64 // unix ns (absoluto)
	lat   int64 // us; -1 em timeout (nao se registra latencia)
	key   int32
	vid   uint64 // PUT: id escrito; GET: id lido
	op    byte   // 'G' | 'P'
	ok    bool
	node  int8 // -1 se o cliente nao e fixo
	errc  byte // 0 ok, 'T' timeout, 'E' outro erro
}

func doRun(sys string, threads int, dur, warm time.Duration, seed uint64, timeout time.Duration,
	keys, getPct int, pin bool, conns int, ser bool, out string) {

	// clientes: [no][conn]
	var pool [][]KV
	if pin {
		for n := 0; n < 3; n++ {
			var row []KV
			for c := 0; c < conns; c++ {
				kv, err := newKV(sys, n, ser, 2)
				if err != nil {
					die(err)
				}
				row = append(row, kv)
			}
			pool = append(pool, row)
		}
	} else {
		var row []KV
		for c := 0; c < conns; c++ {
			kv, err := newKV(sys, -1, ser, 2)
			if err != nil {
				die(err)
			}
			row = append(row, kv)
		}
		pool = append(pool, row)
	}
	keyNames := make([]string, keys)
	for i := range keyNames {
		keyNames[i] = keyName(i)
	}

	mon := startCPUMonitor()
	t0run := time.Now()
	deadline := t0run.Add(dur)
	res := make([][]sample, threads)
	var wg sync.WaitGroup
	for t := 0; t < threads; t++ {
		wg.Add(1)
		go func(t int) {
			defer wg.Done()
			rng := rand.New(rand.NewPCG(seed, uint64(t))) // semente fixa por thread
			nodeIdx, kv := int8(-1), pool[0][t%conns]
			if pin {
				nodeIdx = int8(t % 3)
				kv = pool[t%3][(t/3)%conns]
			}
			buf := make([]byte, valSize)
			for i := range buf {
				buf[i] = byte('a' + rng.IntN(26))
			}
			var seq uint64
			local := make([]sample, 0, 1024)
			for time.Now().Before(deadline) {
				// 1. sorteia operacao e chave
				isGet := rng.IntN(100) < getPct
				k := rng.IntN(keys)
				s := sample{key: int32(k), node: nodeIdx}
				ctx, cancel := context.WithTimeout(context.Background(), timeout)
				// 2. t0, envia e aguarda resposta ou timeout
				t0 := time.Now()
				var err error
				if isGet {
					s.op = 'G'
					var v []byte
					v, err = kv.Get(ctx, keyNames[k])
					s.vid = parseVid(v)
				} else {
					s.op = 'P'
					seq++
					s.vid = uint64(t+1)<<32 | seq
					err = kv.Put(ctx, keyNames[k], makeVal(buf, s.vid))
				}
				// 3. t1, registra amostra
				t1 := time.Now()
				timedOut := ctx.Err() != nil
				cancel()
				s.start = t0.UnixNano()
				s.lat = t1.Sub(t0).Microseconds()
				switch {
				case err == nil:
					s.ok = true
				case timedOut:
					s.errc, s.lat = 'T', -1
				default:
					s.errc = 'E'
				}
				local = append(local, s)
				// 4. reinicia imediatamente
			}
			res[t] = local
		}(t)
	}
	wg.Wait()
	cpu := mon.stop()
	end := time.Now()

	writeSamples(out+".csv.gz", res)
	writeCPU(out+".cpu.csv", cpu)
	sum := summarize(res, t0run.Add(warm), deadline, cpu)
	sum["sys"], sum["threads"], sum["dur_s"], sum["warmup_s"] = sys, threads, dur.Seconds(), warm.Seconds()
	sum["pin"], sum["serializable"], sum["seed"] = pin, ser, seed
	sum["t_start_ns"], sum["t_end_ns"] = t0run.UnixNano(), end.UnixNano()
	b, _ := json.MarshalIndent(sum, "", "  ")
	os.WriteFile(out+".json", b, 0o644)
	fmt.Println(string(b))
	for _, row := range pool {
		for _, kv := range row {
			kv.Close()
		}
	}
}

func writeSamples(path string, res [][]sample) {
	f, err := os.Create(path)
	if err != nil {
		die(err)
	}
	defer f.Close()
	gz, _ := gzip.NewWriterLevel(f, gzip.BestSpeed)
	defer gz.Close()
	w := bufio.NewWriterSize(gz, 1<<20)
	defer w.Flush()
	w.WriteString("start_ns,lat_us,op,ok,node,key,vid,err\n")
	var b []byte
	for _, r := range res {
		for _, s := range r {
			b = b[:0]
			b = strconv.AppendInt(b, s.start, 10)
			b = append(b, ',')
			if s.lat >= 0 {
				b = strconv.AppendInt(b, s.lat, 10)
			}
			b = append(b, ',', s.op, ',')
			if s.ok {
				b = append(b, '1')
			} else {
				b = append(b, '0')
			}
			b = append(b, ',')
			b = strconv.AppendInt(b, int64(s.node), 10)
			b = append(b, ',')
			b = strconv.AppendInt(b, int64(s.key), 10)
			b = append(b, ',')
			b = strconv.AppendUint(b, s.vid, 10)
			b = append(b, ',')
			if s.errc != 0 {
				b = append(b, s.errc)
			}
			b = append(b, '\n')
			w.Write(b)
		}
	}
}

func pct(xs []int64, p float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	i := int(p*float64(len(xs))+0.5) - 1
	i = max(0, min(i, len(xs)-1))
	return float64(xs[i]) / 1000.0 // ms
}

// Resumo da janela [from, to): vazao efetiva = completacoes com sucesso / s;
// percentis apenas de requisicoes com sucesso.
func summarize(res [][]sample, from, to time.Time, cpu []cpuSample) map[string]any {
	f, t := from.UnixNano(), to.UnixNano()
	lat := map[byte][]int64{}
	var okN, errN, toN int64
	for _, r := range res {
		for _, s := range r {
			if s.start < f || s.start >= t {
				continue
			}
			if s.ok {
				okN++
				lat[s.op] = append(lat[s.op], s.lat)
			} else if s.errc == 'T' {
				toN++
			} else {
				errN++
			}
		}
	}
	win := to.Sub(from).Seconds()
	m := map[string]any{
		"window_s":       win,
		"ok":             okN,
		"errors":         errN,
		"timeouts":       toN,
		"throughput_ok":  float64(okN) / win,
		"error_rate":     float64(errN+toN) / float64(max(1, okN+errN+toN)),
	}
	all := append(append([]int64{}, lat['G']...), lat['P']...)
	for name, xs := range map[string][]int64{"get": lat['G'], "put": lat['P'], "all": all} {
		slices.Sort(xs)
		m[name] = map[string]any{"n": len(xs), "p50_ms": pct(xs, .50), "p95_ms": pct(xs, .95), "p99_ms": pct(xs, .99)}
	}
	var gAvg, gMax, cAvg, cMax float64
	var n float64
	for _, c := range cpu {
		if c.t.Before(from) {
			continue
		}
		n++
		gAvg += c.genCores
		cAvg += c.genCPUsPct
		gMax = max(gMax, c.genCores)
		cMax = max(cMax, c.genCPUsPct)
	}
	if n > 0 {
		gAvg /= n
		cAvg /= n
	}
	m["gen_cpu_cores_avg"], m["gen_cpu_cores_max"] = gAvg, gMax
	m["gen_cpus_util_pct_avg"], m["gen_cpus_util_pct_max"] = cAvg, cMax
	m["gen_cpus"] = numAllowedCPUs()
	return m
}

// ---------------------------------------------------------------- stale (E3)
func doStale(sys string, pairs, workers, putNode, getNode int, timeout time.Duration, ser bool, out string) {
	putKV, err := newKV(sys, putNode, ser, 2)
	if err != nil {
		die(err)
	}
	getKV, err := newKV(sys, getNode, ser, 2)
	if err != nil {
		die(err)
	}
	f, err := os.Create(out + ".csv")
	if err != nil {
		die(err)
	}
	w := bufio.NewWriter(f)
	w.WriteString("worker,v,put_start_ns,put_lat_us,put_ok,get_start_ns,get_lat_us,get_ok,v_read,stale\n")
	var mu sync.Mutex
	var done, stale, putErr, getErr atomic.Int64
	var maxLag atomic.Int64
	var wg sync.WaitGroup
	t0all := time.Now()
	for wk := 0; wk < workers; wk++ {
		wg.Add(1)
		go func(wk int) {
			defer wg.Done()
			key := fmt.Sprintf("stale-%d", wk)
			buf := make([]byte, valSize)
			for i := range buf {
				buf[i] = 'x'
			}
			var v uint64
			for done.Load() < int64(pairs) {
				v++ // valor unico e crescente
				ctx, cancel := context.WithTimeout(context.Background(), timeout)
				tp := time.Now()
				perr := putKV.Put(ctx, key, makeVal(buf, v))
				putLat := time.Since(tp)
				cancel()
				line := fmt.Sprintf("%d,%d,%d,%d,", wk, v, tp.UnixNano(), putLat.Microseconds())
				if perr != nil { // nao confirmado: nao conta
					putErr.Add(1)
					mu.Lock()
					w.WriteString(line + "0,,,,,\n")
					mu.Unlock()
					continue
				}
				// GET so comeca depois do retorno do PUT
				ctx, cancel = context.WithTimeout(context.Background(), timeout)
				tg := time.Now()
				val, gerr := getKV.Get(ctx, key)
				getLat := time.Since(tg)
				cancel()
				if gerr != nil {
					getErr.Add(1)
					mu.Lock()
					w.WriteString(line + fmt.Sprintf("1,%d,%d,0,,\n", tg.UnixNano(), getLat.Microseconds()))
					mu.Unlock()
					continue
				}
				vr := parseVid(val)
				isStale := vr < v
				if isStale {
					stale.Add(1)
					lag := int64(v - vr)
					for {
						cur := maxLag.Load()
						if lag <= cur || maxLag.CompareAndSwap(cur, lag) {
							break
						}
					}
				}
				done.Add(1)
				mu.Lock()
				w.WriteString(line + fmt.Sprintf("1,%d,%d,1,%d,%t\n", tg.UnixNano(), getLat.Microseconds(), vr, isStale))
				mu.Unlock()
			}
		}(wk)
	}
	wg.Wait()
	w.Flush()
	f.Close()
	sum := map[string]any{
		"sys": sys, "serializable": ser, "pairs": done.Load(), "stale": stale.Load(),
		"stale_rate": float64(stale.Load()) / float64(max(1, done.Load())),
		"max_lag_versions": maxLag.Load(), "put_errors": putErr.Load(), "get_errors": getErr.Load(),
		"workers": workers, "put_node": putNode + 1, "get_node": getNode + 1,
		"elapsed_s": time.Since(t0all).Seconds(),
	}
	b, _ := json.MarshalIndent(sum, "", "  ")
	os.WriteFile(out+".json", b, 0o644)
	fmt.Println(string(b))
}

// ---------------------------------------------------------------- dump (E5)
// Le todas as chaves no no indicado. No etcd usa leitura serializable (estado local do no);
// no Cassandra usa CL=ONE com o no como coordenador.
func doDump(sys string, node, keys int, timeout time.Duration, out string) {
	kv, err := newKV(sys, node, true, 2)
	if err != nil {
		die(err)
	}
	defer kv.Close()
	vids := make([]int64, keys)
	var wg sync.WaitGroup
	ch := make(chan int)
	for w := 0; w < 32; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for k := range ch {
				ctx, cancel := context.WithTimeout(context.Background(), timeout)
				v, err := kv.Get(ctx, keyName(k))
				cancel()
				if err != nil {
					vids[k] = -1
				} else {
					vids[k] = int64(parseVid(v))
				}
			}
		}()
	}
	for k := 0; k < keys; k++ {
		ch <- k
	}
	close(ch)
	wg.Wait()
	f, err := os.Create(out)
	if err != nil {
		die(err)
	}
	w := bufio.NewWriter(f)
	w.WriteString("key,vid\n")
	for k, v := range vids {
		fmt.Fprintf(w, "%d,%d\n", k, v)
	}
	w.Flush()
	f.Close()
}
