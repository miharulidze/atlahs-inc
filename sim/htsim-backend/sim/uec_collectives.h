// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-
#ifndef UEC_COLLECTIVES_H
#define UEC_COLLECTIVES_H

// UEC collective endpoints. UecCollectiveSrc is the operation-agnostic
// source base (ACK-less single-shot emission); UecCollectiveSink is THE
// collective endpoint object --- one persistent sink per (host, group),
// holding per-operation state for both packet kinds (mcast / reduce).
// Below them: the concrete per-operation sources and the completion
// recorders for Broadcast (unicast-leg emulation + switch multicast),
// Reduce, Allreduce, and Reduce-Scatter. Formerly split across
// uec_collective.h + uec_bcast.{h,cpp}.

#include "trigger.h"
#include "uec.h"

#include <cstdint>
#include <unordered_map>
#include <vector>

// ===========================================================================
// Operation-agnostic shared machinery: the source base class (concrete
// sources below override emit_once) and the per-(host, group) collective
// sink endpoint.
// ===========================================================================

// One source per collective operation. Single-MTU emission, no CWND
// gating, no retransmission, no ACK processing. Subclasses override
// emit_once() to allocate-and-send the operation-specific packet
// type.
class UecCollectiveSrc : public UecSrc {
  public:
    UecCollectiveSrc(UecLogger *logger, TrafficLogger *pktLogger,
                     EventList &eventList, uint64_t rtt, uint64_t bdp,
                     uint64_t queueDrainTime, int hops)
            : UecSrc(logger, pktLogger, eventList, rtt, bdp,
                     queueDrainTime, hops) {}

    // EventSource hook. Fires from sourceIsPending() at start time.
    void doNextEvent() override { emit_once(); }

    // TriggerTarget hook. Fires when a trigger-started op begins.
    void activate() override { emit_once(); }

    // ACK-less flow: drop any return packet handed back.
    void receivePacket(Packet &pkt) override { pkt.free(); }

    void set_group_id(uint32_t g) { _group_id = g; }
    uint32_t group_id() const { return _group_id; }

    // Ack-less wiring for collective sources: stores the forward route,
    // names the flow, and schedules emission at starttime. No sink, no
    // routeback --- collective sources never receive return traffic.
    void connect_collective(Route *routeout, simtime_picosec starttime) {
        if (_route_strategy == SINGLE_PATH || _route_strategy == ECMP_FIB ||
            _route_strategy == ECMP_FIB_ECN || _route_strategy == REACTIVE_ECN) {
            assert(routeout);
            _route = routeout;
        }
        _flow.set_id(get_id());
        _flow._name = _name;
        eventlist().sourceIsPending(*this, starttime);
    }

  protected:
    // Subclass: allocate one operation-specific packet, populate
    // metadata, and send. Must check _sent_once and call mark_sent()
    // exactly once per source.
    virtual void emit_once() = 0;

    // Helper: mark the source as having emitted (idempotent) and
    // record the operation's start time.
    void mark_sent() {
        if (_sent_once) return;
        _sent_once = true;
        _flow_start_time = eventlist().now();
    }

    uint32_t _group_id  = 0;
    bool     _sent_once = false;
};

// THE collective endpoint: one persistent sink per (host, group) member,
// created by FatTreeTopology::set_up_mcast for every member of every
// group and baked into the member ToR's leaf_routes. It serves every
// operation on its group for the simulation lifetime.
//
// Per-operation expectations are registered incrementally, keyed by the
// op's flow id, into one of two kind-specific maps: mcast ops (bcast
// descents, Allreduce turn-around descents; UEC_MCAST) and reduce ops
// (rooted-Reduce results, Reduce-Scatter blocks; UEC_REDUCE). Two maps
// rather than one preserve the former two-class semantics exactly: a
// packet can only ever count against a registration of its own kind.
// Within a map, flow ids isolate concurrent operations on the same
// group.
//
// Collective sinks are ACK-less, so no per-flow protocol state exists
// and one object per (host, group) suffices; the former per-op
// UecReduceSink allocation was an inheritance from the ACK-based
// unicast transport (see AA-plan-CollectiveSinkMerge/plan.md).
class UecCollectiveSink : public UecSink {
  public:
    UecCollectiveSink(int host_addr, uint32_t group_id)
            : UecSink(), _host_addr(host_addr), _group_id(group_id) {
        _nodename = "uec_collective_sink";
    }

