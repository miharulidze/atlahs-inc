from calendar import c
import os
import argparse
import sys
import subprocess

LOGGOPSIM_DIR = "sim/LogGOPSim"
HTSIM_DIR = "sim/htsim-backend/sim"
ASTRASIM_DIR = "apps/ai/astra-sim"
HPC_GOAL_GEN_DIR = "goal_gen/hpc"
HPC_APPS_DIR = "apps/hpc"
NCCL_GENERATOR_V2_DIR = "goal_gen/ai/nccl_generator_v2_zhiyi"
PCM_SDK_DIR = "sim/pcm-sdk_zhiyi"

ASTRASIM_RUN_SCRIPT = "/workspace/scripts/run_network_analytical.sh"


# Absolute path to the parent directory 
CURR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def print_warning(message: str, verbose: bool = True, flush: bool = True) -> None:
    if verbose:
        # Add color to the message
        print(f"\033[93m{message}\033[0m", file=sys.stderr, flush=flush)

def print_error(message: str, verbose: bool = True, flush: bool = True) -> None:
    if verbose:
        # Add color to the message
        print(f"\033[91m{message}\033[0m", file=sys.stderr, flush=flush)

def print_success(message: str, verbose: bool = True, flush: bool = True) -> None:
    if verbose:
        # Add color to the message
        print(f"\033[92m{message}\033[0m", file=sys.stderr, flush=flush)

def print_info(message: str, verbose: bool = True, flush: bool = True) -> None:
    if verbose:
        # Add color to the message
        print(f"\033[94m{message}\033[0m", file=sys.stderr, flush=flush)


def build_loggopsim(verbose: bool = True) -> None:
    print_info("Building the LogGOPSim...", verbose)
    assert os.path.exists(LOGGOPSIM_DIR), "LogGOPSim not found"
    os.chdir(LOGGOPSIM_DIR)

    # Check if the binary already exists
    if os.path.exists("LogGOPSim"):
        print_warning("LogGOPSim binary already exists. Skipping build.", verbose)
        os.chdir(CURR_DIR)
        return
    
    # Build the binary
    subprocess.run(["make", "clean"], check=True, stdout=sys.stderr, stderr=sys.stderr)
    subprocess.run(["make", "all"], check=True, stdout=sys.stderr, stderr=sys.stderr)
    assert os.path.exists("LogGOPSim"), "LogGOPSim not found"
    assert os.path.exists("txt2bin"), "txt2bin not found"
    print_success("LogGOPSim built successfully", verbose)
    os.chdir(CURR_DIR)
    

def build_htsim(verbose: bool = True) -> None:
    print_info("Building the HTSim...", verbose)
    assert os.path.exists(HTSIM_DIR), "HTSim not found"
    os.chdir(HTSIM_DIR)

    # Build the HTSim binary
    subprocess.run(
        "make clean && cd datacenter/ && make clean && cd .. && "
        "make -j 8 && cd datacenter/ && make -j 8 && cd ..",
        shell=True,
        check=True,
        stdout=sys.stderr,
        stderr=sys.stderr
    )
    print_success("HTSim built successfully", verbose)

    os.chdir(CURR_DIR)


def build_astrasim(verbose: bool = True) -> None:
    print_info("Building the AstraSim...", verbose)
    assert os.path.exists(ASTRASIM_DIR), "AstraSim not found"
    os.chdir(ASTRASIM_DIR)
    # os.system("git submodule update --init --recursive")
    os.chdir("build/astra_analytical/")

    # Check if the binary already exists
    if os.path.exists("build/bin/AstraSim_Analytical_Congestion_Aware") and \
        os.path.exists("build/bin/AstraSim_Analytical_Congestion_Unaware"):
        print_warning("AstraSim binary already exists. Skipping build.", verbose)
        os.chdir(CURR_DIR)
        return

    # Build the binary
    os.system("bash build.sh")
    assert os.path.exists("build/bin/AstraSim_Analytical_Congestion_Aware") and \
        os.path.exists("build/bin/AstraSim_Analytical_Congestion_Unaware"), "AstraSim not found"

    os.chdir(CURR_DIR)
    print_success("AstraSim built successfully", verbose)

    # Copies the run script to the directory in AstraSim
    assert os.path.exists(ASTRASIM_RUN_SCRIPT), "AstraSim run script not found"
    os.system(f"cp {ASTRASIM_RUN_SCRIPT} {ASTRASIM_DIR}/examples/network_analytical/run_network_analytical.sh")


def build_sims(verbose: bool = True) -> None:
    print_info("Building the simulators...")
    build_loggopsim(verbose)
    build_htsim(verbose)
    build_astrasim(verbose)


def build_liballprof(verbose: bool = True) -> None:
    print_info("Building the liballprof...", verbose)
    assert os.path.exists("liballprof"), "liballprof not found"
    os.chdir("liballprof")

    # Check if the binary already exists
    if os.path.exists(".libs/liballprof.so") and \
        os.path.exists(".libs/liballprof_f77.so"):
        print_warning("liballprof binary already exists. Skipping build.", verbose)
        os.chdir("..")
        return

    
    os.system("autoreconf -i")
    os.system("bash setup.sh")
    assert os.path.exists(".libs/liballprof.so"), "liballprof not found"
    assert os.path.exists(".libs/liballprof_f77.so"), "liballprof_f77 not found"
    print_success("liballprof built successfully", verbose)

    os.chdir("..")

