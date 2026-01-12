<div align="center">


ATLAHS Simulator Toolchain
===================
An Application-centric Network Simulator Toolchain for AI, HPC, and Distributed Storage

<div align="left">


## Warning
This repository is still under active development. The code is not yet stable, and the documentation is not yet finalized.

## Overview
![Overview](docs/overview.png)

This repository contains the source code for ATLAHS, a network simulator toolchain for AI, HPC, and storage applications. It contains the following components, detailed documentation of which can be found in their corresponding directories:
- Applications (`apps/`): A collection of applications that are used to test the toolchain.
- GOAL (Group Operation Assembly Language) generators (`goal_gen/`): Tools that trace AI, HPC, and storage applications and converts them into network workloads usable by network simulators.
- Simulation backends (`backends/`): Various backends for simulating network workloads, including LogGOPSim, HTSim, and NS-3 (in progress).

## Custom NCCL builds (NVTX / tracing)

ATLAHS supports **multiple NCCL versions** via version-specific patches and annotated source drops.

If you need to build a custom NCCL with ATLAHS NVTX annotations (for `nsys → sqlite → GOAL → LGS`), start here:

- `goal_gen/ai/nccl_versions/README.md`


## Paper and trace collection


The paper of this work is available on arXiv: [https://arxiv.org/pdf/2505.08936](https://arxiv.org/pdf/2505.08936), and it has been accepted by The International Conference for High Performance Computing, Networking, Storage and Analysis (SC25).

Along with the source code, we also release all the traces (raw files and converted GOAL traces) used in the paper as the [ATLAHS Trace Collection](http://storage2.spcl.ethz.ch/traces/). Not only does it cover a wide range of AI and HPC applications, it is still growing, and we want to encourage the community to contribute more traces to the collection.

## Docker Environment
To facilitate the reproducibility of the results which we publish in the paper, we provide a Docker image that contains all the dependencies that are required to run the ATLAHS toolchain.

Make sure to clone the repository with the `--recurse-submodules` flag to fetch all the dependent submodules.

To build the Docker image, run the following command:

```bash
docker build -t atlahs .
```

To compile the components required to reproduce the results in
the paper, run:
```bash
docker run --user $(id -u):$(id -g) -v $(pwd):/workspace atlahs build -r
```
This mounts the project directory to `/workspace` inside the con-
tainer and invokes the build.py script in the scripts directory.


### Running a quick test
To run a quick test, run the following command:
```bash
docker run --user $(id -u):$(id -g) -v $(pwd):/workspace atlahs run -q
```
This fetches a small subset of the ATLAHS traces from the SPCL storage server,
and tests the functionality of the ATLAHS toolchain. It converts the raw traces of
AI (nsys-reports) and HPC (PMPI traces) applications into the [GOAL format](https://ieeexplore.ieee.org/document/5362477),
and simulates the workloads with different backends (e.g., LogGOPSim, htsim) in ATLAHS.

## Citation

If you use ATLAHS in your work, please cite:

```bibtex
@inproceedings{10.1145/3712285.3759838,
author = {Shen, Siyuan and Bonato, Tommaso and Hu, Zhiyi and Jordan, Pasquale and Chen, Tiancheng and Hoefler, Torsten},
title = {ATLAHS: An Application-centric Network Simulator Toolchain for AI, HPC, and Distributed Storage},
year = {2025},
isbn = {9798400714665},
publisher = {Association for Computing Machinery},
address = {New York, NY, USA},
url = {https://doi.org/10.1145/3712285.3759838},
doi = {10.1145/3712285.3759838},
abstract = {Network simulators play a crucial role in evaluating the performance of large-scale systems. However, existing simulators rely heavily on synthetic microbenchmarks or narrowly focus on specific domains, limiting their ability to provide comprehensive performance insights. In this work, we introduce ATLAHS, a flexible, extensible, and open-source toolchain designed to trace real-world applications and accurately simulate their workloads. ATLAHS leverages the Group Operation Assembly Language (GOAL) format to model communication and computation patterns in AI, HPC, and distributed storage applications. It supports multiple network simulation backends and handles multi-job and multi-tenant scenarios. Through extensive validation, we demonstrate that ATLAHS achieves high accuracy in simulating realistic workloads (consistently less than 5\% error), while significantly outperforming AstraSim, the current state-of-the-art AI systems simulator, in terms of both simulation runtime and trace size efficiency. We further illustrate ATLAHS’s utility via detailed case studies, highlighting the impact of congestion control algorithms on the performance of distributed storage systems, as well as the influence of job-placement strategies on application runtimes.},
booktitle = {Proceedings of the International Conference for High Performance Computing, Networking, Storage and Analysis},
pages = {349–367},
numpages = {19},
keywords = {Network simulation, distributed and high-performance computing},
location = {
},
series = {SC '25}
}
```