    struct OpState {
        uint64_t bytes_received = 0;
        uint64_t expected_bytes = 0;
        Trigger *end_trigger    = nullptr;
        bool     completed      = false;
    };

    // Driver registration, one call per (operation, member). Idempotent:
    // a second call with the same op_flow_id overwrites the prior state,
    // useful when re-running .cm files in a single binary invocation.
    void register_mcast_op(uint32_t op_flow_id, uint64_t expected_bytes,
                           Trigger *end_trigger) {
        register_op_in(_op_state_mcast, op_flow_id, expected_bytes,
                       end_trigger);
    }
    void register_reduce_op(uint32_t op_flow_id, uint64_t expected_bytes,
                            Trigger *end_trigger) {
        register_op_in(_op_state_reduce, op_flow_id, expected_bytes,
                       end_trigger);
    }

    void receivePacket(Packet &pkt) override {
        // Kind dispatch: a packet can only count against a registration
        // of its own kind. Anything else (ACKs, stray unicast) is freed.
        std::unordered_map<uint32_t, OpState> *ops;
        int header_bytes;
        switch (pkt.type()) {
        case UEC_MCAST:  ops = &_op_state_mcast;  header_bytes = UecMcastPacket::acksize;  break;
        case UEC_REDUCE: ops = &_op_state_reduce; header_bytes = UecReducePacket::acksize; break;
        default:         pkt.free(); return;
        }
        if (pkt.header_only())     { pkt.free(); return; }

        auto it = ops->find(pkt.flow_id());
        if (it == ops->end())      { pkt.free(); return; }
        OpState &s = it->second;

        // Count PAYLOAD, not wire size. pkt.size() is payload + header
        // (acksize); every caller registers expected_bytes in payload units,
        // so counting wire bytes over-counts by acksize per packet. For
        // single-stream collectives (bcast/allreduce/reduce) that only shifts
        // completion within a sub-packet, but AllGather sums |G|-1 INDEPENDENT
        // peer streams into one counter, where the accumulated per-packet
        // surplus can fire the barrier before the last peer's block arrives
        // (triggers once (|G|-2)*acksize >= MTU). Subtracting the header makes
        // the byte count match payload-unit expected exactly.
        s.bytes_received += pkt.size() - header_bytes;
        pkt.free();

        if (!s.completed && s.bytes_received >= s.expected_bytes) {
            s.completed = true;
            if (s.end_trigger) s.end_trigger->activate();
        }
    }

    int      host_addr() const { return _host_addr; }
    uint32_t group_id()  const { return _group_id; }

  protected:
    static void register_op_in(std::unordered_map<uint32_t, OpState> &ops,
                               uint32_t op_flow_id, uint64_t expected_bytes,
                               Trigger *end_trigger) {
        OpState s;
        s.expected_bytes = expected_bytes;
        s.end_trigger    = end_trigger;
        ops[op_flow_id] = s;
    }

    int      _host_addr;
    uint32_t _group_id;
    std::unordered_map<uint32_t, OpState> _op_state_mcast;
    std::unordered_map<uint32_t, OpState> _op_state_reduce;
};

// ===========================================================================
// Concrete per-operation endpoints.
// ===========================================================================

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

// Phase-three reduce source. One per group member; emits exactly one
// UecReducePacket per operation, addressed by group_id and routed UP
// the tree by the per-switch INCFib. The fan-in barrier and combine
// happen in the switches (FatTreeSwitch::handle_reduce); this source
// is just an ACK-less single-shot emitter, identical in shape to
// UecBcastSrcMcast but with the reduce packet type and upward route.
class UecReduceSrc : public UecCollectiveSrc {
  public:
    UecReduceSrc(UecLogger *logger, TrafficLogger *pktLogger,
                 EventList &eventList, uint64_t rtt, uint64_t bdp,
                 uint64_t queueDrainTime, int hops)
            : UecCollectiveSrc(logger, pktLogger, eventList, rtt, bdp,
                               queueDrainTime, hops) {}

