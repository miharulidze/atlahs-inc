// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-        
#ifndef PIPE_H
#define PIPE_H

/*
 * A pipe is a dumb device which simply delays all incoming packets
 */

#include <list>
#include <utility>
#include "config.h"
#include "eventlist.h"
#include "network.h"
#include "loggertypes.h"
#include "drawable.h"

typedef struct pktrecord {
    simtime_picosec time;
    Packet* pkt;
} pktrecord_t;

class Pipe : public EventSource, public PacketSink, public Drawable {
 public:
    Pipe(simtime_picosec delay, EventList& eventlist=EventList::getTheEventList());
    virtual void receivePacket(Packet& pkt); // inherited from PacketSink
    virtual void doNextEvent(); // inherited from EventSource
    simtime_picosec delay() { return _delay; }
    const string& nodename() { return _nodename; }
    void forceName(string name) {_nodename = name;}

    void setNext(PacketSink* next_sink) {
            _next_sink = next_sink;
    }
    PacketSink* next() const {
            return _next_sink;
    }

    // PT6 (post-meeting): per-pipe link-cross counter. Increments
    // once per packet the pipe ingests. Each link in the topology
    // has two pipes (one per direction); summing _packet_count
    // across all pipes gives total directional link traversals,
    // useful as a network-footprint metric complementary to
    // completion time. Static class counter aggregates the sum
    // for the headline metric without requiring iteration over
    // topology pipes at simulation end.
    uint64_t packet_count() const { return _packet_count; }
    static uint64_t total_packets() { return _total_packets; }
    static void reset_total_packets() { _total_packets = 0; }
protected:
    string _nodename;
    //typedef pair<simtime_picosec,Packet*> pktrecord_t;
    //list<pktrecord_t> _inflight; // the packets in flight (or being serialized)
    vector<pktrecord_t> _inflight_v;
    int _next_insert, _next_pop, _count, _size;
    // Whether this pipe counts toward the static link-cross total.
    // True for real topology links; CallbackPipe (the switch-latency
    // stage) sets it false so the footprint metric reports physical
    // link traversals only, not internal switch pipes.
    bool _count_in_total = true;
private:
    simtime_picosec _delay;
    PacketSink* _next_sink{nullptr}; // used in generic topology for linkage

    // PT6 link-cross counters.
    uint64_t _packet_count = 0;
    static uint64_t _total_packets;
};


#endif
