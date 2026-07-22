"""Repo paths for simulation-scripts experiments.

The workspace root defaults to the repo checkout this file lives in (its
grandparent directory), which resolves to /workspace inside the Docker
container and to the local clone outside it — so local runs need no env vars.
Every path is individually overridable via the matching env var (the same
names the pre-restructure script used)."""
import os

_SIM_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(name, default):
    return os.environ.get(name, default)


WORKSPACE = _env("ATLAHS_WORKSPACE", os.path.dirname(_SIM_SCRIPTS_DIR))

PCM_APP_HTSIM_ATLAHS_EXEC_PATH = _env(
    "PCM_APP_HTSIM_ATLAHS_EXEC_PATH",
    os.path.join(WORKSPACE, "sim/pcm-sdk_zhiyi/pcm/build/bin/htsim_flow_app_atlahs"))
COLL_TXT2BIN = _env(
    "COLL_TXT2BIN",
    os.path.join(WORKSPACE, "tools/loggopsim-coll/LogGOPSim-1.1/txt2bin"))
GENERATOR_DIR = _env(
    "GENERATOR_DIR",
    os.path.join(WORKSPACE, "goal_gen/ai/nccl_generator_v2"))
TOPO_FILES_PATH = _env(
    "TOPO_FILES_PATH",
    os.path.join(_SIM_SCRIPTS_DIR, "topo_files"))
RESULTS_ROOT = _env("SIM_RESULTS_ROOT", os.path.join(_SIM_SCRIPTS_DIR, "results"))


def results_dir(exp_name):
    """Default output dir for an experiment: simulation-scripts/results/<exp>/."""
    return os.path.join(RESULTS_ROOT, exp_name)


def topo(basename):
    """Resolve a .topo basename against TOPO_FILES_PATH."""
    return os.path.join(TOPO_FILES_PATH, basename)
