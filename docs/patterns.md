# The KV-cache offload I/O pattern, and how kvspill models it

## Where the pattern comes from

Serving stacks that offload KV-cache to NVMe (vLLM/LMCache-class, host-DRAM
staging path) generate storage I/O with structure that generic benchmarks
miss:

- **Sequence resume (restore)**: an evicted conversation returns; its KV
  blocks must be read back before prefill can start. Time-to-first-token is
  gated on *whole-extent completion*, so the tail of extent latency - not
  average bandwidth - is the user-visible metric. Requests arrive in bursts
  (many sequences resume together after a tenant becomes active).
- **Prefix-cache reload**: shared system prompts / long contexts written
  once, contiguously; read back sequentially per stream, several tenants at
  a time.
- **Eviction**: bulk 1-2MB writes when GPU/host KV budgets overflow.
- **Foreground small I/O**: block-table and metadata lookups, embedding
  fetches, other tenants - latency-sensitive 4K reads that share devices
  with all of the above unless the operator separates tiers.

## Fidelity choices

| choice | rationale |
|---|---|
| `O_DIRECT` + `io_uring` | matches the real stacks' I/O engines |
| hugepage-backed buffers | mirrors pinned, physically contiguous staging buffers registered for DMA; also removes the NVMe segment budget (256) as a confound in request-size experiments |
| 1-2MB extents | where per-sequence KV block spans land for 7-70B-class models with contiguous block allocators |
| QD2 per restore job | restore pipelining: fetch the next extent while the current lands |
| per-job reporting in kv-qos | the small-IO job's p99 under storm is the result; group reporting would average it away |

## Stated limits

No compute overlapping I/O, no host-to-device copy stage, extents are
contiguous single reads rather than per-layer scatter. These all sit above
the block/DMA layers kvspill measures. If you need end-to-end TTFT, run a
real serving stack; if you need to understand what the *storage tier* will
do under KV offload - request sizing, tail behavior, interference,
CPU/interrupt cost - kvspill isolates exactly that.

## Findings from the original campaign (tip v7.2-rc1 era, 2026-08)

Measured on Samsung PM9A3 behind AMD-Vi (translated, lazy), comparing 128KB
vs 2MB request-size policy (`max_sectors_kb`):

- extent restore p99 improved 32% with unsplit 2MB requests (no
  sub-request jitter), while the median worsened 23% and aggregate BW lost
  6% - a tail-vs-median trade that TTFT-driven deployments should usually
  take.
- same-device 4K readers under a 2MB storm: p99 went 4.6ms -> 11.1ms
  (+140%). Moved to a separate device: 165us. **Never co-locate an opted-in
  offload tier with latency-critical small I/O.**
- the raw-bandwidth cost of large requests is drive-specific: -8% streaming
  on PM9A3, flat on Micron 7450 (which instead gained -72% sys CPU through
  a stacked fs+md path). Measure per drive model.
- interrupts/GB fall 75-94% with 2MB requests; whether that converts to
  anything depends on where completions are expensive (virtualized NVMe:
  transformative; idle bare-metal cores: nothing).

## Operational gotchas encoded in the driver

- reads of unwritten NVMe LBAs hit the deallocated fast path and benchmark
  the controller's zero-engine, not media: precondition first (done once
  per drive serial).
- fio options after a `--name` are job-local; globals must precede the
  first job section.
- device names shuffle across boots; key any per-drive state by serial.
- record `max_hw_sectors_kb`, `max_sectors_kb`, MDTS, and the IOMMU domain
  type with every run - results are uninterpretable without them.
