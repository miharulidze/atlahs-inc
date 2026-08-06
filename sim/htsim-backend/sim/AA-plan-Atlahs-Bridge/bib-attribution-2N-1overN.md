# Citation attribution for the 2(N−1)/N ideal-ring factor vs the flat-2× INC claim

*Verified 2026-07-06 against the actual PDFs (Hoefler arXiv:2601.19132 fetched + pdftotext; Khalilov
arXiv:2408.13356; MPICH cost-model paper). Ready to paste into `thesis/refs.bib` + prose. Two DIFFERENT
quantities, two DIFFERENT owners — do not conflate.*

## The rule
- **Flat 2× / "halves AllReduce bandwidth"** → Hoefler 2026 (attributes it to Khalilov SC'24, ref [13]).
  It is the asymptotic (N→∞) ceiling, justified by "endpoint sends+receives each segment twice vs INC
  once." No per-N formula appears in either paper.
- **Per-N factor 2(N−1)/N** → classic MPI collective cost-model literature (Thakur–Rabenseifner–Gropp
  2005; Rabenseifner 2004). NOT Hoefler, NOT Khalilov. It is the ring / recursive-halving-doubling
  bandwidth term (reduce-scatter (N−1)/N + allgather (N−1)/N). Evaluates to 1.5× at N=4 → 2× as N→∞, i.e.
  Hoefler's flat 2× is exactly its large-N limit.

Khalilov cite in Hoefler = **CONFIRMED** (ref [13] = "Network-Offloaded Bandwidth-Optimal Broadcast and
Allgather for Distributed AI", SC'24), cited in-text at the AllReduce bandwidth claims.

## refs.bib entries — for the 2(N−1)/N ideal-ring model (D3 computed column + oracle)
```bibtex
@article{thakur2005optimization,
  author={Thakur, Rajeev and Rabenseifner, Rolf and Gropp, William},
  title={Optimization of Collective Communication Operations in {MPICH}},
  journal={International Journal of High Performance Computing Applications},
  volume={19}, number={1}, pages={49--66}, year={2005},
  doi={10.1177/1094342005051521}}
@inproceedings{rabenseifner2004reduction,
  author={Rabenseifner, Rolf},
  title={Optimization of Collective Reduction Operations},
  booktitle={Computational Science -- ICCS 2004},
  series={LNCS}, volume={3036}, pages={1--9}, year={2004},
  publisher={Springer}, doi={10.1007/978-3-540-24685-5_1}}
% optional ring bandwidth-optimality proof:
@article{patarasuk2009bandwidth,
  author={Patarasuk, Pitch and Yuan, Xin},
  title={Bandwidth Optimal All-reduce Algorithms for Clusters of Workstations},
  journal={Journal of Parallel and Distributed Computing},
  volume={69}, number={2}, pages={117--124}, year={2009},
  doi={10.1016/j.jpdc.2008.09.002}}
```
Prose: *"The ideal-ring AllReduce moves 2(N−1)/N of the payload per rank (reduce-scatter (N−1)/N +
allgather (N−1)/N)~\cite{thakur2005optimization,rabenseifner2004reduction}; this is the analytic floor our
measured INC arm is compared against."*

## refs.bib entries — for the flat-2× INC bandwidth claim
```bibtex
@article{hoefler2026inc,
  author={Hoefler, Torsten and Khalilov, Mikhail and Clark, Josiah and Anubolu, Surendra and Kalkunte, Mohan and Schramm, Karen and Spada, Eric and Roweth, Duncan and Underwood, Keith and Caulfield, Adrian and Kabbani, Abdul and Rastegari, Amirreza},
  title={In-Network Collective Operations: Game Changer or Challenge for {AI} Workloads?},
  journal={Computer}, volume={59}, number={1}, pages={24--33}, year={2026},
  publisher={IEEE}, note={arXiv:2601.19132}}
@inproceedings{khalilov2024bcast,
  author={Khalilov, Mikhail and Di Girolamo, Salvatore and Chrapek, Marcin and Nudelman, Rami and Bloch, Gil and Hoefler, Torsten},
  title={Network-Offloaded Bandwidth-Optimal Broadcast and Allgather for Distributed {AI}},
  booktitle={SC24: Int'l Conf. for High Performance Computing, Networking, Storage and Analysis},
  year={2024}, publisher={IEEE Press},
  doi={10.1109/SC41406.2024.00109}, note={arXiv:2408.13356}}
```
Prose (make the asymptote explicit): *"Hoefler et al.~\cite{hoefler2026inc} state that Core-INC cuts the
needed network bandwidth in half for AllReduce, attributing the bound to~\cite{khalilov2024bcast}. This
flat 2× is the N→∞ limit of the classical 2(N−1)/N ring term; at scale-up sizes it is only 1.5× at N=4,
which is why we report the per-N factor rather than a flat 2×."* Do NOT cite Hoefler/Khalilov for the
2(N−1)/N formula itself.

## Caveat on the +1-RTT sync numbers (D1)
Khalilov's paper does NOT print a "1 RTT"/"½ RTT" figure — only "constant-time" + "one handshake round".
The 2600 ns (+1 RTT pessimistic) / 1300 ns (½-RTT one-way sensitivity) are **the thesis's own quantification**
of the 3-phase app-sync — cite them to the thesis, never to Khalilov.
