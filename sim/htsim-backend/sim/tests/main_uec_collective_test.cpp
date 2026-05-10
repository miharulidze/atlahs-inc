// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
//
// Unit tests for the UecCollectiveSrc / UecCollectiveSink base
// classes (phase-two endpoints). Drives the sink shell with a stub
// subclass and verifies per-op state isolation, completion firing,
// and unknown-op drop semantics.
//
// We avoid relying on EventList::triggerIsPending semantics by
// observing the sink's per-op completed flag directly through a
// small accessor on the test subclass. A single shared EventList
// is used because htsim asserts there is only one instance per
// process.

#include "eventlist.h"
#include "trigger.h"
#include "uec_collective.h"
#include "uecpacket.h"
#include "route.h"

#include <cstdio>

namespace {

// Stub sink: accepts UEC_MCAST packets, increments byte count by
// the packet's data_packet_size(), and exposes the completed flag
// for test inspection.
class StubSink : public UecCollectiveSink {
  public:
    StubSink(int host, uint32_t group)
            : UecCollectiveSink(host, group) {}

    bool completed_for(uint32_t op_flow_id) const {
        auto it = _per_op.find(op_flow_id);
        return it != _per_op.end() && it->second.completed;
    }
    uint64_t bytes_for(uint32_t op_flow_id) const {
        auto it = _per_op.find(op_flow_id);
        return it != _per_op.end() ? it->second.bytes_received : 0;
    }
    bool has_op(uint32_t op_flow_id) const {
        return _per_op.find(op_flow_id) != _per_op.end();
    }

  protected:
    bool accepts_packet_type(const Packet &pkt) const override {
        return pkt.type() == UEC_MCAST;
    }
    void process_body(Packet &pkt, OpState &s) override {
        s.bytes_received += pkt.size();
        pkt.free();
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

// 1. Register one op, deliver expected bytes; completion flag set.
int test_register_op_then_complete() {
    StubSink sink(/*host=*/0, /*group=*/7);
    sink.register_op(/*op_flow_id=*/100,
                     /*expected_bytes=*/4096 + 64,
                     /*end_trigger=*/nullptr);

    UecMcastPacket *p = UecMcastPacket::newpkt(flow_for_op(100),
                                               empty_route(),
                                               /*seqno=*/1, /*size=*/4096,
                                               /*group_id=*/7,
                                               /*source_host=*/12);
    sink.receivePacket(*p);

    ASSERT(sink.completed_for(100), "op completes after expected bytes");
    return 0;
}

// 2. Register two ops; complete one. The other must not complete.
int test_multi_op_isolation() {
    StubSink sink(/*host=*/0, /*group=*/7);
    sink.register_op(100, 4160, nullptr);
    sink.register_op(200, 4160, nullptr);

    UecMcastPacket *p = UecMcastPacket::newpkt(flow_for_op(100),
                                               empty_route(),
                                               1, 4096, 7, 12);
    sink.receivePacket(*p);

    ASSERT(sink.completed_for(100), "op 100 completes");
    ASSERT(!sink.completed_for(200), "op 200 does not complete");
    ASSERT(sink.bytes_for(200) == 0, "op 200 byte count unchanged");
    return 0;
}

// 3. Deliver a packet for an unregistered op. No completion, no
//    crash; the packet is freed.
int test_unknown_op_drop() {
    StubSink sink(/*host=*/0, /*group=*/7);
    sink.register_op(100, 4160, nullptr);

    UecMcastPacket *p = UecMcastPacket::newpkt(flow_for_op(999),
                                               empty_route(),
                                               1, 4096, 7, 12);
    sink.receivePacket(*p);

    ASSERT(!sink.completed_for(100), "registered op not affected");
    ASSERT(!sink.has_op(999), "unknown op not registered as side effect");
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
