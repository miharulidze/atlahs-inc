## AI Applications

This directory contains a collection of AI applications.

We provide Dockerfiles (`dockerfiles/`) that can be used to build the Docker image that contains the dependencies for the AI applications. The Dockerfile `Dockerfile.common` is a base image that supports both DLRM, Megatron, and MMaDA. The Dockerfile `Dockerfile.vllm` is a base image that supports vLLM (v0.8.6).

In addition, as a reference, we also provide scripts (`scripts/`) that can be used to run and trace each of the applications. Note that the scripts specifically target the Alps supercomputer, and may need to be adapted to other systems.

Before running the scripts, make sure to apply the patches to the source code of the applications to incorporate the necessary instrumentation code. The patches are provided in the `patches/` directory. Technically, GOAL files can still be successfully generated even without the patches, but collected traces may include sections of code that are not as relevant to the network-intensive and performance-critical parts of the applications.

## Workload Annotation Guide

This section explains how to annotate your AI training application for ATLAHS trace collection.

### Required Annotations

ATLAHS needs two NVTX markers to define the profiling window:

```python
import torch
import os

pid = os.getpid()

# At the start of the iteration range you want to profile
torch.cuda.nvtx.mark(f"nsys profiling start, pid: {pid}")

# At the end of the last iteration to profile  
torch.cuda.nvtx.mark(f"nsys profiling stopped, pid: {pid}")
```

**Important:** The marker strings must match exactly:
- `"nsys profiling start, pid: {pid}"`
- `"nsys profiling stopped, pid: {pid}"`

The `pid` is required to correlate markers across multiple processes in distributed training.

### Megatron-LM Example

In Megatron-LM's `megatron/training/training.py`, we add the markers in the `train()` function. We use Megatron's existing `--profile`, `--profile-step-start`, and `--profile-step-end` command-line arguments to control when markers are emitted:

```python
def train(forward_step_func, model, optimizer, opt_param_scheduler, ...):
    ...
    pid = os.getpid()
    
    while iteration < args.train_iters:
        # Add start marker at the beginning of profiling window
        if args.profile and iteration == args.profile_step_start:
            torch.cuda.nvtx.mark(f"nsys profiling start, pid: {pid}")
        
        # ... training step code ...
        
        iteration += 1

def post_training_step_callbacks(...):
    # Add stop marker at the end of profiling window
    if args.profile and iteration == args.profile_step_end:
        pid = os.getpid()
        torch.cuda.nvtx.mark(f"nsys profiling stopped, pid: {pid}")
```

For other training frameworks, use whatever iteration tracking mechanism is available (e.g., a simple counter or existing profiling hooks).

See `patches/megatron_atlahs_trace.patch` for the complete patch.

### General Guidelines

1. **Place markers around complete iterations** - The start marker should fire before any NCCL operations in the first profiled iteration, and the stop marker after all NCCL operations in the last profiled iteration complete.

2. **Profile multiple iterations** - For accurate simulation, profile at least 3-5 complete training iterations to capture representative communication patterns.

3. **Avoid profiling warmup** - Skip the first few iterations where CUDA kernels are being JIT compiled and NCCL is initializing.

4. **Use consistent iteration boundaries** - Ensure all ranks enter and exit the profiling window at the same logical iteration to capture synchronized collectives properly.

---

### Optional: PyTorch Execution Trace Observer

The Megatron patch also includes optional PyTorch profiler integration for exporting additional trace formats (Kineto/Chrome traces). This is **not required for ATLAHS** but can be useful for other analysis.

```python
from torch.profiler import ExecutionTraceObserver

def trace_handler(prof):
    rank = torch.distributed.get_rank()
    trace_dir = os.environ.get('TRACE_DIR')
    trace_path = os.path.join(trace_dir, f"kineto_trace_{rank}.json")
    prof.export_chrome_trace(trace_path)

# Setup profiler
prof = torch.profiler.profile(
    schedule=torch.profiler.schedule(
        wait=max(args.profile_step_start - 1, 0),
        warmup=0,
        active=args.profile_step_end - args.profile_step_start,
        repeat=0),
    on_trace_ready=trace_handler)

# Setup execution trace observer
et = ExecutionTraceObserver()
trace_file = os.path.join(trace_dir, f'pytorch_et_{rank}.json')
et.register_callback(trace_file)

prof.start()

# In training loop:
prof.step()
if iteration == args.profile_step_start:
    et.start()

# After profiling ends:
prof.stop()
et.stop()
et.unregister_callback()
```

This exports:
- `kineto_trace_{rank}.json` - Chrome trace format viewable in `chrome://tracing`
- `pytorch_et_{rank}.json` - PyTorch execution trace