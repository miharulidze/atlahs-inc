#!/bin/bash
# Converts all nsys reports in the given directory to sqlite
# Usage: bash nsys_reports_to_sqlite.sh <input_dir> [output_dir]
#
# You can override the nsys path via:
#   NSYS_BIN=/path/to/nsys bash nsys_reports_to_sqlite.sh ...
REPORT_DIR_IN=$1
OUTPUT_DIR_RAW=${2:-$REPORT_DIR_IN}
if [ -z "$REPORT_DIR_IN" ]; then
    echo "Usage: bash nsys_reports_to_sqlite.sh <input_dir> [output_dir]"
    exit 1
fi

# Normalize paths
REPORT_DIR=$(realpath "$REPORT_DIR_IN")
mkdir -p "$OUTPUT_DIR_RAW"
OUTPUT_DIR=$(realpath "$OUTPUT_DIR_RAW")

shopt -s nullglob
reports=(${REPORT_DIR}/*.nsys-rep ${REPORT_DIR}/*.qdrep ${REPORT_DIR}/*.qdstrm)
shopt -u nullglob

num_reports=${#reports[@]}
echo "Number of nsys reports: ${num_reports}"

if [ ${num_reports} -eq 0 ]; then
    echo "No nsys reports found in ${REPORT_DIR}"
    exit 0
fi

NSYS_BIN="${NSYS_BIN:-}"
if [ -z "$NSYS_BIN" ]; then
    if command -v nsys >/dev/null 2>&1; then
        NSYS_BIN="$(command -v nsys)"
    elif [ -x /opt/nvidia/hpc_sdk/Linux_aarch64/24.3/compilers/bin/nsys ]; then
        NSYS_BIN=/opt/nvidia/hpc_sdk/Linux_aarch64/24.3/compilers/bin/nsys
    elif [ -x /opt/nvidia/hpc_sdk/Linux_aarch64/24.3/profilers/Nsight_Systems/target-linux-sbsa-armv8/nsys ]; then
        NSYS_BIN=/opt/nvidia/hpc_sdk/Linux_aarch64/24.3/profilers/Nsight_Systems/target-linux-sbsa-armv8/nsys
    else
        echo "ERROR: nsys not found. Set NSYS_BIN=/path/to/nsys or add nsys to PATH."
        exit 2
    fi
fi

for file in "${reports[@]}"; do
    base=$(basename -- "$file")
    base="${base%.*}"
    echo "Converting ${file} to ${OUTPUT_DIR}/${base}.sqlite"
    "$NSYS_BIN" export --type=sqlite --force-overwrite=true --output=${OUTPUT_DIR}/${base}.sqlite ${file} &
done

wait
exit 0
