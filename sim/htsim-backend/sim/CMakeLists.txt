cmake_minimum_required(VERSION 3.15)
project(sim LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_compile_options(-Wall -Wsign-compare -Wuninitialized -O3 -g -fPIE)

add_library(sim STATIC
    eventlist.cpp
    tcppacket.cpp
    pipe.cpp
    queue.cpp
    meter.cpp
    queue_lossless.cpp
    queue_lossless_input.cpp
    queue_lossless_output.cpp
    ecnqueue.cpp
    tcp.cpp
    dctcp.cpp
    mtcp.cpp
    loggers.cpp
    logfile.cpp
    clock.cpp
    config.cpp
    network.cpp
    qcn.cpp
    exoqueue.cpp
    randomqueue.cpp
    cbr.cpp
    cbrpacket.cpp
    sent_packets.cpp
    ndp.cpp
    ndptunnel.cpp
    ndppacket.cpp
    roce.cpp
    rocepacket.cpp
    eth_pause_packet.cpp
    tcp_transfer.cpp
    tcp_periodic.cpp
    compositequeue.cpp
    prioqueue.cpp
    cpqueue.cpp
    ndp_transfer.cpp
    compositeprioqueue.cpp
    switch.cpp
    dctcp_transfer.cpp
    fairpullqueue.cpp
    route.cpp
    callback_pipe.cpp
    ndptunnelpacket.cpp
    swiftpacket.cpp
    swift.cpp
    swift_scheduler.cpp
    routetable.cpp
    trigger.cpp
    hpccpacket.cpp
    uec.cpp
    uec_bcast.cpp
    uecpacket.cpp
    hpcc.cpp
    strackpacket.cpp
    strack.cpp
    priopullqueue.cpp
    rng.cpp
    ecnprioqueue.cpp
    eqdspacket.cpp
    eqds.cpp
    eqds_logger.cpp
    aeolusqueue.cpp
    null_event.cpp
    compute_event.cpp
)

target_include_directories(sim PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/datacenter
)

set(DATACENTER_COMMON
    datacenter/firstfit.cpp
    datacenter/vl2_topology.cpp
    datacenter/fat_tree_topology.cpp
    datacenter/fat_tree_switch.cpp
    datacenter/bcube_topology.cpp
    datacenter/connection_matrix.cpp
    datacenter/oversubscribed_fat_tree_topology.cpp
    datacenter/shortflows.cpp
    datacenter/multihomed_fat_tree_topology.cpp
    datacenter/star_topology.cpp
    datacenter/logsim-interface.cpp
    datacenter/atlahs_htsim_api.cpp
)

add_executable(htsim_uec
    datacenter/main_uec.cpp
    ${DATACENTER_COMMON}
)
target_link_libraries(htsim_uec PRIVATE sim)

add_executable(htsim_ndp
    datacenter/main_ndp.cpp
    ${DATACENTER_COMMON}
)
target_link_libraries(htsim_ndp PRIVATE sim)

add_executable(htsim_eqds
    datacenter/main_eqds.cpp
    ${DATACENTER_COMMON}
)
target_link_libraries(htsim_eqds PRIVATE sim)

# ----------------------------------------------------------------------
# Non-compiled project files (Python sweep tooling, thesis docs).
#
# Listing them under a custom target's SOURCES attribute makes CLion's
# project view pick them up and index them for navigation/editing,
# without CMake trying to build or link them. The target is never
# triggered; it exists only as an editor-visibility hook.
# ----------------------------------------------------------------------
add_custom_target(bcast_scripts SOURCES
    datacenter/connection_matrices/gen_bcast_sweep.py
    datacenter/connection_matrices/run_bcast_sweep.py
    ../plotting/plot_bcast_baseline.py
    datacenter/bcast_tests/run_tests.py
    datacenter/bcast_tests/t01_simple_bcast.cm
    datacenter/bcast_tests/t02_parallel_bcasts.cm
    datacenter/bcast_tests/t03_bcast_with_recv_trigger.cm
    datacenter/bcast_tests/t04_bcast_plus_p2p.cm
    datacenter/bcast_tests/t05_full_group_bcast.cm
    datacenter/bcast_tests/t06_missing_id_rejected.cm
    datacenter/bcast_tests/t07_trigger_started_bcast.cm
    datacenter/bcast_tests/t08_bcast_chain.cm
    datacenter/bcast_tests/t09_p2p_recv_done_trigger.cm
    AA-thesis-baseline.md
    AA-plan-Baseline.md
    AA-htsim-architecture.md
    AA_Notes.md
    datacenter/compact-overview-23.04.26.md
)
