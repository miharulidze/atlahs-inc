// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-
#include "config.h"
#include "network.h"
#include "queue_lossless_input.h"
#include "randomqueue.h"
#include <iostream>
#include <math.h>

#include <sstream>
#include <fstream>
#include <string.h>
// #include "subflow_control.h"
#include "clock.h"
#include "compositequeue.h"
#include "connection_matrix.h"
#include "eventlist.h"
#include "firstfit.h"
#include "logfile.h"
#include "loggers.h"
#include "logsim-interface.h"
#include "pipe.h"
#include "shortflows.h"
#include "topology.h"
#include "uec.h"
#include "uec_bcast.h"
#include <filesystem>
// #include "vl2_topology.h"

// Fat Tree topology was modified to work with this script, others won't work
// correctly
// #include "oversubscribed_fat_tree_topology.h"
// #include "multihomed_fat_tree_topology.h"
// #include "star_topology.h"
// #include "bcube_topology.h"
#include <list>

// Simulation params

#define PRINT_PATHS 0

#define PERIODIC 0
#include "main.h"

// int RTT = 10; // this is per link delay; identical RTT microseconds = 0.02 ms
uint32_t RTT = 400; // this is per link delay in ns; identical RTT microseconds
                    // = 0.02 ms
int DEFAULT_NODES = 128;
#define DEFAULT_QUEUE_SIZE 100000000 // ~100MB, just a large value so we can ignore queues
// int N=128;

FirstFit *ff = NULL;
unsigned int subflow_count = 1;

string ntoa_uec(double n);
string itoa_uec(uint64_t n);

// #define SWITCH_BUFFER (SERVICE * RTT / 1000)
#define USE_FIRST_FIT 0
#define FIRST_FIT_INTERVAL 100

EventList eventlist;

// Phase-2: -bcast_mode flag. baseline = phase-1's |G|-1 unicast leg
// expansion (default). mcast = single-source multicast via the
// switch-level INC FIB. Selected at the is_bcast block in main.
enum BcastMode { BCAST_BASELINE, BCAST_MCAST };
static BcastMode bcast_mode = BCAST_BASELINE;

// PT6 (post-meeting): when non-null, write a tiny CSV with the
// total link-cross count at simulation end. Set via the
// -link_crosses_csv CLI flag.
static const char *link_crosses_csv = nullptr;

Logfile *lg;

void exit_error(char *progr) {
    cout << "Usage " << progr
         << " [UNCOUPLED(DEFAULT)|COUPLED_INC|FULLY_COUPLED|COUPLED_EPSILON] "
            "[epsilon][COUPLED_SCALABLE_TCP"
         << endl;
    exit(1);
}

