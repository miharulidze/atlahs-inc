# htsim Simulator Architecture — `main_uec` view

*A condensed architecture note scoped to what the broadcast baseline
actually exercises: the **`main_uec`** driver, a three-tier k-ary
**`FatTreeTopology`**, **ECMP_FIB** routing, and the **UEC** transport
layer plus our `UecBcast*` subclasses. Nothing about NDP, EQDS,
LGS/GOAL, or lossless-input flow control — those are out of scope.*

------------------------------------------------------------------------

## 0. TL;DR

`htsim` is a **discrete-event network simulator**: nothing has a wall
clock, only a `uint64_t` of picoseconds that advances when the event
queue decides. Every "physical" effect — packet serialisation,
wire propagation, queueing delay — is a scheduled `doNextEvent()`
callback.

The simulator is built out of three kinds of objects:

- **Event sources** (queues, pipes, transport sources) — things the
  clock calls when their turn comes.
- **Packet sinks** (queues, pipes, switches, transport sinks) — things
  that receive packets by method call, not by time advance.
- **Triggers and trigger targets** — a zero-time signalling layer on
  top of the clock, used for `.cm`-level dependencies and — in our
  baseline — for barrier synchronisation across broadcast legs.

The `main_uec` driver wires these together at startup (assembly time)
and then just `while (eventlist.doNextEvent()) {}` until the fabric
drains (runtime).

------------------------------------------------------------------------

## 1. The four abstract roles

Almost every concrete class derives from one or more of four bases:

```
                                Logged
                          uint32_t _log_id
                         (unique, monotonic,
                          used for tracing)
                                 ▲
        ┌─────────────────┬──────┴──────┬───────────────────┐
        │                 │             │                   │
   EventSource       PacketSink    TriggerTarget        PacketFlow
 doNextEvent()=0  receivePacket  activate() = 0    flow_id: partition
 (woken by time)   (Packet&)=0   (fired w/o time)  [0, 1e9) manual
                                                   [1e9, ∞) auto
```

Concrete things this thesis touches:

| Concrete class              | Inherits                                      |
|-----------------------------|-----------------------------------------------|
| `Queue` (incl. `HostQueue`) | `EventSource`, `PacketSink`                   |
| `Pipe`, `CallbackPipe`      | `EventSource`, `PacketSink`                   |
| `FatTreeSwitch`             | `Switch` (which is `EventSource`+`PacketSink`) |
| `UecSrc`, `UecBcastSrc`     | `EventSource`, `PacketSink`, `TriggerTarget`  |
| `UecSink`, `UecBcastSink`   | `PacketSink`, `DataReceiver`                  |
| `BarrierTrigger`            | `Trigger` (sibling of `TriggerTarget`)        |
| `CollectiveCompletionRecorder` | `TriggerTarget`                               |

Note: `Trigger` is a *sibling* of `TriggerTarget`, not a subclass.
Triggers fire targets. This asymmetry is exactly why `TriggerRelay`
exists in `uec_bcast.h` — to bridge the two hierarchies.

------------------------------------------------------------------------

## 2. `EventList` — the clock

All simulated time lives on one global `EventList` object. It holds
two queues:

```
┌────────────────── EventList (static, process-wide) ──────────────────┐
│                                                                      │
│  _pendingsources   :  std::multimap<simtime_picosec, EventSource*>   │
│                       sorted by wake-up time                         │
│                       ("who wakes up next, and when?")               │
│                                                                      │
│  _pending_triggers :  std::vector<TriggerTarget*>                    │
│                       LIFO-ish, fired-now                            │
│                       ("activate these targets, no time advance")    │
│                                                                      │
│  _lasteventtime    :  simtime_picosec   (what EventList::now() returns)│
└──────────────────────────────────────────────────────────────────────┘
```

`doNextEvent()` is a 20-line method that runs the whole simulation:

```
                       doNextEvent()
                             │
        ┌────────────────────┴────────────────────────┐
        ▼                                              ▼
  pending_triggers                                 else: pop earliest
    non-empty?                                     entry of _pendingsources
        │                                              │
   ── YES ──►                                          │
     pop one,                                          ▼
     call target.activate()                    _lasteventtime = its time
     (no time advance)                         src.doNextEvent()
```

