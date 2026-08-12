# Measured profile: LMCache 0.5.3 + vLLM 0.27.1, Qwen2.5-1.5B-Instruct

Captured 2026-08-12 on an RTX 3090 pod (strace of the disk backend; raw
distributions in `data/calib.tgz`). This calibrates kvspill's synthetic
shapes against what the real stack actually issues.

## Measured facts

- **Object size: exactly 7,340,032 bytes (7 MiB), every read and write.**
  Formula-exact: 2(K+V) x 28 layers x 2 kv_heads x 128 head_dim x 2 B
  (bf16) x 256-token chunk. Scales linearly with model KV geometry and
  `chunk_size`: Llama-3.1-8B-class (GQA-8) at chunk 256 = **32 MiB**
  objects.
- **I/O discipline: single whole-object syscalls.** One `write(2)` per
  store, one `readinto()` per restore. No sub-chunking, no scatter.
  Buffered by default; `extra_config: {use_odirect: true}` switches both
  sides to O_DIRECT (verified both modes).
- **Concurrency: a 4-thread worker pool** (`disk_io_threads`, default 4)
  doing synchronous ops - effective QD ~= 4 at 7 MiB, not deep queues of
  small requests.
- Capture run: 108 stores, 200 restores, all exactly 7 MiB.

## kvspill mapping

The calibrated restore shape for this stack class is therefore:

    numjobs=4 iodepth=1 bs=7m (or model-derived) randread

rather than the generic default (16 jobs x QD2 x 2MB). See
`workloads/kv-restore-calibrated.fio`. Set `bs` from the object-size
formula for the model under study.

## Deployment pitfalls discovered during capture

1. **vLLM prefix caching ON suppressed LMCache lookups entirely** in the
   offline-LLM setup: identical chunks were re-stored every epoch and no
   restore ever hit the cache (double storage, zero benefit). Disabling
   `enable_prefix_caching` restored correct lookup/hit behavior. Verify
   hit counters ("LMCache hit tokens") before trusting any offload
   deployment.
2. **The backend defaults to buffered I/O** - the kernel-side consumer is
   the writeback path, not direct I/O, unless `use_odirect` is set.
3. An inclusive CPU tier absorbs restores: disk reads only happen once
   the CPU tier is genuinely smaller than the working set.
