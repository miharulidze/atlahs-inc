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
