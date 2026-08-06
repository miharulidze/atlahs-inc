# Validation checks

These checks cover reference-data integrity, analytic claims, source provenance, and
packet-level collective behavior. Run the complete fast gate after building the Docker
image:

```bash
simulation-scripts/validate_artifact.sh
```

Use `--extended` to include the supplementary and superseded runners. `--pfc-smoke`
adds a packet-level flow-control run, but constructing the 256-host fabric is slow; the
full PFC experiment is run separately.

## Committed-data checks

```bash
python3 simulation-scripts/model_checks/audit_reference_data.py
python3 simulation-scripts/model_checks/verify_ch5_snapshot.py
python3 simulation-scripts/model_checks/verify_models.py
```

- `audit_reference_data.py` rejects appended duplicate generations in every tracked
  result CSV.
- `verify_ch5_snapshot.py` checks all ten preserved source files against the canonical
  Chapter 5 manifest.
- `verify_models.py` re-derives the Chapter 4 model claims, Chapter 5 speedups and
  simulator-model factors, and the topology/PFC anchor values from committed data.

The model verifier imports its equations from `_gen_rsag_tables.py`, the same code that
generates the thesis tables. No constants are fitted: bandwidth, link latency, switch
latency, header size, and MSS come from the topology and packet model.

A passing committed-data check means the archived dataset is internally coherent. It
does not imply bit-identical timing from a newer backend. Historical and current
results are kept separate as described in `../REFERENCE_DATA.md`.

## Packet regressions

`validate_artifact.sh` runs these inside the container:

- `test_trace_contract.py`: trace-wide operation-ID allocation and node-major rank
  mapping in both captured-trace and synthetic-workload generator paths.
- `collective_validation_test.py`: all supported INC collectives, delayed arrivals,
  signature validation, globally unique operation IDs, partial-domain population, and
  malformed/incomplete-operation failures.
- `rooted_domain_localization_test.py`: Broadcast and Reduce in domain 0 and a nonzero
  domain, including root translation and rejection of an out-of-domain root.
- representative `scaleup_coll_ab` and footprint cells on partially populated fixed
  fabrics.

These tests exercise the packet backend. The runners' `--validate` modes only generate
and compile traces, which is useful but insufficient on its own.

## Historical derivation probes

The shell probes and `rsag_model_check{,2,3,4}.py` preserve the derivation trail. The
retained `_fold_off/scaleup_coll_ab.csv`, `_fold_on/scaleup_coll_ab.csv`, and
`_podstep/scaleup_coll_ab.csv` are the compact inputs used by the supported verifier;
temporary logs and superseded `_dchk*`/`_foldsweep*` runs are intentionally omitted.
These probes are not part of the supported validation and some encode assumptions that later
experiments refuted. Use `verify_models.py` for the supported current interpretation.

The thesis model keeps Reduce-Scatter's own slice in the aggregation stream
(`kappa = N`). The retired `-rs_local_fold` arm remains only as a sensitivity study.

`regen_all.sh` runs the Chapter 4 completion-time, group-size, footprint, and
supplementary bandwidth matrices in sequence and stops at the first failure. The
individual frozen wrappers retain a separately named run archive before updating their
result CSV.
