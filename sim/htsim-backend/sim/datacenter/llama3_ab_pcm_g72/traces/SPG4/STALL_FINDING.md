# SPG4 INC arm: reproducible engine livelock (2026-07-16)

Both attempts (plain + INC_STALL_DBG=1 rerun) hang identically: the first
AllGather (group 0, one domain) completes at 6,674 ns, then the clock freezes
at now_ns = 6,815 with 144/288 ranks holding a type-15 (OP_COLL_DONE-pending)
op at offset 3, sends_active = 0, ev_pending = 73, next event 0.2 ns away —
the event loop spins at ~99 % CPU forever (12 h observed, zero progress).
Stall dump: inc_stalldbg.out.gz (~300 identical stagnation dumps).

Configuration triangulation: plain-INC multi-domain (G4, 4 domains, AllReduce)
completes; SP single-domain (SPG1) completes; SP multi-domain (SPG4) livelocks.
Prime suspect: the DOCUMENTED shared-intranode-topology aliasing (all su_apis
share ONE intranode FatTreeTopology; node-local remap collides member sinks/FIB
across domains — htsim_app_atlahs.cpp:1058/:1214, open item in the port notes).
SP's asymmetric AG/RS interleaving desynchronises the domains, so cross-domain
aliased contributions can satisfy one domain's fan-in barrier while starving
another — plain-G4's symmetric AllReduce ordering survives by timing luck.
The known ~5-line fix (per-node intranode topology objects) is Zhiyi-repo,
propose-then-approve; it would also RETIME every multi-domain number (G2-G4,
H4, and the original C1-C4), so it is a coordinated decision, not a patch.

Until then: SPG4 inc = STALLED in results; the completed multi-domain rows
(G2/G3/G4/H4) carry the aliasing threat as their documented caveat (32/32
collectives, zero drops, but cross-domain flow aliasing may distort timing).
