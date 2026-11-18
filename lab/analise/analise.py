#!/usr/bin/env python3
"""Gera figuras e tabelas do relatorio a partir de lab/resultados.

Uso: .venv/bin/python analise/analise.py [e1|e2|e3|e4|e5|e6 ...]   (sem argumentos: tudo)
Saida: relatorio/figs/*.png e relatorio/tabelas/*.md
Convencao: em todas as tabelas, "mediana [min-max]" das 3 repeticoes.
"""
import glob, json, os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(LAB, "resultados")
OUT = os.path.join(os.path.dirname(LAB), "relatorio")
FIG, TAB = os.path.join(OUT, "figs"), os.path.join(OUT, "tabelas")
os.makedirs(FIG, exist_ok=True); os.makedirs(TAB, exist_ok=True)
SYSS = ["etcd", "cassandra"]
LABEL = {"etcd": "etcd (forte, Raft)", "cassandra": "Cassandra (eventual, CL=ONE)"}
COLOR = {"etcd": "#1b7a5a", "cassandra": "#5b4bc4"}
TIMEOUT_NS = 2_000_000_000


def mmm(xs, fmt="{:.1f}"):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and np.isnan(x))]
    if not xs:
        return "-"
    return f"{fmt.format(np.median(xs))} [{fmt.format(min(xs))}-{fmt.format(max(xs))}]"


def jload(p):
    with open(p) as f:
        return json.load(f)


def br(t):
    """formato brasileiro: milhar com ponto, decimal com virgula"""
    return t.replace(",", "#").replace(".", ",").replace("#", ".")


def write(name, text):
    lines = text.rstrip().split("\n")
    text = "\n".join(lines[:2] + [br(x) for x in lines[2:]])
    with open(os.path.join(TAB, name), "w") as f:
        f.write(text.rstrip() + "\n")
    print(text)


def load_samples(path):
    df = pd.read_csv(path, dtype={"op": "category", "err": "category"})
    # instante de conclusao: inicio + latencia; timeout conclui em inicio + 2 s
    lat_ns = df["lat_us"].fillna(TIMEOUT_NS / 1000) * 1000
    df["end_ns"] = df["start_ns"] + lat_ns.astype("int64")
    df["ok"] = df["ok"].astype(bool)
    return df


def events(path):
    ev = pd.read_csv(path)
    return dict(zip(ev["event"], ev["t_ns"]))


