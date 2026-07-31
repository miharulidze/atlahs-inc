// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
//
// Unit tests for ConnectionMatrix::load(istream&) — the .cm collective
// grammar: Grp headers, the five start_* collective verbs, trigger_bcast,
// and the parser-level exit(1) rejections (checked via fork so the parent
// test binary survives). Payload semantics of the sinks are covered by
// main_uec_collective_test.cpp; the driver-level setup guardrails live in
// main_uec.cpp and are exercised by connection_matrices/tests/test_bcast.py.

#include "datacenter/connection_matrix.h"

#include <cstdio>
#include <sstream>
#include <string>
#include <sys/wait.h>
#include <unistd.h>

namespace {

#define ASSERT(cond, label)                                                    \
    do {                                                                       \
        if (!(cond)) {                                                         \
            std::fprintf(stderr, "FAIL  %s [%s line %d]\n", label,             \
                         __FILE__, __LINE__);                                  \
            return 1;                                                          \
        }                                                                      \
    } while (0)

// Parse a .cm text into a fresh ConnectionMatrix; caller asserts on fields.
ConnectionMatrix *parse(const std::string &text, uint32_t n = 16) {
    ConnectionMatrix *cm = new ConnectionMatrix(n);
    std::stringstream ss(text);
    cm->load(ss);
    return cm;
}

// Fork-and-parse: the .cm grammar rejects malformed input with exit(1),
// so rejection tests must run in a child process.
int expect_parse_dies(const std::string &text, const char *label) {
    // Flush before forking: the child's fclose() would otherwise re-flush
    // the parent's buffered output, duplicating earlier PASS lines.
    std::fflush(nullptr);
    pid_t pid = fork();
    if (pid == 0) {
        // Child: silence the parser's error output, then parse. If load()
        // returns instead of exiting, report success (which the parent
        // treats as a test failure).
        std::fclose(stderr);
        std::fclose(stdout);
        std::stringstream ss(text);
        ConnectionMatrix cm(16);
        cm.load(ss);
        _exit(0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    const bool rejected = WIFEXITED(status) && WEXITSTATUS(status) != 0;
    if (!rejected) {
        std::fprintf(stderr, "FAIL  %s [parser accepted malformed input]\n", label);
        return 1;
    }
    return 0;
}

// 1. Groups parse in file order; membership preserved.
int test_grp_parsing() {
    ConnectionMatrix *cm = parse(
        "Nodes 16\n"
        "Grp 1 2 3 6 9 12 14\n"
        "Grp 5 7 10 13 15\n"
        "Connections 1\n"
        "0->0 id 1 start_bcast 0 size 4096\n");
    ASSERT(cm->N == 16, "Nodes parsed");
    ASSERT(cm->groups.size() == 2, "two groups");
    ASSERT(cm->groups[0].size() == 7 && cm->groups[0][0] == 1
           && cm->groups[0][6] == 14, "group 0 members in order");
    ASSERT(cm->groups[1].size() == 5 && cm->groups[1][4] == 15,
           "group 1 members in order");
    delete cm;
    return 0;
}

// 2. Each of the five collective verbs sets exactly its own flag.
int test_collective_verbs() {
    struct Verb {
        const char *token;
        bool connection::*flag;
    };
    const Verb verbs[] = {
        {"start_bcast",          &connection::is_bcast},
        {"start_reduce",         &connection::is_reduce},
        {"start_allreduce",      &connection::is_allreduce},
        {"start_reduce_scatter", &connection::is_reduce_scatter},
        {"start_allgather",      &connection::is_allgather},
    };
    for (const Verb &v : verbs) {
        std::string cmtext = std::string("Nodes 16\nGrp 0 4 8 12\nConnections 1\n")
            + "0->0 id 1 " + v.token + " 0 size 16384\n";
        ConnectionMatrix *cm = parse(cmtext);
        ASSERT(cm->conns->size() == 1, "one connection");
        connection *c = (*cm->conns)[0];
        int set = static_cast<int>(c->is_bcast) + static_cast<int>(c->is_reduce)
                + static_cast<int>(c->is_allreduce)
                + static_cast<int>(c->is_reduce_scatter)
                + static_cast<int>(c->is_allgather);
        ASSERT(set == 1, "exactly one collective flag set");
        ASSERT(c->*(v.flag), "the verb's own flag set");
        ASSERT(c->flowid == 1 && c->size == 16384 && c->start == 0,
               "id/size/start parsed");
        delete cm;
    }
    return 0;
}

// 3. trigger_bcast marks the connection as bcast AND trigger-started;
//    send_done_trigger on the feeding connection is recorded.
int test_trigger_bcast() {
    ConnectionMatrix *cm = parse(
        "Nodes 16\n"
        "Grp 0 4 8 12\n"
        "Connections 2\n"
        "Triggers 1\n"
        "0->0 id 1 start_bcast 0 size 4096 send_done_trigger 5\n"
        "4->4 id 2 trigger_bcast 5 size 4096\n"
        "trigger id 5 oneshot\n");
    ASSERT(cm->conns->size() == 2, "two connections");
    connection *c = (*cm->conns)[1];
    ASSERT(c->is_bcast, "trigger_bcast sets is_bcast");
    ASSERT(c->trigger == 5, "trigger id recorded");
    ASSERT((*cm->conns)[0]->send_done_trigger == 5, "send_done_trigger recorded");
    delete cm;
    return 0;
}

// 4-6. Parser-level rejections (each exits nonzero in a child process).
int test_reject_flowid_zero() {
    return expect_parse_dies(
        "Nodes 16\nGrp 0 4 8 12\nConnections 1\n"
        "0->0 id 0 start_bcast 0 size 4096\n",
        "flow id zero rejected");
}

int test_reject_unknown_token() {
    return expect_parse_dies(
        "Nodes 16\nGrp 0 4 8 12\nConnections 1\n"
        "0->0 id 1 start_frobnicate 0 size 4096\n",
        "unknown verb rejected");
}

int test_reject_bcast_without_ids() {
    // If any connection is a bcast, EVERY connection needs an explicit id.
    return expect_parse_dies(
        "Nodes 16\nGrp 0 4 8 12\nConnections 2\n"
        "0->0 id 1 start_bcast 0 size 4096\n"
        "1->2 start 0 size 4096\n",
        "bcast requires ids on all connections");
}

}  // namespace

int main() {
    int failed = 0;
    struct {
        const char *name;
        int (*fn)();
    } tests[] = {
            {"grp_parsing",              test_grp_parsing},
            {"collective_verbs",         test_collective_verbs},
            {"trigger_bcast",            test_trigger_bcast},
            {"reject_flowid_zero",       test_reject_flowid_zero},
            {"reject_unknown_token",     test_reject_unknown_token},
            {"reject_bcast_without_ids", test_reject_bcast_without_ids},
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
