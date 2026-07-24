#!/usr/bin/env python3
"""Generate a self-contained HTML slide deck of the isolation results, ordered by
collective (Broadcast -> Reduce -> ReduceScatter -> AllGather -> AllReduce), then the
cross-cutting structural results. Embeds the isolation-plots PNGs as data URIs."""
import base64
import csv
import os

PLOTS = "/Users/wstaempfli/CLionProjects/atlahs/simulation-scripts/isolation-plots"
OUT = "/private/tmp/claude-501/-Users-wstaempfli-CLionProjects-atlahs/30984ddf-9d6e-478e-80c4-f0162f62f6d1/scratchpad/deck.html"


def uri(name):
    p = os.path.join(PLOTS, name)
    if not os.path.isfile(p):
        return ""
    with open(p, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def fig(name, cap=""):
    u = uri(name)
    if not u:
        return f'<figure class="missing">missing: {name}</figure>'
    c = f'<figcaption>{cap}</figcaption>' if cap else ""
    return f'<figure><img src="{u}" alt="{cap or name}">{c}</figure>'


def stat(v, l):
    return f'<div class="stat"><span class="v">{v}</span><span class="l">{l}</span></div>'


def coll_slide(eyebrow, title, takeaway, stats_html, figs_html):
    return f"""
<section class="slide">
  <div class="eyebrow">{eyebrow}</div>
  <h2>{title}</h2>
  <p class="takeaway">{takeaway}</p>
  <div class="stats">{stats_html}</div>
  <div class="figs two">{figs_html}</div>
</section>"""


AB_CSV = "/Users/wstaempfli/CLionProjects/atlahs/simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv"


def _load_ab():
    try:
        with open(AB_CSV) as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


_AB = _load_ab()


def _sz(b):
    b = int(b)
    for u, d in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if b >= d:
            return f"{b / d:g}{u}"
    return f"{b}B"


def dual_table(c1, c1name, c2, c2name, topo_key="single_switch"):
    """Side-by-side measured completion times + speed-ups for two collectives (ring baseline)."""
    rows = [r for r in _AB if topo_key in r.get("su_topo", "")
            and r.get("baseline_algo") == "ring" and r.get("collective") in (c1, c2)]
    if not rows:
        return "<p class='note'>no data</p>"
    sizes = sorted({int(r["msg_bytes"]) for r in rows if r.get("msg_bytes")})

    def cell(coll, size, key):
        for r in rows:
            if r["collective"] == coll and int(r["msg_bytes"]) == size and r.get(key):
                return r[key]
        return None

    def tns(v):
        return f"{int(float(v)):,}" if v else "—"

    def tsp(v):
        return f"{float(v):.1f}×" if v else "—"

    body = "".join(
        f"<tr><td>{_sz(s)}</td>"
        f"<td>{tns(cell(c1, s, 'inc_ns'))}</td><td>{tns(cell(c2, s, 'inc_ns'))}</td>"
        f"<td>{tsp(cell(c1, s, 'speedup'))}</td><td>{tsp(cell(c2, s, 'speedup'))}</td></tr>"
        for s in sizes)
    return (f'<table class="data"><thead><tr><th>message size</th>'
            f'<th>{c1name}<span>time (ns)</span></th><th>{c2name}<span>time (ns)</span></th>'
            f'<th>{c1name}<span>speed-up</span></th><th>{c2name}<span>speed-up</span></th>'
            f'</tr></thead><tbody>{body}</tbody></table>')


slides = []

# ---- 1. title ------------------------------------------------------------
slides.append(f"""
<section class="slide title">
  <div class="eyebrow">ATLAHS · in-network collectives · scale-up isolation</div>
  <h1>In-Network Collectives<span class="thin"> — isolation results</span></h1>
  <p class="lede">Per-collective results on the multi-domain (pcm-sdk) simulator, post-fix real
  receive datapath. Endpoint baselines come from the NCCL generator's own decomposition.
  Measured only &middot; <span class="ok">0 packet drops across every run</span>.</p>
  <div class="stats wide">
    {stat("5", "collectives")}
    {stat("|G|=64", "default group size")}
    {stat("2", "topologies (switch · 3-tier)")}
    {stat("0", "packet drops")}
  </div>
  <div class="hint">→ / space to advance &nbsp;·&nbsp; click edges &nbsp;·&nbsp; dots below</div>
</section>""")

# ---- 2. how to read ------------------------------------------------------
slides.append(f"""
<section class="slide">
  <div class="eyebrow">how to read this</div>
  <h2>What is measured, per collective</h2>
  <div class="matrix">
    <div class="mrow"><span class="tag ma">time</span><b>Completion-time speed-up</b><span>in-network vs endpoint baseline, single-switch &amp; 3-tier, 4KB–256MB</span></div>
    <div class="mrow"><span class="tag x1">|G|</span><b>Constant-in-group-size</b><span>does completion stay flat as the group grows?</span></div>
    <div class="mrow"><span class="tag mb">bytes</span><b>Network footprint</b><span>byte×link crossings vs the decomposition (Khalilov)</span></div>
    <div class="mrow"><span class="tag e1">AR</span><b>AllReduce extras</b><span>switch turnaround vs composed (RS + AG); lossless backpressure under load</span></div>
  </div>
  <p class="note">Legend throughout: <span class="chip inc">in-network</span> <span class="chip base">endpoint baseline</span> &nbsp;·&nbsp; pcm-sdk, measured, 0 drops.</p>
</section>""")

# ---- 3. Broadcast --------------------------------------------------------
slides.append(coll_slide(
    "① Broadcast &middot; multicast fan-out",
    "Broadcast — one root, replicated in the switch",
    "The root emits once; each switch replicates onto its tree ports. Completion is independent of "
    "group size, and every link carries each byte exactly once (bandwidth-optimal multicast).",
    stat("124× → 2.3×", "speed-up, 4KB → 256MB (single-switch)")
    + stat("~9840 ns", "flat across |G| = 2 … 64")
    + stat("each byte ×1", "per link (footprint-optimal)"),
    fig("inc_time_bcast__single_switch.png", "single-switch crossbar")
    + fig("inc_time_bcast__fat3tier.png", "256-host 3-tier fat-tree")))

# ---- 4. Reduce -----------------------------------------------------------
slides.append(coll_slide(
    "② Reduce &middot; aggregation fan-in",
    "Reduce — the dual of Broadcast",
    "Switches combine children on the way up and deliver one result to the root. Same constant-in-|G| "
    "behaviour; losslessness is the correctness precondition (a dropped packet corrupts the sum).",
    stat("124× → 2.3×", "speed-up, 4KB → 256MB (single-switch)")
    + stat("~9832 ns", "flat across |G| = 2 … 64")
    + stat("k → 1", "byte collapse at each switch"),
    fig("inc_time_reduce__single_switch.png", "single-switch crossbar")
    + fig("inc_time_reduce__fat3tier.png", "256-host 3-tier fat-tree")))

# ---- 5. Broadcast vs Reduce (table) --------------------------------------
slides.append(f"""
<section class="slide">
  <div class="eyebrow">① &amp; ② &middot; measurements side by side</div>
  <h2>Broadcast and Reduce coincide — the numbers</h2>
  <p class="takeaway">Single-switch, |G|=64, ring baseline. The in-network completion times track each
  other to within ~1% (rooted duals), and the speed-ups fall the same way as messages grow.</p>
  {dual_table("bcast", "Broadcast", "reduce", "Reduce")}
</section>""")

# ---- 6. ReduceScatter ----------------------------------------------------
slides.append(coll_slide(
    "③ ReduceScatter &middot; per-chunk fan-in",
    "ReduceScatter — reduce, but each member keeps one block",
    "Per-chunk aggregation delivers only the 1/P block each member owns. Largest win at small messages; "
    "at bulk sizes it approaches the endpoint ring, where the footprint reduction becomes the story.",
    stat("125× → 1.3×", "speed-up, 4KB → 256MB (single-switch)")
    + stat("S / P", "delivered downstream per member")
    + stat("→ footprint", "the multi-tier advantage"),
    fig("inc_time_reduce_scatter__single_switch.png", "single-switch crossbar")
    + fig("inc_time_reduce_scatter__fat3tier.png", "256-host 3-tier fat-tree")))

# ---- 7. AllGather --------------------------------------------------------
slides.append(f"""
<section class="slide">
  <div class="eyebrow">④ AllGather &middot; the ingress-bound case</div>
  <h2>AllGather — ties the ring on bandwidth, wins on latency &amp; footprint</h2>
  <p class="takeaway">Every member must ingest the P−1 blocks it lacks through its one NIC — an irreducible
  floor the endpoint ring already saturates. In-network does not beat bulk bandwidth here; it wins on
  small-message latency and on fabric footprint. The opposite of AllReduce.</p>
  <div class="stats">
    {stat("124× → 1.3×", "speed-up, 4KB → 256MB (single-switch)")}
    {stat("(P−1)·block", "irreducible ingress floor")}
    {stat("Khalilov", "the paper's own collective")}
  </div>
  <div class="figs two">
    {fig("inc_time_allgather__single_switch.png", "completion time, single-switch")}
    {fig("footprint_allgather.png", "footprint reduction vs P")}
  </div>
</section>""")

# ---- 8. ReduceScatter vs AllGather (table) -------------------------------
slides.append(f"""
<section class="slide">
  <div class="eyebrow">③ &amp; ④ &middot; measurements side by side</div>
  <h2>ReduceScatter vs AllGather — the numbers</h2>
  <p class="takeaway">Single-switch, |G|=64, ring baseline. Closely matched at small messages; at bulk
  sizes the two edge apart as AllGather approaches its ingress floor.</p>
  {dual_table("reduce_scatter", "ReduceScatter", "allgather", "AllGather")}
</section>""")

# ---- 7. AllReduce --------------------------------------------------------
slides.append(coll_slide(
    "⑤ AllReduce &middot; apex switch turnaround",
    "AllReduce — reduce up, turn around in-switch, multicast down",
    "The headline collective. A single in-network pass turns the completed reduction around at the apex "
    "switch and multicasts it back — no host round-trip. It genuinely halves traffic, and the win is "
    "largest in the latency regime, biggest on the deeper fabric.",
    stat("246× → 2.6×", "single-switch, 4KB → 256MB")
    + stat("248× → 4.0×", "3-tier fat-tree")
    + stat("halves traffic", "aggregation shrinks data"),
    fig("inc_time_allreduce__single_switch.png", "single-switch crossbar")
    + fig("inc_time_allreduce__fat3tier.png", "256-host 3-tier fat-tree")))

# ---- 8. AllReduce: turnaround vs composed --------------------------------
slides.append(f"""
<section class="slide">
  <div class="eyebrow">⑤ AllReduce &middot; turnaround vs composed</div>
  <h2>Switch turnaround beats the composed form</h2>
  <p class="takeaway">The same AllReduce done two ways. Switch turnaround approaches wire speed; the
  composed RS + AG form — what NVLS-multimem / MSCCL++ ship — plateaus at half, because it pays a host
  round-trip between its two waves.</p>
  <div class="stats">
    {stat("98.2%", "switch turnaround, % of wire @ 256MB")}
    {stat("49.5%", "composed (RS + AG), % of wire")}
    {stat("~2×", "turnaround advantage, large msgs")}
  </div>
  {fig("ar_bandwidth.png")}
</section>""")

# ---- 9. speed-up overview ------------------------------------------------
slides.append(f"""
<section class="slide">
  <div class="eyebrow">across collectives</div>
  <h2>Latency is where in-network wins</h2>
  <p class="takeaway">Speed-up over the endpoint baseline is largest for small messages, then converges
  toward the traffic bound as messages grow. The deeper fabric widens the win (more baseline hops to collapse).</p>
  <div class="figs">{fig("inc_speedup_overview.png")}</div>
</section>""")

# ---- 10. constant-in-|G| -------------------------------------------------
slides.append(f"""
<section class="slide">
  <div class="eyebrow">structural result &middot; group size</div>
  <h2>In-network completion is flat in |G|</h2>
  <p class="takeaway">AllReduce, Reduce and Broadcast complete in the same time regardless of group size —
  the core structural claim. AllGather is the exception: it rises with |G| along the (P−1)·block ingress floor.</p>
  <div class="stats">
    {stat("~9840 ns", "AllReduce / Reduce / Bcast, all |G|")}
    {stat("5582 → 9707", "AllGather, |G| = 2 → 64 (ns)")}
    {stat("84 rows", "0 drops")}
  </div>
  <div class="figs two">
    {fig("groupsweep_time__4194304.png", "completion time vs |G| (4MB)")}
    {fig("groupsweep_speedup__4194304.png", "speed-up vs |G| (4MB)")}
  </div>
</section>""")

# ---- 11. lossless backpressure -------------------------------------------
slides.append(f"""
<section class="slide">
  <div class="eyebrow">structural result &middot; losslessness</div>
  <h2>PFC serialises a shared core — losslessly</h2>
  <p class="takeaway">N concurrent disjoint AllReduces: pin every tree to one core and PFC backpressure
  serialises them (completion climbs); spread them across cores and they stay independent. Zero drops throughout.</p>
  <div class="stats">
    {stat("4.79 µs", "distributed — flat in N")}
    {stat("4.79 → 6.94 µs", "pinned, N = 1 → 16")}
    {stat("0", "packet drops")}
  </div>
  {fig("pfc_backpressure.png")}
</section>""")

# ---- 12. footprint -------------------------------------------------------
slides.append(f"""
<section class="slide">
  <div class="eyebrow">structural result &middot; footprint</div>
  <h2>Bandwidth-usage reduction scales with the fabric</h2>
  <p class="takeaway">Byte×link crossings vs the Ring / Recursive-Doubling decomposition. Flat ~2× on a single
  switch; on multi-tier fabrics the RD baseline's cross-tier traffic makes the reduction climb with scale.</p>
  <div class="stats">
    {stat("~2.0×", "single-switch (flat)")}
    {stat("4.47×", "3-tier, P = 256")}
    {stat("5.15×", "radix-32, P = 1024")}
  </div>
  <div class="figs two">
    {fig("footprint_all_collectives.png", "by collective × topology")}
    {fig("analytic_khalilov.png", "measured vs analytic model")}
  </div>
</section>""")

# ---- 13. takeaways -------------------------------------------------------
slides.append(f"""
<section class="slide">
  <div class="eyebrow">takeaways</div>
  <h2>What the isolation results establish</h2>
  <ul class="takeaways">
    <li><b>Latency is where in-network wins.</b> Speed-ups are largest for small messages and large groups; they converge to the traffic bound at scale.</li>
    <li><b>Switch turnaround beats the composed form.</b> Turnaround reaches wire speed; the shipped RS + AG decomposition sits at half — the host round-trip is the cost.</li>
    <li><b>Constant-in-|G| holds</b> for AllReduce / Reduce / Broadcast; AllGather is the ingress-bound exception.</li>
    <li><b>Lossless under contention.</b> PFC serialises a pinned core with zero drops — the correctness precondition for aggregation.</li>
    <li><b>Footprint reduction scales</b> on multi-tier fabrics (up to 5.15× at 1024 hosts).</li>
  </ul>
  <p class="note">pcm-sdk · post-fix real receive datapath · measured only · 0 drops · endpoint baselines from the generator's own decomposition.</p>
</section>""")

# ---- 14. appendix --------------------------------------------------------
appendix_imgs = [
    "inc_time_allreduce_rs_ag__single_switch.png", "inc_time_allreduce_rs_ag__fat3tier.png",
    "footprint_allgather.png",
    "groupsweep_time__4096.png", "groupsweep_speedup__4096.png",
]
grid = "".join(fig(n, n.replace(".png", "").replace("__", " · ").replace("_", " ")) for n in appendix_imgs)
slides.append(f"""
<section class="slide appendix">
  <div class="eyebrow">appendix</div>
  <h2>Composed AllReduce time · 4KB group-sweep</h2>
  <div class="figs grid">{grid}</div>
</section>""")

n = len(slides)
dots = "".join(f'<button class="dot" data-i="{i}" aria-label="slide {i+1}"></button>' for i in range(n))

STYLE = """
<style>
:root{
  --ink:#0e141b; --ink2:#151d27; --panel:#1b2430; --text:#e6edf3; --muted:#8b97a6;
  --inc:#2dd4bf; --base:#e0a33d; --line:rgba(255,255,255,.10); --ok:#5ed6a0;
  --card:#ffffff;
  color-scheme:dark;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{background:var(--ink);color:var(--text);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;overflow:hidden}
.deck{height:100vh;width:100vw;position:relative}
.slide{position:absolute;inset:0;display:none;flex-direction:column;
  padding:clamp(28px,5vw,72px);gap:clamp(10px,1.4vh,20px);opacity:0;
  transition:opacity .32s ease}
.slide.on{display:flex;opacity:1}
@media (prefers-reduced-motion:reduce){.slide{transition:none}}
.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:12px;font-weight:600;
  color:var(--muted);display:flex;align-items:center;gap:10px}
h1{font-size:clamp(34px,5.4vw,68px);font-weight:800;line-height:1.02;
  text-wrap:balance;letter-spacing:-.02em}
h1 .thin{font-weight:300;color:var(--muted)}
h2{font-size:clamp(24px,3.2vw,40px);font-weight:750;line-height:1.08;
  text-wrap:balance;letter-spacing:-.015em}
.lede{max-width:64ch;color:#c3ccd6;font-size:clamp(15px,1.5vw,19px);line-height:1.5}
.takeaway{max-width:80ch;color:#c3ccd6;font-size:clamp(14px,1.35vw,18px);line-height:1.45}
.ok,.lede .ok{color:var(--ok);font-weight:600}
.title{justify-content:center;gap:22px}
.hint{position:absolute;bottom:26px;left:50%;transform:translateX(-50%);
  color:var(--muted);font-size:12.5px;letter-spacing:.03em}
/* stats */
.stats{display:flex;flex-wrap:wrap;gap:14px}
.stats.wide{gap:26px}
.stat{background:var(--ink2);border:1px solid var(--line);border-radius:12px;
  padding:12px 18px;display:flex;flex-direction:column;gap:3px;min-width:120px}
.stat .v{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums;font-size:clamp(18px,2vw,26px);font-weight:700;
  color:var(--inc);letter-spacing:-.01em}
.stat .l{font-size:12px;color:var(--muted);letter-spacing:.02em}
/* figures */
.figs{display:flex;gap:18px;flex:1;min-height:0;margin-top:2px}
.figs.two{flex-direction:row}
.figs.two figure{flex:1}
figure{background:var(--card);border-radius:12px;padding:12px;display:flex;
  flex-direction:column;align-items:center;justify-content:center;min-height:0;
  box-shadow:0 6px 24px rgba(0,0,0,.35)}
.slide > figure{flex:1;margin-top:2px}
figure img{max-width:100%;max-height:100%;object-fit:contain;border-radius:4px}
figcaption{margin-top:8px;font-size:11.5px;color:#4a5568;letter-spacing:.02em;text-align:center}
figure.missing{background:var(--panel);color:var(--muted);align-items:center;justify-content:center;
  font-family:ui-monospace,monospace;font-size:13px}
.figs.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;overflow-y:auto}
.figs.grid figure{padding:8px}
.figs.grid figcaption{font-size:9.5px}
.appendix h2{margin-bottom:4px}
/* data table */
.data{border-collapse:collapse;margin-top:14px;font-variant-numeric:tabular-nums;
  font-size:clamp(12px,1.35vw,17px);width:100%;max-width:940px}
.data th,.data td{padding:9px 16px;text-align:right;border-bottom:1px solid var(--line)}
.data th:first-child,.data td:first-child{text-align:left;color:var(--muted)}
.data thead th{color:var(--text);font-weight:700;border-bottom:1px solid rgba(255,255,255,.28);
  vertical-align:bottom}
.data thead th span{display:block;font-weight:400;font-size:10.5px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.04em;margin-top:2px}
.data tbody td{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;color:#c3ccd6}
.data tbody tr:hover{background:var(--ink2)}
/* matrix */
.matrix{display:flex;flex-direction:column;gap:10px;margin-top:6px}
.mrow{display:grid;grid-template-columns:64px 300px 1fr;align-items:center;gap:16px;
  background:var(--ink2);border:1px solid var(--line);border-radius:10px;padding:12px 16px}
.mrow b{font-size:16px}.mrow span:last-child{color:var(--muted);font-size:14px}
.tag{font-family:ui-monospace,monospace;font-weight:700;font-size:12px;text-align:center;
  padding:5px 0;border-radius:7px;color:#0e141b;background:var(--inc)}
.tag.ma{background:#7cc4ff}.tag.x1{background:#b79cff}.tag.x2{background:var(--base)}
.tag.mb{background:#f07f8e}.tag.e1{background:var(--inc)}
.note{color:var(--muted);font-size:13px;margin-top:auto}
.chip{font-size:12px;padding:2px 9px;border-radius:20px;font-weight:600}
.chip.inc{background:rgba(45,212,191,.16);color:var(--inc)}
.chip.base{background:rgba(224,163,61,.16);color:var(--base)}
.takeaways{display:flex;flex-direction:column;gap:12px;max-width:92ch;margin-top:6px}
.takeaways li{list-style:none;padding-left:20px;position:relative;font-size:clamp(14px,1.4vw,18px);
  line-height:1.45;color:#c3ccd6}
.takeaways li::before{content:"";position:absolute;left:0;top:.62em;width:8px;height:8px;
  border-radius:2px;background:var(--inc)}
.takeaways b{color:var(--text)}
/* nav */
.rail{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:20}
.dot{width:9px;height:9px;border-radius:50%;border:0;background:rgba(255,255,255,.22);cursor:pointer;padding:0}
.dot.on{background:var(--inc);transform:scale(1.25)}
.dot:focus-visible{outline:2px solid var(--inc);outline-offset:2px}
.count{position:fixed;top:18px;right:22px;font-family:ui-monospace,monospace;
  font-variant-numeric:tabular-nums;font-size:13px;color:var(--muted);z-index:20}
.zone{position:fixed;top:0;bottom:0;width:16%;z-index:10;cursor:pointer}
.zone.l{left:0}.zone.r{right:0}
</style>"""

SCRIPT = """
<script>
(function(){
  var slides=[].slice.call(document.querySelectorAll('.slide'));
  var dots=[].slice.call(document.querySelectorAll('.dot'));
  var count=document.querySelector('.count');
  var i=0;
  function show(n){
    i=Math.max(0,Math.min(slides.length-1,n));
    slides.forEach(function(s,k){s.classList.toggle('on',k===i)});
    dots.forEach(function(d,k){d.classList.toggle('on',k===i)});
    count.textContent=(i+1)+' / '+slides.length;
  }
  document.addEventListener('keydown',function(e){
    if(e.key==='ArrowRight'||e.key===' '||e.key==='PageDown'){e.preventDefault();show(i+1)}
    else if(e.key==='ArrowLeft'||e.key==='PageUp'){e.preventDefault();show(i-1)}
    else if(e.key==='Home'){show(0)} else if(e.key==='End'){show(slides.length-1)}
  });
  dots.forEach(function(d){d.addEventListener('click',function(){show(+d.dataset.i)})});
  document.querySelector('.zone.l').addEventListener('click',function(){show(i-1)});
  document.querySelector('.zone.r').addEventListener('click',function(){show(i+1)});
  show(0);
})();
</script>"""

html = ("<title>In-Network Collectives — Isolation Results</title>\n"
        + STYLE
        + '\n<div class="count"></div>\n'
        + '<div class="zone l"></div><div class="zone r"></div>\n'
        + '<div class="deck">\n' + "\n".join(slides) + "\n</div>\n"
        + f'<div class="rail">{dots}</div>\n'
        + SCRIPT)

with open(OUT, "w") as f:
    f.write(html)
print("wrote", OUT, "(", round(len(html) / 1024), "KB )", "slides:", n)