Two scheduling APIs the rest of the codebase uses:

- `sourceIsPending(src, when)` — add `src` to `_pendingsources` at
  absolute simulator time `when`.
- `sourceIsPendingRel(src, delay)` — shorthand for `when = _now + delay`.
- `triggerIsPending(target)` — push onto `_pending_triggers`. Fires on
  the very next `doNextEvent()` call, *without* advancing `_now`.

This is why the full chain
`sink → barrier → recorder` emits its `BCAST_COMPLETE` line at exactly
the same `_now` as the sink received its last byte — all the trigger
machinery lives on the zero-time queue.

------------------------------------------------------------------------

## 3. Queue + Pipe — the two fabric atoms

A **link** in htsim is not a first-class object. It's modelled by two
co-operating `EventSource`s:

```
      ┌──────── Queue ────────┐       ┌──────── Pipe ────────┐
      │                       │       │                      │
      │  _enqueued FIFO       │       │  _delay  (constant)  │
      │  drainTime(pkt) =     │       │                      │
      │    pkt.size() *       │──►────│                      │──►──▶ next
      │    _ps_per_byte       │       │                      │        hop
      │                       │       │                      │
      │  on fire:             │       │  on fire:            │
      │    pop head           │       │    pop head          │
      │    pkt.sendOn()       │       │    pkt.sendOn()      │
      └───────────────────────┘       └──────────────────────┘
           bandwidth model                propagation model
       (time-varying w/ load)               (always constant)
```

For a 100 Gbps link, `_ps_per_byte = (10^12 * 8) / 10^11 = 80 ps/B`,
so a 4160-byte on-wire UEC packet drains in `332.8 ns`. For our
measurements the pipe latency is set via `-hop_latency` (default
400 ns).

`p.sendOn()` itself is instantaneous — it just advances a `_nexthop`
counter on the packet and calls the next hop's `receivePacket`. All
*delay* comes from queues and pipes scheduling their own
`doNextEvent` in the future.

### A `Route` is a list of sinks

```
  class Route { vector<PacketSink*> _sinklist; … };

  p._route   →  [ queue_out, pipe_out, next-switch ]
               ^           ^           ^
               nexthop=0   =1          =2
```

`Packet::sendOn()` reads `_route->at(_nexthop)`, increments, and calls
`receivePacket` on that sink. Switches use `set_route(newroute)` to
swap in a fresh single-hop route for the next leg of the journey;
**`set_route` resets `_nexthop = 0`**.

------------------------------------------------------------------------

## 4. `FatTreeSwitch` — ingress / egress dance

Switches do not put packets into one input queue and out the other;
htsim models them as a *pass-through router with an internal pipe*:

```
  Packet p arrives at FatTreeSwitch::receivePacket
                       │
                       ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  Have I seen this packet before?  (check _packets.find(&p)) │
  ├─────────────────────────────────────────────────────────────┤
  │                                                              │
  │  NO  ──►  I N G R E S S   phase:                             │
  │          _packets[&p] = true                                 │
  │          nh = getNextHop(p, …)               [FIB lookup]    │
  │          p.set_route(*nh)                    [nexthop ← 0]   │
  │          _pipe->receivePacket(p)             [switch_lat]    │
  │             └─ schedules _pipe->doNextEvent at              │
  │                now + switch_latency                          │
  │                (main_uec passes 0 → instant)                 │
  │                                                              │
  │  YES ──►  E G R E S S   phase (after _pipe fires):           │
  │          _packets.erase(&p)                                  │
  │          p.sendOn()             [onto the fresh route set    │
  │                                   during INGRESS]            │
  │                                                              │
  └─────────────────────────────────────────────────────────────┘
```

Two consequences:

1. A switch appears in the event schedule twice per packet (ingress
   call, egress call via `_pipe`). When `switch_latency = 0` the two
   happen at the same `_now`.
