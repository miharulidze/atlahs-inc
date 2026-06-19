import os
import argparse
import shutil
from pathlib import Path
from typing import Optional


# ── Paths ─────────────────────────────────────────────────────────────────────

OUTPUT_DIR = "/workspace/data/validation_zhiyi/"

PCM_APP_HTSIM_ATLAHS_EXEC_PATH = "/workspace/sim/pcm-sdk_zhiyi/pcm/build/bin/htsim_flow_app_atlahs"

TOPO_FILES_PATH        = "/workspace/scripts/topo_files_zhiyi"
PCM_CC_CONFIG_FILES_PATH = "/workspace/scripts/pcm_cc_configs"

NETWORK_TOPO    = "tree1024_bw200Gbps.topo"
NIC_SPEED       = 200000
INTRANODE_TOPO  = "tree16_bw3600Gbps.topo"
COPY_ENG_SPEED  = 3600000

# ── Experiment cases ───────────────────────────────────────────────────────────

NETWORK_TOPO_TYPE_CASES = [
    {"case_name": "fattree", "network_topo_type": "fattree", "network_topo_file": "tree1024_bw200Gbps.topo"},
    {"case_name": "dragonfly_p32a4h2", "network_topo_type": "dragonfly", "network_topo_file": "dragonfly/p32a4h2"},
    {"case_name": "dragonfly_p4a8h4", "network_topo_type": "dragonfly", "network_topo_file": "dragonfly/p4a8h4"},
    {"case_name": "slimfly_p32q4", "network_topo_type": "slimfly", "network_topo_file": "slimfly/p32q4"},
    {"case_name": "slimfly_p7q9", "network_topo_type": "slimfly", "network_topo_file": "slimfly/p7q9"},
]

INTRANODE_BW_CASES = [
    {"case_name": "tree16_bw12800Gbps", "intranode_topo": "tree16_bw12800Gbps.topo", "intranode_linkspeed": 12800000},
    {"case_name": "tree16_bw6400Gbps", "intranode_topo": "tree16_bw6400Gbps.topo", "intranode_linkspeed": 6400000},
    {"case_name": "tree16_bw3600Gbps", "intranode_topo": "tree16_bw3600Gbps.topo", "intranode_linkspeed": 3600000},
    {"case_name": "tree16_bw3200Gbps", "intranode_topo": "tree16_bw3200Gbps.topo", "intranode_linkspeed": 3200000},
    {"case_name": "tree16_bw1600Gbps", "intranode_topo": "tree16_bw1600Gbps.topo", "intranode_linkspeed": 1600000},
    {"case_name": "tree16_bw800Gbps", "intranode_topo": "tree16_bw800Gbps.topo", "intranode_linkspeed": 800000},
    {"case_name": "tree16_bw400Gbps", "intranode_topo": "tree16_bw400Gbps.topo", "intranode_linkspeed": 400000},
    {"case_name": "tree16_bw200Gbps", "intranode_topo": "tree16_bw200Gbps.topo", "intranode_linkspeed": 200000},
    {"case_name": "tree16_bw100Gbps", "intranode_topo": "tree16_bw100Gbps.topo", "intranode_linkspeed": 100000},
]

NETWORK_BW_CASES = [
    {"case_name": "tree1024_bw12800Gbps", "topo": "tree1024_bw12800Gbps.topo", "linkspeed": 12800000},
    {"case_name": "tree1024_bw6400Gbps", "topo": "tree1024_bw6400Gbps.topo", "linkspeed": 6400000},
    {"case_name": "tree1024_bw3600Gbps", "topo": "tree1024_bw3600Gbps.topo", "linkspeed": 3600000},
    {"case_name": "tree1024_bw3200Gbps", "topo": "tree1024_bw3200Gbps.topo", "linkspeed": 3200000},
    {"case_name": "tree1024_bw1600Gbps", "topo": "tree1024_bw1600Gbps.topo", "linkspeed": 1600000},
    {"case_name": "tree1024_bw1000Gbps", "topo": "tree1024_bw1000Gbps.topo", "linkspeed": 1000000},
    {"case_name": "tree1024_bw800Gbps", "topo": "tree1024_bw800Gbps.topo", "linkspeed": 800000},
    {"case_name": "tree1024_bw600Gbps", "topo": "tree1024_bw600Gbps.topo", "linkspeed": 600000},
    {"case_name": "tree1024_bw400Gbps", "topo": "tree1024_bw400Gbps.topo", "linkspeed": 400000},
    {"case_name": "tree1024_bw200Gbps", "topo": "tree1024_bw200Gbps.topo", "linkspeed": 200000},
    {"case_name": "tree1024_bw100Gbps", "topo": "tree1024_bw100Gbps.topo", "linkspeed": 100000},
]

