# ATLAHS Pipeline Execution Summary

## Environment Setup Completed
- **Repository**: Cloned ATLAHS to `/users/btommaso/atlahs_testing_new`
- **Branch**: `nccl_versions`
- **Megatron-LM Submodule**: Initialized at commit `b1efb3c`
- **Patch Applied**: `megatron_atlahs_trace.patch`

## NCCL Libraries Used
| Version | Path | Status |
|---------|------|--------|
| NCCL 2.20.5 | `/users/btommaso/nccl_nvtx_v2.20.5-1/nccl/build/lib/libnccl.so.2.20.5` | Pre-built |
| NCCL 2.28.3 | `/users/btommaso/scratch/nccl_228_atlahs/nccl/build/lib/` | Pre-built with ATLAHS patch |

## Megatron Training Runs
| Job ID | NCCL Version | Nodes | Account | Partition | Status |
|--------|--------------|-------|---------|-----------|--------|
| 1410562 | 2.20.5 | 4 | a-g200 | normal | ✅ Completed |
| 1410563 | 2.28.3 | 4 | a-g200 | normal | ✅ Completed |

### Training Configuration
- Model: Llama2 7B
- TP=1, PP=1, DP=16
- Global Batch Size: 32
- Train Iterations: 12
- Profile Steps: 8-10

## GOAL Generation & LGS Simulation Results

### NCCL 2.20.5 (from new traces)
- **Input**: `/users/btommaso/scratch/megatron_traces_new/nccl_220_sqlite/`
- **Output**: `/users/btommaso/scratch/goal_output_nccl220/`
- **Events Processed**: 6,156,336
- **Max Completion Time**: 1.91 seconds (1,913,357,204 ns)
- **Average FCT**: 27,210.35 ns

### NCCL 2.28.3 (from new traces)
- **Input**: `/users/btommaso/scratch/megatron_traces_new/nccl_228_sqlite/`
- **Output**: `/users/btommaso/scratch/goal_output_nccl228/`
- **Events Processed**: 6,156,226
- **Max Completion Time**: 1.91 seconds (1,914,924,833 ns)
- **Average FCT**: 27,211.49 ns

### External Traces (pre-generated binary from storage2.spcl.ethz.ch)
- **Input**: `/users/btommaso/scratch/external_goal/llama.bin`
- **Events Processed**: 5,769,970
- **Max Completion Time**: 2.12 seconds (2,122,502,605 ns)
- **Average FCT**: 27,211.27 ns

### External Traces (generated from SQLite conversion)
- **Input**: `/users/btommaso/scratch/external_traces_sqlite/`
- **Output**: `/users/btommaso/scratch/goal_output_external/`
- **Events Processed**: 6,156,226
- **Max Completion Time**: 2.12 seconds (2,119,239,050 ns)
- **Average FCT**: 27,211.50 ns

## Validation Metrics
| Metric | Target | NCCL 2.20 | NCCL 2.28 | External (pre-gen) | External (SQLite) |
|--------|--------|-----------|-----------|--------------------|--------------------|
| Max Time | ~2s | ✅ 1.91s | ✅ 1.91s | ✅ 2.12s | ✅ 2.12s |
| Events | ~6M | ✅ 6.15M | ✅ 6.15M | ✅ 5.77M | ✅ 6.15M |

## File Locations
- **Traces (NCCL 2.20)**: `/users/btommaso/scratch/megatron_traces_new/nccl_220/`
- **Traces (NCCL 2.28)**: `/users/btommaso/scratch/megatron_traces_new/nccl_228/`
- **SQLite (NCCL 2.20)**: `/users/btommaso/scratch/megatron_traces_new/nccl_220_sqlite/`
- **SQLite (NCCL 2.28)**: `/users/btommaso/scratch/megatron_traces_new/nccl_228_sqlite/`
- **GOAL Output (NCCL 2.20)**: `/users/btommaso/scratch/goal_output_nccl220/`
- **GOAL Output (NCCL 2.28)**: `/users/btommaso/scratch/goal_output_nccl228/`
- **External Traces**: `/users/btommaso/scratch/external_traces/`
- **External GOAL**: `/users/btommaso/scratch/external_goal/`

## LGS Parameters Used
```bash
./LogGOPSim -f <goal_file.bin> -L 3700 -o 200 -g 5 -O 0 -G 0.04 -S 0
```

## Conclusion
✅ **All validation metrics met successfully!**
- Max completion time for all simulations is approximately 2 seconds
- All simulations processed approximately 6 million events
- Both NCCL 2.20.5 and NCCL 2.28.3 show consistent results
- External traces from storage2.spcl.ethz.ch match the expected metrics