2. The old route the packet was carrying gets replaced. On egress
   `_nexthop = 0`, so the new hop sequence (egress queue → egress
   pipe → next switch) is what `sendOn()` walks.

### The FIB

Every switch holds a `RouteTable`:

```
  _fib     : unordered_map<int, vector<FibEntry*>*>
             key = destination, val = equal-cost next-hop set
             (used for prefix-level / upward routing)

  _hostfib : unordered_map<int, unordered_map<int, HostFibEntry*>*>
             outer key = destination host
             inner key = flow_id
             val = single deterministic next-hop
             (ToR only, populated by addHostPort)
```

`addHostPort(host, flow_id, endpoint)` installs into `_hostfib`; each
`UecBcastSrc`/`UecBcastSink` pair registers both ends at setup time.
`getNextHop()` consults `_hostfib` at a ToR for locally-attached
hosts; at every other tier (or for non-local ToR traffic) it pulls
from `_fib`.

### Path selection on the upward set

Under `ECMP_FIB` — the strategy `main_uec` passes — the switch hashes
`(flow_id, pathid, _hash_salt)` through a `freeBSDHash` and picks
`hash % fib_entries.size()`:

```
  ecmp_choice = freeBSDHash(pkt.flow_id(), pkt.pathid(), _hash_salt)
                  % available_hops->size();
```

The sender sets `pkt.set_pathid(_path_ids[crt])` before `sendOn()`, so
different path-ids hash to different cores. For our broadcast, that
means consecutive legs (different pathids picked round-robin inside
`UecSrc::choose_route`) land on different aggregation upstreams.

------------------------------------------------------------------------

## 5. `FatTreeTopology` — assembly

### Parameters

For `k`-ary, three tiers (the only case `main_uec` uses):

```
  N_srv   = k³/4        Hosts
  N_tor   = k²/2        ToR switches  (k/2 per pod × k pods)
  N_agg   = k²/2        Aggregation switches
  N_core  = k²/4        Core switches

  pod    = k
  radix  = k ports per switch, same at every tier
```

For the three fabrics used in Chapter 5: k=4 (16 hosts), k=8 (128),
k=16 (1024).

### Queue and pipe arrays

The topology holds six directed queue arrays and six parallel pipe
arrays, one pair per tier-to-tier direction. The naming is
`<src>_<dst>` where the token encodes the tier:

```
  ns  = Node / Server (host)
  nlp = Node / Leaf-Pod (ToR)
  nup = Node / Upper-Pod (Aggregation)
  nc  = Node / Core
```

```
  Hosts ─► ToR                 ToR ─► Agg                Agg ─► Core
  ─────────────                ─────────────              ─────────────
  queues_ns_nlp  [N_srv][N_tor][b]    queues_nlp_nup [N_tor][N_agg][b]
  pipes_ns_nlp   [N_srv][N_tor][b]    pipes_nlp_nup  [N_tor][N_agg][b]
                               queues_nup_nc  [N_agg][N_core][b]
                               pipes_nup_nc   [N_agg][N_core][b]

  ToR ─► Host                 Agg ─► ToR                 Core ─► Agg
  ─────────────                ─────────────              ─────────────
  queues_nlp_ns  [N_tor][N_srv][b]    queues_nup_nlp [N_agg][N_tor][b]
  pipes_nlp_ns   [N_tor][N_srv][b]    pipes_nup_nlp  [N_agg][N_tor][b]
                               queues_nc_nup  [N_core][N_agg][b]
                               pipes_nc_nup   [N_core][N_agg][b]
```

`b` is the bundle size (number of parallel links in the bundle). For
our runs `b = 1`.

### A quick reference picture

```
   Hosts            ToRs              Aggs            Cores
 (N_srv)         (N_tor)           (N_agg)         (N_core)

            queues_ns_nlp       queues_nlp_nup     queues_nup_nc
   ○──────────►[queue+pipe]─────►[queue+pipe]─────►[queue+pipe]─────►○
                                                                     │
   ○◄──────────[queue+pipe]◄─────[queue+pipe]◄─────[queue+pipe]◄─────○
            queues_nlp_ns       queues_nup_nlp     queues_nc_nup

  Helpful mnemonic: read the suffix as the destination. A queue named
  "queues_ns_nlp" sits on the host (ns) and delivers to the ToR (nlp),
  i.e. host → ToR.
```

