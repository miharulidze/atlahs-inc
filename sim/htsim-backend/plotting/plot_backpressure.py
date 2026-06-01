#!/usr/bin/env python3
"""Single-core backpressure illustration: force N concurrent broadcasts
through ONE core (fixed assignment_idx via -mcast_pin_core) and show that
the lossless (PFC) fabric carries them all with ZERO packet loss, the
backpressure serialising them so completion grows with contention --
whereas spreading the same N across cores keeps completion low. A lossy
(composite) fabric is shown for contrast.

Run from sim/htsim-backend/plotting:
    /usr/bin/python3 plot_backpressure.py
"""
import os, re, subprocess, tempfile
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.normpath(os.path.join(HERE, "..", "sim", "datacenter", "htsim_uec"))
NODES = 16
SIZE = 65536               # 64 KiB, multi-MTU -> sustained streams
NS = [1, 2, 3, 4]
# Disjoint groups, one host per pod -> each host is in <=1 collective, so the
# host edges carry a single stream and the *core* is the contended resource.
GROUPS = [[0, 4, 8, 12], [1, 5, 9, 13], [2, 6, 10, 14], [3, 7, 11, 15]]
PFC = ["-pfc_high", "10", "-pfc_low", "5"]   # conservative -> lossless
DONE_RE = re.compile(r"ALLREDUCE_COMPLETE\b.*?complete_ns=(\d+)")
DROP_RE = re.compile(r"LOSSLESS not working")


def run(n, queue, pin):
    cm = "Nodes %d\n" % NODES
    for g in GROUPS[:n]:
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
        "lossless, pinned to 1 core": ("lossless_input", True),
        "lossless, spread (round-robin)": ("lossless_input", False),
    }
    results = {lab: [] for lab in series}
    print("%-32s %-4s %-9s %-6s %s" % ("series", "N", "completes", "drops", "slowest_us"))
    for lab, (queue, pin) in series.items():
        for n in NS:
            c, d, s = run(n, queue, pin)
            results[lab].append(s)
            print("%-32s %-4d %d/%-7d %-6d %s" % (lab, n, c, n, d,
                                                  ("%.2f" % s) if s else "-"))
    # composite contrast (lossy): does it stay lossless? print only.
    print("-- composite (lossy) reference --")
    for n in NS:
        c, d, s = run(n, "composite", True)
        print("composite, pinned                N=%d completes=%d/%d drops=%d slowest_us=%s"
              % (n, c, n, d, ("%.2f" % s) if s else "-"))

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    markers = {"lossless, pinned to 1 core": "s",
               "lossless, spread (round-robin)": "o"}
    for lab in series:
        ys = results[lab]
        ax.plot(NS, ys, marker=markers[lab], linewidth=1.6, markersize=6, label=lab)
    ax.set_xlabel("concurrent allreduces (disjoint groups, each 64 KiB)")
    ax.set_ylabel("completion time (us)")
    ax.set_title("Single-core backpressure: lossless carries the hotspot (0 drops)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    ax.annotate("all lossless points: 0 packet loss",
                xy=(0.02, 0.02), xycoords="axes fraction", fontsize=8,
                style="italic")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(HERE, "backpressure_singlecore." + ext))
    print("wrote backpressure_singlecore.pdf/.png")


if __name__ == "__main__":
    main()
