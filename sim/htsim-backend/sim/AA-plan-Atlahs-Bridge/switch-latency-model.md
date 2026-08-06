# Switch latency in htsim_uec — exploration + realism addition

Date: 2026-06-24. Context: making the INC-vs-P2P AllReduce microbenchmark realistic.
The supervisor's point — "a switch hop should incur a small latency, currently it does
not" — is mostly right, with one nuance the audit turned up.

## What the simulator already models (and what it doesn't)

A packet crossing a fat-tree switch in this fork pays three latency components:

1. **Link propagation** — the `Pipe` on each `Route` element, default `RTT = 400 ns`
   (`main_uec.cpp:45`), set per link from `Downlink_Latency_ns` in a `.topo`. This is
   *wire/SerDes* delay, not switching.
2. **Queue serialization** — egress `Queue` drains at link rate; `payload / linkspeed`.
3. **Switch processing** — every packet is pushed once through the switch's own
   `CallbackPipe _pipe` (`fat_tree_switch.cpp:18`, delay = `switch_latency`). This is the
   store-and-forward / pipeline / lookup cost of the switch itself.

The catch: **`switch_latency` defaults to 0 ns** (`main_uec.cpp:102`,
`timeFromNs(0)`), and neither the bcast sweep harness *nor the validations two-tier
harness* (`tree16_*.topo` ship `Switch_Latency_ns 0`) ever overrides it. So although the
plumbing exists, **switch processing latency is effectively un-modelled today** — the
supervisor's instinct is correct in practice. A `-switch_latency <ns>` CLI flag already
exists (`main_uec.cpp:290`) and feeds all switch tiers uniformly; the per-tier
`.topo` key `Switch_Latency_ns` overrides it.

What is **genuinely missing**: the **in-switch aggregation compute cost**. An INC reduce
hop does more than forward — it buffers operands, aligns them, and runs an ALU over the
vector. Before this change every reduce combine/turn-around went through the *same* `_pipe`
as plain forwarding (`fat_tree_switch.cpp:336/385` and the apex fanout), so INC aggregation
was charged exactly the same as a bit-for-bit forward. That under-charges INC.

## The addition: opt-in `-reduce_compute_latency`

**Two paths, one subtlety (found while validating the microbenchmark).** When the topology
is loaded from a `.topo` file (`-topo`, which the microbenchmark uses), the CLI
`-switch_latency` flag is **ignored** — the per-tier switch latency comes from the topo's
`Switch_Latency_ns`. That topo value sets each switch's `_pipe` delay and is incurred by
**both** arms, per hop: the ring's plain p2p flows are FIB-routed through
`FatTreeSwitch::receivePacket` → `_pipe` exactly like INC's switch-dispatched packets.
Verified: with `Switch_Latency_ns = 1000`, INC grows +1000 (≈ its ~2 switch hops) and the
ring grows +12000 (≈ its 12 serial hops × 1000) — i.e. the per-hop cost is symmetric, and
the ring simply traverses more hops. (The CLI `-switch_latency` *does* work, but only on the
non-`.topo`, `-nodes`-built fat tree — which is why an earlier probe that varied the CLI flag
on a `-topo` run saw no change. Earlier drafts mis-attributed this to source-routing; the
real cause is the CLI flag being ignored on the `-topo` path.)

The microbenchmark therefore places the **symmetric** per-hop costs in the `.topo` (both arms
see them) and reserves the INC-only term for the CLI:

- **`.topo Downlink_Latency_ns`** (500 ns) — link/wire latency, every hop, both arms.
  Verified the ring responds to it (36.9 µs at 500 ns vs 6.5 µs at 1 ns for |G|=8/64 KiB).
- **`.topo Switch_Latency_ns`** (300 ns, NVSwitch-class) — switch processing latency, every
  hop, both arms. The validations tier ships 0; we add a realistic value for the eval.
  Because the ring makes far more switch traversals than INC, it pays more total switch
  latency — realistic, and exactly the fewer-hops advantage INC is meant to capture.