# ------------------------------------------------------------------ E1
def e1():
    rows, curves = [], {}
    for s in SYSS:
        recs = []
        for p in glob.glob(f"{RES}/{s}/e1/rep*/n*.json"):
            j = jload(p)
            recs.append(dict(rep=p.split("/")[-2], N=j["threads"], thr=j["throughput_ok"],
                             p99=j["all"]["p99_ms"], err=j["error_rate"],
                             cpu=j["gen_cpus_util_pct_max"]))
        if not recs:
            continue
        d = pd.DataFrame(recs)
        g = d.groupby("N").agg(thr=("thr", "median"), thr_min=("thr", "min"), thr_max=("thr", "max"),
                               p99=("p99", "median"), p99_min=("p99", "min"), p99_max=("p99", "max"),
                               err=("err", "max"), cpu=("cpu", "max"), reps=("rep", "count")).reset_index()
        g["ratio"] = g["thr"] / g["p99"]
        curves[s] = (d, g)
    if not curves:
        return
    summary = []
    for s, (d, g) in curves.items():
        iopt = g["ratio"].idxmax()
        tmax = g["thr"].max()
        plateau = g[g["thr"] >= 0.95 * tmax]
        summary.append(dict(sys=s, nopt=int(g.loc[iopt, "N"]), ratio=g.loc[iopt, "ratio"],
                            thr_opt=g.loc[iopt, "thr"], p99_opt=g.loc[iopt, "p99"], tmax=tmax,
                            n_tmax=int(g.loc[g["thr"].idxmax(), "N"]),
                            plateau_ns="; ".join(str(int(n)) for n in plateau["N"]),
                            cpu=g["cpu"].max(), iopt=iopt, plateau=plateau))
        rows.append(g.assign(sys=s))
    # painel (a): eixo x linear, como na Figura 1 do enunciado; painel (b): eixo x log (regiao de N pequeno)
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    for k, ax in enumerate(axes):
        for (s, (d, g)), sm in zip(curves.items(), summary):
            iopt, plateau = sm["iopt"], sm["plateau"]
            ax.errorbar(g["thr"], g["p99"], xerr=[g["thr"] - g["thr_min"], g["thr_max"] - g["thr"]],
                        yerr=[g["p99"] - g["p99_min"], g["p99_max"] - g["p99"]],
                        fmt="-o" if s == "etcd" else "-s", ms=4, color=COLOR[s], label=LABEL[s], capsize=2, lw=1.4)
            for _, r in g.iterrows():
                if k == 1 or r["N"] in (1, g.loc[iopt, "N"]) or r["N"] == g["N"].max():
                    ax.annotate(f"{int(r['N'])}" if k == 1 else f"N={int(r['N'])}", (r["thr"], r["p99"]),
                                textcoords="offset points", xytext=(5, 6 if s == "etcd" else -12),
                                fontsize=7, color=COLOR[s])
            ax.scatter([g.loc[iopt, "thr"]], [g.loc[iopt, "p99"]], s=160, facecolors="none",
                       edgecolors=COLOR[s], lw=2, zorder=5)
            if k == 0:
                ax.annotate("ótimo (joelho)", (g.loc[iopt, "thr"], g.loc[iopt, "p99"]), textcoords="offset points",
                            xytext=(-80, 12), fontsize=8, color=COLOR[s])
                ax.annotate("platô (saturação)", (plateau["thr"].median(), plateau["p99"].max()),
                            textcoords="offset points", xytext=(-40, 8), fontsize=8, color=COLOR[s])
            ax.axvspan(plateau["thr"].min(), plateau["thr"].max(), color=COLOR[s], alpha=0.08)
        ax.set_yscale("log")
        if k == 1:
            ax.set_xscale("log")
        ax.set_xlabel("Vazão efetiva (req/s com sucesso)" + (", escala log" if k else ""))
        ax.set_ylabel("Latência p99 (ms, escala log)")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(loc="upper left", fontsize=8)
        ax.set_title("(a) eixo x linear" if k == 0 else "(b) eixo x log; rótulos = N")
    fig.suptitle("E1 - Latência p99 × vazão efetiva, um ponto por N (mediana de 3 repetições; barras = mín-máx; "
                 "círculo = N ótimo; faixa = platô)", fontsize=10)
    fig.tight_layout(); fig.savefig(f"{FIG}/e1_p99_vazao.png", dpi=150); plt.close(fig)

    t = "| N | " + " | ".join(f"{s}: vazão (req/s) | {s}: p99 (ms) | {s}: vazão/p99" for s in curves) + " |\n"
    t += "|---|" + "---|---|---|" * len(curves) + "\n"
    Ns = sorted(set().union(*[set(g["N"]) for _, g in curves.values()]))
    for n in Ns:
        cells = []
        for s, (d, g) in curves.items():
            r = g[g["N"] == n]
            if r.empty:
                cells += ["-", "-", "-"]; continue
            r = r.iloc[0]
            cells += [f"{r['thr']:,.0f} [{r['thr_min']:,.0f}-{r['thr_max']:,.0f}]",
                      f"{r['p99']:.2f} [{r['p99_min']:.2f}-{r['p99_max']:.2f}]", f"{r['ratio']:,.0f}"]
        t += f"| {n} | " + " | ".join(cells) + " |\n"
    write("e1_tabela.md", t)
    t = "| Sistema | N ótimo | vazão no ótimo (req/s) | p99 no ótimo (ms) | vazão/p99 (req/s por ms) | vazão máx. sustentada (req/s) | N no platô (≥95% do máx.) | CPU máx. do gerador (%) |\n|---|---|---|---|---|---|---|---|\n"
    for r in summary:
        t += (f"| {r['sys']} | {r['nopt']} | {r['thr_opt']:,.0f} | {r['p99_opt']:.2f} | {r['ratio']:,.0f} | "
              f"{r['tmax']:,.0f} (N={r['n_tmax']}) | {r['plateau_ns']} | {r['cpu']:.0f} |\n")
    write("e1_resumo.md", t)


