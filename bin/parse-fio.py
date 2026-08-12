#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Emit one RESULT line per job per direction from fio JSON output."""
import json
import sys

j = json.load(open(sys.argv[1]))
case, rep, irqs = sys.argv[2], sys.argv[3], int(sys.argv[4])
tot_gb = sum(job[d]["io_bytes"] for job in j["jobs"]
             for d in ("read", "write")) / (1 << 30)
for job in j["jobs"]:
    for d in ("read", "write"):
        r = job[d]
        if r["io_bytes"] == 0:
            continue
        print("RESULT case=%s job=%s dir=%s rep=%s bw_MiBps=%.0f iops=%.0f "
              "p50_us=%.0f p99_us=%.0f usr=%.1f sys=%.1f irq_per_gb=%.0f" % (
                  case, job["jobname"], d, rep,
                  r["bw_bytes"] / (1 << 20), r["iops"],
                  r["clat_ns"]["percentile"]["50.000000"] / 1e3,
                  r["clat_ns"]["percentile"]["99.000000"] / 1e3,
                  job["usr_cpu"], job["sys_cpu"],
                  irqs / tot_gb if tot_gb else 0))
