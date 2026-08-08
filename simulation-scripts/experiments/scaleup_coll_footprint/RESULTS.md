# Tracked network-footprint results

The tracked thesis matrix measures byte-link footprint reduction on the single-switch
and three-tier fabrics for groups 2–64. The metric is:

```text
endpoint byte-link crossings / in-network byte-link crossings
```

Messages are MTU-aligned (`-mtu 4160`, one 4096-byte payload per rank), and reduction
compute is disabled. The source is
`results/scaleup_coll_footprint/scaleup_coll_footprint.csv`; its lineage and deduplication
record are in `../../REFERENCE_DATA.md`.

## AllGather

| fabric / baseline | P=2 | P=4 | P=8 | P=16 | P=32 | P=64 |
|---|---:|---:|---:|---:|---:|---:|
| single switch, Ring | 1.015 | 1.523 | 1.777 | 1.904 | 1.967 | 1.999 |
| single switch, recursive doubling | 1.015 | 1.523 | 1.769 | 1.890 | 1.950 | 1.979 |
| three tier, Ring | 1.015 | 1.523 | 1.777 | 1.904 | 1.967 | 1.999 |
| three tier, recursive doubling | 1.015 | 1.523 | 2.223 | 2.721 | 3.594 | 4.093 |

On the single switch every pair is two hops, so Ring and recursive doubling approach
the same 2x data-movement limit. On the three-tier fabric, recursive doubling sends its
later, larger exchanges across leaf and pod boundaries, so its endpoint footprint grows
relative to multicast.

Ring AllReduce has the same byte ratios as AllGather. ReduceScatter is below one at P=2
(0.677), where the aggregation setup is not amortized, and exceeds one from P=4 onward.
Broadcast and Reduce show the same qualitative multicast/aggregation benefit.

## Interpretation and limits

- Use `ratio_bytes`, not `ratio_crosses`. Endpoint transports emit small ACK/pull packets
  that inflate packet counts but contribute only about 1.5% of bytes.
- This is a capacity/traffic-footprint result, not a completion-time speedup. The two
  metrics answer different questions.
- The radix-32, 1024-host topology and three-tier points above P=64 are supported as
  exploratory runner options but are not included in the tracked thesis CSV. Keep them
  in a separately named result set.
- The model is a PFC-style lossless abstraction, not hardware CBFC. The interpretation
  is limited to the tested workloads and topologies.

Regenerate the two tracked figures with the commands in the experiment README. The
frozen wrapper stages a complete run before replacing the reference CSV.