# ------------------------------------------------------------------ E2 / E4
def e2e4():
    data = {}
    for s in SYSS:
        for e in ("e2", "e4"):
            js = [jload(p) for p in sorted(glob.glob(f"{RES}/{s}/{e}/rep*.json"))]
            if js:
                data[(s, e)] = js
    if not data:
        return
    t = "| Sistema | Métrica | E2 (rede original) | E4 (+50 ms/sentido, RTT 100 ms) |\n|---|---|---|---|\n"
    for s in SYSS:
        if (s, "e2") not in data:
            continue
        n = data[(s, "e2")][0]["threads"]
        t += f"| **{s}** (N={n}) | vazão efetiva (req/s) | " + " | ".join(
            mmm([j["throughput_ok"] for j in data.get((s, e), [])], "{:,.0f}") for e in ("e2", "e4")) + " |\n"
        for op in ("get", "put"):
            for p in ("p50", "p95", "p99"):
                t += f"| | {op.upper()} {p} (ms) | " + " | ".join(
                    mmm([j[op][p + "_ms"] for j in data.get((s, e), [])], "{:.2f}") for e in ("e2", "e4")) + " |\n"
        t += f"| | erros+timeouts (%) | " + " | ".join(
            mmm([100 * j["error_rate"] for j in data.get((s, e), [])], "{:.3f}") for e in ("e2", "e4")) + " |\n"
        t += f"| | CPU máx. gerador (%) | " + " | ".join(
            mmm([j["gen_cpus_util_pct_max"] for j in data.get((s, e), [])], "{:.0f}") for e in ("e2", "e4")) + " |\n"
    write("e2_e4.md", t)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for ax, s in zip(axes, SYSS):
        labels, x = [], np.arange(6)
        for k, (e, c) in enumerate((("e2", "#8fbcd4"), ("e4", "#d4785f"))):
            js = data.get((s, e), [])
            if not js:
                continue
            vals = [np.median([j[op][p + "_ms"] for j in js]) for op in ("get", "put") for p in ("p50", "p95", "p99")]
            ax.bar(x + (k - 0.5) * 0.38, vals, 0.38, color=c,
                   label=f"{e.upper()} ({np.median([j['throughput_ok'] for j in js]):,.0f} req/s)".replace(",", "."))
        ax.set_xticks(x, [f"{op} {p}" for op in ("GET", "PUT") for p in ("p50", "p95", "p99")])
        ax.set_yscale("log"); ax.set_ylabel("latência (ms, log)"); ax.grid(True, axis="y", which="both", alpha=0.3)
        ax.set_title(LABEL[s]); ax.legend(fontsize=8)
    fig.suptitle("E2 × E4 - latência por percentil (mesmo N)")
    fig.tight_layout(); fig.savefig(f"{FIG}/e2_e4_percentis.png", dpi=150); plt.close(fig)


# ------------------------------------------------------------------ E3
def e3():
    t = ("| Sistema | Leitura | Condição | pares | stale reads (total) | taxa de stale (%) | atraso máx. (versões) | "
         "PUT p50 (ms) | GET p50 (ms) | PUTs com erro |\n|---|---|---|---|---|---|---|---|---|---|\n")
    found = False
    for s in SYSS:
        for var in sorted(os.listdir(f"{RES}/{s}/e3")) if os.path.isdir(f"{RES}/{s}/e3") else []:
            for cond in ("normal", "atraso"):
                js = [jload(p) for p in sorted(glob.glob(f"{RES}/{s}/e3/{var}/{cond}/rep*.json"))]
                if not js:
                    continue
                found = True
                lat = [pd.read_csv(p) for p in sorted(glob.glob(f"{RES}/{s}/e3/{var}/{cond}/rep*.csv"))]
                pput = [np.median(d.loc[d.put_ok == 1, "put_lat_us"]) / 1000 for d in lat]
                pget = [np.median(d.loc[d.get_ok == 1, "get_lat_us"].dropna()) / 1000 for d in lat]
                cname = "original" if cond == "normal" else "+50 ms/sentido"
                t += (f"| {s} | {var} | {cname} | {mmm([j['pairs'] for j in js], '{:.0f}')} | "
                      f"{sum(j['stale'] for j in js)} / {sum(j['pairs'] for j in js)} | "
                      f"{mmm([100 * j['stale_rate'] for j in js], '{:.3f}')} | {max(j['max_lag_versions'] for j in js)} | "
                      f"{mmm(pput, '{:.2f}')} | {mmm(pget, '{:.2f}')} | {sum(j['put_errors'] for j in js)} |\n")
    if found:
        write("e3.md", t)