### `HOST_POD_SWITCH(host)`

A one-liner the topology and the switches use everywhere:

```cpp
uint32_t HOST_POD_SWITCH(uint32_t src) {
    return src / _radix_down[TOR_TIER];     // hosts per ToR
}
```

Returns the global index of the ToR that directly serves `src`. With
k=4, `_radix_down[TOR]=2`, so hosts 0,1 → ToR 0; hosts 2,3 → ToR 1;
and so on.

------------------------------------------------------------------------

## 6. Packet life-cycle for one broadcast leg

Concrete example, cross-pod leg on k=16. `T₀` is the scheduled op start
(`crt->start`). Tabs are just to show the path; everything happens at
the indicated simulator time.

```
T₀            UecBcastSrc::bcast_send_once() runs
                ├─ p = UecPacket::newpkt(…, 4160 B)
                └─ p.sendOn()                           [nexthop=0]
                   └─ HostQueue::receivePacket (at root)
                      ├─ enqueue
                      └─ beginService → schedule drain in 332.8 ns

T₀ + 332.8    HostQueue::doNextEvent = completeService
                ├─ pop p
                └─ p.sendOn()                           [nexthop=1]
                   └─ Pipe(ns→nlp)::receivePacket
                      └─ schedule doNextEvent in 400 ns

T₀ + 732.8    Pipe::doNextEvent
                └─ p.sendOn()                           [nexthop=2]
                   └─ FatTreeSwitch(root's ToR)::receivePacket  [INGRESS]
                      ├─ getNextHop: ECMP pick Agg upstream
                      ├─ p.set_route(egress route)       [nexthop←0]
                      └─ _pipe::receivePacket            [0 ns delay]

T₀ + 732.8    _pipe::doNextEvent   (same _now)
                └─ FatTreeSwitch::receivePacket         [EGRESS]
                   └─ p.sendOn()
                      └─ queues_nlp_nup::receivePacket
                         └─ schedule drain 332.8 ns

T₀ + 1065.6   queues_nlp_nup::doNextEvent ...
              (repeat queue→pipe→switch through Agg, Core,
               dst Agg, dst ToR)

            ...

T₀ + Tₑₙₑ    UecBcastSink::receivePacket       (at dest host)
                ├─ _bytes_received += 4160
                └─ if complete: _end_trigger->activate()
                                 └─ BarrierTrigger::activate()
                                    ├─ --count
                                    └─ if count==0:
                                       triggerIsPending(each target)

T₀ + Tₑₙₑ     CollectiveCompletionRecorder::activate() (same _now)
                └─ cout << "BCAST_COMPLETE … duration_ns=Tₑₙₑ/1000"
```

Measured for a single cross-pod leg under our phase-one assumptions:
`Tₑₙₑ ≈ 3.58 µs`. That's 6 queue+pipe hops (host→ToR→Agg→Core→Agg→ToR→host)
with drain ≈ 332.8 ns + prop ≈ 400 ns each plus a bit of unaccounted
per-packet cost (§5.2 of the thesis).

------------------------------------------------------------------------

## 7. UEC transport — what's inherited, what's overridden

```
        UecSrc                              UecSink
         │  full UEC:                        │  full UEC:
         │  • send_packets (CWND-gated)      │  • receivePacket counts
         │  • processAck                     │    bytes, emits UecAck
         │  • _rtx_timeout                   │  • send_ack / send_nack
         │                                   │  • end_trigger declared
         │                                   │    but NEVER fired
         ▼                                   ▼
      UecBcastSrc                         UecBcastSink
         │                                   │
         │  overrides:                       │  overrides:
         │  • doNextEvent  → bcast_send_once │  • receivePacket: count
         │  • activate     → bcast_send_once │    bytes, fire _end_trigger
         │  • receivePacket → drop silently  │    on last byte, no ACK
         │                                   │
         │  NOT inherited behavior-wise:     │  NOT inherited:
         │  • CWND gating                    │  • send_ack / send_nack
         │  • _rtx_timeout                   │
         │  • processAck                     │
         │                                   │
         │  INHERITED mechanics:             │  INHERITED mechanics:
         │  • _route, _path_ids, _flow,      │  • _end_trigger wiring
         │    _mss (via friendship)          │  • PacketSink dispatch
         │  • choose_route (ECMP cycling)    │    at the ToR
         │  • connect() plumbing             │
         └─────────────────────┬─────────────┘
                               │
                 friend declarations in uec.h
                 let the subclasses read the
                 private routing members
```