PCM_CC_CONFIG_CASES = [
    {"case_name": "cubic", "pcm_cc_config_file": "pcm_cc_config_all_cubic.json"},
    {"case_name": "cubic_v2", "pcm_cc_config_file": "pcm_cc_config_all_cubic_v2.json"},

    {"case_name": "dcqcn", "pcm_cc_config_file": "pcm_cc_config_all_dcqcn.json"},  ## abnormal

    {"case_name": "dctcp", "pcm_cc_config_file": "pcm_cc_config_all_dctcp.json"},
    {"case_name": "dctcp_v2", "pcm_cc_config_file": "pcm_cc_config_all_dctcp_v2.json"},

    {"case_name": "momentum", "pcm_cc_config_file": "pcm_cc_config_all_momentum.json"},

    {"case_name": "newreno", "pcm_cc_config_file": "pcm_cc_config_all_newreno.json"},
    {"case_name": "newreno_v2", "pcm_cc_config_file": "pcm_cc_config_all_newreno_v2.json"},

    {"case_name": "nscc", "pcm_cc_config_file": "pcm_cc_config_all_nscc.json"},
    {"case_name": "nscc_v2", "pcm_cc_config_file": "pcm_cc_config_all_nscc_v2.json"},

    {"case_name": "smartt", "pcm_cc_config_file": "pcm_cc_config_all_smartt.json"},
    {"case_name": "smartt_v2", "pcm_cc_config_file": "pcm_cc_config_all_smartt_v2.json"},

    {"case_name": "strack_light", "pcm_cc_config_file": "pcm_cc_config_all_strack_light.json"},
    {"case_name": "strack_light_v2", "pcm_cc_config_file": "pcm_cc_config_all_strack_light_v2.json"},

    {"case_name": "strack", "pcm_cc_config_file": "pcm_cc_config_all_strack.json"},
    {"case_name": "strack_v2", "pcm_cc_config_file": "pcm_cc_config_all_strack_v2.json"},

    {"case_name": "swift", "pcm_cc_config_file": "pcm_cc_config_all_swift.json"},
    {"case_name": "swift_v2", "pcm_cc_config_file": "pcm_cc_config_all_swift_v2.json"},

    {"case_name": "uec_dctcp", "pcm_cc_config_file": "pcm_cc_config_all_uec_dctcp.json"},
    {"case_name": "uec_dctcp_v2", "pcm_cc_config_file": "pcm_cc_config_all_uec_dctcp_v2.json"},
]

# VT_OPT_CASES = [
    # {"case_name": "vt_no_opt", "goal": "/workspace/data/ai/llama/Llama7B_N4_GPU16_TP1_PP1_DP16_BS32/Llama7B_N4_GPU16_TP1_PP1_DP16_BS32.bin"},
    # {"case_name": "vt_opt", "goal": "/workspace/data/ai/llama/Llama7B_N4_GPU16_TP1_PP1_DP16_BS32/vt_opt/Llama7B_N4_GPU16_TP1_PP1_DP16_BS32.bin"},
# ]

# ── Jobs ──────────────────────────────────────────────────────────────────────

