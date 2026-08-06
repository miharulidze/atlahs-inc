# Intranode CC-Bypass + Fair INC Endpoint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use `- [ ]` checkboxes.

**Goal:** Make the scale-up (intranode/NVLink) domain **congestion-control-free** (line-rate NIC serialization + lossless PFC only, no DCTCP), while keeping DCTCP on the scale-out fabric, AND route the INC collective through the same intranode NIC as the baseline — so the INC-vs-baseline A/B is fair and NVLink-faithful.

**Architecture:** Three coupled changes in the pcm-sdk simulator: (1) per-API CC — `so_api` keeps DCTCP-via-PCM, `su_api` becomes plain `UecSrc` with the native `CONSTANT` (no-op) window; (2) size the intranode lossless PFC threshold + queue to the BDP so lossless copes without a window loop; (3) pace the INC `coll` emission through the base `UecSrc` NIC (line rate) instead of the current unpaced pipe-direct dump. Validation is behavioral, via the `intranode_linkspeed_sweep` experiment (both arms must slope with link speed, INC ≤ baseline, 0 drops, bounded makespan).

**Tech Stack:** C++ (pcm-sdk htsim, C++23), Python experiment driver, Docker (`atlahs-sim`).

## Global Constraints

- Author `Wanja Stämpfli <stampfliwanja4@gmail.com>`; **never** add Co-Authored-By / Claude trailers.
- `static_cast<T>(x)` etc. — no C-style casts in new code; match surrounding style.
- Edits to the HTSIM tree go in **`sim/pcm-sdk_zhiyi/HTSIM_spcl-patch/`** (build_sim.py copies `HTSIM_spcl-patch/.` → `HTSIM_spcl/htsim/sim/` before compiling). App edits go in `sim/pcm-sdk_zhiyi/pcm/apps/htsim_atlahs/`.
- **No header-dep tracking** — after editing any `.h`, do a clean pcm build.
- Rebuild in Docker: `docker run --rm -v "$(pwd)":/workspace atlahs-sim build` (or `pcm/build.py --debug --build-htsim-atlahs`). Binaries are bind-mounted back to the host.
- The pcm-sdk is a submodule on branch `wanja/inc-port`; changes are committed there + an umbrella pin bump — **only when the user asks**.
- Backward compatibility: every new flag defaults to today's behavior (so `scaleup_coll_ab` and existing runs are unchanged).

## File Map