Implementation detail worth knowing: the ACK-less sink still passes
`UecSrc::connect(…, UecBcastSink&, …)` because the signature takes a
`UecSink&` reference and the subclass **is-a** `UecSink`. We only need
to avoid *calling* the ACK-emission code-path, not to avoid inheriting
its fields.

------------------------------------------------------------------------

## 8. `main_uec`: assembly time vs. runtime

```
┌─────────────────────────────────────────────────────────────────┐
│                   A S S E M B L Y   T I M E                      │
│          (everything before while(doNextEvent) {})               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. CLI parsing (-tm, -strat, -nodes, -linkspeed, -seed, …)      │
│                                                                  │
│  2. Build FatTreeTopology                                        │
│       ├─ alloc_vectors → resize switch/queue/pipe arrays         │
│       ├─ construct FatTreeSwitch objects (ToR, Agg, Core)        │
│       └─ wire edges tier-by-tier (set remote endpoints, add      │
│           queues as switch ports, populate upward FIB)           │
│                                                                  │
│  3. Load ConnectionMatrix from .cm                               │
│       ├─ parse "Grp …"        → conns->groups                    │
│       ├─ parse "<s>-><d> …"   → connection record (with is_bcast) │
│       ├─ parse "trigger id N" → named Trigger on demand          │
│       └─ validate: any start_bcast ⇒ every conn has explicit id  │
│                                                                  │
│  4. Seed synthetic counters                                      │
│       next_bcast_barrier_id  = conns->max_triggerid()            │
│       next_bcast_leg_flow_id = conns->max_flowid()               │
│                                                                  │
│  5. Per connection:                                              │
│       ├─ bcast:  expand → |G|-1 legs  +  one BarrierTrigger      │
│       │          per leg:                                        │
│       │             • new UecBcastSrc / UecBcastSink             │
│       │             • set_flowid  (++next_bcast_leg_flow_id)     │
│       │             • ms->connect(src_route, dst_route,          │
│       │                           msink, crt->start)             │
│       │               → sourceIsPending(*ms, crt->start)         │
│       │             • addHostPort at root ToR AND dest ToR       │
│       └─ p2p:    symmetric with UecSrc / UecSink, plus optional  │
│                  send_done_trigger / recv_done_trigger           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                        R U N T I M E                              │
│               while (eventlist.doNextEvent()) {}                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Every iteration:                                                │
│    1. Drain _pending_triggers if any (zero time).                │
│    2. Else pop earliest _pendingsources entry, advance _now,     │
│       call src->doNextEvent().                                   │
│                                                                  │
│  Typical calls, roughly in the order they fire:                  │
│    • UecBcastSrc::doNextEvent   (at crt->start)                  │
│    • HostQueue::doNextEvent     (drain, 332.8 ns later)          │
│    • Pipe::doNextEvent          (hop latency later)              │
│    • FatTreeSwitch::receivePacket → _pipe → receivePacket again  │
│    • ... repeat through the fabric ...                           │
│    • UecBcastSink::receivePacket (final hop)                     │
│    • BarrierTrigger::activate                                    │
│    • CollectiveCompletionRecorder::activate → stdout line        │
│                                                                  │
│  The loop ends when                                              │
│    • _pendingsources is empty (fabric quiescent)                 │
│    • OR EventList::_endtime was set and now() exceeds it         │
│      (main_uec sets 60 s with setEndtime(timeFromSec(60)))       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

------------------------------------------------------------------------

## 9. How to read an htsim stdout trace

What you'll typically see printed to stdout during a run, in order:

```
traffic matrix input file: …                 ← CLI echo
Using BDP of … - Queue is … - RTT …           ← assembly banner
Fat Tree topology with 0.4us links and …
_no_of_nodes 16 | K 4 | Queue type 3
Nodes: 16 Connections: 2 Triggers: 0          ← .cm header digest
set_up_mcast unimplemented                    ← phase-two stub noise
Setting CWND to …                             ← per-conn CC banner

