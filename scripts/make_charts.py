"""Turn results/*.json into PNG charts for the report."""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fe = json.load(open("results/failure_experiment.json"))
lt = json.load(open("results/load_test.json"))
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})

# 1. recovery time vs damaged records
a = fe["scenario_A"]
fig, ax = plt.subplots(figsize=(6.4, 3.2))
x = [r["damaged"] for r in a]
ax.bar([str(v) for v in x], [r["recovery_time_ms_server"] for r in a], color="#355c9a", label="Restore (server RTO)")
ax.plot([str(v) for v in x], [r["detect_ms"] for r in a], "o-", color="#c47f00", label="Detection (verify API)")
ax.set_xlabel("Damaged records (half deleted, half corrupted) out of 1000")
ax.set_ylabel("Time (ms)"); ax.set_title("Detection and recovery time vs damage size"); ax.legend(frameon=False)
fig.tight_layout(); fig.savefig("results/chart_recovery.png", dpi=170)

# 2. load test latency + throughput
fig, ax = plt.subplots(figsize=(6.4, 3.2))
lv = [f"{r['level']}\n({r['concurrent_users']} users)" for r in lt]
import numpy as np
i = np.arange(len(lt)); w = 0.27
ax.bar(i - w, [r["p50_ms"] for r in lt], w, label="p50", color="#7aa0d6")
ax.bar(i, [r["p95_ms"] for r in lt], w, label="p95", color="#355c9a")
ax.bar(i + w, [r["p99_ms"] for r in lt], w, label="p99", color="#1d3357")
ax.set_xticks(i); ax.set_xticklabels(lv); ax.set_ylabel("Latency (ms)")
ax2 = ax.twinx(); ax2.plot(i, [r["throughput_rps"] for r in lt], "o--", color="#c47f00", label="throughput")
ax2.set_ylabel("Requests / second"); ax2.spines["top"].set_visible(False)
h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, frameon=False, loc="upper left"); ax.set_title("Load test: latency percentiles and throughput")
fig.tight_layout(); fig.savefig("results/chart_load.png", dpi=170)

# 3. retries under flaky storage
b = fe["scenario_B"]["runs"]
fig, ax = plt.subplots(figsize=(6.4, 2.8))
ax.bar([f"job {k+1}" for k in range(len(b))], [r["recovery_time_ms"] for r in b], color="#2f7d46")
for k, r in enumerate(b):
    ax.text(k, r["recovery_time_ms"], f"{r['attempts']} att / {r['storage_retries']} retry", ha="center", va="bottom", fontsize=8)
ax.set_ylabel("Recovery time (ms)"); ax.set_title("Restores with 50% transient storage failures")
fig.tight_layout(); fig.savefig("results/chart_flaky.png", dpi=170)
print("charts ok")
