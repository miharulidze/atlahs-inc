// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-
#include "uec_bcast.h"
#include "uecpacket.h"
#include <iostream>

UecBcastSrc::UecBcastSrc(UecLogger *logger, TrafficLogger *pktLogger,
                         EventList &eventList, uint64_t rtt, uint64_t bdp,
                         uint64_t queueDrainTime, int hops)
        : UecSrc(logger, pktLogger, eventList, rtt, bdp, queueDrainTime, hops) {}

void UecBcastSrc::doNextEvent() { bcast_send_once(); }

void UecBcastSrc::activate() { bcast_send_once(); }

void UecBcastSrc::receivePacket(Packet &pkt) { pkt.free(); }

// Emit ceil(_flow_size / _mss) data packets back-to-back through the
// configured entropy-selected ECMP path. No CWND gating, no _sent_packets
// bookkeeping, no RTO — those are reliability mechanisms that assumption
// (2) makes unnecessary and that have no correct behaviour without ACKs.
void UecBcastSrc::bcast_send_once() {
    if (_sent_once) return;
    _sent_once = true;

    _flow_start_time = eventlist().now();
    // NOTE: This loop iterates exactly once if we satisfy our assumption that payload < MSS
    while (_highest_sent < _flow_size) {
        UecPacket *p = UecPacket::newpkt(_flow, *_route, _highest_sent + 1,
                                         /*dataseqno=*/0, _mss,
                                         /*retransmitted=*/false, _dstaddr);
        p->set_route(*_route);
        int crt = choose_route();
        // ECMP_FIB: cycle through path_ids then at switch the path_id is
        // hashed to generate a port output (among possible ECMP ports)
        // TODO: imagine Bcast groupsize = # ECMP paths
        // TODO: Then when we send several Bcasts we always route the same (not too important)
        p->set_pathid(_path_ids[crt]);
        p->from = this->from;
        p->to = this->to;
        p->tag = this->tag;
        p->timestamp_sent = eventlist().now();

        _highest_sent += _mss;
        _packets_sent += _mss;

        p->sendOn();
    }
}

// Phase-two multicast source: emit exactly one UecMcastPacket per
// operation, seeded with group_id and the source host's address
// for the path-derived _pathid running hash.
void UecBcastSrcMcast::emit_once() {
    if (_sent_once) return;
    mark_sent();
    while (_highest_sent < _flow_size) {
        UecMcastPacket *p = UecMcastPacket::newpkt(
                _flow, *_route,
                /*seqno=*/_highest_sent + 1, /*size=*/_mss,
                _group_id,
                /*source_host_id=*/static_cast<uint32_t>(this->from),
                /*op_seq_id=*/0);
        p->from = this->from;
        p->to   = this->to;
        p->tag  = this->tag;
        p->timestamp_sent = eventlist().now();

        _highest_sent  += _mss;
        _packets_sent  += _mss;

        p->sendOn();
    }
}

UecBcastSink::UecBcastSink() : UecSink() { _nodename = "uec_bcast_sink"; }

// No ACK, no NACK. Count bytes; fire _end_trigger exactly once when the
// payload is complete. _end_trigger is normally a BarrierTrigger shared
// by all legs of the broadcast.
void UecBcastSink::receivePacket(Packet &pkt) {
    if (pkt.type() != UEC) {
        // ACK or NACK arriving at a bcast sink is unexpected under the
        // phase-one assumptions; drop defensively rather than aborting.
        pkt.free();
        return;
    }

    // Under assumption (3) switches never trim. If a trimmed header
    // somehow reaches us, drop it rather than counting it as payload.
    if (pkt.header_only()) {
        pkt.free();
        return;
    }

    UecPacket *p = dynamic_cast<UecPacket *>(&pkt);
    int size = p->data_packet_size();
    _bytes_received += size;
    p->free();

    if (!_completed && _bytes_received >= _expected_bytes) {
        _completed = true;
        if (_end_trigger) _end_trigger->activate();
    }
}

void BcastCompletionRecorder::activate() {
    simtime_picosec now = _eventlist.now();
    simtime_picosec dt = (now > _start) ? (now - _start) : 0;
    std::cout << "BCAST_COMPLETE"
              << " op_id=" << _op_id
              << " root=" << _root
              << " group=" << _group_idx
              << " size=" << _size
              << " legs=" << _leg_count
              << " start_ns=" << (_start / 1000)
              << " complete_ns=" << (now / 1000)
              << " duration_ns=" << (dt / 1000)
              << std::endl;
}
