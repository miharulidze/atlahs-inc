# Simulation-only ATLAHS environment — CPU, no GPU/tracing deps.
#
# Builds the pcm-sdk two-tier htsim simulator (our INC datapath) + the
# coll-extended LogGOPSim txt2bin, and runs pure-simulation experiments
# (scale-up INC-vs-endpoint collective A/B). Deliberately NOT the NVIDIA
# pytorch base of the top-level Dockerfile: that one provisions the
# real-workload TRACING/GENERATION toolchain (torch/deepspeed/flash-attn),
# whose flash-attn build needs a GPU box's RAM and is irrelevant to
# simulation. This image builds anywhere Docker runs (arm64/amd64).
#
# The repo is bind-mounted at /workspace (submodules materialized on the host):
#   docker build -t atlahs-sim simulation-scripts/
#   docker run --rm --user $(id -u):$(id -g) -v $(pwd):/workspace atlahs-sim build
#   docker run --rm --user $(id -u):$(id -g) -v $(pwd):/workspace atlahs-sim run scaleup_coll_ab --validate
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Build toolchain for the CPU-only pcm/htsim C++ (C++23 -> g++ 13/14 on 24.04),
# the coll txt2bin (re2c/gengetopt), and Python for the generator + experiment
# driver. python3-yaml: pcm/build.py imports yaml. pandas: the trace-contract
# regression and captured-trace generator paths. numpy/scipy/tqdm: the
# simple_sim analytical generator's core graph-build path (the viz/aggregate
# stack it also ships is guarded out in simple_sim/__init__.py). matplotlib: the
# experiment plotters. All via apt to avoid 24.04's externally-managed-pip block.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        g++ \
        gengetopt \
        re2c \
        patch \
        wget \
        curl \
        ca-certificates \
        git \
        python3 \
        python3-yaml \
        python3-pandas \
        python3-numpy \
        python3-scipy \
        python3-tqdm \
        python3-matplotlib \
        libomp-dev \
        libsleef-dev \
        libunwind-dev \
        libopenblas-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