def build_liballprof2(verbose: bool = True) -> None:
    print_info("Building the liballprof2...", verbose)
    assert os.path.exists("liballprof2"), "liballprof2 not found"
    os.chdir("liballprof2")

    if os.path.exists("liballprof.so") and \
        os.path.exists("liballprof_f77.so"):
        print_warning("liballprof2 binary already exists. Skipping build.", verbose)
        os.chdir("..")
        return
    
    os.system("make")

    assert os.path.exists("liballprof.so"), "liballprof2 not found"
    assert os.path.exists("liballprof_f77.so"), "liballprof2_f77 not found"
    print_success("liballprof2 built successfully", verbose)
    os.chdir("..")

def build_schedgen(verbose: bool = True) -> None:
    print_info("Building the schedgen...", verbose)
    assert os.path.exists("Schedgen"), "schedgen not found"
    os.chdir("Schedgen")

    if os.path.exists("schedgen"):
        print_warning("schedgen binary already exists. Skipping build.", verbose)
        os.chdir("..")
        return

    os.system("make")
    assert os.path.exists("schedgen"), "schedgen not found"
    print_success("schedgen built successfully", verbose)
    os.chdir("..")



def build_hpc_goal_gen(verbose: bool = True) -> None:
    print_info("Building the HPC goal gen...", verbose)
    assert os.path.exists(HPC_GOAL_GEN_DIR), "HPC goal gen not found"
    os.chdir(HPC_GOAL_GEN_DIR)
    build_liballprof(verbose)
    build_liballprof2(verbose)
    build_schedgen(verbose)
    os.chdir(CURR_DIR)


def build_hpc_apps(verbose: bool = True) -> None:
    print_info("Building the HPC apps...", verbose)
    assert os.path.exists(HPC_APPS_DIR), "HPC apps not found"
    os.chdir(HPC_APPS_DIR)

    os.system("python3 build_apps.py -v -j 16 --app cloverleaf")
    
    os.chdir(CURR_DIR)

def build_nccl_generator_v2(verbose: bool = True) -> None:
    print_info("Building nccl_generator_v2_zhiyi...", verbose)
    assert os.path.exists(NCCL_GENERATOR_V2_DIR), \
        "nccl_generator_v2_zhiyi not found (run git submodule update --init --recursive first)"

    os.chdir(NCCL_GENERATOR_V2_DIR)
    try:
        subprocess.run(
            ["uv", "sync"],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr
        )

        print_success("nccl_generator_v2_zhiyi built (env synced) successfully", verbose)
    finally:
        os.chdir(CURR_DIR)

def build_pcm_sdk(verbose: bool = True) -> None:
    print_info("Building pcm-sdk_zhiyi...", verbose)
    assert os.path.exists(PCM_SDK_DIR), \
        "pcm-sdk_zhiyi not found (run git submodule update --init --recursive first)"

    os.chdir(PCM_SDK_DIR)
    try:
        required_dirs = [
            "uet-htsim-patch",
            "uet-htsim/htsim/sim",
            "HTSIM_spcl-patch",
            "HTSIM_spcl/htsim/sim",
            "xxHash",
            "pcm",
        ]
        missing = [d for d in required_dirs if not os.path.exists(d)]
        assert not missing, f"pcm-sdk_zhiyi missing paths: {missing}"

        subprocess.run(
            [
                "bash",
                "-lc",
                "set -euo pipefail; "
                "command -v cmake >/dev/null 2>&1; "
                "cp -rf ./uet-htsim-patch/. ./uet-htsim/htsim/sim/; "
                "cd ./uet-htsim/htsim/sim; "
                "cmake -S . -B build; "
                "cmake --build build --parallel; "
                "cd - >/dev/null; "
                "cp -rf ./HTSIM_spcl-patch/. ./HTSIM_spcl/htsim/sim/; "
                "cd ./HTSIM_spcl/htsim/sim; "
                "cmake -S . -B build; "
                "cmake --build build --parallel; "
                "cd - >/dev/null; "
                "cd xxHash; make; cd - >/dev/null; "
                "cd pcm; python3 build.py --debug --clean --build-htsim-atlahs; cd - >/dev/null"
            ],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr
        )

        print_success("pcm-sdk_zhiyi built successfully", verbose)
    finally:
        os.chdir(CURR_DIR)

def build_all_apps(verbose: bool = True) -> None:
    """
    Build all applications that are required for tracing and producing the GOAL schedules.
    """
    print_info("Building all applications that are required for tracing and producing the GOAL schedules...", verbose)
    build_hpc_goal_gen(verbose)
    build_hpc_apps(verbose)


def build_apps(reproduce: bool, trace: bool, verbose: bool = True) -> None:
    print_info("Building the simulators...")
    if reproduce:
        print_info("Building the simulators for reproducing the results...")
        build_sims(verbose)
        build_nccl_generator_v2(verbose)
        build_pcm_sdk(verbose)
        # FIXME: Ugly code
        assert os.path.exists(HPC_GOAL_GEN_DIR), "HPC goal gen not found"
        os.chdir(HPC_GOAL_GEN_DIR)
        build_schedgen(verbose)
        os.chdir(CURR_DIR)
    elif trace:
        print_info("Building the simulators for tracing and producing the GOAL schedules...")
        build_all_apps(verbose)
        build_nccl_generator_v2(verbose)
        build_pcm_sdk(verbose)
    else:
        print_error("Invalid build option. Please use -r or -t.")
        exit(1)



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the simulators.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print verbose output.")
    parser.add_argument("-r", "--reproduce", action="store_true", help="Build the required applications for reproducing the results, which means only the simulators.")
    parser.add_argument("-t", "--trace", action="store_true", help="Build all applications that are required for tracing and producing the GOAL schedules.")
    args = parser.parse_args()

    build_apps(args.reproduce, args.trace, args.verbose)