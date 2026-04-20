// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-
#ifndef UEC_BCAST_H
#define UEC_BCAST_H

#include "trigger.h"
#include "uec.h"

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

#endif
