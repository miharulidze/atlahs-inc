// make_coll_test.cpp — programmatic GOAL .bin generator for the Phase-4 bridge.
//
// Builds an N-rank schedule where every rank performs ONE in-network collective on
// group 0, instance 1, followed by a dependent compute that may only run after the
// collective completes (exercises the completion -> DAG release path). The text lexer
// for the `coll` verb is Stage-2 work, so we build the binary directly via the Goal
// API, mirroring txt2bin's per-rank SerializeSchedule loop (txt2bin.cpp:2944-2967).
//
// Build:  g++ -std=c++17 -I.. -I. make_coll_test.cpp -o make_coll_test
// Run:    ./make_coll_test <out.bin> [num_ranks=16] [size=4096] [kind=allreduce] [root=-1]
//         kind = allreduce | reduce | bcast | reduce_scatter
#include "lgs/Goal.hpp"  // pulls in Parser.hpp (OPTYPE_* defines) transitively
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr,
                "usage: %s <out.bin> [num_ranks=16] [size=4096] "
                "[kind=allreduce|reduce|bcast|reduce_scatter] [root=-1]\n",
                argv[0]);
        return 1;
    }
    const char *out = argv[1];
    uint32_t N = (argc > 2) ? (uint32_t)atoi(argv[2]) : 16;
    uint64_t size = (argc > 3) ? (uint64_t)atoll(argv[3]) : 4096;
    const char *kind = (argc > 4) ? argv[4] : "allreduce";
    int root = (argc > 5) ? atoi(argv[5]) : -1;

    char optype;
    if (!strcmp(kind, "bcast"))               optype = OPTYPE_BCAST;
    else if (!strcmp(kind, "reduce"))         optype = OPTYPE_REDUCE;
    else if (!strcmp(kind, "reduce_scatter")) optype = OPTYPE_REDUCE_SCATTER;
    else                                      optype = OPTYPE_ALLREDUCE;

    uint32_t group = 0;     // indexes the .groups sidecar (first Grp line)
    uint32_t instance = 1;  // shared op id across all ranks of this collective

    for (uint32_t r = 0; r < N; r++) {
        Goal *g = new Goal;
        goalop_t a = g->Collective(optype, group, size, instance, root, 0, 0);
        goalop_t c = g->Calc(r, 100, 0, 0);
        g->Dependency(c, a);  // c requires a: c may not run before a finishes
        g->SetRank(r);
        g->SetNumRanks(N);
        g->SerializeSchedule((char *)out);
        delete g;
    }
    printf("wrote %s: %u ranks, 1 %s each (group %u, instance %u, %lu B, root %d) "
           "+ dependent calc\n",
           out, N, kind, group, instance, (unsigned long)size, root);
    return 0;
}