int main(int argc, char **argv) {
    Packet::set_packet_size(4096);
    // eventlist.setEndtime(timeFromSec(1));
    Clock c(timeFromSec(5 / 100.), eventlist);
    mem_b queuesize = 100;
    int no_of_conns = 0, cwnd = 10, no_of_nodes = DEFAULT_NODES;
    stringstream filename(ios_base::out);
    RouteStrategy route_strategy = NOT_SET;
    std::string goal_filename;
    linkspeed_bps linkspeed = speedFromMbps((double)HOST_NIC);
    simtime_picosec hop_latency = timeFromNs((uint32_t)RTT);
    simtime_picosec switch_latency = timeFromNs((uint32_t)0);
    simtime_picosec pacing_delay = 1000;
    int packet_size = 2048;
    int kmin = -1;
    int kmax = -1;
    int bts_threshold = -1;
    int seed = -1;
    bool reuse_entropy = false;
    int number_entropies = 256;
    queue_type queue_choice = COMPOSITE;
    bool ignore_ecn_data = true;
    bool ignore_ecn_ack = true;
    UecSrc::set_fast_drop(false);
    bool do_jitter = false;
    bool do_exponential_gain = false;
    bool use_fast_increase = false;
    double gain_value_med_inc = 1;
    double jitter_value_med_inc = 1;
    double delay_gain_value_med_inc = 5;
    int target_rtt_percentage_over_base = 50;
    bool collect_data = false;
    int fat_tree_k = 1; // 1:1 default
    bool use_super_fast_increase = false;
    double y_gain = 1;
    double x_gain = 0.15;
    double z_gain = 1;
    double w_gain = 1;
    bool collect_flow_info = false;
    double bonus_drop = 1;
    double drop_value_buffer = 1;
    double starting_cwnd_ratio = 0;
    uint64_t explicit_starting_cwnd = 0;
    uint64_t explicit_starting_buffer = 0;
    uint64_t explicit_base_rtt = 0;
    uint64_t explicit_target_rtt = 0;
    uint64_t explicit_bdp = 0;
    double queue_size_ratio = 0;
    bool disable_case_3 = false;
    bool disable_case_4 = false;
    int ratio_os_stage_1 = 1;
    int pfc_low = 0;
    int pfc_high = 0;
    int pfc_marking = 0;
    double quickadapt_lossless_rtt = 2.0;
    int reaction_delay = 1;
    bool stop_after_quick = false;
    char *tm_file = NULL;
    char* topo_file = NULL;
    bool use_pacing = false;
    int precision_ts = 1;
    int once_per_rtt = 0;
    bool use_mixed = false;
    int phantom_size;
    int phantom_slowdown = 10;
    bool use_phantom = false;
    double exp_avg_ecn_value = .3;
    double exp_avg_rtt_value = .3;
    double exp_avg_alpha = 0.125;
    bool use_exp_avg_ecn = false;
    bool use_exp_avg_rtt = false;
    int percentage_lgs = 0;
    int jump_to = 0;
    int stop_pacing_after_rtt = 0;
    int num_failed_links = 0;
    bool topology_normal = true;
    uint64_t interdc_delay = 0;
    uint64_t max_queue_size = 0;
    int linkspeed_gbps = 0;
    int lgs_o = 0;
    int lgs_O = 0;
    int lgs_g = 0;

    int i = 1;
    filename << "logout.dat";

    while (i < argc) {
        if (!strcmp(argv[i], "-o")) {
            filename.str(std::string());
            filename << argv[i + 1];
            i++;
        } else if (!strcmp(argv[i], "-sub")) {
            subflow_count = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-conns")) {
            no_of_conns = atoi(argv[i + 1]);
            cout << "no_of_conns " << no_of_conns << endl;
            cout << "!!currently hardcoded to 8, value will be ignored!!" << endl;
            i++;
        } else if (!strcmp(argv[i], "-nodes")) {
            no_of_nodes = atoi(argv[i + 1]);
            cout << "no_of_nodes " << no_of_nodes << endl;
            i++;
        } else if (!strcmp(argv[i], "-cwnd")) {
            cwnd = atoi(argv[i + 1]);
            cout << "cwnd " << cwnd << endl;
            i++;
        } else if (!strcmp(argv[i], "-q")) {
            queuesize = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-once_per_rtt")) {
            once_per_rtt = atoi(argv[i + 1]);
            UecSrc::set_once_per_rtt(once_per_rtt);
            printf("OnceRTTDecrease: %d\n", once_per_rtt);
            i++;
        } else if (!strcmp(argv[i],"-topo")){
            topo_file = argv[i+1];
            cout << "FatTree topology input file: "<< topo_file << endl;
            i++;
        } else if (!strcmp(argv[i], "-stop_pacing_after_rtt")) {
            stop_pacing_after_rtt = atoi(argv[i + 1]);
            UecSrc::set_stop_pacing(stop_pacing_after_rtt);
            i++;
        } else if (!strcmp(argv[i], "-linkspeed")) {
            // linkspeed specified is in Mbps
            linkspeed = speedFromMbps(atof(argv[i + 1]));
            linkspeed_gbps = atof(argv[i + 1]) / 1000;
            // Saving this for UEC reference, Gbps
            i++;
        } else if (!strcmp(argv[i], "-kmin")) {
            // kmin as percentage of queue size (0..100)
            kmin = atoi(argv[i + 1]);
            printf("KMin: %d\n", atoi(argv[i + 1]));
            // /CompositeQueue::set_kMin(kmin);
            UecSrc::set_kmin(kmin / 100.0);
            i++;
        } else if (!strcmp(argv[i], "-k")) {
            fat_tree_k = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-ratio_os_stage_1")) {
            ratio_os_stage_1 = atoi(argv[i + 1]);
            UecSrc::set_os_ratio_stage_1(ratio_os_stage_1);
            i++;
        } else if (!strcmp(argv[i], "-kmax")) {
            // kmin as percentage of queue size (0..100)
            kmax = atoi(argv[i + 1]);
            printf("KMax: %d\n", atoi(argv[i + 1]));
            //CompositeQueue::set_kMax(kmax);
            UecSrc::set_kmax(kmax / 100.0);
            i++;
        } else if (!strcmp(argv[i], "-pfc_marking")) {
            pfc_marking = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-quickadapt_lossless_rtt")) {
            quickadapt_lossless_rtt = std::stod(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-bts_trigger")) {
            bts_threshold = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-mtu")) {
            packet_size = atoi(argv[i + 1]);
            //PKT_SIZE_MODERN = packet_size; // Saving this for UEC reference, Bytes
            i++;
        } else if (!strcmp(argv[i], "-reuse_entropy")) {
            reuse_entropy = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-disable_case_3")) {
            disable_case_3 = atoi(argv[i + 1]);
            UecSrc::set_disable_case_3(disable_case_3);
            printf("DisableCase3: %d\n", disable_case_3);
            i++;
        } else if (!strcmp(argv[i], "-jump_to")) {
            UecSrc::jump_to = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-reaction_delay")) {
            reaction_delay = atoi(argv[i + 1]);
            UecSrc::set_reaction_delay(reaction_delay);
            printf("ReactionDelay: %d\n", reaction_delay);
            i++;
        } else if (!strcmp(argv[i], "-precision_ts")) {
            precision_ts = atoi(argv[i + 1]);
            //FatTreeSwitch::set_precision_ts(precision_ts * 1000);
            UecSrc::set_precision_ts(precision_ts * 1000);
            printf("Precision: %d\n", precision_ts * 1000);
            i++;
        } else if (!strcmp(argv[i], "-disable_case_4")) {
            disable_case_4 = atoi(argv[i + 1]);
            UecSrc::set_disable_case_4(disable_case_4);
            printf("DisableCase4: %d\n", disable_case_4);
            i++;
        } else if (!strcmp(argv[i], "-stop_after_quick")) {
            UecSrc::set_stop_after_quick(true);
            printf("StopAfterQuick: %d\n", true);
        } else if (!strcmp(argv[i], "-lgs_flow_stats")) {
            collect_flow_info = true;
            printf("Flow collection: %d\n", true);
        } else if (!strcmp(argv[i], "-paths")) {
            number_entropies = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-switch_latency")) {
            switch_latency = timeFromNs(atof(argv[i + 1]));
            i++;
        } else if (!strcmp(argv[i], "-hop_latency")) {
            hop_latency = timeFromNs(atof(argv[i + 1]));
            //LINK_DELAY_MODERN = hop_latency / 1000; // Saving this for UEC reference, ps to ns
            i++;
        } else if (!strcmp(argv[i], "-ignore_ecn_ack")) {
            ignore_ecn_ack = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-lgs_o")) {
            lgs_o = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-lgs_g")) {
            lgs_g = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-lgs_O")) {
            lgs_O = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-ignore_ecn_data")) {
            ignore_ecn_data = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-pacing_delay")) {
            pacing_delay = atoi(argv[i + 1]);
            UecSrc::set_pacing_delay(pacing_delay);
            i++;
        } else if (!strcmp(argv[i], "-use_pacing")) {
            use_pacing = atoi(argv[i + 1]);
            UecSrc::set_use_pacing(use_pacing);
            i++;
        } else if (!strcmp(argv[i], "-fast_drop")) {
            UecSrc::set_fast_drop(atoi(argv[i + 1]));
            printf("FastDrop: %d\n", atoi(argv[i + 1]));
            i++;
        } else if (!strcmp(argv[i], "-seed")) {
            seed = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-link_crosses_csv")) {
            // PT6: emit total link-crosses to this file path
            // when the simulation ends.
            link_crosses_csv = argv[i + 1];
            i++;
        } else if (!strcmp(argv[i], "-bcast_mode")) {
            // Phase-2: select baseline (|G|-1 unicast legs) vs.
            // mcast (single-source switch-level INC fanout).
            if (!strcmp(argv[i + 1], "baseline")) {
                bcast_mode = BCAST_BASELINE;
            } else if (!strcmp(argv[i + 1], "mcast")) {
                bcast_mode = BCAST_MCAST;
            } else {
                cerr << "unknown -bcast_mode value: "
                     << argv[i + 1]
                     << " (expected baseline|mcast)" << endl;
                exit(1);
            }
            i++;
        } else if (!strcmp(argv[i], "-mcast_pin_core")) {
            // Experiment: pin every multicast tree to assignment index N,
            // collapsing all trees onto one aggregation position + one core
            // switch (single-core hotspot for lossless backpressure tests).
            // Use 0 for the canonical single core. -1 keeps round-robin.
            FatTreeTopology::set_mcast_pin_assignment(atoi(argv[i + 1]));
            i++;
        } else if (!strcmp(argv[i], "-interdc_delay")) {
            interdc_delay = atoi(argv[i + 1]);
            interdc_delay *= 1000;
            i++;
        } else if (!strcmp(argv[i], "-max_queue_size")) {
            max_queue_size = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-percentage_lgs")) {
            percentage_lgs = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-pfc_low")) {
            pfc_low = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-pfc_high")) {
            pfc_high = atoi(argv[i + 1]);
            i++;
        } else if (!strcmp(argv[i], "-collect_data")) {
            collect_data = atoi(argv[i + 1]);
            // /COLLECT_DATA = collect_data;
            i++;
        } else if (!strcmp(argv[i], "-do_jitter")) {
            do_jitter = atoi(argv[i + 1]);
            UecSrc::set_do_jitter(do_jitter);
            printf("DoJitter: %d\n", do_jitter);
            i++;
        } else if (!strcmp(argv[i], "-do_exponential_gain")) {
            do_exponential_gain = atoi(argv[i + 1]);
            UecSrc::set_do_exponential_gain(do_exponential_gain);
            printf("DoExpGain: %d\n", do_exponential_gain);
            i++;
        } else if (!strcmp(argv[i], "-use_fast_increase")) {
            use_fast_increase = atoi(argv[i + 1]);
            UecSrc::set_use_fast_increase(use_fast_increase);
            printf("FastIncrease: %d\n", use_fast_increase);
            i++;
        } else if (!strcmp(argv[i], "-use_super_fast_increase")) {
            use_super_fast_increase = atoi(argv[i + 1]);
            UecSrc::set_use_super_fast_increase(use_super_fast_increase);
            printf("FastIncreaseSuper: %d\n", use_super_fast_increase);
            i++;
        } else if (!strcmp(argv[i], "-gain_value_med_inc")) {
            gain_value_med_inc = std::stod(argv[i + 1]);
            // UecSrc::set_gain_value_med_inc(gain_value_med_inc);
            printf("GainValueMedIncrease: %f\n", gain_value_med_inc);
            i++;
        } else if (!strcmp(argv[i], "-jitter_value_med_inc")) {
            jitter_value_med_inc = std::stod(argv[i + 1]);
            // UecSrc::set_jitter_value_med_inc(jitter_value_med_inc);
            printf("JitterValue: %f\n", jitter_value_med_inc);
            i++;
        } else if (!strcmp(argv[i], "-decrease_on_nack")) {
            double decrease_on_nack = std::stod(argv[i + 1]);
            UecSrc::set_decrease_on_nack(decrease_on_nack);
            i++;
        } else if (!strcmp(argv[i], "-phantom_in_series")) {
            //CompositeQueue::set_use_phantom_in_series();
            printf("PhantomQueueInSeries: %d\n", 1);
            // i++;
        } else if (!strcmp(argv[i], "-phantom_both_queues")) {
            //CompositeQueue::set_use_both_queues();
            printf("PhantomUseBothForECNMarking: %d\n", 1);
        } else if (!strcmp(argv[i], "-delay_gain_value_med_inc")) {
            delay_gain_value_med_inc = std::stod(argv[i + 1]);
            // UecSrc::set_delay_gain_value_med_inc(delay_gain_value_med_inc);
            printf("DelayGainValue: %f\n", delay_gain_value_med_inc);
            i++;
        } else if (!strcmp(argv[i], "-tm")) {
            tm_file = argv[i + 1];
            cout << "traffic matrix input file: " << tm_file << endl;
            i++;
        } else if (!strcmp(argv[i], "-target_rtt_percentage_over_base")) {
            target_rtt_percentage_over_base = atoi(argv[i + 1]);
            UecSrc::set_target_rtt_percentage_over_base(target_rtt_percentage_over_base);
            printf("TargetRTT: %d\n", target_rtt_percentage_over_base);
            i++;
        } else if (!strcmp(argv[i], "-num_failed_links")) {
            num_failed_links = atoi(argv[i + 1]);
            //FatTreeTopology::set_failed_links(num_failed_links);
            i++;
        } else if (!strcmp(argv[i], "-fast_drop_rtt")) {
            UecSrc::set_fast_drop_rtt(atoi(argv[i + 1]));
            i++;
        } else if (!strcmp(argv[i], "-y_gain")) {
            y_gain = std::stod(argv[i + 1]);
            UecSrc::set_y_gain(y_gain);
            printf("YGain: %f\n", y_gain);
            i++;
        } else if (!strcmp(argv[i], "-x_gain")) {
            x_gain = std::stod(argv[i + 1]);
            UecSrc::set_x_gain(x_gain);
            printf("XGain: %f\n", x_gain);
            i++;
        } else if (!strcmp(argv[i], "-z_gain")) {
            z_gain = std::stod(argv[i + 1]);
            UecSrc::set_z_gain(z_gain);
            printf("ZGain: %f\n", z_gain);
            i++;
        } else if (!strcmp(argv[i], "-w_gain")) {
            w_gain = std::stod(argv[i + 1]);
            UecSrc::set_w_gain(w_gain);
            printf("WGain: %f\n", w_gain);
            i++;
        } else if (!strcmp(argv[i], "-starting_cwnd_ratio")) {
            starting_cwnd_ratio = std::stod(argv[i + 1]);
            printf("StartingWindowRatio: %f\n", starting_cwnd_ratio);
            i++;
        } else if (!strcmp(argv[i], "-explicit_starting_cwnd")) {
            explicit_starting_cwnd = atoi(argv[i + 1]);
            printf("StartingWindowForced: %d\n", explicit_starting_cwnd);
            i++;
        } else if (!strcmp(argv[i], "-explicit_starting_buffer")) {
            explicit_starting_buffer = atoi(argv[i + 1]);
            printf("StartingBufferForced: %d\n", explicit_starting_buffer);
            explicit_bdp = explicit_starting_buffer;
            i++;
        } else if (!strcmp(argv[i], "-explicit_base_rtt")) {
            explicit_base_rtt = ((uint64_t)atoi(argv[i + 1])) * 1000;
            printf("BaseRTTForced: %d\n", explicit_base_rtt);
            UecSrc::set_explicit_rtt(explicit_base_rtt);
            i++;
        } else if (!strcmp(argv[i], "-explicit_target_rtt")) {
            explicit_target_rtt = ((uint64_t)atoi(argv[i + 1])) * 1000;
            printf("TargetRTTForced: %lu\n", explicit_target_rtt);
            UecSrc::set_explicit_target_rtt(explicit_target_rtt);
            i++;
        } else if (!strcmp(argv[i], "-queue_size_ratio")) {
            queue_size_ratio = std::stod(argv[i + 1]);
            printf("QueueSizeRatio: %f\n", queue_size_ratio);
            i++;
        } else if (!strcmp(argv[i], "-bonus_drop")) {
            bonus_drop = std::stod(argv[i + 1]);
            UecSrc::set_bonus_drop(bonus_drop);
            printf("BonusDrop: %f\n", bonus_drop);
            i++;
        } else if (!strcmp(argv[i], "-drop_value_buffer")) {
            drop_value_buffer = std::stod(argv[i + 1]);
            UecSrc::set_buffer_drop(drop_value_buffer);
            printf("BufferDrop: %f\n", drop_value_buffer);
            i++;
        } else if (!strcmp(argv[i], "-goal")) {
            goal_filename = argv[i + 1];
            i++;
        } else if (!strcmp(argv[i], "-use_phantom")) {
            use_phantom = atoi(argv[i + 1]);
            printf("UsePhantomQueue: %d\n", use_phantom);
            //CompositeQueue::set_use_phantom_queue(use_phantom);
            i++;
        } else if (!strcmp(argv[i], "-use_exp_avg_ecn")) {
            use_exp_avg_ecn = atoi(argv[i + 1]);
            printf("UseExpAvgEcn: %d\n", use_exp_avg_ecn);
            UecSrc::set_exp_avg_ecn(use_exp_avg_ecn);
            i++;
        } else if (!strcmp(argv[i], "-use_exp_avg_rtt")) {
            use_exp_avg_rtt = atoi(argv[i + 1]);
            printf("UseExpAvgRtt: %d\n", use_exp_avg_rtt);
            UecSrc::set_exp_avg_rtt(use_exp_avg_rtt);
            i++;
        } else if (!strcmp(argv[i], "-exp_avg_rtt_value")) {
            exp_avg_rtt_value = std::stod(argv[i + 1]);
            printf("UseExpAvgRttValue: %d\n", exp_avg_rtt_value);
            UecSrc::set_exp_avg_rtt_value(exp_avg_rtt_value);
            i++;
        } else if (!strcmp(argv[i], "-exp_avg_ecn_value")) {
            exp_avg_ecn_value = std::stod(argv[i + 1]);
            printf("UseExpAvgecn_value: %d\n", exp_avg_ecn_value);
            UecSrc::set_exp_avg_ecn_value(exp_avg_ecn_value);
            i++;
        } else if (!strcmp(argv[i], "-exp_avg_alpha")) {
            exp_avg_alpha = std::stod(argv[i + 1]);
            printf("UseExpAvgalpha: %d\n", exp_avg_alpha);
            UecSrc::set_exp_avg_alpha(exp_avg_alpha);
            i++;
        } else if (!strcmp(argv[i], "-phantom_size")) {
            phantom_size = atoi(argv[i + 1]);
            printf("PhantomQueueSize: %d\n", phantom_size);
            //CompositeQueue::set_phantom_queue_size(phantom_size);
            i++;
        } else if (!strcmp(argv[i], "-os_border")) {
            int os_b = atoi(argv[i + 1]);
            //FatTreeInterDCTopology::set_os_ratio_border(os_b);
            i++;
        } else if (!strcmp(argv[i], "-phantom_slowdown")) {
            phantom_slowdown = atoi(argv[i + 1]);
            printf("PhantomQueueSize: %d\n", phantom_slowdown);
            //CompositeQueue::set_phantom_queue_slowdown(phantom_slowdown);
            i++;
        } else if (!strcmp(argv[i], "-strat")) {
            if (!strcmp(argv[i + 1], "perm")) {
                route_strategy = SCATTER_PERMUTE;
            } else if (!strcmp(argv[i + 1], "rand")) {
                route_strategy = SCATTER_RANDOM;
            } else if (!strcmp(argv[i + 1], "pull")) {
                route_strategy = PULL_BASED;
            } else if (!strcmp(argv[i + 1], "single")) {
                route_strategy = SINGLE_PATH;
            } else if (!strcmp(argv[i + 1], "ecmp_host")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                //FatTreeInterDCSwitch::set_strategy(FatTreeInterDCSwitch::ECMP);
            } else if (!strcmp(argv[i + 1], "ecmp_host_random_ecn")) {
                //route_strategy = ECMP_RANDOM_ECN;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                //FatTreeInterDCSwitch::set_strategy(FatTreeInterDCSwitch::ECMP);
            } else if (!strcmp(argv[i + 1], "ecmp_host_random2_ecn")) {
                //route_strategy = ECMP_RANDOM2_ECN;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                //FatTreeInterDCSwitch::set_strategy(FatTreeInterDCSwitch::ECMP);
            }
            i++;
        } else if (!strcmp(argv[i], "-topology")) {
            if (!strcmp(argv[i + 1], "normal")) {
                topology_normal = true;
            } else if (!strcmp(argv[i + 1], "interdc")) {
                topology_normal = false;
            }
            i++;
        } else if (!strcmp(argv[i], "-queue_type")) {
            if (!strcmp(argv[i + 1], "composite")) {
                queue_choice = COMPOSITE;
                UecSrc::set_queue_type("composite");
            } else if (!strcmp(argv[i + 1], "composite_bts")) {
                //queue_choice = COMPOSITE_BTS;
                UecSrc::set_queue_type("composite_bts");
                printf("Name Running: UEC BTS\n");
            } else if (!strcmp(argv[i + 1], "lossless_input")) {
                queue_choice = LOSSLESS_INPUT;
                UecSrc::set_queue_type("lossless_input");
                printf("Name Running: UEC Queueless\n");
            }
            i++;
        } else if (!strcmp(argv[i], "-algorithm")) {
            if (!strcmp(argv[i + 1], "delayA")) {
                UecSrc::set_alogirthm("delayA");
                printf("Name Running: UEC Version A\n");
            } else if (!strcmp(argv[i + 1], "smartt")) {
                UecSrc::set_alogirthm("smartt");
                printf("Name Running: SMaRTT\n");
            } else if (!strcmp(argv[i + 1], "mprdma")) {
                UecSrc::set_alogirthm("mprdma");
                printf("Name Running: SMaRTT Per RTT\n");
            } else if (!strcmp(argv[i + 1], "min_cc")) {
                UecSrc::set_alogirthm("min_cc");
            } else if (!strcmp(argv[i + 1], "no_cc")) {
                UecSrc::set_alogirthm("no_cc");
                printf("Name Running: STrack\n");
            } else if (!strcmp(argv[i + 1], "swift_like")) {
                UecSrc::set_alogirthm("swift_like");
                printf("Name Running: swift_like\n");
            } else if (!strcmp(argv[i + 1], "rtt")) {
                UecSrc::set_alogirthm("rtt");
                printf("Name Running: SMaRTT RTT Only\n");
            } else if (!strcmp(argv[i + 1], "ecn")) {
                UecSrc::set_alogirthm("ecn");
                printf("Name Running: SMaRTT ECN Only Constant\n");
            } else if (!strcmp(argv[i + 1], "custom")) {
                UecSrc::set_alogirthm("custom");
                printf("Name Running: SMaRTT ECN Only Variable\n");
            } else if (!strcmp(argv[i + 1], "intersmartt")) {
                UecSrc::set_alogirthm("intersmartt");
                printf("Name Running: SMaRTT InterDataCenter\n");
            } else if (!strcmp(argv[i + 1], "intersmartt_new")) {
                UecSrc::set_alogirthm("intersmartt_new");
                printf("Name Running: SMaRTT InterDataCenter\n");
            } else if (!strcmp(argv[i + 1], "intersmartt_simple")) {
                UecSrc::set_alogirthm("intersmartt_simple");
                printf("Name Running: SMaRTT InterDataCenter\n");
            } else if (!strcmp(argv[i + 1], "intersmartt")) {
                UecSrc::set_alogirthm("intersmartt");
                printf("Name Running: SMaRTT InterDataCenter\n");
            } else if (!strcmp(argv[i + 1], "intersmartt_composed")) {
                UecSrc::set_alogirthm("intersmartt_composed");
                printf("Name Running: SMaRTT InterDataCenter\n");
            } else if (!strcmp(argv[i + 1], "smartt_2")) {
                UecSrc::set_alogirthm("smartt_2");
                printf("Name Running: SMaRTT smartt_2\n");
            } else {
                printf("Wrong Algorithm Name\n");
                exit(0);
            }
            i++;
        } else
            exit_error(argv[0]);

        i++;
    }

    //SINGLE_PKT_TRASMISSION_TIME_MODERN = packet_size * 8 / (LINK_SPEED_MODERN);

    // Initialize Seed, Logging and Other variables
    if (seed != -1) {
        srand(seed);
        srandom(seed);
    } else {
        srand(time(NULL));
        srandom(time(NULL));
    }
    Packet::set_packet_size(packet_size);
    if (route_strategy == NOT_SET) {
        fprintf(stderr, "Route Strategy not set.  Use the -strat param.  "
                        "\nValid values are perm, rand, pull, rg and single\n");
        exit(1);
    }

    eventlist.setEndtime(timeFromSec((uint32_t)60));

    // Calculate Network Info
    int hops = 4; // hardcoded for now
    uint64_t actual_starting_cwnd = 0;
    uint64_t base_rtt_max_hops = (hops * 1000) + (4096 * 8 / (linkspeed_gbps) * hops) +
                                 (hops * 1000) + (64 * 8 / (linkspeed_gbps) * hops);
    uint64_t bdp_local = base_rtt_max_hops * (linkspeed_gbps) / 8;

    if (starting_cwnd_ratio == 0) {
        actual_starting_cwnd = bdp_local; // Equal to BDP if not other info
    } else {
        actual_starting_cwnd = bdp_local * starting_cwnd_ratio;
    }
    if (queue_size_ratio == 0) {
        queuesize = bdp_local; // Equal to BDP if not other info
    } else {
        queuesize = bdp_local * queue_size_ratio;
    }

    if (goal_filename.find("llama_random.bin") != std::string::npos) {
        printf("Setting LLama random\n");
        AtlahsHtsimApi::llama_rand = true;
    }

    if (explicit_starting_buffer != 0) {
        queuesize = explicit_starting_buffer;
    }
    if (explicit_starting_cwnd != 0) {
        actual_starting_cwnd = explicit_starting_cwnd;
        UecSrc::set_explicit_bdp(explicit_bdp);
    }

    UecSrc::set_starting_cwnd(actual_starting_cwnd);
    if (max_queue_size != 0) {
        queuesize = max_queue_size;
        UecSrc::set_switch_queue_size(max_queue_size);
    }

    printf("Using BDP of %lu - Queue is %lld - Starting Window is %lu - RTT "
           "%lu - Bandwidth %lu\n",
           bdp_local, queuesize, actual_starting_cwnd, base_rtt_max_hops, linkspeed_gbps);

    cout << "Using subflow count " << subflow_count << endl;

    // prepare the loggers

    cout << "Logging to " << filename.str() << endl;
    // Logfile
    Logfile logfile(filename.str(), eventlist);

#if PRINT_PATHS
    filename << ".paths";
    cout << "Logging path choices to " << filename.str() << endl;
    std::ofstream paths(filename.str().c_str());
    if (!paths) {
        cout << "Can't open for writing paths file!" << endl;
        exit(1);
    }
#endif

    lg = &logfile;

    logfile.setStartTime(timeFromSec(0));

    // UecLoggerSimple uecLogger;
    // logfile.addLogger(uecLogger);
    TrafficLoggerSimple traffic_logger = TrafficLoggerSimple();
    logfile.addLogger(traffic_logger);

    // UecSrc *uecSrc;
    // UecSink *uecSink;

    UecSrc::setRouteStrategy(route_strategy);
    UecSink::setRouteStrategy(route_strategy);

    // Route *routeout, *routein;
    // double extrastarttime;

    int dest;

    if (topology_normal) {

    } else {
    }

#if USE_FIRST_FIT
    if (subflow_count == 1) {
        ff = new FirstFit(timeFromMs(FIRST_FIT_INTERVAL), eventlist);
    }
#endif

#ifdef FAT_TREE
#endif

#ifdef FAT_TREE_INTERDC_TOPOLOGY_H

#endif

#ifdef OV_FAT_TREE
    OversubscribedFatTreeTopology *top = new OversubscribedFatTreeTopology(&logfile, &eventlist, ff);
#endif

#ifdef MH_FAT_TREE
    MultihomedFatTreeTopology *top = new MultihomedFatTreeTopology(&logfile, &eventlist, ff);
#endif

#ifdef STAR
    StarTopology *top = new StarTopology(&logfile, &eventlist, ff);
#endif

#ifdef BCUBE
    BCubeTopology *top = new BCubeTopology(&logfile, &eventlist, ff);
    cout << "BCUBE " << K << endl;
#endif

#ifdef VL2
    VL2Topology *top = new VL2Topology(&logfile, &eventlist, ff);
#endif

#if USE_FIRST_FIT
    if (ff)
        ff->net_paths = net_paths;
#endif

    map<int, vector<int> *>::iterator it;

    // used just to print out stats data at the end
    list<const Route *> routes;

    int connID = 0;
    dest = 1;
    // int receiving_node = 127;
    vector<int> subflows_chosen;

    ConnectionMatrix *conns = NULL;
    LogSimInterface *lgs = NULL;

    queue_type snd_type = FAIR_PRIO;
    // Honour -queue_type (default COMPOSITE). Previously hardcoded, which
    // silently ignored -queue_type lossless_input.
    queue_type qt = queue_choice;
    FatTreeTopology *top = NULL;

    // PFC thresholds for lossless operation. The LosslessInputQueue
    // constructor asserts _high_threshold > _low_threshold > 0, so these
    // must be set before the topology builds its queues. Expressed in
    // packets (matching main_ndp.cpp); -pfc_high / -pfc_low override the
    // defaults of 100 / 80 packets (both well below the -q buffer).
    if (qt == LOSSLESS_INPUT || qt == LOSSLESS_INPUT_ECN) {
        if (pfc_high == 0) pfc_high = 100;
        if (pfc_low == 0)  pfc_low = 80;
        LosslessInputQueue::_high_threshold =
                Packet::data_packet_size() * pfc_high;
        LosslessInputQueue::_low_threshold =
                Packet::data_packet_size() * pfc_low;
        cout << "PFC lossless: high_threshold=" << pfc_high
             << "pkt low_threshold=" << pfc_low << "pkt" << endl;
    }

    if (tm_file != NULL) {
        if (topo_file) {
            top = FatTreeTopology::load(topo_file, NULL, eventlist, queuesize, qt, snd_type);
        } else {
            FatTreeTopology::set_tiers(3);
            top = new FatTreeTopology(no_of_nodes, linkspeed, queuesize, NULL, 
                                    &eventlist, NULL, qt, hop_latency,
                                    switch_latency,
                                    snd_type);
        }

        conns = new ConnectionMatrix(no_of_nodes);

        if (tm_file) {
            cout << "Loading connection matrix from  " << tm_file << endl;

            if (!conns->load(tm_file)) {
                cout << "Failed to load connection matrix " << tm_file << endl;
                exit(-1);
            }
        } else {
            cout << "Loading connection matrix from  standard input" << endl;
            conns->load(cin);
        }

        map<flowid_t, TriggerTarget *> flowmap;
        vector<connection *> *all_conns = conns->getAllConnections();
        top->groups = &(conns->groups);
        // Rooted Reduce: tell the topology each reduce target's root host
        // before set_up_mcast, so it installs R's descent branch.
        for (connection *c : *all_conns) {
            if (c->is_reduce &&
                static_cast<size_t>(c->dst) < conns->groups.size() &&
                static_cast<size_t>(c->src) < conns->groups[c->dst].size()) {
                top->set_reduce_root(static_cast<uint32_t>(c->dst),
                                     conns->groups[c->dst][c->src]);
            }
        }
        top->set_up_mcast(); // for real mcast switch routing, configure switch tables
        UecSrc *uecSrc;
        UecSink *uecSnk;

        // Starting points for synthesised IDs used by the broadcast leg
        // expansion. Seeded from the user-visible max so bcast legs cannot
        // collide with user-assigned flow_ids on a shared (host, flow_id)
        // ToR FIB entry, and synthesised barriers are unambiguous in logs.
        flowid_t next_bcast_leg_flow_id = conns->max_flowid();
        triggerid_t next_bcast_barrier_id = conns->max_triggerid();

        for (size_t c = 0; c < all_conns->size(); c++) {
            connection *crt = all_conns->at(c);
            int src = crt->src;
            int dest = crt->dst;
            printf("Reaching here1\n");
            fflush(stdout);

            uint64_t actual_starting_cwnd = 0;
            uint64_t base_rtt_max_hops = (hops * 1000) + (4096 * 8 / (linkspeed_gbps) * hops) +
                                         (hops * 1000) + (64 * 8 / (linkspeed_gbps) * hops);
            uint64_t bdp_local = base_rtt_max_hops * (linkspeed_gbps) / 8;

            if (starting_cwnd_ratio == 0) {
                actual_starting_cwnd = bdp_local; // Equal to BDP if not other info
            } else {
                actual_starting_cwnd = bdp_local * starting_cwnd_ratio;
            }

            UecSrc::set_starting_cwnd(actual_starting_cwnd * 2);
            printf("Setting CWND to %lu\n", actual_starting_cwnd);

            printf("Using BDP of %lu - Queue is %lld - Starting Window is %lu\n", bdp_local, queuesize,
                   actual_starting_cwnd);

            // --------------------------------------------------------------
            // Broadcast (MPI_Bcast) baseline branch.
            //
            // A `bcast ROOT->GRP` entry (is_bcast=true) is decomposed here
            // into |GRP|-1 ACK-less unicast legs. `src` indexes into the
            // group to select the root; `dest` indexes the group in
            // conns->groups. All legs share one BarrierTrigger whose fire
            // marks the collective complete; each leg's sink is one of the
            // |GRP|-1 activations. See AA-thesis-baseline.md for rationale.
            //
            // Note: the collective operation is "broadcast" (MPI_Bcast).
            // Phase two will implement this on top of a switch-level
            // multicast mechanism — a separate concern; the naming here
            // reflects the collective, not the phase-two mechanism.
            // --------------------------------------------------------------
            // --------------------------------------------------------------
            // Allreduce (MPI_Allreduce), in-network apex turn-around.
            //
            // `allreduce ROOT->GRP` (is_allreduce). Symmetric: every member
            // is both a reduce source (emits one UEC_REDUCE up the tree) and
            // a sink (receives the result the apex switch turns around and
            // multicasts back down). Completion = all |G| members receive.
            // The root index is ignored (no semantic root, §sec:lossless /
            // AA-plan-Aggregation §4).
            // --------------------------------------------------------------
            if (crt->is_allreduce) {
                if (static_cast<size_t>(dest) >= conns->groups.size()) {
                    cerr << "allreduce connection refers to undefined group index "
                         << dest << "\n";
                    exit(1);
                }
                const vector<int32_t> &group = conns->groups[dest];
                if (group.size() < 2) {
                    cerr << "allreduce group " << dest << " has size < 2\n";
                    exit(1);
                }
                int root_label = (static_cast<size_t>(src) < group.size())
                        ? group[src] : group[0];   // label only; symmetric op

                flowid_t op_flow_id = crt->flowid
                        ? crt->flowid : ++next_bcast_leg_flow_id;

                // Completion = every member receives the turned-around result.
                BarrierTrigger *barrier = new BarrierTrigger(
                        eventlist, ++next_bcast_barrier_id, group.size());
                barrier->add_target(*new ReduceCompletionRecorder(
                        eventlist, "ALLREDUCE", op_flow_id, root_label, dest,
                        crt->size, group.size(), crt->start));
                if (crt->recv_done_trigger) {
                    Trigger *downstream = conns->getTrigger(
                            crt->recv_done_trigger, eventlist);
                    barrier->add_target(*new TriggerRelay(downstream));
                }

                for (int32_t m : group) {
                    // Descent sink: receives the result the apex multicasts
                    // back down (the persistent (host,group) UecMcastSink).
                    UecMcastSink* sink = top->get_mcast_sink(m, dest);
                    assert(sink && "set_up_mcast did not create sink");
                    sink->register_op(op_flow_id,
                                      crt->size > 0 ? crt->size : 0, barrier);

                    // Ascent source: emit one UEC_REDUCE contribution up the
                    // tree; the switch fan-in barriers combine toward the apex.
                    UecReduceSrc *rs = new UecReduceSrc(
                            NULL, NULL, eventlist,
                            base_rtt_max_hops, bdp_local, 100, 6);
                    rs->setNumberEntropies(256);
                    rs->set_group_id(static_cast<uint32_t>(dest));
                    rs->set_flowid(op_flow_id);
                    if (crt->size > 0) rs->setFlowSize(crt->size);
                    if (crt->trigger) {
                        Trigger *trig = conns->getTrigger(crt->trigger, eventlist);
                        trig->add_target(*rs);
                    }
                    rs->setName("uec_allreduce_src_" + ntoa_uec(m)
                                + "_g" + ntoa_uec(dest));
                    logfile.writeName(*rs);

                    Route *srctotor = new Route();
                    uint32_t tor = top->HOST_POD_SWITCH(m);
                    srctotor->push_back(top->queues_ns_nlp[m][tor][0]);
                    srctotor->push_back(top->pipes_ns_nlp[m][tor][0]);
                    srctotor->push_back(
                            top->queues_ns_nlp[m][tor][0]->getRemoteEndpoint());

                    rs->from = m;
                    rs->to   = -1;
                    rs->set_paths(number_entropies);
                    rs->connect_collective(srctotor, crt->start);
                }
                continue;
            }

            // --------------------------------------------------------------
            // Reduce (MPI_Reduce), in-network many->one.
            //
            // `reduce ROOT->GRP` (is_reduce). Every member is a reduce source
            // (emits one UEC_REDUCE up the tree); the aggregate turns around
            // at the apex and is delivered down R's single branch to the one
            // root host R. Completion = R receives. set_up_mcast installed R's
            // descent under a synthetic group id (reduce_descent_group).
            // --------------------------------------------------------------
            if (crt->is_reduce) {
                if (static_cast<size_t>(dest) >= conns->groups.size()) {
                    cerr << "reduce connection refers to undefined group index "
                         << dest << "\n";
                    exit(1);
                }
                const vector<int32_t> &group = conns->groups[dest];
                if (static_cast<size_t>(src) >= group.size()) {
                    cerr << "reduce root-index " << src
                         << " out of range for group " << dest << "\n";
                    exit(1);
                }
                int root = group[src];

                flowid_t op_flow_id = crt->flowid
                        ? crt->flowid : ++next_bcast_leg_flow_id;

                // Completion = the single root receives the combined result.
                BarrierTrigger *barrier = new BarrierTrigger(
                        eventlist, ++next_bcast_barrier_id, 1);
                barrier->add_target(*new ReduceCompletionRecorder(
                        eventlist, "REDUCE", op_flow_id, root, dest,
                        crt->size, group.size(), crt->start));
                if (crt->recv_done_trigger) {
                    Trigger *downstream = conns->getTrigger(
                            crt->recv_done_trigger, eventlist);
                    barrier->add_target(*new TriggerRelay(downstream));
                }

                // R's reduce sink: receives the result the apex unicasts down
                // via the regular FIB. Registered as a host route at R's ToR
                // (keyed by op_flow_id), so getHostRoute resolves the
                // descending packet to it; register_op fires completion.
                UecReduceSink* rsink = new UecReduceSink(root,
                        static_cast<uint32_t>(dest));
                rsink->register_op(op_flow_id,
                                   crt->size > 0 ? crt->size : 0, barrier);
                top->switches_lp[top->HOST_POD_SWITCH(root)]
                        ->addHostPort(root, op_flow_id, rsink);

                // Every member contributes one UEC_REDUCE up the tree.
                for (int32_t m : group) {
                    UecReduceSrc *rs = new UecReduceSrc(
                            NULL, NULL, eventlist,
                            base_rtt_max_hops, bdp_local, 100, 6);
                    rs->setNumberEntropies(256);
                    rs->set_group_id(static_cast<uint32_t>(dest));
                    rs->set_flowid(op_flow_id);
                    if (crt->size > 0) rs->setFlowSize(crt->size);
                    if (crt->trigger) {
                        Trigger *trig = conns->getTrigger(crt->trigger, eventlist);
                        trig->add_target(*rs);
                    }
                    rs->setName("uec_reduce_src_" + ntoa_uec(m)
                                + "_g" + ntoa_uec(dest));
                    logfile.writeName(*rs);

                    Route *srctotor = new Route();
                    uint32_t tor = top->HOST_POD_SWITCH(m);
                    srctotor->push_back(top->queues_ns_nlp[m][tor][0]);
                    srctotor->push_back(top->pipes_ns_nlp[m][tor][0]);
                    srctotor->push_back(
                            top->queues_ns_nlp[m][tor][0]->getRemoteEndpoint());

                    rs->from = m;
                    rs->to   = -1;
                    rs->set_paths(number_entropies);
                    rs->connect_collective(srctotor, crt->start);
                }
                continue;
            }

            if (crt->is_bcast) {
                // Development-phase guardrails against malformed .cm input.
                // TODO: remove once the collective track stabilises.
                if (static_cast<size_t>(dest) >= conns->groups.size()) {
                    cerr << "bcast connection refers to undefined group index "
                         << dest << " (have " << conns->groups.size()
                         << " groups)\n";
                    exit(1);
                }
                const vector<int32_t> &group = conns->groups[dest];
                if (static_cast<size_t>(src) >= group.size()) {
                    cerr << "bcast connection root-index " << src
                         << " out of range for group " << dest
                         << " (size " << group.size() << ")\n";
                    exit(1);
                }
                int root = group[src];
                size_t leg_count = group.size() - 1;
                if (leg_count == 0) {
                    cerr << "bcast group " << dest << " has size 1; no legs\n";
                    exit(1);
                }
                BarrierTrigger *barrier = new BarrierTrigger(
                        eventlist, ++next_bcast_barrier_id, leg_count);
                // Always attach a completion recorder — it emits the
                // BCAST_COMPLETE line to stdout for downstream plotting
                // and also satisfies BarrierTrigger's targets>0 fire
                // assertion. Chain to the user-specified downstream
                // trigger if present.
                barrier->add_target(*new BcastCompletionRecorder(
                        eventlist, crt->flowid, root, dest,
                        crt->size, leg_count, crt->start));
                if (crt->recv_done_trigger) {
                    Trigger *downstream = conns->getTrigger(
                            crt->recv_done_trigger, eventlist);
                    barrier->add_target(*new TriggerRelay(downstream));
                }

                // -----------------------------------------------------
                // Phase-2 mcast branch: one UecBcastSrcMcast emits one
                // UecMcastPacket; persistent UecMcastSinks (created by
                // FatTreeTopology::set_up_mcast) absorb deliveries.
                // The baseline branch below is preserved byte-identical.
                // -----------------------------------------------------
                if (bcast_mode == BCAST_MCAST) {
                    flowid_t op_flow_id = crt->flowid
                            ? crt->flowid : ++next_bcast_leg_flow_id; // name is misleading since each "leg" has the same id in mcast

                    // Register per-op expectations on each member's
                    // persistent (host, group) sink.
                    for (int32_t m : group) {
                        if (m == root) continue;
                        UecMcastSink* sink =
                                top->get_mcast_sink(m, dest);
                        assert(sink &&
                               "set_up_mcast did not create sink");
                        sink->register_op(op_flow_id,
                                          crt->size > 0 ? crt->size : 0,
                                          barrier);
                    }

                    // One source per op. Connect via the standard
                    // host-to-TOR srctotor route. The first packet
                    // enters the root TOR with _type = UEC_MCAST and
                    // FatTreeSwitch::receivePacket dispatches to
                    // handle_mcast.
                    UecBcastSrcMcast *bs = new UecBcastSrcMcast(
                            NULL, NULL, eventlist,
                            base_rtt_max_hops, bdp_local, 100, 6);
                    bs->setNumberEntropies(256);    // not relevant for this src type
                    bs->set_group_id(static_cast<uint32_t>(dest));
                    bs->set_flowid(op_flow_id);
                    if (crt->size > 0) bs->setFlowSize(crt->size);

                    if (crt->trigger) {
                        Trigger *trig = conns->getTrigger(
                                crt->trigger, eventlist);
                        trig->add_target(*bs);
                    }

                    string op_tag = crt->flowid
                            ? ("op" + ntoa_uec(crt->flowid) + "_")
                            : "";
                    bs->setName("uec_bcast_mcast_" + op_tag
                                + ntoa_uec(root) + "_g"
                                + ntoa_uec(dest));
                    logfile.writeName(*bs);

                    Route *srctotor = new Route();
                    if (top != NULL) {
                        srctotor->push_back(top->queues_ns_nlp[root][top->HOST_POD_SWITCH(root)][0]);
                        srctotor->push_back(top->pipes_ns_nlp[root][top->HOST_POD_SWITCH(root)][0]);
                        srctotor->push_back(top->queues_ns_nlp[root][top->HOST_POD_SWITCH(root)][0]->getRemoteEndpoint());
                    }

                    bs->from = root;
                    bs->to   = -1;     // multicast: no single dst
                    bs->set_paths(number_entropies);
                    bs->connect_collective(srctotor, crt->start);

                    continue;
                }

                for (int32_t m : group) {
                    if (m == root) continue;

                    UecBcastSrc *bs = new UecBcastSrc(
                            NULL, NULL, eventlist,
                            base_rtt_max_hops, bdp_local, 100, 6);
                    bs->setNumberEntropies(256);
                    bs->set_dst(m);
                    // TODO: Can we find a better solution for the FlowID assignment?
                    bs->set_flowid(++next_bcast_leg_flow_id);
                    if (crt->size > 0) bs->setFlowSize(crt->size);

                    if (crt->trigger) {
                        // All legs start when the named trigger fires.
                        Trigger *trig = conns->getTrigger(
                                crt->trigger, eventlist);
                        trig->add_target(*bs);
                    }

                    UecBcastSink *bsink = new UecBcastSink();
                    bsink->set_src(root);
                    bsink->set_expected_bytes(crt->size > 0 ? crt->size : 0);
                    // TODO: So each sink has pointer to barrier to decrement when their packet arrives
                    bsink->set_end_trigger(*barrier);

                    // Use crt->flowid as the broadcast-operation tag in
                    // leg names (MPI-communicator-like). Transport-level
                    // flow_id stays synthetic for ToR-FIB uniqueness; the
                    // op tag only affects trace/log readability.
                    string op_tag = crt->flowid
                            ? ("op" + ntoa_uec(crt->flowid) + "_")
                            : "";
                    bs->setName("uec_bcast_" + op_tag + ntoa_uec(root) + "_"
                                + ntoa_uec(m));
                    logfile.writeName(*bs);
                    bsink->setName("uec_bcast_sink_" + op_tag + ntoa_uec(root)
                                   + "_" + ntoa_uec(m));
                    logfile.writeName(*bsink);

                    switch (route_strategy) {
                    case ECMP_FIB:
                    case ECMP_FIB_ECN:
                    case REACTIVE_ECN: {
                        Route *srctotor = new Route();
                        Route *dsttotor = new Route();

                        if (top != NULL) {
                            srctotor->push_back(top->queues_ns_nlp[root][top->HOST_POD_SWITCH(root)][0]);
                            srctotor->push_back(top->pipes_ns_nlp[root][top->HOST_POD_SWITCH(root)][0]);
                            srctotor->push_back(top->queues_ns_nlp[root][top->HOST_POD_SWITCH(root)][0]->getRemoteEndpoint());
                            // TODO: Why would we need that?
                            dsttotor->push_back(top->queues_ns_nlp[m][top->HOST_POD_SWITCH(m)][0]);
                            dsttotor->push_back(top->pipes_ns_nlp[m][top->HOST_POD_SWITCH(m)][0]);
                            dsttotor->push_back(top->queues_ns_nlp[m][top->HOST_POD_SWITCH(m)][0]->getRemoteEndpoint());
                        }

                        bs->from = root;
                        bsink->to = m;
                        bs->set_paths(number_entropies);
                        bsink->set_paths(number_entropies);
                        bs->connect(srctotor, dsttotor, *bsink, crt->start);

                        if (top != NULL) {
                            top->switches_lp[top->HOST_POD_SWITCH(root)]
                                    ->addHostPort(root, bs->flow_id(), bs);
                            top->switches_lp[top->HOST_POD_SWITCH(m)]
                                    ->addHostPort(m, bs->flow_id(), bsink);
                        }
                        break;
                    }
                    case NOT_SET:
                        abort();
                        break;
                    default:
                        abort();
                        break;
                    }
                }
                continue;
            }

            // --------------------------------------------------------------
            // Regular P2P unicast branch.
            // --------------------------------------------------------------
            uecSrc = new UecSrc(NULL, NULL, eventlist, base_rtt_max_hops, bdp_local, 100, 6);

            uecSrc->setNumberEntropies(256);
            uecSrc->set_dst(dest);
            if (crt->flowid) {
                uecSrc->set_flowid(crt->flowid);
                assert(flowmap.find(crt->flowid) == flowmap.end()); // don't have dups
                flowmap[crt->flowid] = uecSrc;
            }

            if (crt->size > 0) {
                uecSrc->setFlowSize(crt->size);
            }

            if (crt->trigger) {
                Trigger *trig = conns->getTrigger(crt->trigger, eventlist);
                trig->add_target(*uecSrc);
            }
            if (crt->send_done_trigger) {
                Trigger *trig = conns->getTrigger(crt->send_done_trigger, eventlist);
                uecSrc->set_end_trigger(*trig);
            }

            uecSnk = new UecSink();

            uecSrc->setName("uec_" + ntoa_uec(src) + "_" + ntoa_uec(dest));

            cout << "uec_" + ntoa_uec(src) + "_" + ntoa_uec(dest) << endl;
            logfile.writeName(*uecSrc);

            uecSnk->set_src(src);

            uecSnk->setName("uec_sink_" + ntoa_uec(src) + "_" + ntoa_uec(dest));
            logfile.writeName(*uecSnk);
            if (crt->recv_done_trigger) {
                Trigger *trig = conns->getTrigger(crt->recv_done_trigger, eventlist);
                uecSnk->set_end_trigger(*trig);
                // Opt in to sink-side completion detection so the named
                // trigger actually fires when the last byte arrives.
                if (crt->size > 0) uecSnk->set_expected_bytes(crt->size);
            }

            // uecRtxScanner->registerUec(*uecSrc);

            switch (route_strategy) {
            case ECMP_FIB:
            case ECMP_FIB_ECN:
            case REACTIVE_ECN: {
                Route *srctotor = new Route();
                Route *dsttotor = new Route();

                if (top != NULL) {
                    srctotor->push_back(top->queues_ns_nlp[src][top->HOST_POD_SWITCH(src)][0]);
                    srctotor->push_back(top->pipes_ns_nlp[src][top->HOST_POD_SWITCH(src)][0]);
                    srctotor->push_back(top->queues_ns_nlp[src][top->HOST_POD_SWITCH(src)][0]->getRemoteEndpoint());

                    dsttotor->push_back(top->queues_ns_nlp[dest][top->HOST_POD_SWITCH(dest)][0]);
                    dsttotor->push_back(top->pipes_ns_nlp[dest][top->HOST_POD_SWITCH(dest)][0]);
                    dsttotor->push_back(top->queues_ns_nlp[dest][top->HOST_POD_SWITCH(dest)][0]->getRemoteEndpoint());
                } 

                uecSrc->from = src;
                uecSnk->to = dest;
                uecSrc->set_paths(number_entropies);
                uecSnk->set_paths(number_entropies);
                // populates the eventlist
                uecSrc->connect(srctotor, dsttotor, *uecSnk, crt->start);
                
                // register src and snk to receive packets src their respective
                // TORs.
                if (top != NULL) {
                    top->switches_lp[top->HOST_POD_SWITCH(src)]->addHostPort(src, uecSrc->flow_id(), uecSrc);
                    top->switches_lp[top->HOST_POD_SWITCH(dest)]->addHostPort(dest, uecSrc->flow_id(), uecSnk);
                } 
                break;
            }
            case NOT_SET: {
                abort();
                break;
            }
            default: {
                abort();
                break;
            }
            }
        }
        // simulate traffic
        while (eventlist.doNextEvent()) {
        }

        // PT6 (post-meeting): emit link-crosses CSV if requested.
        // One row, one column: total directional pipe traversals
        // across the entire simulation (every packet in flight is
        // one cross). Sweep harness reads this back into the
        // per-run row.
        if (link_crosses_csv) {
            std::ofstream lcf(link_crosses_csv);
            lcf << "total_link_crosses\n"
                << Pipe::total_packets() << "\n";
        }
    } else if (goal_filename.size() > 0) {
        printf("Starting LGS Interface");

        if (topo_file) {
            top = FatTreeTopology::load(topo_file, NULL, eventlist, queuesize, qt, snd_type);
        } else {
            FatTreeTopology::set_tiers(3);
            top = new FatTreeTopology(no_of_nodes, linkspeed, queuesize, NULL, 
                                    &eventlist, NULL, qt, hop_latency,
                                    switch_latency,
                                    snd_type);
        }

        AtlahsHtsimApi *api = new AtlahsHtsimApi();
        api->setTopology(top);
        api->setEventList(&eventlist);
        api->setComputeEvent(new ComputeEvent(eventlist));
        api->setNullEvent(new NullEvent(eventlist));
        lgs = new LogSimInterface(NULL, NULL, eventlist, top, nullptr);
        api->setLogSimInterface(lgs);
        lgs->htsim_api = api;
        lgs->set_protocol(SENDER_PROTOCOL);
        lgs->htsim_api->linkspeed = linkspeed;
        lgs->percentage_lgs = percentage_lgs;
        lgs->print_stats_flows = collect_flow_info;
        lgs->htsim_api->print_stats_flows = collect_flow_info;
        

        double linkSpeedBytesPerSec = (linkspeed/1000000000 * 1e9) / 8.0;

        // Calculate LGS parameters
        lgs->htsim_api->htsim_G  = 1e9 / linkSpeedBytesPerSec;
        lgs->htsim_api->linkspeed_gbps = linkspeed_gbps;
        lgs->lgs_O = lgs_O;
        lgs->lgs_o = lgs_o;
        lgs->lgs_g = lgs_g;

        lgs->htsim_api->total_nodes = no_of_nodes;
        lgs->htsim_api->setSenderCwnd(bdp_local);
        lgs->htsim_api->setSenderBdp(bdp_local);
        lgs->htsim_api->setSenderRtt(base_rtt_max_hops);
        lgs->htsim_api->Setup();
        printf("Started LGS\n");
        
        
        start_lgs(goal_filename, *lgs);
        printf("Finished all\n");
        fflush(stdout);
        return 0;
    }
}

string ntoa_uec(double n) {
    stringstream s;
    s << n;
    return s.str();
}

string itoa_uec(uint64_t n) {
    stringstream s;
    s << n;
    return s.str();
}
