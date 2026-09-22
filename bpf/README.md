# DMA map/unmap cost probes

`bin/kvspill-dmacost` wraps a workload in bpftrace and reports how long
nvme-pci spends in the DMA API mapping and unmapping requests. It picks the
probe set from the running kernel:

| kernel | main script | what "one request" means |
|---|---|---|
| < v6.17 | `dma-legacy.bt` | one `dma_map_sgtable()` / `dma_unmap_sg_attrs()` pair |
| >= v6.17 | `dma-twostep.bt` | `blk_rq_dma_map_iter_start()` plus every `iter_next()`; `dma_iova_destroy()` to unmap |

plus one per-segment fragment (`seg-phys.bt` on >= v6.18 where
`dma_map_phys()` exists, `seg-page.bt` otherwise) and, when the symbols are
not inlined, `nvme-req.bt` for the driver-level `nvme_map_data()` /
`nvme_unmap_data()` totals that exist on both eras.

Only devices bound to the `nvme` driver are traced. Kernel BTF is required
for the struct casts. Run as root.

```
kvspill-dmacost -- fio workloads/kv-restore.fio
kvspill-dmacost --duration 30
```

## Reading the report

Map keys are upper-bound buckets: KiB of request or segment size for the
two-step and per-segment maps, scatterlist entry count for the legacy maps.

- `map_req_*` / `unmap_req_*` (two-step): whole-request cost, keyed by
  path then size. On the `iova` path the whole map happens inside
  `blk_rq_dma_map_iter_start`, so `map_req_ns[iova, ...]` equals
  `iter_start_ns`. On the `direct` path it is iter_start plus every
  iter_next, flushed when the request object is next reused. `path[iova]`
  vs `path[direct]` says which path requests took; if `direct` dominates,
  the IOMMU is off or in passthrough and the per-segment maps carry the
  unmap side.
- `iova_alloc_ns`, `iova_link_ns`, `iova_sync_ns`: the three steps inside a
  two-step map. Link is per physical segment, so hugepage-backed buffers
  should show few links per request.
- `sg_map_*` / `sg_unmap_*` (legacy): whole-request cost keyed by nents.
  `sg_map_nents` gives the nents distribution so the two eras can be lined
  up by segment count.
- `seg_map_*` / `seg_unmap_*`: per-segment direct mappings. On both eras
  this includes the single-segment fast path that skips the scatterlist or
  iterator entirely, so small-IO workloads (kv-qos's 4K reader) live here.
- `nvme_map_ns` / `nvme_unmap_ns`: driver-level total per request,
  comparable across eras when present.
- `map_fail`, `iova_unlink`: error paths; expect zero.

Unmap runs in completion context, so its numbers include whatever the IOMMU
driver does on TLB invalidation (strict vs lazy mode). Record the
`iommu_group_type` GATE line with every run; results are not comparable
across domain types.

## Caveats

- A request whose map fails midway never flushes its `map_req` entry; the
  `map_fail` counter records it instead.
- Direct-path `map_req` entries are flushed lazily on request reuse, so
  the handful in flight when tracing stops are dropped.
- Probes are fentry/fexit keyed on the request, IOVA state, scatterlist or
  address, so interrupt-context unmaps nesting inside task-context ones do
  not corrupt timestamps. Per-thread keys did, and produced impossible
  multi-day latencies in the first run.
- The `nvme_*_data` probes disappear when the compiler inlines those
  functions; the GATE line says `nvme_req_probe=inlined` when that happens.
