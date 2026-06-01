// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#ifndef INC_FIB_H
#define INC_FIB_H

// Per-switch INC FIB and FIB-entry shapes for phase-two multicast
// (and forward-compatible with phase-three aggregation).
//
//   INCFib       --- per-switch hash map keyed by group_id;
//                    holds one INCFibEntry per multicast group
//                    that traverses this switch.
//   INCFibEntry  --- bitmap of tree-member ports + sparse list
//                    of pre-baked routes for leaf-TOR member
//                    ports + (phase-3 only) optional toward-root
//                    port index for reduce-direction ops.
//
// See AA-plan-Phase2/v4.md §3.3 for the design rationale and
// §10 for the phase-3 forward-compatibility argument.

#include "route.h"

#include <bitset>
#include <cstdint>
#include <unordered_map>
#include <utility>
#include <vector>

class INCFibEntry {
  public:
    INCFibEntry() = default;

    // Multicast tree at this switch. Bit i set ↔ Switch::_ports[i]
    // is in this group's tree. Width 128 covers htsim's per-switch
    // port limit (≤ 96 on TORs, ≤ 64 elsewhere) with margin.
    std::bitset<128> tree_port_mask;

    // For leaf-TOR member ports: pre-baked route ending at the
    // (host, group)'s UecMcastSink. Sparse — typically one entry
    // per local member host. Empty at interior switches (where
    // FatTreeSwitch::_port_egress_routes provides the route
    // instead).
    std::vector<std::pair<uint8_t, Route *>> leaf_routes;

    // PHASE 3 ONLY: which port leads "toward root" for a reduce-
    // direction op. nullopt at the root switch and at switches
    // that don't participate in reduce ops. Phase-2 install leaves
    // this nullopt unconditionally; phase-three set_up_inc will
    // populate it per (group, reduction-root) pair.
    //
    // We don't pull in <optional> here to keep the header lean;
    // a plain int with -1 sentinel works the same and avoids
    // forcing C++17 into headers that include this one.
    int root_port_idx_or_neg1 = -1;

    // PHASE 3 (reduce/allreduce): how many downstream contributions
    // this switch must collect before it emits one combined packet
    // toward the root (fan-in barrier size). For a leaf TOR this is the
    // number of local member hosts; for an interior switch it is the
    // count of downstream tree ports (child switches). Zero for phase-2
    // multicast-only entries. Set by set_up_reduce / set_up_allreduce.
    int expected_children = 0;

    // PHASE 3 (apex only): which group's tree to fan the aggregate down
    // when this switch is the apex (root_port == -1). -1 (default) means
    // "use my own group" --- Allreduce, where the descent tree IS the
    // ascent tree (fan to all members). Rooted Reduce sets this to a
    // synthetic single-member descent group so the apex delivers only to
    // the reduction root R. Ignored at non-apex switches.
    int turnaround_group_or_neg1 = -1;

    // Look up the leaf-TOR member route for a given port index, or
    // nullptr if the port is interior at this switch. O(N) over a
    // sparse list; N ≤ K/2 for any leaf TOR. Negligible.
    Route *leaf_route_for(uint8_t port_idx) const {
        for (auto &p : leaf_routes) {
            if (p.first == port_idx) return p.second;
        }
        return nullptr;
    }
};

class INCFib {
  public:
    INCFib() = default;

    // Returns the entry for the given group, or nullptr if
    // this switch holds no state for that group.
    INCFibEntry *lookup(uint32_t group_id) const {
        auto it = _entries.find(group_id);
        return it == _entries.end() ? nullptr : it->second;
    }

    // Replaces any existing entry for group_id without freeing
    // it (caller-managed lifecycle; the topology owns these).
    void install(uint32_t group_id, INCFibEntry *entry) {
        _entries[group_id] = entry;
    }

  private:
    std::unordered_map<uint32_t, INCFibEntry *> _entries;
};

#endif
