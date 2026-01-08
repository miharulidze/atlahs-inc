import argparse
import sys
import os
import subprocess

def main():
    parser = argparse.ArgumentParser(description="ATLAHS Simulator Entry")
    parser.add_argument(
        "--backend",
        type=str,
        required=True,
        help="Specify the simulator backend to use: 'NS-3', 'htsim', or 'LGS'"
    )
    parser.add_argument(
        "--goal_file",
        type=str,
        required=True,
        help="GOAL file describing the workload"
    )
    parser.add_argument(
        "--output_file",
        type=str,
        required=True,
        help="File to save the output information"
    )

    args = parser.parse_args()
    backend = args.backend.lower()

    if backend == "ns-3":
        # Initialize and run NS-3 simulator
        print("Using NS-3 simulator")
    elif backend == "htsim":
        # Initialize and run htsim simulator
        print("Using htsim simulator")
        cmd = f"./sim/htsim_test/sim/datacenter/htsim_eqds -topo sim/htsim_test/sim/datacenter/topologies/leaf_spine_tiny.topo -tm sim/htsim_test/sim/datacenter/connection_matrices/perm_32n_32c_2MB.cm > {args.output_file}"
        subprocess.run(cmd, shell=True)
    elif backend == "lgs":
        # If input is .goal, convert to .bin
        if args.goal_file.endswith(".goal"):
            print("Creating LogGOPSim .bin file from .goal file")
            base, _ = os.path.splitext(args.goal_file)  # removes .goal
            bin_file = base + ".bin"  # replace extension
            cmd = f"./sim/LogGOPSim/txt2bin -i {args.goal_file} -o {bin_file}"
            subprocess.run(cmd, shell=True)
            args.goal_file = bin_file
        if not args.goal_file.endswith(".bin"):
            print("Error: LGS backend requires a .bin GOAL file")
            sys.exit(1)
        print("Using LGS simulator")
        cmd = f"./sim/LogGOPSim/LogGOPSim -f {args.goal_file} | tee {args.output_file}"
        subprocess.run(cmd, shell=True)
    else:
        print(f"Error: Unknown simulator backend '{args.backend}'")
        sys.exit(1)

if __name__ == "__main__":
    main()
