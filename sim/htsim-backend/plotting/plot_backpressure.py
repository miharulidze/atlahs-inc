#!/usr/bin/env python3
"""Single-core backpressure sanity check: force N concurrent Allreduces
(disjoint 4-host groups, one host per pod across pods 0-3) through ONE core
(fixed assignment_idx via -mcast_pin_core) versus distributing them across
cores, on the lossless fabric. Expected behaviour: pinning serialises the
operations via PFC backpressure so completion grows ~linearly with N, while
distributing keeps it flat -- both with ZERO packet loss. Confirms the
lossless mechanism absorbs a hotspot by pausing, not dropping.

Run from sim/htsim-backend/plotting (k=12, 432-host tree):
    /usr/bin/python3 plot_backpressure.py
"""
import os, re, subprocess, tempfile
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.normpath(os.path.join(HERE, "..", "sim", "datacenter", "htsim_uec"))
NODES = 432                # k=12 fat tree: 12 pods x 36 hosts, 36 cores
HOSTS_PER_POD = 36
SIZE = 65536               # 64 KiB, multi-MTU -> sustained streams
NS = [1, 4, 8, 12, 16, 20]
PFC = ["-pfc_high", "4", "-pfc_low", "2"]    # conservative -> lossless (0 drops)
DONE_RE = re.compile(r"ALLREDUCE_COMPLETE\b.*?complete_ns=(\d+)")
DROP_RE = re.compile(r"LOSSLESS not working")


def disjoint_group(g):
    """g-th disjoint 4-host group, one host per pod across pods 0-3 (slot g).
    Disjoint => each host is in exactly one collective, so the host edges
    carry a single stream and the *core* is the contended resource. All
    groups span the same four pods, so when pinned they share the same
    core<->agg links (monotonic contention); 36 hosts/pod allows up to 36."""
    return [p * HOSTS_PER_POD + g for p in (0, 1, 2, 3)]


def run(n, queue, pin):
    cm = "Nodes %d\n" % NODES
    for g in (disjoint_group(i) for i in range(n)):
        cm += "Grp %s\n" % " ".join(map(str, g))
    cm += "Connections %d\n" % n
    for i in range(n):
        cm += "0->%d id %d start_allreduce 0 size %d\n" % (i, i + 1, SIZE)
    with tempfile.NamedTemporaryFile("w", suffix=".cm", delete=False) as f:
        f.write(cm); path = f.name
    args = [BIN, "-strat", "ecmp_host", "-tm", path, "-nodes", str(NODES),
            "-linkspeed", "100000", "-seed", "1", "-queue_type", queue]
    if queue == "lossless_input":
        args += PFC
    if pin:
        args += ["-mcast_pin_core", "0"]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=300).stdout
    finally:
        os.unlink(path)
    times = [int(m.group(1)) for m in DONE_RE.finditer(out)]
    drops = len(DROP_RE.findall(out))
    slowest = max(times) / 1000.0 if times else None      # us
    return len(times), drops, slowest


def main():
    series = {
        "single core (pinned)": ("lossless_input", True),
        "distributed (across cores)": ("lossless_input", False),
    }
    results = {lab: [] for lab in series}
    maxdrops = 0
    print("%-28s %-4s %-9s %-6s %s" % ("series", "N", "completes", "drops", "slowest_us"))
    for lab, (queue, pin) in series.items():
        for n in NS:
            c, d, s = run(n, queue, pin)
            results[lab].append(s)
            maxdrops = max(maxdrops, d)
            print("%-26s %-4d %d/%-7d %-6d %s" % (lab, n, c, n, d,
                                                  ("%.2f" % s) if s else "-"))

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    markers = {"single core (pinned)": "s", "distributed (across cores)": "o"}
    for lab in series:
        ax.plot(NS, results[lab], marker=markers[lab], linewidth=1.6,
                markersize=6, label=lab)
    ax.set_xlabel("concurrent allreduces (disjoint groups, each 64 KiB)")
    ax.set_ylabel("completion time (us)")
    ax.set_title("Lossless backpressure: one core serialises, many cores share")
    ax.set_xticks(NS)                       # integer ticks, no fractions
    ax.set_xlim(left=0)
    ax.grid(True, alpha=0.3)
    ax.legend()
    ax.annotate("lossless throughout: %d packet drops" % maxdrops,
                xy=(0.02, 0.02), xycoords="axes fraction", fontsize=8,
                style="italic")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(HERE, "backpressure_singlecore." + ext))
    print("wrote backpressure_singlecore.pdf/.png")


if __name__ == "__main__":
    main()
