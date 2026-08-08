"""
Llama3 Training Simulation Script

Cluster configuration:
- 32 GPUs total (8 nodes × 4 GPUs/node)
- TP=4 (tensor parallelism within a node)
- DP=4 (data parallelism across nodes)  
- PP=2 (pipeline parallelism, 2 stages)

This script generates the computation graph for 2 training iterations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from simple_sim import (
    Group, ShardSpec, Tensor, Token, MemoryCategory,
    input_tensor, parameter,
    matmul, attention, elementwise_unary, elementwise_binary, reduction, reshape, slice_view,
    backward,
    Zero1Plan, zero1_optimizer_step,
    get_graph, topo_sort, ExtractedGraph,
    build_pp_stage_graph, ParallelDim, PPSchedule,
    GPipeSchedule, OneFOneBSchedule,
)
from simple_sim.tp_megatron import tp_column_linear, tp_row_linear


# ============================================================================
# Llama3 Model Configuration
# ============================================================================

@dataclass
class Llama3Config:
    """Llama3 8B-like configuration"""
    hidden_size: int = 4096
    intermediate_size: int = 14336  # FFN hidden dim (3.5x hidden for SwiGLU)
    num_attention_heads: int = 32
    num_kv_heads: int = 32           # GQA: grouped query attention
    head_dim: int = 0               # derived: hidden_size // num_attention_heads
    num_layers: int = 2
    vocab_size: int = 128256
    max_seq_len: int = 8192
    
    # Training config
    batch_size: int = 1             # micro-batch size per DP rank
    seq_len: int = 128             # sequence length for this run

    def __post_init__(self):
        derived = self.hidden_size // self.num_attention_heads
        if self.head_dim == 0:
            self.head_dim = derived
        else:
            assert self.head_dim == derived, (
                f"head_dim {self.head_dim} != hidden_size // num_attention_heads "
                f"({self.hidden_size} // {self.num_attention_heads} = {derived})"
            )


# ============================================================================
# Llama3 Transformer Block (with TP)
# ============================================================================

def llama3_attention(
    x: Tensor,
    w_qkv: Tensor,
    wo: Tensor,
    *,
    tp_group: Group,
    config: Llama3Config,
    name: str,
) -> Tensor:
    """
    Llama3 attention with GQA (Grouped Query Attention).

    TP strategy:
    - Fused QKV projection: single column-parallel matmul on w_qkv
      (shape [H, 3*num_heads*head_dim]) followed by three zero-cost
      slice views to obtain Q, K, V.
    - Output projection: row parallel (allreduce at the end).
    """
    batch_seq = x.shape[0]  # batch * seq_len (flattened)
    batch = config.batch_size
    seq = config.seq_len
    num_heads = config.num_attention_heads
    num_kv_heads = config.num_kv_heads
    head_dim = config.head_dim

    # Fused QKV projection — single column-parallel matmul
    # Weight shape: (H, (num_heads + 2*num_kv_heads)*head_dim), column-parallel
    qkv = tp_column_linear(x, w_qkv, tp_group=tp_group, name=f"{name}.qkv_proj")

    # Zero-cost slice views into Q, K, V
    # Q: (batch_seq, num_heads*head_dim); K/V: (batch_seq, num_kv_heads*head_dim)
    q_shard_2d = ShardSpec("sharded", axis=1, parts=tp_group.size)
    kv_shard_2d = ShardSpec("sharded", axis=1, parts=tp_group.size)
    q = slice_view(qkv, shape=(batch_seq, num_heads * head_dim), shard=q_shard_2d, name=f"{name}.q_slice")
    k = slice_view(qkv, shape=(batch_seq, num_kv_heads * head_dim), shard=kv_shard_2d, name=f"{name}.k_slice")
    v = slice_view(qkv, shape=(batch_seq, num_kv_heads * head_dim), shard=kv_shard_2d, name=f"{name}.v_slice")

    # Reshape to 4D for FlashAttention
    # Q: (batch, seq, num_heads, head_dim); K/V: (batch, seq, num_kv_heads, head_dim)
    q_4d = reshape(q, shape=(batch, seq, num_heads, head_dim),
                   shard=ShardSpec("sharded", axis=2, parts=tp_group.size), name=f"{name}.q_reshape")
    k_4d = reshape(k, shape=(batch, seq, num_kv_heads, head_dim),
                   shard=ShardSpec("sharded", axis=2, parts=tp_group.size), name=f"{name}.k_reshape")
    v_4d = reshape(v, shape=(batch, seq, num_kv_heads, head_dim),
                   shard=ShardSpec("sharded", axis=2, parts=tp_group.size), name=f"{name}.v_reshape")

    # FlashAttention
    attn_out = attention(q_4d, k_4d, v_4d, name=f"{name}.flash_attn")

    # Reshape back to 2D: (batch*seq, num_heads*head_dim), sharded on axis=1
    attn_flat = reshape(
        attn_out,
        shape=(batch * seq, num_heads * head_dim),
        shard=ShardSpec("sharded", axis=1, parts=tp_group.size),
        name=f"{name}.attn_reshape",
    )

    # Output projection (row parallel with allreduce)
    out = tp_row_linear(attn_flat, wo, tp_group=tp_group, name=f"{name}.o_proj")
    return out


def llama3_mlp(
    x: Tensor,
    w_gate: Tensor,
    w_up: Tensor,
    w_down: Tensor,
    *,
    tp_group: Group,
    name: str,
) -> Tensor:
    """
    Llama3 SwiGLU MLP.
    
    SwiGLU: output = down_proj(silu(gate_proj(x)) * up_proj(x))
    
    TP strategy:
    - gate_proj, up_proj: column parallel
    - down_proj: row parallel (allreduce at the end)
    """
    # Gate and up projections (column parallel, no allreduce)
    gate = tp_column_linear(x, w_gate, tp_group=tp_group, name=f"{name}.gate_proj")
    up = tp_column_linear(x, w_up, tp_group=tp_group, name=f"{name}.up_proj")

    # Fused SiLU(gate) * up — single memory-bound kernel
    hidden = elementwise_binary(gate, up, name=f"{name}.silu_mul")

    # Down projection (row parallel with allreduce)
    out = tp_row_linear(hidden, w_down, tp_group=tp_group, name=f"{name}.down_proj")
    return out


def llama3_block(
    x: Tensor,
    attn_params: Dict[str, Tensor],
    mlp_params: Dict[str, Tensor],
    *,
    tp_group: Group,
    config: Llama3Config,
    name: str,
) -> Tensor:
    """
    Single Llama3 transformer block.

    Structure (fused memory-bound ops):
    - Pre-attention RMSNorm  (elementwise_unary)
    - Attention
    - Fused post-attention residual-add + pre-MLP RMSNorm  (elementwise_binary)
    - MLP
    - Post-MLP residual-add  (elementwise_binary)
    """
    # Pre-attention RMSNorm: reads x, writes norm(x)
    x_norm1 = elementwise_unary(x, name=f"{name}.input_norm")

    # Self-attention
    attn_out = llama3_attention(
        x_norm1,
        attn_params["w_qkv"],
        attn_params["wo"],
        tp_group=tp_group,
        config=config,
        name=f"{name}.attn",
    )

    # Fused post-attention residual-add + pre-MLP RMSNorm:
    # reads x and attn_out, writes norm(x + attn_out) — one kernel
    x_norm2 = elementwise_binary(x, attn_out, name=f"{name}.attn_residual_norm")

    # MLP
    mlp_out = llama3_mlp(
        x_norm2,
        mlp_params["w_gate"],
        mlp_params["w_up"],
        mlp_params["w_down"],
        tp_group=tp_group,
        name=f"{name}.mlp",
    )

    # Post-MLP residual-add: reads x_norm2 and mlp_out, writes output
    x = elementwise_binary(x_norm2, mlp_out, name=f"{name}.mlp_residual")
    return x


# ============================================================================
# Training Graph Builder (uses build_pp_stage_graph)
# ============================================================================

def build_full_training(
    *,
    device_id: int,
    tp_size: int = 4,
    dp_size: int = 2,
    pp_size: int = 2,
    config=None,
    num_microbatches: int = 1,
    num_iterations: int = 2,
    schedule: PPSchedule | None = None,
) -> "ExtractedGraph":
    """
    Build computation graph for multiple training iterations using the
    generalized :func:`simple_sim.build_pp_stage_graph` scheduler.

    Parallel degrees and the model dimensions are parameters (defaults reproduce
    the original hardcoded 16-GPU TP=4/DP=2/PP=2 config byte-for-byte). Total
    devices = tp_size * dp_size * pp_size; num_layers must be divisible by pp_size.

    Args:
        device_id: Global device index in [0, tp_size * dp_size * pp_size).
                   Standard Megatron convention: TP is fastest-varying, then
                   DP, then PP.  E.g. with TP=4, DP=4, PP=1:
                     stage 0: device_ids 0–15
        tp_size, dp_size, pp_size: tensor/data/pipeline parallel degrees.
        config: a Llama3Config; None => the default (8B-like dims).
        num_microbatches: Microbatches accumulated before each optimizer step.
        num_iterations: Number of training iterations to unroll.
        schedule: Pipeline schedule; None preserves the historical GPipe default.
    """
    total_devices = tp_size * dp_size * pp_size
    assert num_microbatches >= 1, "num_microbatches must be positive"
    assert 0 <= device_id < total_devices, (
        f"device_id must be in [0, {total_devices - 1}]"
    )
    pp_rank = device_id // (tp_size * dp_size)

    if config is None:
        config = Llama3Config()
    assert config.num_layers % pp_size == 0, (
        f"num_layers ({config.num_layers}) must be divisible by pp_size ({pp_size})"
    )

    layers_per_stage = config.num_layers // pp_size
    first_layer = pp_rank * layers_per_stage
    last_layer = first_layer + layers_per_stage

    batch_seq = config.batch_size * config.seq_len
    H = config.hidden_size
    I = config.intermediate_size
    num_heads = config.num_attention_heads
    num_kv_heads = config.num_kv_heads
    head_dim = config.head_dim

    print(f"Llama3 Training Simulation")
    print(f"=" * 50)
    print(f"Cluster: {total_devices} GPUs")
    print(f"Parallelism: TP={tp_size}, DP={dp_size}, PP={pp_size}")
    print(f"Pipeline rank: {pp_rank}")
    print(f"Layers: {first_layer}–{last_layer - 1} (per-stage range)")
    print(f"Micro-batch: {config.batch_size} × {config.seq_len} tokens")
    print(f"Accumulation: {num_microbatches} microbatches ({(schedule or GPipeSchedule()).name})")
    print(f"Iterations: {num_iterations}")
    print(f"=" * 50)

    # -----------------------------------------------------------------
    # model_spec: activation shape + one factory per parameter
    # -----------------------------------------------------------------
    # Activation spec: replicated across TP (standard Megatron, no SP)
    model_spec: dict = {
        "_input": lambda tp, dp: (
            (batch_seq, H), "fp16", ShardSpec("replicated")
        ),
    }
    # Per-layer parameter factories — capture loop variables via defaults
    for layer_idx in range(first_layer, last_layer):
        i = layer_idx
        # Fused QKV weight: (H, (num_heads + 2*num_kv_heads)*head_dim), column-parallel
        model_spec[f"layer{i}.w_qkv"] = lambda tp, dp, _i=i: parameter(
            (H, (num_heads + 2 * num_kv_heads) * head_dim), name=f"layer{_i}.w_qkv",
            tp_group=tp, dp_group=dp,
            shard=ShardSpec("sharded", axis=1, parts=tp_size),
        )
        model_spec[f"layer{i}.wo"] = lambda tp, dp, _i=i: parameter(
            (num_heads * head_dim, H), name=f"layer{_i}.wo",
            tp_group=tp, dp_group=dp,
            shard=ShardSpec("sharded", axis=0, parts=tp_size),
        )
        model_spec[f"layer{i}.w_gate"] = lambda tp, dp, _i=i: parameter(
            (H, I), name=f"layer{_i}.w_gate",
            tp_group=tp, dp_group=dp,
            shard=ShardSpec("sharded", axis=1, parts=tp_size),
        )
        model_spec[f"layer{i}.w_up"] = lambda tp, dp, _i=i: parameter(
            (H, I), name=f"layer{_i}.w_up",
            tp_group=tp, dp_group=dp,
            shard=ShardSpec("sharded", axis=1, parts=tp_size),
        )
        model_spec[f"layer{i}.w_down"] = lambda tp, dp, _i=i: parameter(
            (I, H), name=f"layer{_i}.w_down",
            tp_group=tp, dp_group=dp,
            shard=ShardSpec("sharded", axis=0, parts=tp_size),
        )

    # -----------------------------------------------------------------
    # forward_fn: transformer blocks for this stage's layers.
    # Derives is_last_stage from ctx["pp"] to decide whether to append
    # the final RMSNorm + loss reduction.
    # -----------------------------------------------------------------
    def forward_fn(x, params_dict, ctx, *, name):
        tp_group = ctx["tp"].group
        _is_last = ctx["pp"].rank == ctx["pp"].size - 1
        for layer_idx in range(first_layer, last_layer):
            attn_p = {
                k: params_dict[f"layer{layer_idx}.{k}"]
                for k in ("w_qkv", "wo")
            }
            mlp_p = {
                k: params_dict[f"layer{layer_idx}.{k}"]
                for k in ("w_gate", "w_up", "w_down")
            }
            x = llama3_block(
                x, attn_p, mlp_p,
                tp_group=tp_group,
                config=config,
                name=f"{name}.layer{layer_idx}",
            )
        if _is_last:
            x = elementwise_unary(x, name=f"{name}.final_norm")
            x = reduction(x, output_shape=(1,), name=f"{name}.loss")
        return x

    # -----------------------------------------------------------------
    # first_stage_input_fn: embedding (simplified as input_tensor)
    # -----------------------------------------------------------------
    def first_stage_input_fn(params_dict, ctx, *, name):
        return input_tensor((batch_seq, H), name=name)

    # device_id is passed directly to build_pp_stage_graph, which derives
    # pp_stage, tp_rank, dp_rank from it.
    return build_pp_stage_graph(
        forward_fn,
        model_spec,
        device_id=device_id,
        tp_size=tp_size,
        dp_size=dp_size,
        pp_size=pp_size,
        num_microbatches=num_microbatches,
        num_iterations=num_iterations,
        schedule=schedule,
        first_stage_input_fn=first_stage_input_fn,
        include_inference=False,
    )


def analyze_graph(graph) -> None:
    """Analyze and print statistics about the computation graph."""
    nodes = topo_sort(graph.nodes)
    
    # Count ops by kind
    op_counts: Dict[str, int] = {}
    for n in nodes:
        kind = getattr(n, "kind", type(n).__name__)
        op_counts[kind] = op_counts.get(kind, 0) + 1
    
    print(f"\nGraph Statistics:")
    print(f"-" * 40)
    print(f"Total nodes: {len(nodes)}")
    print(f"\nOp counts by kind:")
    for kind, count in sorted(op_counts.items(), key=lambda x: -x[1]):
        print(f"  {kind}: {count}")
    
    # Count communication ops
    comm_ops = ["allreduce", "reduce_scatter", "allgather", "send", "recv"]
    comm_count = sum(op_counts.get(op, 0) for op in comm_ops)
    print(f"\nTotal communication ops: {comm_count}")
    
    # Count compute-bound ops
    compute_ops = ["matmul", "attention", "attention_bwd"]
    compute_count = sum(op_counts.get(op, 0) for op in compute_ops)
    print(f"Total compute-bound ops: {compute_count}")
    
    # Count memory-bound ops
    memory_ops = ["elementwise_unary", "elementwise_binary", "reduction", "reduction_bwd"]
    memory_count = sum(op_counts.get(op, 0) for op in memory_ops)
    print(f"Total memory-bound ops: {memory_count}")


if __name__ == "__main__":
    import argparse
    import pickle
    import pathlib

    _d = Llama3Config()  # dataclass defaults; argparse falls back to these
    ap = argparse.ArgumentParser(
        description="Build simple_sim Llama3 training graphs -> per-device NN.pkl. "
                    "Defaults reproduce the original 16-GPU TP=4/DP=2/PP=2 run.")
    ap.add_argument("--tp", type=int, default=4, help="tensor-parallel degree")
    ap.add_argument("--dp", type=int, default=2, help="data-parallel degree")
    ap.add_argument("--pp", type=int, default=2, help="pipeline-parallel degree")
    ap.add_argument("--iters", type=int, default=2, help="training iterations to unroll")
    ap.add_argument("--hidden", type=int, default=_d.hidden_size)
    ap.add_argument("--ffn", type=int, default=_d.intermediate_size, help="FFN hidden size")
    ap.add_argument("--heads", type=int, default=_d.num_attention_heads)
    ap.add_argument("--kv-heads", type=int, default=_d.num_kv_heads)
    ap.add_argument("--num-layers", type=int, default=_d.num_layers)
    ap.add_argument("--seq-len", type=int, default=_d.seq_len)
    ap.add_argument("--batch", type=int, default=_d.batch_size, help="micro-batch size")
    ap.add_argument("--num-microbatches", type=int, default=1,
                    help="microbatches accumulated before one optimizer step")
    ap.add_argument("--schedule", choices=("gpipe", "1f1b"), default="gpipe",
                    help="pipeline schedule (historical default: gpipe)")
    ap.add_argument("--graphs-dir", default="llama3_graphs",
                    help="output directory for the per-device NN.pkl graphs")
    args = ap.parse_args()

    config = Llama3Config(
        hidden_size=args.hidden, intermediate_size=args.ffn,
        num_attention_heads=args.heads, num_kv_heads=args.kv_heads,
        num_layers=args.num_layers, seq_len=args.seq_len, batch_size=args.batch,
    )
    total_devices = args.tp * args.dp * args.pp
    schedule = GPipeSchedule() if args.schedule == "gpipe" else OneFOneBSchedule()
    graph_dir = pathlib.Path(args.graphs_dir)
    graph_dir.mkdir(parents=True, exist_ok=True)
    print(f"simple_sim Llama3: TP={args.tp} DP={args.dp} PP={args.pp} "
          f"({total_devices} devices), layers={config.num_layers} seq={config.seq_len} "
          f"hidden={config.hidden_size} ffn={config.intermediate_size} iters={args.iters} "
          f"microbatches={args.num_microbatches} schedule={args.schedule} "
          f"-> {graph_dir}")
    for device_id in range(total_devices):
        graph = build_full_training(
            device_id=device_id, tp_size=args.tp, dp_size=args.dp, pp_size=args.pp,
            config=config, num_microbatches=args.num_microbatches,
            num_iterations=args.iters, schedule=schedule)
        analyze_graph(graph)
        with open(graph_dir / f"{device_id:02d}.pkl", "wb") as f:
            pickle.dump(graph, f)
    print(f"wrote {total_devices} graphs to {graph_dir}")