JOBS = [
    "Llama7B_N4_GPU16_TP1_PP1_DP16_BS32_native",
    "Llama7B_N4_GPU16_TP1_PP1_DP16_BS32_synthetic",
    "Llama7B_N4_GPU16_TP1_PP2_DP8_BS32_synthetic",
    "Llama7B_N4_GPU16_TP2_PP1_DP8_BS32_synthetic",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def print_warning(message: str) -> None:
    print(f"\033[93m[WARNING] {message}\033[0m", flush=True)

def print_error(message: str) -> None:
    print(f"\033[91m[ERROR] {message}\033[0m", flush=True)

def print_success(message: str) -> None:
    print(f"\033[92m[SUCCESS] {message}\033[0m", flush=True)

def print_info(message: str) -> None:
    print(f"[INFO] {message}", flush=True)


def parse_htsim_max_host_time(log_file: str) -> Optional[int]:
    host_times = []
    with open(log_file, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("Host"):
                parts = line.split(":")
                if len(parts) >= 2:
                    try:
                        host_times.append(int(parts[1].strip()))
                    except ValueError:
                        pass
    return max(host_times) if host_times else None


# ── Main logic ────────────────────────────────────────────────────────────────

def run_exp_from_goal_workloads(goal_workloads_dir: str, overwrite: bool) -> None:
    """
    Iterates over .bin files specified in JOBS (or all .bin files if JOBS is empty)
    and runs all simulation experiments (PCM_HTSIM_ATLAHS cases) for each one.
    """
    goal_workloads_dir = os.path.abspath(goal_workloads_dir)
    assert os.path.exists(goal_workloads_dir), \
        f"Goal workloads directory {goal_workloads_dir} does not exist."

    if JOBS:
        bin_files = []
        for name in JOBS:
            p = Path(goal_workloads_dir) / f"{name}.bin"
            if not p.exists():
                print_error(f"File not found, skipping: {p}")
            else:
                bin_files.append(p)
    else:
        bin_files = sorted(Path(goal_workloads_dir).glob("*.bin"))

    if not bin_files:
        print_error(f"No .bin files found in {goal_workloads_dir}")
        return
    print_info(f"Found {len(bin_files)} .bin files: {[f.name for f in bin_files]}")

    output_base_dir = os.path.join(OUTPUT_DIR, "goal_workloads")

    if overwrite and os.path.exists(output_base_dir):
        shutil.rmtree(output_base_dir)
        print_success("Overwriting existing results...")

    os.makedirs(output_base_dir, exist_ok=True)

    # Initialize CSV files
    csv_network_topo_type = os.path.join(output_base_dir, "htsim_atlahs_network_topo_type_cases.csv")
    csv_intranode_bw    = os.path.join(output_base_dir, "htsim_atlahs_intranode_cases.csv")
    csv_pcm_cc_configs   = os.path.join(output_base_dir, "pcm_htsim_atlahs_pcm_cc_configs_cases.csv")
    csv_network_bw   = os.path.join(output_base_dir, "htsim_atlahs_network_bw_cases.csv")

    if not os.path.exists(csv_network_topo_type):
        with open(csv_network_topo_type, "w") as f:
            f.write("workload,case_name,network_topo_type,topo,linkspeed,intranode_topo,intranode_linkspeed,max_host_time,log_file,command\n")
    if not os.path.exists(csv_intranode_bw):
        with open(csv_intranode_bw, "w") as f:
            f.write("workload,case_name,topo,linkspeed,intranode_topo,intranode_linkspeed,max_host_time,log_file,command\n")
    if not os.path.exists(csv_pcm_cc_configs):
        with open(csv_pcm_cc_configs, "w") as f:
            f.write("workload,case_name,topo,linkspeed,intranode_topo,intranode_linkspeed,max_host_time,log_file,command\n")
    if not os.path.exists(csv_network_bw):
        with open(csv_network_bw, "w") as f:
            f.write("workload,case_name,topo,linkspeed,intranode_topo,intranode_linkspeed,max_host_time,log_file,command\n")

    for bin_file in bin_files:
        goal       = str(bin_file)
        inner_name = bin_file.stem

        htsim_output_dir = os.path.join(output_base_dir, inner_name, "htsim_output")
        os.makedirs(htsim_output_dir, exist_ok=True)

        print_info(f"=== Running experiments for {inner_name} ===")

        # --- network topo cases ---
        for case in NETWORK_TOPO_TYPE_CASES:
            case_name        = case["case_name"]
            network_topo_type = case["network_topo_type"]
            network_topo_file = case["network_topo_file"]
            pcm_cc_config_file = "pcm_cc_config_all_uec_dctcp.json"
            log_file = os.path.join(htsim_output_dir, f"htsim_output_{case_name}.tmp")
            # cmd = (
            #     f"{PCM_APP_HTSIM_ATLAHS_EXEC_PATH} "
            #     f"-topo_type {network_topo_type} -topo {TOPO_FILES_PATH}/{network_topo_file} -linkspeed {NIC_SPEED} -q 1000000 "
            #     f"-intranode_topo {TOPO_FILES_PATH}/{INTRANODE_TOPO} -intranode_linkspeed {COPY_ENG_SPEED} -intranode_q 1000000 "
            #     f"-nodes 1024 -num_gpus_per_node 4 "
            #     f"-goal {goal} "
            #     f"-end 100000000000 "
            #     f"-sender_cc_only "
            #     f"> {log_file} "
            # )
            cmd = (
                f"{PCM_APP_HTSIM_ATLAHS_EXEC_PATH} "
                f"-topo_type {network_topo_type} -topo {TOPO_FILES_PATH}/{network_topo_file} -linkspeed {NIC_SPEED} -q 1000000 "
                f"-intranode_topo {TOPO_FILES_PATH}/{INTRANODE_TOPO} -intranode_linkspeed {COPY_ENG_SPEED} -intranode_q 1000000 "
                f"-strat ecmp_host -seed 42 -mtu 4096 -paths 128 "
                f"-nodes 1024 -num_gpus_per_node 4 "
                f"-goal {goal} "
                f"-end 100000000000 "
                f"-sender_cc_only "
                f"-pcm_enable -pcm_cc_config_file {PCM_CC_CONFIG_FILES_PATH}/{pcm_cc_config_file} -pcm_sched_poll_delay 1000 -pcm_handler_delay 1000 "
                f"> {log_file} "
            )
            print_info(f"[{inner_name}][{case_name}] Running: {cmd}")
            assert os.system(cmd) == 0, f"Error running HTSim for {inner_name}, case={case_name}."
            max_host_time = parse_htsim_max_host_time(log_file)
            print_info(f"[{inner_name}][{case_name}] max_host_time={max_host_time}")
            with open(csv_network_topo_type, "a") as f:
                f.write(
                    f"{inner_name},{case_name},{network_topo_type},{network_topo_file},{NIC_SPEED},"
                    f"{INTRANODE_TOPO},{COPY_ENG_SPEED},{max_host_time},{log_file},{cmd}\n"
                )
            print_success(f"[{inner_name}][{case_name}] Appended to {csv_network_topo_type}")

        # --- intranode topo cases ---
        for case in INTRANODE_BW_CASES:
            case_name         = case["case_name"]
            intranode_topo    = case["intranode_topo"]
            intranode_linkspeed = case["intranode_linkspeed"]
            pcm_cc_config_file = "pcm_cc_config_all_uec_dctcp.json"
            log_file = os.path.join(htsim_output_dir, f"htsim_output_{case_name}.tmp")
            # cmd = (
            #     f"{PCM_APP_HTSIM_ATLAHS_EXEC_PATH} "
            #     f"-topo {TOPO_FILES_PATH}/{NETWORK_TOPO} -linkspeed {NIC_SPEED} -q 1000000 "
            #     f"-intranode_topo {TOPO_FILES_PATH}/{intranode_topo} -intranode_linkspeed {intranode_linkspeed} -intranode_q 1000000 "
            #     f"-strat ecmp_host -mtu 4096 -paths 128 "
            #     f"-nodes 1024 -num_gpus_per_node 4 "
            #     f"-goal {goal} "
            #     f"-end 100000000000 "
            #     f"-sender_cc_only "
            #     f"> {log_file} "
            # )
            cmd = (
                f"{PCM_APP_HTSIM_ATLAHS_EXEC_PATH} "
                f"-topo {TOPO_FILES_PATH}/{NETWORK_TOPO} -linkspeed {NIC_SPEED} -q 1000000 "
                f"-intranode_topo {TOPO_FILES_PATH}/{intranode_topo} -intranode_linkspeed {intranode_linkspeed} -intranode_q 1000000 "
                f"-strat ecmp_host -seed 42 -mtu 4096 -paths 128 "
                f"-nodes 1024 -num_gpus_per_node 4 "
                f"-goal {goal} "
                f"-end 100000000000 "
                f"-sender_cc_only "
                f"-pcm_enable -pcm_cc_config_file {PCM_CC_CONFIG_FILES_PATH}/{pcm_cc_config_file} -pcm_sched_poll_delay 1000 -pcm_handler_delay 1000 "
                f"> {log_file} "
            )
            print_info(f"[{inner_name}][{case_name}] Running: {cmd}")
            assert os.system(cmd) == 0, f"Error running HTSim for {inner_name}, case={case_name}."
            max_host_time = parse_htsim_max_host_time(log_file)
            with open(csv_intranode_bw, "a") as f:
                f.write(
                    f"{inner_name},{case_name},{NETWORK_TOPO},{NIC_SPEED},"
                    f"{intranode_topo},{intranode_linkspeed},{max_host_time},{log_file},{cmd}\n"
                )
            print_success(f"[{inner_name}][{case_name}] Appended to {csv_intranode_bw}")

        # --- CC config cases ---
        for case in PCM_CC_CONFIG_CASES:
            case_name        = case["case_name"]
            pcm_cc_config_file = case["pcm_cc_config_file"]
            log_file = os.path.join(htsim_output_dir, f"htsim_output_{case_name}.tmp")
            cmd = (
                f"{PCM_APP_HTSIM_ATLAHS_EXEC_PATH} "
                f"-topo {TOPO_FILES_PATH}/{NETWORK_TOPO} -linkspeed {NIC_SPEED} -q 1000000 "
                f"-intranode_topo {TOPO_FILES_PATH}/{INTRANODE_TOPO} -intranode_linkspeed {COPY_ENG_SPEED} -intranode_q 1000000 "
                f"-strat ecmp_host -mtu 4096 -paths 128 "
                f"-nodes 1024 -num_gpus_per_node 4 "
                f"-goal {goal} "
                f"-end 100000000000 "
                f"-sender_cc_only "
                f"-pcm_enable -pcm_cc_config_file {PCM_CC_CONFIG_FILES_PATH}/{pcm_cc_config_file} -pcm_sched_poll_delay 1000 -pcm_handler_delay 1000 "
                f"> {log_file} "
            )
            print_info(f"[{inner_name}][{case_name}] Running: {cmd}")
            assert os.system(cmd) == 0, f"Error running HTSim for {inner_name}, case={case_name}."
            max_host_time = parse_htsim_max_host_time(log_file)
            with open(csv_pcm_cc_configs, "a") as f:
                f.write(
                    f"{inner_name},{case_name},{NETWORK_TOPO},{NIC_SPEED},"
                    f"{INTRANODE_TOPO},{COPY_ENG_SPEED},{max_host_time},{log_file},{cmd}\n"
                )
            print_success(f"[{inner_name}][{case_name}] Appended to {csv_pcm_cc_configs}")

        # --- network BW cases ---
        for case in NETWORK_BW_CASES:
            case_name       = case["case_name"]
            network_topo    = case["topo"]
            network_linkspeed = case["linkspeed"]
            pcm_cc_config_file = "pcm_cc_config_all_uec_dctcp.json"
            log_file = os.path.join(htsim_output_dir, f"htsim_output_{case_name}.tmp")
            cmd = (
                f"{PCM_APP_HTSIM_ATLAHS_EXEC_PATH} "
                f"-topo {TOPO_FILES_PATH}/{network_topo} -linkspeed {network_linkspeed} -q 1000000 "
                f"-intranode_topo {TOPO_FILES_PATH}/{INTRANODE_TOPO} -intranode_linkspeed {COPY_ENG_SPEED} -intranode_q 1000000 "
                f"-strat ecmp_host -seed 42 -mtu 4096 -paths 128 "
                f"-nodes 1024 -num_gpus_per_node 4 "
                f"-goal {goal} "
                f"-end 100000000000 "
                f"-sender_cc_only "
                f"-pcm_enable -pcm_cc_config_file {PCM_CC_CONFIG_FILES_PATH}/{pcm_cc_config_file} -pcm_sched_poll_delay 1000 -pcm_handler_delay 1000 "
                f"> {log_file} "
            )
            print_info(f"[{inner_name}][{case_name}] Running: {cmd}")
            assert os.system(cmd) == 0, f"Error running HTSim for {inner_name}, case={case_name}."
            max_host_time = parse_htsim_max_host_time(log_file)
            with open(csv_network_bw, "a") as f:
                f.write(
                    f"{inner_name},{case_name},{network_topo},{network_linkspeed},"
                    f"{INTRANODE_TOPO},{COPY_ENG_SPEED},{max_host_time},{log_file},{cmd}\n"
                )
            print_success(f"[{inner_name}][{case_name}] Appended to {csv_network_bw}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run HTSim ATLAHS experiments for all .bin files in a goal_workloads directory."
    )
    parser.add_argument(
        "-g", "--goal-workloads-dir", type=str, required=True,
        help="Directory containing .bin goal files to iterate over."
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite existing results."
    )

    args = parser.parse_args()
    run_exp_from_goal_workloads(args.goal_workloads_dir, args.overwrite)

