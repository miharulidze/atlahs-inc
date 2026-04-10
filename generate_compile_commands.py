#!/usr/bin/env python3
"""
Generate compile_commands.json for CLion IDE support.

This does NOT build the project -- it just tells CLion how each file
would be compiled so that code navigation, completion, and error
highlighting work correctly. The actual build still happens in Docker.

Re-run this script whenever you add/remove source files.
"""

import json
import os
import glob as globmod

ROOT = os.path.dirname(os.path.abspath(__file__))

def find_sources(base_dir, extensions=(".cpp", ".cc", ".c"), recursive=False):
    """Find source files under base_dir with given extensions."""
    sources = []
    if recursive:
        for root, _, files in os.walk(base_dir):
            for f in files:
                if any(f.endswith(ext) for ext in extensions):
                    sources.append(os.path.join(root, f))
    else:
        for ext in extensions:
            sources.extend(globmod.glob(os.path.join(base_dir, f"*{ext}")))
    return sorted(sources)


def make_entry(directory, file_path, compiler, flags, includes=()):
    """Create a single compile_commands.json entry."""
    inc_flags = " ".join(f"-I{d}" for d in includes)
    command = f"{compiler} {flags} {inc_flags} -c {file_path}".strip()
    # Collapse multiple spaces
    command = " ".join(command.split())
    return {
        "directory": directory,
        "command": command,
        "file": file_path,
    }