uec_bcast_op1_1_2                             ← leg names (one per leg)
uec_bcast_op1_1_3
…

Trigger 1 activated, activations remaining: 5 ← sink reports to barrier
Trigger 1 activated, activations remaining: 4
…
Trigger 1 fired, 2 targets                    ← last decrement
BCAST_COMPLETE op_id=1 root=1 group=0 …       ← our recorder

..........|..........|                        ← progress dots until
                                                 endtime (not data)
```

Any `Trigger N fired` with `N < 100` is a `.cm`-declared named trigger;
IDs above `conns->max_triggerid()` are our synthetic barriers. The
only line a plotter needs is `BCAST_COMPLETE`.

------------------------------------------------------------------------

## 10. Intentionally-out-of-scope

Read the following only if a future phase starts exercising them —
they live in the source tree but our baseline does not touch them:

- **NDP / EQDS / TCP** variants of `main_*.cpp`. Identical scaffolding,
  different transports. Share the same `FatTreeTopology`.
- **`LosslessInputQueue`** (`queue_lossless_input.*`). Only instantiated
  when the topology's queue type is `LOSSLESS_INPUT*`; our runs use
  `COMPOSITE`.
- **`LogSimInterface` / GOAL** path in `main_uec`: the `else if
  (goal_filename.size() > 0)` branch around line 1030. Phase-two work.
- **`FatTreeTopology::set_up_mcast()`** — stub, slated for phase-two
  switch-level multicast.
- **`FatTreeSwitch` adaptive / flowlet / RR / round-robin variants**.
  Interesting for future evaluations; our `ECMP_FIB` does not exercise
  them.

------------------------------------------------------------------------

## Pointers into code

| Concept                       | File:line                                                         |
|-------------------------------|-------------------------------------------------------------------|
| `EventList`                   | `eventlist.cpp:42-63`, `eventlist.cpp:87-90`                      |
| `Queue::drainTime`            | `queue.h:53-55`                                                   |
| `Queue::beginService`         | `queue.cpp:131-136`, `queue.cpp:139-162`                          |
| `Pipe::receivePacket`         | `pipe.cpp:20-47`                                                  |
| `FatTreeSwitch::receivePacket`| `datacenter/fat_tree_switch.cpp:23-58`                            |
| `FatTreeSwitch::getNextHop`   | `datacenter/fat_tree_switch.cpp:321-517`                          |
| FIB (`RouteTable`)            | `routetable.{h,cpp}`                                              |
| `FatTreeTopology` queue arrays| `datacenter/fat_tree_topology.h:39-51`                            |
| `HOST_POD_SWITCH`             | `datacenter/fat_tree_topology.h:123-125`                          |
| `alloc_vectors`               | `datacenter/fat_tree_topology.cpp:602-632`                        |
| Edge wiring                   | `datacenter/fat_tree_topology.cpp:765-962`                        |
| `UecSrc::connect`             | `uec.cpp:626-642`                                                 |
| `UecBcastSrc::bcast_send_once`| `uec_bcast.cpp:21-43`                                             |
| `UecBcastSink::receivePacket` | `uec_bcast.cpp:50-73`                                             |
| `CollectiveCompletionRecorder` | `uec_collectives.h:357-379`, `uec_collectives.cpp:139-152`        |
| `main_uec` broadcast branch   | `datacenter/main_uec.cpp:~830-925`                                |
| Event loop                    | `datacenter/main_uec.cpp` (`while (eventlist.doNextEvent()) {}`)  |