- **`-reduce_compute_latency <ns>`** (new CLI, default **0**, here 100; applies on the
  `-topo` path too) — extra latency charged **only on the INC reduce path** (`_reduce_pipe`),
  stacking on the topo switch latency (`_reduce_pipe = Switch_Latency_ns + reduce_compute`).
  Models the aggregation ALU + operand-alignment cost the ring does not incur in-network
  (the ring reduces at endpoints — not modelled, standard α-β). Default 0 ⇒ prior results
  unchanged. Since it is charged to INC and not the ring, it
  makes the A/B *conservative* toward INC.

`-switch_latency` (existing) remains available as an INC-only sensitivity knob, but the
headline A/B sets it to 0 and sources per-hop latency from the `.topo` for fairness.

### Implementation (minimal, correct)

Each `FatTreeSwitch` gains a second pipe `_reduce_pipe = CallbackPipe(switch_latency +
reduce_compute_latency)` (`fat_tree_switch.cpp` ctor). The reduce-path emits are routed
through it:

| site | packet | pipe |
|---|---|---|
| `fat_tree_switch.cpp:336` (`*d`) | rooted-Reduce result toward root | `_reduce_pipe` |
| `:385` (`*c`) | combined contribution forwarded up | `_reduce_pipe` |
| apex Allreduce turn-around (`fanout_replicas(..., charge_reduce_compute=true)`) | the multicast-down seed/replicas | `_reduce_pipe` |
| pure multicast (`handle_mcast` → `fanout_replicas(..., false)`) | replication only, **no** aggregation | `_pipe` |

The compute cost is charged **once per aggregating switch**, never per child contribution:
at the apex the `k` fan-out replicas are emitted *concurrently* (all scheduled at
`now()+delay`), so routing them through `_reduce_pipe` adds the term exactly once per
turn-around, not `k` times. Pure multicast is explicitly excluded — replication is line-rate
cut-through, not compute.

When `reduce_compute_latency == 0`, `_reduce_pipe` is identical to `_pipe`, so the change is
a no-op at default settings (verified: `.cm` AllReduce apex = 3398 ns and the GOAL `coll`
arm = 334 ns, both unchanged from before the edit).

### Validation

| run (`.cm` AllReduce apex, |G|=4, 4 KiB) | duration |
|---|---|
| defaults (`switch_latency 0`, `reduce_compute 0`) | 3398 ns (unchanged) |
| `-switch_latency 300` | 4898 ns (+1500 ≈ 5 switch hops on the critical path) |
| `-switch_latency 300 -reduce_compute_latency 100` | 5198 ns (+300, charged at the reduce combines + apex) |

The `.cm` table above is a `-nodes`-path probe where the CLI `-switch_latency` *is* honoured
(the INC arm responds to it). In the microbenchmark (the `-topo` path) the symmetric per-hop
costs are carried by the `.topo` itself — `Downlink_Latency_ns 500` + `Switch_Latency_ns 300`,
both reaching both arms — and INC alone additionally carries `-reduce_compute_latency 100`.
This makes the comparison *conservative*: INC is charged for the in-switch compute it does
and the ring is not, so any measured speedup is a lower bound on that axis.

## Thesis framing

> Per-hop latency in htsim is the link `Pipe` delay (`Downlink_Latency_ns`, 500 ns ≈
> NVLink-class) plus the switch processing pipe (`Switch_Latency_ns`, set to an NVSwitch-class
> 300 ns) plus queue serialization; on the `.topo` path both the in-network and
> point-to-point arms traverse all of these, per hop, so the per-hop cost is symmetric and
> the ring's disadvantage is simply that it makes more switch traversals. In-network
> aggregation additionally incurs an ALU + operand-alignment cost,
> modelled by `reduce_compute_latency` (≈100 ns) charged once per aggregating switch on the
> reduce path — so the in-network arm is never given a free switch relative to the ring.