- **Modify** `pcm/apps/htsim_atlahs/htsim_app_atlahs.cpp`
  - `:762` — PFC `_high_threshold`/`_low_threshold`: hardcoded `*100`/`*80` → read new flags `-lossless_high_pfc`/`-lossless_low_pfc` (default 100/80).
  - `:1263-1265` — `su_api` PCM settings: add per-tier control so intranode can be `pcm_enable=false`.
  - arg-parse block (near `:567-577`) — add `-intranode_cc <dctcp|none>` (default `dctcp` = today's behavior) and the two `-lossless_*_pfc` flags.
- **Modify** `HTSIM_spcl-patch/uec_collectives.cpp` (`UecBcastSrcMcast::emit_once` `:11-27`, `UecReduceSrc::emit_once` `:43-74`) — pace emission through the base `UecSrc` NIC at `_nic.linkspeed()` instead of unpaced `sendOn()`.
- **Modify** `HTSIM_spcl-patch/uec_collectives.h` (`connect_collective` `:59-63`) — wire the NIC so `emit_once` can serialize through it.
- **Modify** `simulation-scripts/experiments/intranode_linkspeed_sweep/run.py` — `run_one_sim` passes `-intranode_cc none` and the BDP-sized `-lossless_high_pfc`; keep `-pcm_enable` + DCTCP for scale-out.
- **Validation** `simulation-scripts/experiments/intranode_linkspeed_sweep/` — the sweep is the behavioral test.

Reference anchors (verified): native window switch `uec.cpp:579-597`; `CONSTANT`→no-op `uec.cpp:589-591` + `:1453-1454`/`:1533-1534`; default native algo NSCC `uec.cpp:63`; NIC serialization `uec.cpp:330`; base NIC handle `uec.h:247` (`_nic`, `_nic.linkspeed()`); `su_api` build loop `htsim_app_atlahs.cpp:1245-1272`; `so_api` `:1213`/`:1236-1238`; PCM-vs-plain source selection `atlahs_htsim_api.cpp:184-197`; lossless static thresholds `queue_lossless_input.cpp:8-9`.

---

### Task 0: De-risking spike — does intranode-no-CC stay bounded?

**Why first:** removing the window loop on the intranode leaves only NIC serialization + lossless PFC to bound the queue. We must confirm that a BDP-sized PFC threshold yields a *bounded, drop-free* intranode at the top of the sweep BEFORE writing the per-tier plumbing. (Prior data: lossless-only overflowed at 12800 Gbps with the 100-pkt threshold.) This spike uses a throwaway constant bump, not the final flags.

**Files:** temporary edit to `pcm/apps/htsim_atlahs/htsim_app_atlahs.cpp:762` (revert after).

- [ ] **Step 1:** Temporarily set `_high_threshold = Packet::data_packet_size() * 4000; _low_threshold = ...*3200;` at `:762-763`, and (temporarily) `su_api->pcm_enable = false;` at `:1265`. Rebuild (`atlahs-sim build`).
- [ ] **Step 2:** Generate the pp1_tp4 bins (`run intranode_linkspeed_sweep --validate --tmpdir /workspace/.../spike`).
- [ ] **Step 3:** Run the baseline INC-arm bin at 12800 Gbps with `-pcm_enable` for scale-out kept, intranode now plain+CONSTANT (`-sender_cc_algo constant`), `-intranode_q` ≥ threshold (e.g. `50000000`). Grep `LOSSLESS not working` (expect 0), `Rtx:` (expect 0), and the makespan.

Run:
```bash
docker run --rm -v "$(pwd)":/workspace -e LD_LIBRARY_PATH=/workspace/sim/pcm-sdk_zhiyi/pcm/build/lib \
  --entrypoint /workspace/sim/pcm-sdk_zhiyi/pcm/build/bin/htsim_flow_app_atlahs atlahs-sim \
  -goal <spike>/pp1_tp4_dp4_pp1/base.bin -nodes 16 -num_gpus_per_node 4 \
  -topo .../tree16_nonblocking_100Gbps.topo -linkspeed 200000 -q 1000000 \
  -intranode_topo .../scaleup_single_switch_4_12800Gbps.topo -intranode_linkspeed 12800000 -intranode_q 50000000 \
  -strat ecmp_host -seed 42 -mtu 4096 -paths 128 -end 1000000000000 -sender_cc_algo constant \
  -intranode_queue_type lossless_input -pcm_enable -pcm_cc_config_file .../pcm_cc_config_all_uec_dctcp_v2.json \
  -pcm_sched_poll_delay 1000 -pcm_handler_delay 1000 2>&1 | grep -icE "LOSSLESS not working|Rtx: [1-9]"
```
Expected: **0** lossless violations, **0** retransmits, makespan bounded and ~in the DCTCP ballpark (not 2 s, not 398 s).

- [ ] **Step 4: DECISION GATE.** If bounded + clean → proceed to Task 1. If it overflows or balloons at every threshold/queue size tried → STOP; the intranode cannot be CC-free with the current lossless queue, and we fall back to the "consistency fix" (keep DCTCP on *both* tiers but route INC through the NIC — Tasks 3-4 only — so the A/B is fair even if not NVLink-faithful). Record which path.
- [ ] **Step 5:** Revert the temporary edits (`git -C sim/pcm-sdk_zhiyi checkout ...`), rebuild.

---

### Task 1: Expose the PFC thresholds as flags (BDP-sizable)

**Files:** Modify `pcm/apps/htsim_atlahs/htsim_app_atlahs.cpp` (arg-parse near `:567`; threshold set `:762-764`).

**Interfaces produced:** CLI `-lossless_high_pfc <packets>` and `-lossless_low_pfc <packets>`; ints `high_pfc` (default 100), `low_pfc` (default 80).

- [ ] **Step 1:** Declare `int high_pfc = 100, low_pfc = 80;` alongside the other flag locals (before the arg loop).
- [ ] **Step 2:** Add to the else-if chain (after the `-intranode_linkspeed` case at `:571-574`):
```cpp
} else if (!strcmp(argv[i], "-lossless_high_pfc")) {
    high_pfc = atoi(argv[i + 1]); i++;
} else if (!strcmp(argv[i], "-lossless_low_pfc")) {
    low_pfc = atoi(argv[i + 1]); i++;
```
- [ ] **Step 3:** Replace `:762-764` with:
```cpp
LosslessInputQueue::_high_threshold = Packet::data_packet_size() * high_pfc;
LosslessInputQueue::_low_threshold  = Packet::data_packet_size() * low_pfc;
cout << "PFC lossless thresholds: high=" << high_pfc << "pkt low=" << low_pfc << "pkt" << endl;
```
- [ ] **Step 4:** Rebuild (`atlahs-sim build`). Verify default run still prints `high=100pkt low=80pkt` (backward-compat) and a `-lossless_high_pfc 4000` run prints `high=4000pkt`.
- [ ] **Step 5:** Commit (`feat(htsim): expose lossless PFC thresholds as CLI flags`).

---

### Task 2: Per-tier CC — DCTCP on scale-out, none on intranode

**Files:** Modify `pcm/apps/htsim_atlahs/htsim_app_atlahs.cpp` (arg-parse; `su_api` block `:1263-1265`).

**Interfaces produced:** CLI `-intranode_cc <dctcp|none>` (default `dctcp`). `none` ⇒ `su_api->pcm_enable=false` (plain `UecSrc`) with the native window forced to `CONSTANT`.

- [ ] **Step 1:** Add `string intranode_cc = "dctcp";` local; parse `-intranode_cc` in the else-if chain.
- [ ] **Step 2:** At the `su_api` PCM-settings site (`:1263-1265`), gate on `intranode_cc`:
```cpp
bool su_pcm = pcm_enable && (intranode_cc != "none");
if (su_pcm) lgs->htsim_api_to_use->pcm_cc_config_filename = pcm_cc_config_filename;
lgs->htsim_api_to_use->pcm_enable = su_pcm;   // false ⇒ plain UecSrc, no PCM/DCTCP
```
- [ ] **Step 3:** When `intranode_cc == "none"`, ensure the native window is the no-op `CONSTANT` (not the NSCC default at `uec.cpp:63`). Set `UecSrc::_sender_cc_algo = UecSrc::CONSTANT;` after arg-parse (guard behind `intranode_cc=="none"`). Verify scale-out is unaffected: `PcmSrc` overrides the window callbacks via its VM (`htsim_pcm_src.hpp:219-224`), so the global `_sender_cc_algo` touches only the plain (intranode) sources.
  - *Caveat to check in review:* `-sender_cc_algo` is global; confirm no scale-out plain `UecSrc` exists (all so_api goal flows are `PcmSrc` when `pcm_enable`). If any plain scale-out source exists, this needs per-API plumbing instead of the global.
- [ ] **Step 4:** Rebuild. Smoke: run pp1_tp4 baseline @12800 with `-intranode_cc none -lossless_high_pfc <BDP from Task 0> -pcm_enable` (scale-out DCTCP). Expect: 0 drops, 0 lossless violations, bounded makespan; DCTCP still active on scale-out (`PcmScheduler: matched tag ... uec_dctcp_v2` still printed).
- [ ] **Step 5:** Commit (`feat(htsim): -intranode_cc none — CC-free scale-up domain (line-rate + lossless PFC)`).

---

### Task 3: Route the INC collective through the intranode NIC

**Files:** Modify `HTSIM_spcl-patch/uec_collectives.h` (`connect_collective` `:59-63`), `HTSIM_spcl-patch/uec_collectives.cpp` (`UecBcastSrcMcast::emit_once` `:11-27`, `UecReduceSrc::emit_once` `:43-74`).

**Interfaces:** consumes the base `UecSrc::_nic` (`uec.h:247`, `_nic.linkspeed()` == `intranode_linkspeed`) and the serialization idiom from `uec.cpp:330`.

- [ ] **Step 1:** In `emit_once`, replace the unpaced `while (_coll_sent < flowsize()) p->sendOn();` loop with NIC-paced emission: schedule each packet's send at `now + (pkt_size*8*timeFromSec(1.0))/_nic.linkspeed()` cumulative offset (mirroring `UecNIC::startSending`, `uec.cpp:330`), so the collective's endpoint injection is serialized at the intranode line rate — identical to the baseline's per-packet NIC cost. Keep the in-network fanout/reduce (switch-side) unchanged.
- [ ] **Step 2:** In `connect_collective`, stop deliberately bypassing the NIC path — attach the source's `_nic` so `emit_once` can query `linkspeed()` and enqueue through it (keep skipping the pull-pacer/cwnd/RTO datapath; we only add line-rate serialization, not CC).
- [ ] **Step 3:** Rebuild. Sanity: the INC arm makespan must now **change with `-intranode_linkspeed`** (it was byte-identical before). Run INC pp1_tp4 at 100 vs 12800 Gbps: expect distinct makespans, INC slower at 100.
- [ ] **Step 4:** Correctness: INC collective must still complete (byte-count at `UecCollectiveSink`), 0 drops, and INC ≤ baseline at each speed.
- [ ] **Step 5:** Commit (`fix(inc): serialize INC collective endpoints at the intranode NIC line rate (fair A/B)`).

---

### Task 4: Wire the experiment + validate the corrected sweep

**Files:** Modify `simulation-scripts/experiments/intranode_linkspeed_sweep/run.py` (`run_one_sim`, `SIM_*` constants); update README.

- [ ] **Step 1:** In `run_one_sim`, add `-intranode_cc none` and `-lossless_high_pfc <BDP>` (value from Task 0), keep `-pcm_enable` + the DCTCP config (scale-out). Add a `pfc_high`/`intranode_cc` CSV column for provenance.
- [ ] **Step 2:** `--validate` still passes (layout unchanged).
- [ ] **Step 3:** Full sweep. Expected NEW shape: **both** INC and baseline slope down with intranode link speed (INC less steeply), INC ≤ baseline everywhere, both converge toward a floor at high speed; 0 drops; makespan bounded. Contrast with the archived flat-INC plots.
- [ ] **Step 4:** Update the experiment README + `AA-plan-Intranode-Linkspeed-Sweep/plan.md` (§12): INC is now NIC-rate-gated; intranode is CC-free (line-rate + lossless PFC); scale-out keeps DCTCP.
- [ ] **Step 5:** Commit (`feat(exp): CC-free intranode + NIC-gated INC in the link-speed sweep`).

---

## Risks / open items
- **Task 0 gate:** if a CC-free intranode can't be made bounded (lossless queue insufficient regardless of threshold), fall back to the consistency fix (Tasks 3-4 only: keep DCTCP both tiers, just NIC-gate INC — fair, if not NVLink-faithful) and document the FC-fidelity gap as future work.
- **Global `_sender_cc_algo`:** Task 2 Step 3 assumes all scale-out goal flows are `PcmSrc`; verify in review or plumb CC per-`AtlahsHtsimApi` instead.
- **INC emit change (Task 3):** the in-network fanout/reduce assumes the current emit model; watch for interaction (double-counting, completion detection) — the byte-count sink check is the guard.
- **Whole-model caveat (document, don't fix here):** modelling NVLink with a UEC/PFC stack is a first-order bandwidth+latency abstraction; credit-vs-PFC and the DCTCP-on-scale-out choices are documented limitations, not claims of NVLink protocol fidelity.
