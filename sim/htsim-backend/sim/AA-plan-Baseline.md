# Plan

read INC_thesis_proposal.md to get a general overview of the project.

Your Task for now is to establish a Baseline for The Broadcast operation (currently labeled as mcast). We'll adjust the naming and
go with the following implementation: traffic matrix .cm contains a Broadcast operation with the group members listed before the traffic patterns
(currently already there but labeled as mcast (see mcast_test.cm, note: mc 0->0 means root is index 0 of group with index 0 so in mcast_test.cm
the root would be 1)).

Then the simulation main_uec.cpp is run. at line 752 we start parsing the traffic matrix. this involves
the connection_matrix.cpp file specifically the load method. The Topology we consider will always
be a FatTreeTopology. Then we add the collective groups to the top class. For now you can ignore
the top->set_up_mcast() as we'll only begin with P2P emulation.

As mentioned we first establish a Baseline. concretely we'll have a P2P baseline. We have the following
Assumptions:
1)  traffic payload fit into MTU i.e single packet
2) There is no packet loss or corruption (thus we don't need any ACKS)
3) The switches have infinite Buffers (irrelevant for now)

The issue we currently have is that regular P2P send traffic automatically creates a SRC + SINK and enforces
a matching RECV operation on the other end and automatically triggers an ACK packet. We don't want any ACK
packets for collective operations. Figure out whats the best way to handle this, maybe we create a new src class?
Come up with a detailed plan that I can overview and approve/change.

after that your task will be implementing the P2P emulation and test it's performance/timing
for several different traffic patterns. Begin with the mcast_test.cm (rename to bcast_test1.cm)
then create more tests with larger topology and larger collective groups. create plots that benchmark the
performance but follow the guidelines (see hoefler_scientific_benchmarking.md)

one final note: there is some partial implemented code for this idea that you can use or discard 
depending on your approach. To understand what is new and what was there in the start, check the git log. 
All changes concerning the -goal path can be ingored.


# Claude: IGNORE THIS: 
PROMPT:
Read AA-plan-Baseline.md carefully. Act as a Senior Network Systems Engineer. Your first objective is
ONLY phase one: analyze main_uec.cpp, connection_matrix.cpp, the base SRC/SINK classes, and the git
log. The core challenge is that regular P2P traffic enforces matching RECVs and triggers ACKs, which
we do not necessarily want for collective operations. Investigate the codebase and propose the BEST
architectural approaches to achieve ACK-less P2P emulation. Outline the pros and cons of your
proposed solutions, then stop and wait for my approval.