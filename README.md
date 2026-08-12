# kvspill

Storage-tier benchmarks shaped like LLM KV-cache offload.

When an inference server offloads KV-cache to NVMe (vLLM/LMCache-class
stacks, host-DRAM staging path), the storage tier sees a distinctive I/O
mix: bursts of large sequential-ish extent reads when sequences resume
(time-to-first-token gated), streaming prefix-cache reloads, bulk eviction
writes under memory pressure, and latency-sensitive small reads sharing
devices with all of it. General-purpose fio profiles don't capture this
mix; kvspill packages it as reproducible workloads with the measurement
discipline (gates, preconditioning, per-job accounting) built in.

Born out of a kernel measurement campaign on the NVMe DMA-optimal
request-size limit, where these shapes surfaced both the wins (extent-tail
p99 −32%) and the hazards (same-device small-IO p99 +140%) that
block-size sweeps alone missed.

## Workloads

| workload | shape | serving pattern it models | headline metric |
|---|---|---|---|
| `kv-restore` | 16 jobs x QD2 x 2MB randread | concurrent sequence resume; each job = one sequence pulling KV extents, QD2 = restore pipelining | whole-extent p99 (TTFT proxy) |
| `kv-prefix` | 8 jobs x QD4 x 1MB seq read, disjoint regions | cold prefix-cache reload, multiple tenants | aggregate BW |
| `kv-qos` | 2MB restore storm + 4K QD16 readers, same device, per-job stats | restores vs metadata/embedding lookups | the 4K job's p99 under storm |
| `kv-evict` | 4 jobs x QD8 x 2MB write | eviction flush under memory pressure | BW, tail shape |

Fidelity choices: `O_DIRECT` + `io_uring` (what the real stacks use),
hugepage-backed buffers (`iomem=mmaphuge`) mirroring pinned staging
buffers registered for DMA — this also keeps request-size experiments
honest by taking the NVMe segment budget out of the equation. Extent
sizes (1–2MB) sit where per-sequence KV block spans land for 7–70B-class
models with contiguous block allocators.

Stated limits: no compute overlapping I/O, no H2D copy stage, extents are
contiguous reads rather than per-layer scatter. All of that sits above
the block/DMA layers these workloads exist to measure.

## Usage

```sh
# everything: gates, precondition-once, 3 reps of all workloads
sudo bin/kvspill-run /dev/nvmeXnY

# A/B a request-size policy (e.g. the max_sectors_kb opt-in)
sudo bin/kvspill-run /dev/nvmeXnY --msk 128
sudo bin/kvspill-run /dev/nvmeXnY --msk 2048
bin/kvspill-compare results/a.log results/b.log
```

`kvspill-run` refuses non-blank devices unless `--force` (checks
partitions, holders, filesystem signatures), preconditions the test
region once per drive serial (reads of unwritten NVMe LBAs hit the
deallocated fast path and measure the controller's zero-path, not media),
verifies and records the gates that make results interpretable
(`max_hw_sectors_kb`, `max_sectors_kb`, IOMMU domain type, MDTS), and
emits one parseable `RESULT` line per job per rep.

## Hard-won details baked in

- fio options after a `--name` are job-local: in multi-job invocations
  globals must precede the first `--name` or later jobs silently fail.
- 2MB requests need segment headroom: scattered 4K buffers split at the
  NVMe 256-segment budget regardless of sector limits.
- Per-drive preconditioning markers are keyed by serial, not device name
  (names shuffle across boots).
- Request-size policy effects are drive-specific: the same opt-in that
  costs 8% streaming on one enterprise NVMe is free on another. Measure
  per drive model; see `docs/patterns.md`.

## License

GPL-2.0-only. See COPYING.
