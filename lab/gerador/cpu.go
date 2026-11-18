package main

// Monitor de CPU do gerador (amostra a cada 1 s):
//  - genCores: CPU consumida pelo processo gerador (em nucleos, via getrusage)
//  - genCPUsPct: utilizacao media das CPUs reservadas ao conteiner do gerador
//    (cpuset), lida de /proc/stat. Se chegar perto de 100%, o gerador saturou.

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

type cpuSample struct {
	t          time.Time
	genCores   float64
	genCPUsPct float64
}

type cpuMon struct {
	stopc chan struct{}
	done  chan []cpuSample
}

func allowedCPUs() map[int]bool {
	var set unix.CPUSet
	m := map[int]bool{}
	if unix.SchedGetaffinity(0, &set) != nil {
		return m
	}
	for i := 0; i < 1024; i++ {
		if set.IsSet(i) {
			m[i] = true
		}
	}
	return m
}

func numAllowedCPUs() int { return len(allowedCPUs()) }

// soma (ocupado, total) em jiffies das CPUs permitidas
func readProcStat(cpus map[int]bool) (busy, total uint64) {
	f, err := os.Open("/proc/stat")
	if err != nil {
		return
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		fl := strings.Fields(sc.Text())
		if len(fl) < 8 || !strings.HasPrefix(fl[0], "cpu") || fl[0] == "cpu" {
			continue
		}
		id, err := strconv.Atoi(fl[0][3:])
		if err != nil || !cpus[id] {
			continue
		}
		var sum, idle uint64
		for i, x := range fl[1:] {
			v, _ := strconv.ParseUint(x, 10, 64)
			sum += v
			if i == 3 || i == 4 { // idle, iowait
				idle += v
			}
		}
		busy += sum - idle
		total += sum
	}
	return
}

func procCPU() float64 {
	var ru syscall.Rusage
	syscall.Getrusage(syscall.RUSAGE_SELF, &ru)
	return float64(ru.Utime.Nano()+ru.Stime.Nano()) / 1e9
}

func startCPUMonitor() *cpuMon {
	m := &cpuMon{stopc: make(chan struct{}), done: make(chan []cpuSample, 1)}
	cpus := allowedCPUs()
	go func() {
		var out []cpuSample
		lastT, lastP := time.Now(), procCPU()
		lastB, lastTot := readProcStat(cpus)
		tk := time.NewTicker(time.Second)
		defer tk.Stop()
		for {
			select {
			case <-m.stopc:
				m.done <- out
				return
			case now := <-tk.C:
				p := procCPU()
				b, tot := readProcStat(cpus)
				s := cpuSample{t: now, genCores: (p - lastP) / now.Sub(lastT).Seconds()}
				if tot > lastTot {
					s.genCPUsPct = 100 * float64(b-lastB) / float64(tot-lastTot)
				}
				out = append(out, s)
				lastT, lastP, lastB, lastTot = now, p, b, tot
			}
		}
	}()
	return m
}

func (m *cpuMon) stop() []cpuSample {
	close(m.stopc)
	return <-m.done
}

func writeCPU(path string, cs []cpuSample) {
	f, err := os.Create(path)
	if err != nil {
		return
	}
	defer f.Close()
	fmt.Fprintln(f, "t_ns,gen_cores,gen_cpus_util_pct")
	for _, c := range cs {
		fmt.Fprintf(f, "%d,%.3f,%.2f\n", c.t.UnixNano(), c.genCores, c.genCPUsPct)
	}
}
