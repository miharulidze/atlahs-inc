// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
//
// Unit tests for the merged UecCollectiveSink endpoint (one persistent
// sink per (host, group), two kind-specific op-state maps). Verifies
// per-op state isolation, completion firing, unknown-op drop semantics,
// and kind isolation (a registration only counts packets of its own
// kind: mcast vs reduce).
//
// We avoid relying on EventList::triggerIsPending semantics by
// observing the sink's per-op completed flag directly through a
// small accessor subclass. A single shared EventList is used because
// htsim asserts there is only one instance per process.

#include "eventlist.h"
#include "trigger.h"
#include "uec_collectives.h"
#include "uecpacket.h"
#include "route.h"

#include <cstdio>

namespace {

// Accessor subclass: peeks at the two protected op-state maps so the
// tests can assert on per-op state without trigger plumbing.
class TestableSink : public UecCollectiveSink {
  public:
    TestableSink(int host, uint32_t group)
            : UecCollectiveSink(host, group) {}

    bool mcast_completed_for(uint32_t op_id) const {
        return completed_in(_op_state_mcast, op_id);
    }
    uint64_t mcast_bytes_for(uint32_t op_id) const {
        return bytes_in(_op_state_mcast, op_id);
    }
    bool mcast_has_op(uint32_t op_id) const {
        return _op_state_mcast.find(op_id) != _op_state_mcast.end();
    }
    bool reduce_completed_for(uint32_t op_id) const {
        return completed_in(_op_state_reduce, op_id);
    }
    uint64_t reduce_bytes_for(uint32_t op_id) const {
        return bytes_in(_op_state_reduce, op_id);
    }

  private:
    static bool completed_in(const std::unordered_map<uint32_t, OpState> &m,
                             uint32_t op_id) {
        auto it = m.find(op_id);
        return it != m.end() && it->second.completed;
    }
    static uint64_t bytes_in(const std::unordered_map<uint32_t, OpState> &m,
                             uint32_t op_id) {
        auto it = m.find(op_id);
        return it != m.end() ? it->second.bytes_received : 0;
    }
};

Route &empty_route() {
    static Route r;
    return r;
}

PacketFlow &flow_for_op(uint32_t op_id) {
    // Static so the PacketFlow outlives any packet that points at
    // it. Reset _flow_id per-call so we can synthesise different
    // ops in successive tests.
    static PacketFlow f(nullptr);
    f.set_flowid(op_id);
    return f;
}

#define ASSERT(cond, label)                                                    \
    do {                                                                       \
        if (!(cond)) {                                                         \
            std::fprintf(stderr, "FAIL  %s [%s line %d]\n", label,             \
                         __FILE__, __LINE__);                                  \
            return 1;                                                          \
        }                                                                      \
    } while (0)

// 1. Register one mcast op, deliver expected bytes; completion flag set.
int test_register_op_then_complete() {
    TestableSink sink(/*host=*/0, /*group=*/7);
    sink.register_mcast_op(/*op_flow_id=*/100,
                           /*expected_bytes=*/4096 + 64,
                           /*end_trigger=*/nullptr);

    UecMcastPacket *p = UecMcastPacket::newpkt(flow_for_op(100),
                                               empty_route(),
                                               /*seqno=*/1, /*size=*/4096,
                                               /*group_id=*/7,
                                               /*source_host=*/12);
    sink.receivePacket(*p);

    ASSERT(sink.mcast_completed_for(100), "op completes after expected bytes");
    return 0;
}

// 2. Register two ops; complete one. The other must not complete.
int test_multi_op_isolation() {
    TestableSink sink(/*host=*/0, /*group=*/7);
    sink.register_mcast_op(100, 4160, nullptr);
    sink.register_mcast_op(200, 4160, nullptr);

    UecMcastPacket *p = UecMcastPacket::newpkt(flow_for_op(100),
                                               empty_route(),
                                               1, 4096, 7, 12);
    sink.receivePacket(*p);

    ASSERT(sink.mcast_completed_for(100), "op 100 completes");
    ASSERT(!sink.mcast_completed_for(200), "op 200 does not complete");
    ASSERT(sink.mcast_bytes_for(200) == 0, "op 200 byte count unchanged");
    return 0;
}

// 3. Deliver a packet for an unregistered op. No completion, no
//    crash; the packet is freed.
int test_unknown_op_drop() {
    TestableSink sink(/*host=*/0, /*group=*/7);
    sink.register_mcast_op(100, 4160, nullptr);

    UecMcastPacket *p = UecMcastPacket::newpkt(flow_for_op(999),
                                               empty_route(),
                                               1, 4096, 7, 12);
    sink.receivePacket(*p);

    ASSERT(!sink.mcast_completed_for(100), "registered op not affected");
    ASSERT(!sink.mcast_has_op(999), "unknown op not registered as side effect");
    return 0;
}

// 4. Kind isolation: a registration only counts packets of its own
//    kind. A reduce registration must not absorb an mcast packet with
//    the same flow id, and vice versa --- this is the property the two
//    maps exist to preserve from the former two-class design.
int test_kind_isolation() {
    TestableSink sink(/*host=*/0, /*group=*/7);
    sink.register_reduce_op(100, 4160, nullptr);
    sink.register_mcast_op(200, 4160, nullptr);

    // MCAST packet carrying the reduce op's flow id: dropped.
    UecMcastPacket *m = UecMcastPacket::newpkt(flow_for_op(100),
                                               empty_route(),
                                               1, 4096, 7, 12);
    sink.receivePacket(*m);
    ASSERT(sink.reduce_bytes_for(100) == 0,
           "reduce op untouched by mcast packet");

    // REDUCE packet carrying the mcast op's flow id: dropped.
    UecReducePacket *r = UecReducePacket::newpkt(flow_for_op(200),
                                                 empty_route(),
                                                 1, 4096, 7, 12,
                                                 /*reduce_root=*/0);
    sink.receivePacket(*r);
    ASSERT(sink.mcast_bytes_for(200) == 0,
           "mcast op untouched by reduce packet");

    // Right-kind packets complete both ops.
    UecReducePacket *r2 = UecReducePacket::newpkt(flow_for_op(100),
                                                  empty_route(),
                                                  1, 4096, 7, 12, 0);
    sink.receivePacket(*r2);
    ASSERT(sink.reduce_completed_for(100), "reduce op completes via reduce pkt");

    UecMcastPacket *m2 = UecMcastPacket::newpkt(flow_for_op(200),
                                                empty_route(),
                                                1, 4096, 7, 12);
    sink.receivePacket(*m2);
    ASSERT(sink.mcast_completed_for(200), "mcast op completes via mcast pkt");
    return 0;
}

}  // namespace

int main() {
    int failed = 0;
    struct {
        const char *name;
        int (*fn)();
    } tests[] = {
            {"register_op_then_complete", test_register_op_then_complete},
            {"multi_op_isolation",        test_multi_op_isolation},
            {"unknown_op_drop",           test_unknown_op_drop},
            {"kind_isolation",            test_kind_isolation},
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