# ------------------------------------------------------------------ E5 / E6 helpers
def timeline(ax, df, t0, groups, bin_s=1.0):
    """completacoes (sucesso) e erros por segundo, por grupo."""
    for name, mask, color in groups:
        sub = df[mask]
        tt = (sub["end_ns"] - t0) / 1e9
        bins = np.arange(0, np.ceil(tt.max()) + bin_s, bin_s) if len(tt) else np.arange(2)
        okc, _ = np.histogram(tt[sub["ok"]], bins)
        erc, _ = np.histogram(tt[~sub["ok"]], bins)
        ax.plot(bins[:-1], okc / bin_s, color=color, lw=1.2, label=f"{name} - sucesso/s")
        if erc.sum():
            ax.plot(bins[:-1], erc / bin_s, color=color, lw=1.2, ls="--", label=f"{name} - erros/s")


def stale_reads(df):
    """leituras que devolveram uma versao estritamente anterior a ultima escrita confirmada
    (concluida antes do inicio da leitura). Retorna mascara booleana alinhada a df[GET ok]."""
    puts = df[df.op == "P"].copy()
    wend = dict(zip(puts["vid"], np.where(puts["ok"], puts["end_ns"], np.iinfo(np.int64).max)))
    conf = puts[puts["ok"]].sort_values("end_ns")[["key", "end_ns", "start_ns"]].rename(
        columns={"end_ns": "t", "start_ns": "last_conf_start"})
    gets = df[(df.op == "G") & df["ok"]].copy()
    gets["t"] = gets["start_ns"]
    gets = gets.sort_values("t")
    m = pd.merge_asof(gets, conf, on="t", by="key", direction="backward", allow_exact_matches=False)
    read_end = m["vid"].map(wend).fillna(-1)  # vid 0 = valor inicial (pre-carga)
    m["stale"] = m["last_conf_start"].notna() & (read_end < m["last_conf_start"])
    return m


def lost_writes(df, final):
    """escritas confirmadas perdidas: W confirmada tal que o valor final da chave veio de uma
    escrita que terminou antes de W comecar (ou do valor inicial)."""
    puts = df[df.op == "P"]
    wend = dict(zip(puts["vid"], np.where(puts["ok"], puts["end_ns"], np.iinfo(np.int64).max)))
    fin = final.set_index("key")["vid"]
    conf = puts[puts["ok"]]
    fend = conf["key"].map(fin).map(lambda v: wend.get(v, -1))
    return int((conf["start_ns"] > fend).sum()), len(conf)