    // Operation kind for this source's contributions: -1 = Allreduce
    // (apex fans the result to all members), >= 0 = rooted Reduce to that
    // host. Carried on every emitted packet so the apex needs no per-group
    // state. Default -1 (Allreduce).
    void set_reduce_root(int root) { _reduce_root = root; }

    // Reduce-Scatter: per-packet (per-block) root assignment. The per-rank
    // vector is partitioned into equal blocks; block_owner_hosts[i] is the
    // host that owns block i, and block_bytes is the per-block size. Once
    // configured, emit_once stamps every packet's reduce_root with the owner
    // of the block its byte-offset falls in, overriding _reduce_root. Every
    // member runs the identical mapping, so all contributions to a given
    // chunk (seqno) agree on its root and the apex needs no per-group state.
    // block_bytes must be a multiple of the MTU so no packet straddles a
    // block boundary.
    void set_reduce_scatter(const std::vector<int> &block_owner_hosts,
                            uint64_t block_bytes) {
        _rs_owners = block_owner_hosts;
        _rs_block_bytes = block_bytes;
        _is_reduce_scatter = true;
    }

  protected:
    void emit_once() override;
    int _reduce_root = -1;

    bool _is_reduce_scatter = false;
    std::vector<int> _rs_owners;   // block index -> owner host id
    uint64_t _rs_block_bytes = 0;  // per-block size in bytes
};

// Records collective-completion timestamps for a single collective
// operation (broadcast, reduce, allreduce, reduce-scatter, allgather).
// Attached as a TriggerTarget to the operation's BarrierTrigger so it
// fires exactly once when the barrier saturates -- i.e. the last leg /
// member / root result is delivered. Emits one machine-parseable line to
// stdout that downstream plotting and test scripts grep for.
//
// Output format (single line, space-separated key=value):
//   <label>_COMPLETE op_id=<id> root=<node> group=<idx> size=<bytes>
//   <count_field>=<n> start_ns=<t0> complete_ns=<t1> duration_ns=<dt>
//
// `label` is the collective name in upper case: BCAST, REDUCE, ALLREDUCE,
// REDUCE_SCATTER, ALLGATHER (or ALLREDUCE_RB for the reduce+bcast .cm
// path). `count_field` names the reported cardinality: "legs" for
// broadcast (the |G|-1 receiving legs; the root does not receive) or
// "members" for the aggregation / gather family (the full group |G|).
// Both `label` and `count_field` must be string literals -- they are
// stored by pointer, never copied.
//
// Who fires the barrier varies by collective: bcast on the last of |G|-1
// legs (fan-out); rooted reduce on the single root sink's trigger
// (fan-in); allreduce / reduce-scatter / allgather on all |G| members
// receiving their result. This class only formats the resulting line;
// the BarrierTrigger count is set by the caller.
//
// Times are in nanoseconds; sim time is internally picoseconds so we
// divide by 1000. start_ns is the scheduled start time for the operation;
// complete_ns is the sim time at which the barrier fired.
class CollectiveCompletionRecorder : public TriggerTarget {
  public:
    CollectiveCompletionRecorder(EventList &eventlist, const char *label,
                                 const char *count_field, flowid_t op_id,
                                 int root, int group_idx, int payload_bytes,
                                 size_t count, simtime_picosec scheduled_start)
            : _eventlist(eventlist), _label(label), _count_field(count_field),
              _op_id(op_id), _root(root), _group_idx(group_idx),
              _size(payload_bytes), _count(count), _start(scheduled_start) {}

    void activate() override;

  private:
    EventList &_eventlist;
    const char *_label;
    const char *_count_field;
    flowid_t _op_id;
    int _root;
    int _group_idx;
    int _size;
    size_t _count;
    simtime_picosec _start;
};

#endif
