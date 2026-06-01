// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-        
#ifndef _LOSSLESS_INPUT_QUEUE_H
#define _LOSSLESS_INPUT_QUEUE_H
#include "queue.h"
/*
 * A FIFO queue that supports PAUSE frames and lossless operation
 */

#include <list>
#include <vector>
#include <utility>
#include "config.h"
#include "eventlist.h"
#include "network.h"
#include "loggertypes.h"
#include "eth_pause_packet.h"
#include "switch.h"
#include "callback_pipe.h"

class Switch;

class LosslessInputQueue : public Queue, public VirtualQueue {
public:
    LosslessInputQueue(EventList &eventlist);
    LosslessInputQueue(EventList &eventlist,BaseQueue* peer, Switch* sw, simtime_picosec wire_latency);
    LosslessInputQueue(EventList &eventlist,BaseQueue* peer);

    virtual void receivePacket(Packet& pkt);

    void sendPause(unsigned int wait);
    virtual void completedService(Packet& pkt);

    // Release `bytes` of ingress occupancy and re-evaluate the RESUME
    // threshold. completedService() is the normal (per-packet) caller;
    // McastFanoutCredit calls this directly once a fanned-out packet's
    // last replica has drained.
    void release_bytes(mem_b bytes);

    virtual void setName(const string& name) {
        Logged::setName(name); 
        _nodename += name;
    }
    virtual string& nodename() { return _nodename; }

    enum {PAUSED,READY,PAUSE_RECEIVED};

    static uint64_t _low_threshold;
    static uint64_t _high_threshold;

private:
    int _state_recv;
    CallbackPipe* _wire;
};

// Refcounting release token for multicast fan-out under PFC.
//
// A multicast packet enters a switch at one ingress port (charging its
// LosslessInputQueue once) and is replicated into k egress copies. The
// ingress buffer occupancy is one stored copy that must persist until the
// LAST replica has been transmitted, then be released exactly once --- this
// is what keeps the upstream paused while any branch is still buffered
// (preserving losslessness) without over-counting ingress occupancy.
//
// handle_mcast() creates one credit per fanned-out packet with _pending = k
// and points every replica's ingress_queue at it. Each replica's egress
// LosslessOutputQueue calls completedService() as it drains; the k-th call
// releases the original charge from the real ingress queue and self-deletes.
class McastFanoutCredit : public VirtualQueue {
public:
    McastFanoutCredit(LosslessInputQueue* iq, mem_b size, int pending)
        : _iq(iq), _size(size), _pending(pending) {}

    virtual void completedService(Packet& pkt) {
        if (--_pending == 0) {
            _iq->release_bytes(_size);
            delete this;
        }
    }

private:
    LosslessInputQueue* _iq;
    mem_b _size;
    int _pending;
};

// Fan-in release credit for in-network aggregation under PFC --- the dual of
// McastFanoutCredit. A reduce switch absorbs k child contributions (each having
// charged its ingress LosslessInputQueue) and emits one (or, at the Allreduce
// apex, several) result packet(s). The children's ingress charges are HELD
// until the result has drained from the egress: this keeps the switch's ingress
// occupied while the uplink is paused, so the children get paused too and no
// egress queue overflows (true losslessness). The credit holds the list of
// (ingress queue, bytes) to release and a count of egress drains to wait for
// (1 for a combined-up / rooted-Reduce-down packet; k for the Allreduce apex
// fan-out, where all k replicas share one credit).
class ReduceFanInCredit : public VirtualQueue {
public:
    ReduceFanInCredit(std::vector<std::pair<LosslessInputQueue*, mem_b>> charges,
                      int pending)
        : _charges(std::move(charges)), _pending(pending) {}

    virtual void completedService(Packet& pkt) {
        if (--_pending == 0) {
            for (auto& c : _charges)
                c.first->release_bytes(c.second);
            delete this;
        }
    }

private:
    std::vector<std::pair<LosslessInputQueue*, mem_b>> _charges;
    int _pending;
};

// Zero-allocation no-op virtual queue for switch-originated lossless packets
// that carry no ingress charge to release --- the Allreduce apex turn-around
// fanout and the reduce combined-up packet. It exists only to give the egress
// LosslessOutputQueue a non-null prev to pair with; completedService does
// nothing. A single shared instance is reused for every such packet (no
// per-packet allocation), which matters on the multi-MTU streaming path.
class NoOpVirtualQueue : public VirtualQueue {
public:
    virtual void completedService(Packet& pkt) {}
    static NoOpVirtualQueue* instance() {
        static NoOpVirtualQueue inst;
        return &inst;
    }
};

#endif