def main():
    entries = []

    # ── 1. HTSim backend: sim/ root ──────────────────────────────────────
    htsim_sim = os.path.join(ROOT, "sim", "htsim-backend", "sim")
    htsim_flags = "-Wall -std=c++11 -g -Wsign-compare -Wuninitialized -fPIE -O3"
    for src in find_sources(htsim_sim, extensions=(".cpp",)):
        entries.append(make_entry(htsim_sim, src, "g++", htsim_flags))

    # ── 2. HTSim backend: sim/datacenter/ ────────────────────────────────
    htsim_dc = os.path.join(htsim_sim, "datacenter")
    htsim_dc_flags = "-Wall -std=c++11 -g -Wsign-compare -O3"
    htsim_dc_includes = [htsim_sim, htsim_dc]
    for src in find_sources(htsim_dc, extensions=(".cpp",), recursive=True):
        entries.append(make_entry(htsim_dc, src, "g++", htsim_dc_flags, htsim_dc_includes))

    # ── 3. HTSim backend: sim/tests/ ─────────────────────────────────────
    htsim_tests = os.path.join(htsim_sim, "tests")
    htsim_tests_flags = "-Wall -std=c++11 -g -Wsign-compare -Wuninitialized -O2"
    htsim_tests_includes = [htsim_sim, htsim_tests]
    for src in find_sources(htsim_tests, extensions=(".cpp",)):
        entries.append(make_entry(htsim_tests, src, "g++", htsim_tests_flags, htsim_tests_includes))

    # ── 4. HTSim backend: sim/lgs/ (LogGOPSim copy inside htsim) ────────
    htsim_lgs = os.path.join(htsim_sim, "lgs")
    lgs_flags = "-O3 -g -pedantic -Wno-deprecated -Wall -Wno-long-long"
    for src in find_sources(htsim_lgs, extensions=(".cpp", ".c")):
        entries.append(make_entry(htsim_lgs, src, "g++", lgs_flags))

    # ── 5. LogGOPSim ─────────────────────────────────────────────────────
    loggopsim = os.path.join(ROOT, "sim", "LogGOPSim")
    for src in find_sources(loggopsim, extensions=(".cpp", ".c")):
        entries.append(make_entry(loggopsim, src, "g++", lgs_flags))

    # ── 6. AstraSim core ─────────────────────────────────────────────────
    astrasim = os.path.join(ROOT, "apps", "ai", "astra-sim")
    astrasim_flags = "-std=c++17 -O2"
    astrasim_includes = [
        astrasim,
        os.path.join(astrasim, "extern", "graph_frontend", "chakra"),
        os.path.join(astrasim, "extern", "graph_frontend", "chakra", "schema", "protobuf"),
        os.path.join(astrasim, "extern", "graph_frontend", "chakra", "src", "third_party", "utils"),
        os.path.join(astrasim, "extern", "helper"),
        os.path.join(astrasim, "extern", "helper", "fmt", "include"),
        os.path.join(astrasim, "extern", "helper", "spdlog", "include"),
    ]
    # Core AstraSim sources (matching CMakeLists.txt globs)
    astrasim_src_patterns = [
        "astra-sim/system/*.cc",
        "astra-sim/workload/*.cc",
        "astra-sim/system/collective/*.cc",
        "astra-sim/system/topology/*.cc",
        "astra-sim/system/memory/*.cc",
        "astra-sim/system/scheduling/*.cc",
        "astra-sim/common/*.cc",
        "extern/graph_frontend/chakra/src/third_party/utils/*.cc",
        "extern/graph_frontend/chakra/schema/protobuf/*.cc",
        "extern/graph_frontend/chakra/src/feeder/*.cpp",
        "extern/remote_memory_backend/analytical/*.cc",
    ]
    for pattern in astrasim_src_patterns:
        for src in sorted(globmod.glob(os.path.join(astrasim, pattern))):
            entries.append(make_entry(astrasim, src, "g++", astrasim_flags, astrasim_includes))

    # ── 7. AstraSim network frontend (analytical) ────────────────────────
    nf_analytical = os.path.join(astrasim, "astra-sim", "network_frontend", "analytical")
    nf_includes = astrasim_includes + [
        os.path.join(astrasim, "extern", "network_backend", "analytical", "include"),
        os.path.join(astrasim, "extern", "network_backend", "analytical", "include", "astra-network-analytical"),
        os.path.join(astrasim, "extern", "network_backend", "analytical", "extern", "yaml-cpp", "include"),
    ]
    for src in find_sources(nf_analytical, extensions=(".cc", ".cpp"), recursive=True):
        entries.append(make_entry(nf_analytical, src, "g++", astrasim_flags, nf_includes))

    # ── 8. AstraSim network backend (analytical) ─────────────────────────
    nb_analytical = os.path.join(astrasim, "extern", "network_backend", "analytical")
    nb_includes = [
        os.path.join(nb_analytical, "include"),
        os.path.join(nb_analytical, "include", "astra-network-analytical"),
        os.path.join(nb_analytical, "extern"),
        os.path.join(nb_analytical, "extern", "yaml-cpp", "include"),
    ]
    nb_src_patterns = [
        "common/*.cpp",
        "common/event-queue/*.cpp",
        "common/network-parser/*.cpp",
        "congestion_unaware/topology/*.cpp",
        "congestion_unaware/basic-topology/*.cpp",
        "congestion_unaware/multi-dim-topology/*.cpp",
        "congestion_aware/network/*.cpp",
        "congestion_aware/topology/*.cpp",
        "congestion_aware/basic-topology/*.cpp",
        "congestion_unaware/example.cpp",
        "congestion_aware/example.cpp",
    ]
    for pattern in nb_src_patterns:
        for src in sorted(globmod.glob(os.path.join(nb_analytical, pattern))):
            entries.append(make_entry(nb_analytical, src, "g++", astrasim_flags, nb_includes))

    # ── 9. Schedgen (goal generator) ─────────────────────────────────────
    schedgen = os.path.join(ROOT, "goal_gen", "hpc", "Schedgen")
    schedgen_flags = "-g -O3 -Wno-deprecated -Wall"
    if os.path.isdir(schedgen):
        for src in find_sources(schedgen, extensions=(".cpp", ".c")):
            entries.append(make_entry(schedgen, src, "g++", schedgen_flags))

    # ── Deduplicate (same file might match multiple patterns) ────────────
    seen = set()
    unique = []
    for e in entries:
        if e["file"] not in seen:
            seen.add(e["file"])
            unique.append(e)

    out_path = os.path.join(ROOT, "compile_commands.json")
    with open(out_path, "w") as f:
        json.dump(unique, f, indent=2)

    print(f"Wrote {len(unique)} entries to {out_path}")


if __name__ == "__main__":
    main()
