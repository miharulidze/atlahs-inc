// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-
#ifndef UEC_COLLECTIVE_H
#define UEC_COLLECTIVE_H

// Operation-agnostic base classes for the phase-two (and phase-three)
// collective endpoints. The shared machinery --- ACK-less single-shot
// emission, per-operation state map, completion-trigger plumbing ---
// lives here. Concrete sub-classes (UecBcastSrcMcast, UecMcastSink in
// phase 2; UecReduceSrc, UecReduceSink in phase 3) only override the
// packet-type-specific bits.
//
// See AA-plan-Phase2/v4.md §3.7.

#include "trigger.h"
#include "uec.h"

#include <cstdint>
#include <unordered_map>

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

// One sink per (host, group). Persistent for the simulation lifetime
// (created at set_up_mcast time). Per-operation expectations are
// registered incrementally via register_op(); the receivePacket
// shell type-checks, looks up per-op state, delegates to the
// subclass for body interpretation, and fires the end trigger when
// the byte count reaches the expected size.
class UecCollectiveSink : public UecSink {
  public:
    UecCollectiveSink(int host_addr, uint32_t group_id)
            : UecSink(), _host_addr(host_addr), _group_id(group_id) {}

    struct OpState {
        uint64_t bytes_received = 0;
        uint64_t expected_bytes = 0;
        Trigger *end_trigger    = nullptr;
        bool     completed      = false;
    };

    // Driver registration. Idempotent: a second call with the same
    // op_flow_id overwrites the prior state, useful when re-running
    // .cm files in a single binary invocation.
    void register_op(uint32_t op_flow_id, uint64_t expected_bytes,
                     Trigger *end_trigger) {
        OpState s;
        s.bytes_received = 0;
        s.expected_bytes = expected_bytes;
        s.end_trigger    = end_trigger;
        s.completed      = false;
        _per_op[op_flow_id] = s;
    }

    void receivePacket(Packet &pkt) override {
        if (!accepts_packet_type(pkt))     { pkt.free(); return; }
        if (pkt.header_only())             { pkt.free(); return; }

        auto it = _per_op.find(pkt.flow_id());
        if (it == _per_op.end())           { pkt.free(); return; }
        OpState &s = it->second;

        // Subclass updates s.bytes_received and frees pkt.
        process_body(pkt, s);

        if (!s.completed && s.bytes_received >= s.expected_bytes) {
            s.completed = true;
            if (s.end_trigger) s.end_trigger->activate();
        }
    }

    int      host_addr() const { return _host_addr; }
    uint32_t group_id()  const { return _group_id; }

  protected:
    // Subclass: which packet type does this sink accept?
    virtual bool accepts_packet_type(const Packet &pkt) const = 0;

    // Subclass: update s.bytes_received from the packet body and
    // call pkt.free().
    virtual void process_body(Packet &pkt, OpState &s) = 0;

    int      _host_addr;
    uint32_t _group_id;
    std::unordered_map<uint32_t, OpState> _per_op;
};

#endif
