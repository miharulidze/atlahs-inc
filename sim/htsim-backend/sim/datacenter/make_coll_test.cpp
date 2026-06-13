// make_coll_test.cpp — programmatic GOAL .bin generator for the Phase-4 bridge MVP.
//
// Builds an N-rank schedule where every rank performs ONE in-network allreduce
// (OPTYPE_ALLREDUCE) on group 0, instance 1, followed by a dependent compute that
// must only run after the allreduce completes (exercises the completion -> DAG
// release path). The text lexer for the `coll` verb is Stage-2 work, so we build
// the binary directly via the Goal API, mirroring txt2bin's per-rank
// SerializeSchedule loop (txt2bin.cpp:2944-2967).
//
// Build:  g++ -std=c++17 -I.. -I. make_coll_test.cpp -o make_coll_test
// Run:    ./make_coll_test test_coll.bin 16 4096
#include "lgs/Goal.hpp"  // pulls in Parser.hpp (OPTYPE_* defines) transitively
#include <cstdint>
#include <cstdio>
#include <cstdlib>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <out.bin> [num_ranks=16] [size_bytes=4096]\n",
                argv[0]);
        return 1;
    }
    const char *out = argv[1];
    uint32_t N = (argc > 2) ? (uint32_t)atoi(argv[2]) : 16;
    uint64_t size = (argc > 3) ? (uint64_t)atoll(argv[3]) : 4096;
    uint32_t group = 0;     // indexes the .groups sidecar (first Grp line)
    uint32_t instance = 1;  // shared op id across all ranks of this collective

    for (uint32_t r = 0; r < N; r++) {
        Goal *g = new Goal;
        // One allreduce op for this rank, then a dependent compute.
        goalop_t a = g->Collective(OPTYPE_ALLREDUCE, group, size, instance, 0, 0);
        goalop_t c = g->Calc(r, 100, 0, 0);
        g->Dependency(c, a);  // c requires a: c may not run before a finishes
        g->SetRank(r);
        g->SetNumRanks(N);
        g->SerializeSchedule((char *)out);
        delete g;
    }
    printf("wrote %s: %u ranks, 1 allreduce each (group %u, instance %u, %lu B) "
           "+ dependent calc\n",
           out, N, group, instance, (unsigned long)size);
    return 0;
}
