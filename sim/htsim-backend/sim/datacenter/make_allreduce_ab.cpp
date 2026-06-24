// make_allreduce_ab.cpp — GOAL .bin generator for the INC-vs-ring AllReduce A/B.
//
// Emits ONE of two renderings of the SAME logical AllReduce over a group of N
// ranks (hosts 0..N-1), with IDENTICAL framing (no trailing calc) so the GOAL
// "Maximum finishing time at host" oracle == the collective time for both arms:
//
//   arm = inc  : each rank issues one in-network Collective(ALLREDUCE) on group 0
//                (switch reduces + multicasts). Also writes the .groups sidecar
//                ("0 1 ... N-1") the GOAL INC bring-up consumes (-groups).
//   arm = ring : each rank runs a chunked, bandwidth-optimal ring AllReduce out of
//                plain point-to-point Send/Recv (NO INC). 2*(N-1) steps of size/N
//                bytes each -> the textbook 2(N-1)/N * size bytes moved per rank
//                (matches the Hoefler/Khalilov ring cost). Switches only forward.
//
// This is the controlled microbenchmark form of Shuhao's per-domain emit idea
// (TP AllReduce kept whole = INC arm; decomposed = the p2p ring baseline).
//
// Build: g++ -std=c++17 -I.. -I. make_allreduce_ab.cpp -o make_allreduce_ab
// Run:   ./make_allreduce_ab <out.bin> <inc|ring> <num_ranks> <size_bytes> [groups_out]
//
// Dependency(a, b) means "a requires b" (a may not run before b finishes) — the
// convention used by make_coll_test.cpp and verified empirically (the dependent
// op waits). For the ring, step t's Send forwards what step t-1 received, so each
// Send_t (t>=1) requires the previous Recv.
#include "lgs/Goal.hpp"  // pulls in Parser.hpp (OPTYPE_* defines) transitively
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

int main(int argc, char **argv) {
    if (argc < 5) {
        fprintf(stderr,
                "usage: %s <out.bin> <inc|ring> <num_ranks> <size_bytes> "
                "[groups_out]\n"
                "  inc : one in-network Collective(ALLREDUCE) per rank + .groups\n"
                "  ring: chunked bandwidth-optimal p2p ring (2*(N-1) steps of "
                "size/N)\n",
                argv[0]);
        return 1;
    }
    const char *out  = argv[1];
    const char *arm  = argv[2];
    uint32_t N       = (uint32_t)atoi(argv[3]);
    uint64_t size    = (uint64_t)atoll(argv[4]);
    if (N < 2) { fprintf(stderr, "num_ranks must be >= 2\n"); return 1; }

    const bool inc = !strcmp(arm, "inc");
    if (!inc && strcmp(arm, "ring")) {
        fprintf(stderr, "arm must be 'inc' or 'ring' (got '%s')\n", arm);
        return 1;
    }

    // SerializeSchedule appends per-rank, so start from a clean file — otherwise
    // re-generating over an existing .bin doubles the schedule (num_ranks mismatch).
    remove(out);

    if (inc) {
        // INC arm: one whole AllReduce per rank on group 0, instance 1, rootless.
        // No trailing calc -> makespan == collective completion.
        const uint32_t group = 0, instance = 1;
        const int root = -1;
        for (uint32_t r = 0; r < N; r++) {
            Goal *g = new Goal;
            goalop_t a = g->Collective(OPTYPE_ALLREDUCE, group, size, instance, root, 0, 0);
            // A GOAL Collective is offloaded/async and does not advance the host's
            // local clock, so "Maximum finishing time at host" would read 0. A tiny
            // dependent calc (1 time unit) makes the host clock reflect collective
            // completion -> the same oracle works for both arms. The 1-unit tail is
            // identical in both arms and cancels in the A/B.
            goalop_t c = g->Calc(r, 1, 0, 0);
            g->Dependency(c, a);  // c requires a: runs after the collective completes
            g->SetRank(r);
            g->SetNumRanks(N);
            g->SerializeSchedule((char *)out);
            delete g;
        }
        // Sidecar .groups: members are hosts 0..N-1 (one scale-up domain).
        std::string gpath = (argc > 5) ? argv[5] : (std::string(out) + ".groups");
        FILE *gf = fopen(gpath.c_str(), "w");
        if (!gf) { fprintf(stderr, "cannot write groups file %s\n", gpath.c_str()); return 1; }
        for (uint32_t r = 0; r < N; r++) fprintf(gf, "%u%s", r, r + 1 < N ? " " : "\n");
        fclose(gf);
        printf("wrote %s (INC): %u ranks, 1 allreduce each (%llu B); groups -> %s\n",
               out, N, (unsigned long long)size, gpath.c_str());
        return 0;
    }

    // ring arm: chunked bandwidth-optimal ring AllReduce out of plain p2p flows.
    // chunk = size/N; 2*(N-1) pipelined steps. Equal chunks are required for the
    // ring pipeline, so pick sizes that are multiples of N (warn otherwise; the
    // floor chunk slightly under-counts total bytes, which is conservative).
    uint64_t chunk = size / N;
    if (size % N != 0) {
        fprintf(stderr,
                "warning: size %llu not divisible by N=%u; using chunk=%llu "
                "(total %llu < %llu). Prefer multiples of N.\n",
                (unsigned long long)size, N, (unsigned long long)chunk,
                (unsigned long long)(chunk * N), (unsigned long long)size);
    }
    if (chunk == 0) { fprintf(stderr, "size/N == 0; size too small for N\n"); return 1; }
    const uint32_t steps = 2 * (N - 1);

    for (uint32_t r = 0; r < N; r++) {
        Goal *g = new Goal;
        uint32_t dst  = (r + 1) % N;        // ring successor
        uint32_t pred = (r + N - 1) % N;    // ring predecessor
        goalop_t prev_recv = nullptr;
        for (uint32_t t = 0; t < steps; t++) {
            // tag = global step index t; rank pred's Send(pred->r, tag t) matches
            // this rank's Recv(pred->r, tag t).
            goalop_t snd = g->Send(r, dst, chunk, t, 0, 0);
            goalop_t rcv = g->Recv(pred, r, chunk, t, 0, 0);
            // Forward-after-receive: step t sends on what step t-1 delivered.
            if (t >= 1 && prev_recv) g->Dependency(snd, prev_recv);
            prev_recv = rcv;
        }
        // Match the INC arm's framing: a 1-unit calc after the last recv so the
        // host clock (and the "Maximum finishing" oracle) reflects completion.
        goalop_t c = g->Calc(r, 1, 0, 0);
        if (prev_recv) g->Dependency(c, prev_recv);
        g->SetRank(r);
        g->SetNumRanks(N);
        g->SerializeSchedule((char *)out);
        delete g;
    }
    printf("wrote %s (RING): %u ranks, %u steps x %llu B chunk "
           "(%llu B/rank moved, =2(N-1)/N*size); no INC\n",
           out, N, steps, (unsigned long long)chunk,
           (unsigned long long)((uint64_t)steps * chunk));
    return 0;
}
