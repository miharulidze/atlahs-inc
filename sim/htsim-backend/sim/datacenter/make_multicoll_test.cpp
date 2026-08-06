// make_multicoll_test.cpp — minimal MULTI-instance / MULTI-group coll test.
//
// Isolates whether htsim's GOAL coll path hangs on >1 concurrent collective
// (the Phase-5 / Shuhao-trace failure mode), vs the single-collective traces
// (make_coll_test) that complete. NO generator involved — schedules built
// directly via the Goal API.
//
// N ranks, G groups (consecutive blocks of N/G), each rank does K sequential
// AllReduce `coll` ops on ITS group (instances 1..K), chained by a dependent
// 1-unit calc so coll k+1 starts only after coll k completes. Writes a
// <out.bin>.groups sidecar with the G groups.
//
// Build:  g++ -std=c++17 -I.. -I. make_multicoll_test.cpp -o make_multicoll_test
// Run:    ./make_multicoll_test <out.bin> <N> <K_instances> <G_groups> <size>
#include "lgs/Goal.hpp"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>

int main(int argc, char **argv) {
    if (argc < 6) {
        fprintf(stderr, "usage: %s <out.bin> <N> <K_instances> <G_groups> <size>\n", argv[0]);
        return 1;
    }
    const char *out = argv[1];
    uint32_t N = (uint32_t)atoi(argv[2]);
    uint32_t K = (uint32_t)atoi(argv[3]);
    uint32_t G = (uint32_t)atoi(argv[4]);
    uint64_t size = (uint64_t)atoll(argv[5]);
    if (N % G != 0) { fprintf(stderr, "N must be divisible by G\n"); return 1; }
    uint32_t per = N / G;
    remove(out);

    for (uint32_t r = 0; r < N; r++) {
        Goal *g = new Goal;
        uint32_t grp = r / per;                 // group index for this rank
        goalop_t prev = nullptr;
        for (uint32_t k = 1; k <= K; k++) {
            // op_flow_id (the coll instance) must be GLOBALLY UNIQUE across groups
            // (htsim keys the per-op barrier by op_flow_id alone) yet identical
            // across a group's members -> fold the group into it: (grp<<16)|k.
            uint32_t inst = (grp << 16) | k;
            goalop_t a = g->Collective(OPTYPE_ALLREDUCE, grp, size, inst, -1, 0, 0);
            if (prev) g->Dependency(a, prev);   // serialize: coll k after calc k-1
            goalop_t c = g->Calc(r, 1, 0, 0);
            g->Dependency(c, a);                // calc after this coll completes
            prev = c;
        }
        g->SetRank(r);
        g->SetNumRanks(N);
        g->SerializeSchedule((char *)out);
        delete g;
    }
    std::string gp = std::string(out) + ".groups";
    FILE *f = fopen(gp.c_str(), "w");
    for (uint32_t gi = 0; gi < G; gi++)
        for (uint32_t j = 0; j < per; j++)
            fprintf(f, "%u%s", gi * per + j, j + 1 < per ? " " : "\n");
    fclose(f);
    printf("wrote %s: N=%u K=%u G=%u per=%u size=%llu; groups -> %s\n",
           out, N, K, G, per, (unsigned long long)size, gp.c_str());
    return 0;
}
