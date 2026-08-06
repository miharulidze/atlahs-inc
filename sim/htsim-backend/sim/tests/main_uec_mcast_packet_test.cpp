// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
//
// Unit tests for UecMcastPacket (phase-two INC).
//
// Each test_* function returns 0 on success, 1 on failure. main()
// runs them all and exits 0 iff every test passes. No event-list,
// no topology --- just construct packets and check their fields.

#include "network.h"
#include "uecpacket.h"
#include "route.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace {

// Empty-route helper: factories want a const Route&. They store a
// pointer to it via set_route, but we never actually walk it in
// this unit test, so an empty Route is sufficient.
Route &empty_route() {
    static Route r;
    return r;
}

PacketFlow &test_flow() {
    static PacketFlow f(nullptr);
    return f;
}

#define ASSERT_EQ(a, b, label)                                                 \
    do {                                                                       \
        auto _av = (a);                                                        \
        auto _bv = (b);                                                        \
        if (_av != _bv) {                                                      \
            std::fprintf(stderr,                                               \
                         "FAIL  %s: expected %llu, got %llu  [%s line %d]\n",  \
                         label, (unsigned long long)_bv,                       \
                         (unsigned long long)_av, __FILE__, __LINE__);         \
            return 1;                                                          \
        }                                                                      \
    } while (0)

// 1. Alloc / free round-trip via PacketDB.
int test_alloc_free_roundtrip() {
    UecMcastPacket *p1 = UecMcastPacket::newpkt(test_flow(), empty_route(),
                                                /*seqno=*/1, /*size=*/4096,
                                                /*group_id=*/7,
                                                /*source_host=*/3);
    ASSERT_EQ(p1->type(), UEC_MCAST, "type tag");
    ASSERT_EQ(p1->group_id(), 7u, "group_id");
    p1->free();

    UecMcastPacket *p2 = UecMcastPacket::newpkt(test_flow(), empty_route(),
                                                /*seqno=*/1, /*size=*/4096,
                                                /*group_id=*/8,
                                                /*source_host=*/4);
    // PacketDB recycles via freelist; not asserting same pointer (the
    // implementation could change), just that the new alloc is fresh
    // and clean.
    ASSERT_EQ(p2->group_id(), 8u, "recycled allocator returns clean state");
    p2->free();
    return 0;
}

// 2. Source-side path-hash seed: (group ^ source) * 2654435761.
int test_pathid_source_seed() {
    uint32_t group = 5, host = 42;
    UecMcastPacket *p = UecMcastPacket::newpkt(test_flow(), empty_route(),
                                               /*seqno=*/1, /*size=*/4096,
                                               group, host);
    uint32_t expected = (group ^ host) * UecMcastPacket::PATHID_SEED_MIX;
    ASSERT_EQ(p->pathid(), expected, "source-seed pathid");
    p->free();
    return 0;
}

// 3. Replica chain: child = parent * 31 + egress_idx + 1.
int test_pathid_replica_chain() {
    UecMcastPacket *src = UecMcastPacket::newpkt(test_flow(), empty_route(),
                                                 /*seqno=*/1, /*size=*/4096,
                                                 /*group_id=*/9,
                                                 /*source_host=*/2);
    uint32_t S = src->pathid();
    UecMcastPacket *r = UecMcastPacket::newpkt_replica(*src, empty_route(),
                                                       /*egress_port_idx=*/7);
    uint32_t expected = S * UecMcastPacket::PATHID_HOP_MIX + 7u + 1u;
    ASSERT_EQ(r->pathid(), expected, "single-hop replica pathid");
    src->free();
    r->free();
    return 0;
}

// 4. Two-hop match: hand-build a chain of two replicas, compute the
//    expected leaf pathid manually, and assert equality.
int test_pathid_two_hop_match() {
    UecMcastPacket *src = UecMcastPacket::newpkt(test_flow(), empty_route(),
                                                 /*seqno=*/1, /*size=*/4096,
                                                 /*group_id=*/13,
                                                 /*source_host=*/100);
    uint32_t S = src->pathid();
    UecMcastPacket *hop1 =
            UecMcastPacket::newpkt_replica(*src, empty_route(), 5);
    UecMcastPacket *hop2 =
            UecMcastPacket::newpkt_replica(*hop1, empty_route(), 2);
    uint32_t expected = ((S * 31u + 5u + 1u) * 31u + 2u + 1u);
    ASSERT_EQ(hop2->pathid(), expected, "two-hop pathid");
    src->free();
    hop1->free();
    hop2->free();
    return 0;
}

