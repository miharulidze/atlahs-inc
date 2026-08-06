// make_sr_test.cpp — programmatic GOAL .bin generator for the p2p send/recv path.
//
// Exercises AtlahsHtsimApi::Send (the OP_SEND UEC branch fixed in bf7e7ab) and the
// flow_over -> OP_MSG -> posted-recv completion path -- the part the collective
// launcher does NOT touch. Builds N ranks of concurrent flows: even rank r sends to
// r+1, odd rank r+1 receives from r, each followed by a dependent calc that may only
// run once its send/recv has completed (verifies DAG release, not just non-hang).
//
// Build:  g++ -std=c++17 -I.. -I. make_sr_test.cpp -o make_sr_test
// Run:    ./make_sr_test <out.bin> [num_ranks=16] [size=1048576]
#include "lgs/Goal.hpp"  // pulls in Parser.hpp (OPTYPE_* defines) transitively
#include <cstdint>
#include <cstdio>
#include <cstdlib>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <out.bin> [num_ranks=16] [size=1048576]\n", argv[0]);
        return 1;
    }
    const char *out = argv[1];
    uint32_t N = (argc > 2) ? (uint32_t)atoi(argv[2]) : 16;
    uint64_t size = (argc > 3) ? (uint64_t)atoll(argv[3]) : 1048576;
    const uint32_t tag = 0;

    for (uint32_t r = 0; r < N; r++) {
        Goal *g = new Goal;
        if ((r % 2) == 0 && r + 1 < N) {
            // sender: r -> r+1, then a calc that depends on the send finishing
            goalop_t s = g->Send(r, r + 1, size, tag, 0, 0);
            goalop_t c = g->Calc(r, 100, 0, 0);
            g->Dependency(c, s);
        } else if ((r % 2) == 1) {
            // receiver: recv from r-1, then a calc that depends on the recv finishing
            goalop_t rv = g->Recv(r - 1, r, size, tag, 0, 0);
            goalop_t c = g->Calc(r, 100, 0, 0);
            g->Dependency(c, rv);
        } else {
            // odd N leftover rank: a lone calc so every rank has a schedule
            g->Calc(r, 100, 0, 0);
        }
        g->SetRank(r);
        g->SetNumRanks(N);
        g->SerializeSchedule((char *)out);
        delete g;
    }
    printf("wrote %s: %u ranks, %u concurrent flows (r->r+1, %lu B) + dependent calc\n",
           out, N, N / 2, (unsigned long)size);
    return 0;
}
