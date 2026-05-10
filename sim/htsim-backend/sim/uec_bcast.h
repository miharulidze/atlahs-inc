// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-
#ifndef UEC_BCAST_H
#define UEC_BCAST_H

#include "trigger.h"
#include "uec.h"
#include "uec_collective.h"

// ACK-less broadcast leg source.
//
// One UecBcastSrc object models a single (root -> member) unicast leg of a
// broadcast collective (MPI_Bcast). The operation's (|G|-1) legs are
// siblings scheduled independently and joined at a BarrierTrigger at their
// sinks. This class reuses UecSrc's routing, entropy, and packet-
// construction plumbing, but bypasses CWND gating, retransmission, and ACK
// processing under the phase-one assumptions (single-MTU payload, no loss,
// infinite buffers).
class UecBcastSrc : public UecSrc {
  public:
    UecBcastSrc(UecLogger *logger, TrafficLogger *pktLogger,
                EventList &eventList, uint64_t rtt, uint64_t bdp,
                uint64_t queueDrainTime, int hops);

    // EventSource hook: fires from sourceIsPending(), scheduled by
    // UecSrc::connect(). Emits the leg's data packets exactly once.
    void doNextEvent() override;

    // TriggerTarget hook: fires when a send-triggered connection starts.
    void activate() override;

    // No return traffic is expected on an ACK-less flow. Drop silently.
    void receivePacket(Packet &pkt) override;

  private:
    void bcast_send_once();
    bool _sent_once = false;
};

// ACK-less broadcast leg sink.
//
// Counts received bytes and fires _end_trigger once the full payload has
// arrived. Never emits UecAck or UecNack. The BarrierTrigger shared by all
// legs of one broadcast is the typical _end_trigger target.
class UecBcastSink : public UecSink {
  public:
    UecBcastSink();

    void receivePacket(Packet &pkt) override;

    // Set by the driver at leg setup time so the sink knows when the
    // last byte of the payload has arrived.
    void set_expected_bytes(uint64_t n) { _expected_bytes = n; }

    // Book-keeping; read by benchmark code.
    uint64_t bytes_received() const { return _bytes_received; }
    bool completed() const { return _completed; }

  private:
    uint64_t _bytes_received = 0;
    uint64_t _expected_bytes = 0;
    bool _completed = false;
};

// Trigger plumbing helpers for BarrierTrigger wiring.

// BarrierTrigger::activate asserts _targets.size() > 0 when it fires.
// When a broadcast operation has no downstream trigger to chain into, we
// attach a NoOpTriggerTarget so the assertion holds.
class NoOpTriggerTarget : public TriggerTarget {
  public:
    void activate() override {}
};

// Forwards BarrierTrigger fire into a named Trigger (e.g. the .cm
// recv_done_trigger of the broadcast operation). A TriggerTarget that
// happens to fire another Trigger.
class TriggerRelay : public TriggerTarget {
  public:
    explicit TriggerRelay(Trigger *downstream) : _downstream(downstream) {}
    void activate() override {
        if (_downstream) _downstream->activate();
    }

  private:
    Trigger *_downstream;
};

// Records collective-completion timestamps for a single broadcast
// operation. Attached as a TriggerTarget to the operation's
// BarrierTrigger so it fires exactly once when the last leg reports
// last-byte-received. Emits one machine-parseable line to stdout that
// a downstream plotting script can grep for.
//
// Output format (single line, space-separated key=value):
//   BCAST_COMPLETE op_id=<id> root=<node> group=<idx> size=<bytes>
//   legs=<|G|-1> start_ns=<t0> complete_ns=<t1> duration_ns=<dt>
//
// Times are in nanoseconds; sim time is internally picoseconds so we
// divide by 1000. start_ns is the .cm-scheduled start time for the
// operation (crt->start); complete_ns is the sim time at which the
// last sink's byte was received.
// Phase-two multicast broadcast source. Emits exactly one
// UecMcastPacket per operation, addressed by group_id (no
// per-leg unicast destination). All bookkeeping --- ACK-less
// emission, single-shot guard, completion-trigger plumbing ---
// is inherited from UecCollectiveSrc; this subclass only fixes
// the packet type and seeds the path-hash from the source host
// id.
class UecBcastSrcMcast : public UecCollectiveSrc {
  public:
    UecBcastSrcMcast(UecLogger *logger, TrafficLogger *pktLogger,
                     EventList &eventList, uint64_t rtt, uint64_t bdp,
                     uint64_t queueDrainTime, int hops)
            : UecCollectiveSrc(logger, pktLogger, eventList, rtt, bdp,
                               queueDrainTime, hops) {}

  protected:
    void emit_once() override;
};

// Phase-two multicast sink. One persistent instance per (host,
// group), created by FatTreeTopology::set_up_mcast. Per-operation
// expectations are added incrementally via register_op() from
// the driver. Accepts UEC_MCAST packets only; counts bytes via
// Packet::size() (the on-wire packet size).
class UecMcastSink : public UecCollectiveSink {
  public:
    UecMcastSink(int host, uint32_t group)
            : UecCollectiveSink(host, group) {
        _nodename = "uec_mcast_sink";
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

class BcastCompletionRecorder : public TriggerTarget {
  public:
    BcastCompletionRecorder(EventList &eventlist, flowid_t op_id, int root,
                            int group_idx, int payload_bytes,
                            size_t leg_count, simtime_picosec scheduled_start)
            : _eventlist(eventlist), _op_id(op_id), _root(root),
              _group_idx(group_idx), _size(payload_bytes),
              _leg_count(leg_count), _start(scheduled_start) {}

    void activate() override;

  private:
    EventList &_eventlist;
    flowid_t _op_id;
    int _root;
    int _group_idx;
    int _size;
    size_t _leg_count;
    simtime_picosec _start;
};

#endif