# ------------------------------------------------------------------ E5
def e5():
    t = ("| Sistema | rep | lado | GET ok | GET erro | PUT ok | PUT erro | stale reads | escritas confirmadas perdidas | stale reads cessam após a cura (s) | réplicas idênticas após a carga |\n"
         "|---|---|---|---|---|---|---|---|---|---|---|\n")
    found = False
    for s in SYSS:
        reps = sorted(glob.glob(f"{RES}/{s}/e5/rep*"))
        for i, rd in enumerate(reps):
            if not os.path.exists(f"{rd}/load.csv.gz"):
                continue
            found = True
            ev = events(f"{rd}/events.csv")
            df = load_samples(f"{rd}/load.csv.gz")
            t0 = df["start_ns"].min()
            tp, th = ev["partition"], ev["heal"]
            st = stale_reads(df)
            conv = pd.read_csv(f"{rd}/convergencia.csv")
            conv_t = conv["t_s"].iloc[-1] if (conv.iloc[-1, 1:] == 0).all() else None
            final = pd.read_csv(f"{rd}/dump_n2.csv")
            lost, nconf = lost_writes(df, final)
            # instante (apos a cura) a partir do qual os stale reads/s voltam ao nivel anterior a particao
            sec = ((st["start_ns"] - t0) // 1e9).astype(int)
            per = st["stale"].groupby(sec).sum()
            base = per[per.index < int((tp - t0) // 1e9)].max() if len(per) else 0
            hs = int((th - t0) // 1e9)
            above = per[(per.index >= hs) & (per > base)]
            stop = (above.index.max() + 1 - (th - t0) / 1e9) if len(above) else 0.0
            part = df[(df["start_ns"] >= tp) & (df["start_ns"] < th)]
            stp = st[(st["start_ns"] >= tp) & (st["start_ns"] < th)]
            for side, nodes in (("n1 (minoria)", [0]), ("n2+n3 (maioria)", [1, 2])):
                p = part[part["node"].isin(nodes)]
                c = lambda op, ok: int(((p.op == op) & (p.ok == ok)).sum())
                nst = int(stp[stp["node"].isin(nodes)]["stale"].sum())
                t += (f"| {s} | {i+1} | {side} | {c('G', True)} | {c('G', False)} | {c('P', True)} | {c('P', False)} | "
                      f"{nst} | {lost if side.startswith('n1') else ''}{' / ' + str(nconf) + ' (total)' if side.startswith('n1') else ''} | "
                      f"{f'{max(0, stop):.1f}' if side.startswith('n1') else ''} | "
                      f"{(f'sim ({conv_t} s)' if conv_t is not None else 'não') if side.startswith('n1') else ''} |\n")
            if i == 0:  # figura da primeira repeticao
                fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
                for ax, op, nm in ((axes[0], "P", "PUT"), (axes[1], "G", "GET")):
                    timeline(ax, df, t0, [("n1 (minoria)", (df.op == op) & (df.node == 0), "#d62728"),
                                          ("n2+n3 (maioria)", (df.op == op) & (df.node > 0), "#1f77b4")])
                    for k, c in (("partition", "k"), ("heal", "g")):
                        ax.axvline((ev[k] - t0) / 1e9, color=c, ls=":", lw=1.5)
                    ax.set_ylabel(f"{nm} por segundo"); ax.grid(alpha=0.3); ax.legend(fontsize=8, loc="center left")
                    ax.set_yscale("symlog", linthresh=10); ax.set_ylim(bottom=0)
                axes[0].set_title(f"E5 - {LABEL[s]}: partição de n1 (linha preta) e cura (linha verde), rep 1")
                axes[1].set_xlabel("tempo desde o início da carga (s)")
                fig.tight_layout(); fig.savefig(f"{FIG}/e5_{s}.png", dpi=150); plt.close(fig)
                # stale reads ao longo do tempo
                fig, ax = plt.subplots(figsize=(11, 3))
                for side, nodes, c in (("n1", [0], "#d62728"), ("n2+n3", [1, 2], "#1f77b4")):
                    x = st[st["node"].isin(nodes)]
                    tt = (x["start_ns"] - t0) / 1e9
                    bins = np.arange(0, np.ceil(tt.max()) + 1)
                    h, _ = np.histogram(tt[x["stale"]], bins)
                    ax.plot(bins[:-1], h, color=c, label=f"stale reads/s em {side}")
                for k, c in (("partition", "k"), ("heal", "g")):
                    ax.axvline((ev[k] - t0) / 1e9, color=c, ls=":", lw=1.5)
                ax.set_xlabel("tempo desde o início da carga (s)"); ax.grid(alpha=0.3); ax.legend(fontsize=8)
                ax.set_title(f"E5 - {LABEL[s]}: leituras desatualizadas por segundo, rep 1")
                fig.tight_layout(); fig.savefig(f"{FIG}/e5_{s}_stale.png", dpi=150); plt.close(fig)
    if found:
        write("e5.md", t)


# ------------------------------------------------------------------ E6
def failover(df, tk, t0):
    """tempo ate as escritas voltarem: primeiro instante t >= kill (passos de 100 ms) a partir do qual
    a vazao de PUT com sucesso na janela [t, t+1 s) volta a >= 50% da media dos 20 s anteriores ao kill.
    E 0 se as escritas nunca cairam abaixo disso."""
    put = df[(df.op == "P") & df["ok"]]
    pre = ((put["end_ns"] >= tk - 20e9) & (put["end_ns"] < tk)).sum() / 20.0
    e = np.sort(put["end_ns"].values)
    rec = 30.0
    for k in range(300):
        t = tk + k * 1e8
        n = np.searchsorted(e, t + 1e9) - np.searchsorted(e, t)
        if n >= 0.5 * pre:
            rec = k * 0.1
            break
    # maior intervalo sem nenhuma escrita com sucesso (em [kill - 1 s, kill + 30 s]) e o instante em que termina
    w = e[(e >= tk - 1e9) & (e <= tk + 30e9)]
    w = np.concatenate([[tk], w]) if len(w) and w[0] > tk else w
    d = np.diff(w)
    i = int(np.argmax(d)) if len(d) else 0
    gap = d[i] / 1e9 if len(d) else 30.0
    resume = max(0.0, (w[i + 1] - tk) / 1e9) if len(d) and gap > 0.2 else 0.0
    return rec, gap, pre, resume


def e6():
    t = ("| Sistema | variante (vítima) | maior intervalo sem PUT ok (s) | kill → escritas voltam (s) | kill → vazão de PUT ≥ 50% (s) | "
         "erros na falha (kill→start) | taxa de erro na falha (%) | erros nos 30 s após o restart | vazão pós-restart / pré-falha |\n"
         "|---|---|---|---|---|---|---|---|---|\n")
    found = False
    for s in SYSS:
        for var in ("lider", "seguidor"):
            recs = []
            for i, rd in enumerate(sorted(glob.glob(f"{RES}/{s}/e6/{var}/rep*"))):
                if not os.path.exists(f"{rd}/load.csv.gz"):
                    continue
                found = True
                ev = events(f"{rd}/events.csv")
                kill = [v for k, v in ev.items() if k.startswith("kill_")][0]
                start = [v for k, v in ev.items() if k.startswith("start_")][0]
                victim = [k for k in ev if k.startswith("kill_")][0][5:]
                df = load_samples(f"{rd}/load.csv.gz")
                t0 = df["start_ns"].min()
                rec, gap, pre, resume = failover(df, kill, t0)
                w = df[(df["end_ns"] >= kill) & (df["end_ns"] < start)]
                errs = int((~w["ok"]).sum())
                a = df[(df["end_ns"] >= start) & (df["end_ns"] < start + 30e9)]
                post = df[(df["end_ns"] >= start + 60e9) & (df["end_ns"] < start + 110e9) & df["ok"]].shape[0] / 50
                prev = df[(df["end_ns"] >= kill - 30e9) & (df["end_ns"] < kill) & df["ok"]].shape[0] / 30
                recs.append(dict(rec=rec, gap=gap, resume=resume, errs=errs, rate=100 * errs / max(1, len(w)),
                                 aerr=int((~a["ok"]).sum()), ratio=post / prev))
                if i == 0:
                    fig, ax = plt.subplots(figsize=(11, 4.5))
                    timeline(ax, df, t0, [("PUT", df.op == "P", "#d62728"), ("GET", df.op == "G", "#1f77b4")])
                    ax.axvline((kill - t0) / 1e9, color="k", ls=":", lw=1.5, label=f"docker kill {victim}")
                    ax.axvline((start - t0) / 1e9, color="g", ls=":", lw=1.5, label=f"docker start {victim}")
                    ax.set_yscale("symlog", linthresh=10); ax.set_ylim(bottom=0); ax.grid(alpha=0.3); ax.legend(fontsize=8, loc="lower left")
                    ax.set_xlabel("tempo desde o início da carga (s)"); ax.set_ylabel("operações por segundo")
                    ax.set_title(f"E6 - {LABEL[s]}: falha do {var} ({victim}), rep 1 - escritas interrompidas por {gap:.2f} s")
                    fig.tight_layout(); fig.savefig(f"{FIG}/e6_{s}_{var}.png", dpi=150); plt.close(fig)
            if recs:
                r = pd.DataFrame(recs)
                t += (f"| {s} | {var} ({'n1' if var == 'lider' else 'n3'}) | {mmm(r.gap, '{:.2f}')} | {mmm(r.resume, '{:.2f}')} | {mmm(r.rec, '{:.1f}')} | "
                      f"{mmm(r.errs, '{:.0f}')} | {mmm(r.rate, '{:.2f}')} | {mmm(r.aerr, '{:.0f}')} | {mmm(r.ratio, '{:.2f}')} |\n")
    if found:
        write("e6.md", t)


if __name__ == "__main__":
    todo = sys.argv[1:] or ["e1", "e2", "e3", "e5", "e6"]
    for x in todo:
        {"e1": e1, "e2": e2e4, "e4": e2e4, "e3": e3, "e5": e5, "e6": e6}[x]()
