# kvspill

Storage-tier benchmarks shaped like LLM KV-cache offload — now with a
measured profile from a real serving stack.

When an inference server offloads KV-cache to NVMe (vLLM/LMCache-class
stacks, host-DRAM staging path), the storage tier sees a distinctive I/O
mix: bursts of large extent reads when sequences resume (time-to-first-token
gated), streaming prefix-cache reloads, bulk eviction writes under memory
pressure, and latency-sensitive small reads sharing devices with all of it.
General-purpose fio profiles don't capture this mix; kvspill packages it as
reproducible workloads with the measurement discipline (gates,
preconditioning, per-job accounting) built in.

Born out of a kernel measurement campaign on the NVMe DMA-optimal
request-size limit, where these shapes surfaced both the wins (extent-tail
p99 −32% bare metal; +777% restore bandwidth and 9x lower J/GiB in
shadow-vIOMMU guests) and the hazards (same-device small-IO p99 +140%)
that block-size sweeps alone missed.

## Workloads

| workload | shape | serving pattern it models |
|---|---|---|
| `kv-restore` | 16 jobs x QD2 x 2MB randread | generic sequence-resume shape; whole-extent p99 is the TTFT proxy |
| `kv-restore-calibrated` | **4 jobs x QD1 x 7MB randread** | the **measured** LMCache restore shape (see below) |
| `kv-prefix` | 8 jobs x QD4 x 1MB seq read, disjoint regions | cold prefix-cache reload, multiple tenants |
| `kv-qos` | 2MB restore storm + 4K QD16 readers, same device, per-job stats | restores vs metadata/embedding lookups; the 4K job's p99 under storm is the result |
| `kv-evict` | 4 jobs x QD8 x 2MB write | eviction flush under memory pressure |

## The measured profile (LMCache 0.5.3 + vLLM 0.27.1)

`profiles/lmcache-qwen2.5-1.5b.md` documents an strace capture of the real
disk backend under forced eviction/restore churn:

- **Whole-object I/O**: every store is one `write(2)`, every restore one
  `readinto()` — no sub-chunking. Object size is formula-exact:
  `2 x layers x kv_heads x head_dim x dtype_bytes x chunk_tokens`
  (7 MiB for Qwen2.5-1.5B at chunk 256; **32 MiB** for Llama-8B-class GQA).
- **Shallow concurrency**: a 4-thread synchronous worker pool — effective
  QD ≈ 4 at object size, not deep queues of small requests.
- **Buffered by default**: the kernel-side consumer is the writeback path
  unless `extra_config: {use_odirect: true}` is set (both modes verified).

Deployment pitfalls the capture surfaced (details in the profile):
vLLM `enable_prefix_caching=True` can suppress LMCache lookups entirely
(chunks re-stored forever, never restored — verify the "LMCache hit tokens"
counters); an inclusive CPU tier absorbs restores until it is genuinely
smaller than the working set.

## Findings worth knowing before tuning request sizes

From the originating kernel campaign (details and data in the
[measurement study](https://github.com/davidlohr/dma-opt-clamp-report)):

- **The superpage cliff (virtualized)**: in shadow-vIOMMU guests
  (`intel-iommu,caching-mode=on` over VFIO passthrough), request-size gains
  are a cliff, not a slope — +12/+17/+21% at 256K/512K/1M vs **+837% at
  2MB**, because only ≥2MB requests with physically contiguous buffers map
  as one IOMMU superpage (one shadow-sync trap instead of per-4K-PTE).
  Marginal energy drops from 49 to 5.4 J/GiB at the cliff.
- **Tail-vs-median trade (bare metal)**: unsplit large requests improved
  whole-extent p99 32% while the median worsened 23% — TTFT-driven
  deployments usually take that trade.
- **Never co-locate**: an opted-in offload device penalizes same-device 4K
  readers severely (p99 4.6ms → 11.1ms); on a separate device they are
  untouched (165µs). Per-device request-size policy maps onto tier layout.
- **Drive-specific costs**: large requests cost −8% streaming on one
  enterprise NVMe and were free on another. Measure per drive model.

## Usage

```sh
# everything: gates, precondition-once, 3 reps of all workloads
sudo bin/kvspill-run /dev/nvmeXnY

# A/B a request-size policy (e.g. the max_sectors_kb opt-in)
sudo bin/kvspill-run /dev/nvmeXnY --msk 128
sudo bin/kvspill-run /dev/nvmeXnY --msk 2048
bin/kvspill-compare results/a.log results/b.log
```

`kvspill-run` refuses non-blank devices unless `--force` (partitions,
holders, mounts, fs signatures), preconditions the test region once per
drive serial (reads of unwritten NVMe LBAs hit the deallocated fast path
and measure the controller's zero-engine, not media), records the gates
that make results interpretable (`max_hw_sectors_kb`, `max_sectors_kb`,
IOMMU domain type, MDTS), and emits one parseable `RESULT` line per job
per rep.

## Repository layout

- `workloads/` — fio job files (generic shapes + calibrated variants)
- `profiles/` — measured stack profiles and their capture data
- `bin/kvspill-run`, `bin/kvspill-compare`, `bin/parse-fio.py`
- `docs/patterns.md` — the pattern↔workload mapping, fidelity and limits
- `docs/roadmap.md` — enhancement plan (calibration: done; open-loop
  arrivals, sequence-completion latency, filesystem/writeback mode, device
  characterization, CI smoke mode)

## Hard-won details baked in

- fio options after a `--name` are job-local: in multi-job invocations
  globals must precede the first `--name` or later jobs silently fail.
- Large requests need segment headroom: scattered 4K buffers split at the
  NVMe 256-segment budget regardless of sector limits — hugepage-backed
  buffers (`iomem=mmaphuge`) keep request-size experiments honest, and are
  required to reach the superpage cliff at all.
- Per-drive preconditioning markers are keyed by serial, not device name
  (names shuffle across boots).
- strace-based capture of threaded I/O needs unfinished/resumed syscall
  joining and a process-global fd map, or most of the I/O is invisible.

## License

GPL-2.0-only. See COPYING.
