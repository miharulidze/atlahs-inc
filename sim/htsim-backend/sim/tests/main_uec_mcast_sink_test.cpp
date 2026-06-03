// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
//
// Unit tests for UecMcastSink (phase-two leaf-TOR sink).
// Verifies: single-op completion, wrong-type drop, multi-op
// concurrency on the same persistent (host, group) sink.

#include "uec_collectives.h"
#include "uecpacket.h"
#include "route.h"

#include <cstdio>

namespace {

// Test-friendly accessor on top of UecMcastSink: peek at per-op
// state via friend access. We declare a thin subclass that exposes
// the relevant fields rather than reaching into the protected
// UecCollectiveSink::_per_op directly from main().
class TestableMcastSink : public UecMcastSink {
  public:
    TestableMcastSink(int host, uint32_t group)
            : UecMcastSink(host, group) {}

    bool completed_for(uint32_t op_id) const {
        auto it = _per_op.find(op_id);
        return it != _per_op.end() && it->second.completed;
    }
    uint64_t bytes_for(uint32_t op_id) const {
        auto it = _per_op.find(op_id);
        return it != _per_op.end() ? it->second.bytes_received : 0;
    }
};

Route &empty_route() {
    static Route r;
    return r;
}

PacketFlow &flow_for_op(uint32_t op_id) {
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

// 1. Single op completes when expected bytes arrive.
int test_single_op_completion() {
    TestableMcastSink sink(/*host=*/0, /*group=*/7);
    sink.register_op(/*op_id=*/100,
                     /*expected=*/4096 + UecMcastPacket::acksize,
                     /*end_trigger=*/nullptr);

    UecMcastPacket *p = UecMcastPacket::newpkt(flow_for_op(100),
                                               empty_route(),
                                               1, 4096, 7, 12);
    sink.receivePacket(*p);

    ASSERT(sink.completed_for(100), "op 100 completed");
    return 0;
}

// 2. Wrong packet type (UEC, not UEC_MCAST) is dropped without
//    affecting the per-op state.
int test_wrong_packet_type_dropped() {
    TestableMcastSink sink(/*host=*/0, /*group=*/7);
    sink.register_op(/*op_id=*/100, /*expected=*/4160,
                     /*end_trigger=*/nullptr);

    UecPacket *p = UecPacket::newpkt(flow_for_op(100), empty_route(),
                                     1, 0, 4096, false, 0);
    sink.receivePacket(*p);

    ASSERT(!sink.completed_for(100),
           "wrong-type packet does not advance op state");
    ASSERT(sink.bytes_for(100) == 0, "no bytes counted");
    return 0;
}

// 3. Two concurrent ops on the same (host, group) sink with
//    distinct op_flow_ids do not contaminate each other.
int test_multi_op_concurrent() {
    TestableMcastSink sink(/*host=*/0, /*group=*/7);
    sink.register_op(7, 4160, nullptr);
    sink.register_op(9, 4160, nullptr);

    UecMcastPacket *p7 = UecMcastPacket::newpkt(flow_for_op(7),
                                                empty_route(),
                                                1, 4096, 7, 12);
    sink.receivePacket(*p7);

    ASSERT(sink.completed_for(7), "op 7 completes");
    ASSERT(!sink.completed_for(9), "op 9 unaffected");
    ASSERT(sink.bytes_for(9) == 0, "op 9 byte count zero");
    return 0;
}

}  // namespace

int main() {
    int failed = 0;
    struct {
        const char *name;
        int (*fn)();
    } tests[] = {
            {"single_op_completion",       test_single_op_completion},
            {"wrong_packet_type_dropped",  test_wrong_packet_type_dropped},
            {"multi_op_concurrent",        test_multi_op_concurrent},
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
