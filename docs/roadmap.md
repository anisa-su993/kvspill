# Roadmap: making the emulation sharper

Ranked by how much realism each buys per unit of work.

## 1. Calibrate extent sizes against real stacks (highest value)
Today's fixed 1-2MB extents are an informed guess. Instrument a real
vLLM+LMCache deployment (strace/eBPF on the disk backend) and capture the
actual distribution of I/O sizes, queue depths and arrival gaps, then feed
it back as a measured `bssplit` profile per (model size, block allocator,
chunk config). A cheap GPU cloud pod is enough - the capture doesn't need
custom kernels. This turns "shaped like the workload" into "sized by the
workload".

## 2. Open-loop arrival process (fixes coordinated omission)
fio is closed-loop: when the device stalls, load generation stalls with it,
which hides exactly the tail behavior TTFT cares about. Add an open-loop
mode (fio `rate_iops` with iodepth headroom as a first step; a small
io_uring driver as the real fix) with bursty arrivals - Poisson batches of
sequence resumes - so a slow restore piles up queue like production does.

## 3. Sequence-completion latency (true TTFT proxy)
A resume is only done when *all* K extents of a sequence have landed; p99
of per-extent latency underestimates the tail of max-of-K. Small io_uring
driver: submit K-extent batches per sequence, report sequence-completion
percentiles. K distribution from item 1.

## 4. Filesystem mode
Real disk backends are file-per-chunk on a filesystem, not raw devices.
Add a mode that lays out files (fallocate, O_DIRECT) and ages the
filesystem (create/delete churn) to capture extent-allocation and
fragmentation effects the raw-device mode deliberately excludes.

## 5. Steady-state duty cycle
Serving at capacity evicts and restores concurrently. A combined workload
with rate-capped eviction writes under restore reads (plus the qos_small
reader) approximates the steady state; sweep the write fraction.

## 6. Device parallelism characterization (`kvspill-charter`)
The campaign found drives diverge on large-request handling (PM9A3 loses
8% streaming at 2MB, Micron 7450 doesn't). A sweep tool - request size x
QD -> bandwidth/latency surface - would let operators triage a drive model
for the `max_sectors_kb` opt-in in minutes, and would have found the
divergence without two days of A/B.

## 7. CI smoke mode
Back the suite with null_blk so the harness, parsers and A/B compare run
in CI without hardware. Functional only - no numbers.

## 8. GDS/P2P mode (long-term)
When GPUDirect Storage hardware is available: cuFile-based restore path,
NVMe DMA directly to GPU BAR. Different kernel path entirely (P2PDMA);
only worth building alongside kernel-side huge-folio/P2P work.
