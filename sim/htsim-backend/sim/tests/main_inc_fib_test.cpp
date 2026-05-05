// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
//
// Unit tests for INCFib + INCFibEntry (phase-two switch FIB).
// Self-contained; no event-list, no topology. Returns 0 iff all
// pass.

#include "inc_fib.h"
#include "route.h"

#include <cstdio>

namespace {

#define ASSERT(cond, label)                                                    \
    do {                                                                       \
        if (!(cond)) {                                                         \
            std::fprintf(stderr, "FAIL  %s [%s line %d]\n", label,             \
                         __FILE__, __LINE__);                                  \
            return 1;                                                          \
        }                                                                      \
    } while (0)

int test_install_lookup() {
    INCFib fib;
    INCFibEntry *e = new INCFibEntry();
    fib.install(/*group_id=*/42, e);
    ASSERT(fib.lookup(42) == e, "lookup returns installed entry");
    ASSERT(fib.lookup(43) == nullptr, "lookup of unknown group returns null");
    delete e;
    return 0;
}

int test_bitmask_set_query() {
    INCFibEntry e;
    e.tree_port_mask.set(5);
    e.tree_port_mask.set(17);
    e.tree_port_mask.set(99);
    ASSERT(e.tree_port_mask.count() == 3, "popcount = 3");
    ASSERT(e.tree_port_mask.test(5), "bit 5 set");
    ASSERT(e.tree_port_mask.test(17), "bit 17 set");
    ASSERT(e.tree_port_mask.test(99), "bit 99 set");
    ASSERT(!e.tree_port_mask.test(6), "bit 6 unset");
    return 0;
}

int test_leaf_route_lookup() {
    INCFibEntry e;
    Route *r3 = new Route();
    Route *r8 = new Route();
    e.leaf_routes.emplace_back(3, r3);
    e.leaf_routes.emplace_back(8, r8);
    ASSERT(e.leaf_route_for(3) == r3, "lookup port 3");
    ASSERT(e.leaf_route_for(8) == r8, "lookup port 8");
    ASSERT(e.leaf_route_for(5) == nullptr,
           "lookup unknown port returns null");
    delete r3;
    delete r8;
    return 0;
}

}  // namespace

int main() {
    int failed = 0;
    struct {
        const char *name;
        int (*fn)();
    } tests[] = {
            {"install_lookup",     test_install_lookup},
            {"bitmask_set_query",  test_bitmask_set_query},
            {"leaf_route_lookup",  test_leaf_route_lookup},
    };
    for (auto &t : tests) {
        if (t.fn() == 0) {
            std::printf("  PASS  %s\n", t.name);
        } else {
            std::printf("  FAIL  %s\n", t.name);
            ++failed;
        }
    }
    int total = static_cast<int>(sizeof(tests) / sizeof(tests[0]));
    std::printf("\n%d/%d passed, %d failed\n", total - failed, total, failed);
    return failed == 0 ? 0 : 1;
}
