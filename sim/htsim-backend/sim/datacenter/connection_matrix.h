// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#ifndef connection_matrix
#define connection_matrix

#include "main.h"
#include "tcp.h"
#include "topology.h"
#include "randomqueue.h"
#include "fat_tree_switch.h"
#include "eventlist.h"
#include <list>
#include <map>

#define NO_START ((simtime_picosec)0xffffffffffffffff)

struct connection{
    int src, dst, size;
    bool is_bcast;
    bool is_reduce;     // phase-3: in-network Reduce (many->one root)
    bool is_allreduce;  // phase-3: in-network Allreduce (apex turn-around)
    bool is_reduce_scatter; // phase-3: in-network Reduce-Scatter (per-block roots)
    flowid_t flowid;
    triggerid_t send_done_trigger;
    triggerid_t recv_done_trigger;
    triggerid_t trigger;
    simtime_picosec start;
    int priority;
};

typedef enum {UNSPECIFIED, SINGLE_SHOT, MULTI_SHOT, BARRIER} trigger_type;

// hold (temporary) state to set up trigger
struct trigger {
    triggerid_t id;
    trigger_type type;
    int count; // used for barriers
    vector <flowid_t> flows; // flows to be triggered by this trigger
    Trigger *trigger;  // the actual trigger
};

//describe link failures
struct failure {
    FatTreeSwitch::switch_type switch_type;
    uint32_t switch_id;
    uint32_t link_id;
};


class ConnectionMatrix{
public:
    ConnectionMatrix(uint32_t );
    void addConnection(uint32_t src, uint32_t dest);
    void setPermutation(uint32_t conn);
    void setPermutation(uint32_t conn, uint32_t rack_size);
    void setPermutation();
    void setPermutationShuffle(uint32_t conn);
    void setPermutationShuffleIncast(uint32_t conn);
    void setRandom(uint32_t conns);
    void setStride(uint32_t many);
    void setLocalTraffic(Topology* top);
    void setStaggeredRandom(Topology* top, uint32_t conns, double local);
    void setStaggeredPermutation(Topology* top, double local);
    void setVL2();
    void setHotspot(uint32_t hosts_per_spot, uint32_t count);
    void setHotspotOutcast(uint32_t hosts_per_hotspot, uint32_t count);
    void setIncastLocalPerm(uint32_t hosts_per_hotspot);
  
    void setIncast(uint32_t hosts_per_hotspot, uint32_t center);
    void setOutcast(uint32_t src, uint32_t hosts_per_hotspot, uint32_t center);
    void setManytoMany(uint32_t hosts);

    bool save(const char * filename);
    bool save(FILE*);
    bool load(const char * filename);  
    /*bool load(FILE*);*/
    bool load(istream& file);
  
    vector<connection*>* getAllConnections();
    Trigger* getTrigger(triggerid_t id, EventList& eventlist);
    void bindTriggers(connection* c, EventList& eventlist);

    // Largest flow_id used by any user-parsed connection, or 0 if none.
    // Use this to pick starting points for synthesised flow_ids (e.g.
    // broadcast legs) that cannot collide with user-assigned ones.
    flowid_t max_flowid() const;

    // Largest trigger_id referenced either by a named `trigger ... id N`
    // declaration or by a connection-level `trigger`/`send_done_trigger`/
    // `recv_done_trigger` attribute, or 0 if none. Use as the starting
    // point for synthesised barrier triggers.
    triggerid_t max_triggerid() const;

    uint32_t N;
    uint32_t M;
    vector<connection*>* conns;
    vector<vector<int32_t>> groups;
    map<uint32_t, vector<uint32_t>*> connections;
    vector<failure*> failures; 
private:
    map<triggerid_t, trigger*> triggers;
};

#endif