// 5. Cross-op stability: same (group, source, port-sequence) →
//    same pathid, even across distinct source-packet allocations.
//    This is the headline §3.2.1 property.
int test_pathid_cross_op_stability() {
    // Two source packets with the same (group, source_host).
    UecMcastPacket *s1 = UecMcastPacket::newpkt(test_flow(), empty_route(),
                                                /*seqno=*/1, /*size=*/4096,
                                                /*group_id=*/77,
                                                /*source_host=*/12);
    UecMcastPacket *s2 = UecMcastPacket::newpkt(test_flow(), empty_route(),
                                                /*seqno=*/1, /*size=*/4096,
                                                /*group_id=*/77,
                                                /*source_host=*/12);
    ASSERT_EQ(s1->pathid(), s2->pathid(), "source-side identical");

    // Trace each through the same two-hop fanout (ports 4, 9).
    UecMcastPacket *a1 = UecMcastPacket::newpkt_replica(*s1, empty_route(), 4);
    UecMcastPacket *a2 = UecMcastPacket::newpkt_replica(*a1, empty_route(), 9);
    UecMcastPacket *b1 = UecMcastPacket::newpkt_replica(*s2, empty_route(), 4);
    UecMcastPacket *b2 = UecMcastPacket::newpkt_replica(*b1, empty_route(), 9);
    ASSERT_EQ(a2->pathid(), b2->pathid(),
              "leaf pathid identical across operations");

    // Sanity: a different egress sequence yields a different pathid.
    UecMcastPacket *c1 = UecMcastPacket::newpkt_replica(*s1, empty_route(), 4);
    UecMcastPacket *c2 = UecMcastPacket::newpkt_replica(*c1, empty_route(), 8);
    if (a2->pathid() == c2->pathid()) {
        std::fprintf(stderr,
                     "FAIL  different port sequences should produce "
                     "different pathids\n");
        return 1;
    }

    s1->free(); s2->free();
    a1->free(); a2->free();
    b1->free(); b2->free();
    c1->free(); c2->free();
    return 0;
}

// 6. Wire size includes header overhead (acksize). Queue serialisation
//    latency reads pkt.size() and must include the 64-byte header so
//    multicast packets account for the same on-wire bytes UecPacket
//    does. Replica inherits the wire size via source.size().
int test_wire_size_includes_acksize() {
    UecMcastPacket *p = UecMcastPacket::newpkt(test_flow(), empty_route(),
                                               /*seqno=*/1, /*size=*/4096,
                                               /*group_id=*/1,
                                               /*source_host=*/0);
    ASSERT_EQ(p->size(),
              static_cast<unsigned>(4096 + UecMcastPacket::acksize),
              "source-side wire size");
    UecMcastPacket *r =
            UecMcastPacket::newpkt_replica(*p, empty_route(), 0);
    ASSERT_EQ(r->size(), p->size(), "replica inherits wire size");
    p->free();
    r->free();
    return 0;
}

}  // namespace

int main() {
    int failed = 0;
    struct {
        const char *name;
        int (*fn)();
    } tests[] = {
            {"alloc_free_roundtrip",        test_alloc_free_roundtrip},
            {"pathid_source_seed",          test_pathid_source_seed},
            {"pathid_replica_chain",        test_pathid_replica_chain},
            {"pathid_two_hop_match",        test_pathid_two_hop_match},
            {"pathid_cross_op_stability",   test_pathid_cross_op_stability},
            {"wire_size_includes_acksize",  test_wire_size_includes_acksize},
    };
    for (auto &t : tests) {
        if (t.fn() == 0) {
            std::printf("  PASS  %s\n", t.name);
        } else {
            std::printf("  FAIL  %s\n", t.name);
            ++failed;
        }
    }
    int total = static_cast<int>(sizeof(tests) / sizeof(tests[0]));
    std::printf("\n%d/%d passed, %d failed\n", total - failed, total, failed);
    return failed == 0 ? 0 : 1;
}
