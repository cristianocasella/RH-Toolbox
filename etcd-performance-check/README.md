# etcd Performance Check

Collects etcd performance metrics from an OpenShift cluster, rates them against best-practice thresholds, and generates a markdown report.

## Overview

Healthy etcd is critical for a stable OpenShift cluster. This tool queries Prometheus and etcd pod logs to gather five key metric families, then rates each per-node value on a five-level scale (Superb / Good / Acceptable / Warning / Critical). Results are shown in the terminal with color-coded tables and saved to a markdown file for sharing.

**Metrics collected:**

- **WAL fsync duration** (99th percentile) -- disk write latency for the write-ahead log
- **Compaction pause duration** (99th percentile) -- time the MVCC compactor pauses the database
- **Backend commit duration** (99th percentile) -- time to commit a backend transaction to disk
- **DB size & fragmentation** -- total size vs. in-use size derived from compaction logs
- **Compaction time** -- average/min/max duration of compaction events from etcd logs

The script requires only the Python standard library (no pip dependencies) and uses the `oc` CLI to communicate with the cluster.

## Prerequisites

- **Python 3.6+**
- **`oc` CLI** -- must be logged in to the target OpenShift cluster (`oc login`)
- **Prometheus** -- the default OpenShift monitoring stack must be running
- **`promtool`** -- available inside the `prometheus-k8s-0` pod (ships with the monitoring stack)

## Quick Start

```bash
# Basic run -- uses all defaults
python3 etcd_performance_check.py

# Use a wider rate window for smoother percentiles
python3 etcd_performance_check.py --rate-interval 10m

# Save the report to a specific path
python3 etcd_performance_check.py -o /tmp/etcd-report.md
```

## Usage

```
usage: etcd_performance_check.py [-h] [-n NAMESPACE] [--monitoring-ns NS]
                                  [--prom-pod POD] [--log-lines N]
                                  [-o OUTPUT] [--rate-interval INTERVAL]
```

### Options

| Parameter | Default | Description |
|---|---|---|
| `-n`, `--namespace` | `openshift-etcd` | Namespace where etcd pods run |
| `--monitoring-ns` | `openshift-monitoring` | Namespace where Prometheus runs |
| `--prom-pod` | `prometheus-k8s-0` | Name of the Prometheus pod to exec into |
| `--log-lines` | `2000` | Number of recent log lines to fetch per etcd pod |
| `-o`, `--output` | `etcd-report-<cluster>-<date>.md` | Path for the markdown report |
| `--rate-interval` | `5m` | Prometheus `rate()` window for histogram queries |

## Rating Thresholds

### WAL Fsync Duration (milliseconds)

| Rating | Threshold |
|---|---|
| Superb | < 2 ms |
| Good | < 5 ms |
| Acceptable | < 10 ms |
| Warning | < 20 ms |
| Critical | >= 20 ms |

### Compaction Pause Duration (milliseconds)

| Rating | Threshold |
|---|---|
| Superb | < 100 ms |
| Good | < 500 ms |
| Acceptable | < 900 ms |
| Warning | < 1500 ms |
| Critical | >= 1500 ms |

### Backend Commit Duration (milliseconds)

| Rating | Threshold |
|---|---|
| Superb | < 10 ms |
| Good | < 25 ms |
| Acceptable | < 50 ms |
| Warning | < 100 ms |
| Critical | >= 100 ms |

### Compaction Time (milliseconds)

| Rating | Threshold |
|---|---|
| Superb | < 500 ms |
| Good | < 700 ms |
| Acceptable | < 900 ms |
| Warning | < 1500 ms |
| Critical | >= 1500 ms |

### DB Fragmentation (percentage)

| Rating | Threshold |
|---|---|
| Superb | < 20% |
| Good | < 30% |
| Acceptable | < 40% |
| Warning | < 50% |
| Critical | >= 50% |

## Usage Examples

```bash
# Default: openshift-etcd namespace, 5m rate window
python3 etcd_performance_check.py

# Custom etcd namespace (e.g. single-node or non-standard deployment)
python3 etcd_performance_check.py -n my-etcd-ns

# Use a different Prometheus pod (e.g. HA pair)
python3 etcd_performance_check.py --prom-pod prometheus-k8s-1

# Fetch more log lines for better compaction statistics
python3 etcd_performance_check.py --log-lines 10000

# Wider rate window for clusters with low request rates
python3 etcd_performance_check.py --rate-interval 15m

# Combine options
python3 etcd_performance_check.py --rate-interval 10m --log-lines 5000 -o /tmp/report.md
```

## Example Output

### Terminal

```
etcd Performance Check
────────────────────────────────────────

[1/4] Detecting cluster ...
  Cluster: ocp4.example.com

[2/4] Discovering etcd pods ...
  etcd-master-0.example.com (10.0.1.10)
  etcd-master-1.example.com (10.0.1.11)
  etcd-master-2.example.com (10.0.1.12)

[3/4] Collecting Prometheus metrics ...
  Collecting WAL fsync duration ... (3 nodes)
  Collecting compaction pause duration ... (3 nodes)
  Collecting backend commit duration ... (3 nodes)
  Collecting DB size ... (3 nodes)

[4/4] Collecting compaction logs ...
  Collecting compaction logs (2000 lines per pod) ...
    etcd-master-0.example.com ... (48 entries)
    etcd-master-1.example.com ... (45 entries)
    etcd-master-2.example.com ... (47 entries)

════════════════════════════════════════════════════════════
  etcd Performance Report — ocp4.example.com
  2026-07-30 14:00 UTC
════════════════════════════════════════════════════════════

WAL Fsync Duration (99th percentile)
┌──────────────────────┬──────────┬────────┬────────────────┐
│ Node                 │ Value    │ Target │ Status         │
├──────────────────────┼──────────┼────────┼────────────────┤
│ master-0.example.com │ 3.21ms   │ <10ms  │ ✅ Good        │
│ master-1.example.com │ 2.98ms   │ <10ms  │ ✅ Good        │
│ master-2.example.com │ 4.15ms   │ <10ms  │ ✅ Good        │
└──────────────────────┴──────────┴────────┴────────────────┘

Overall: ✅ ALL METRICS HEALTHY

Report saved to: etcd-report-ocp4.example.com-20260730.md
```

### Markdown Report

The generated markdown file contains the same tables and ratings in standard markdown format, suitable for pasting into tickets, wikis, or documentation systems.

## How It Works

1. **Cluster Detection** -- Reads the current `oc` login context to identify the cluster name.
2. **Pod Discovery** -- Lists etcd pods in the target namespace via label selector `app=etcd`.
3. **Prometheus Collection** -- Executes `promtool query instant` inside the Prometheus pod to gather histogram percentiles and gauge values for WAL fsync, compaction pause, backend commit, and DB size.
4. **Log Collection** -- Fetches the last N lines from each etcd pod's logs and extracts compaction duration entries, including DB size and in-use size for fragmentation calculation.
5. **Analysis** -- Each metric value is compared against the threshold table to produce a rating. Fragmentation is computed as `(1 - in_use / total) * 100%`.
6. **Output** -- Results are rendered as color-coded terminal tables and written to a markdown report file.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Script completed successfully |
| `1` | No etcd pods found, or an unrecoverable error occurred |

## Troubleshooting

### No etcd pods found

**Problem**: Script exits with "No etcd pods found"

**Solution**:
- Verify you are logged in: `oc whoami`
- Check the namespace: `oc get pods -n openshift-etcd -l app=etcd`
- If etcd runs in a different namespace, use `-n <namespace>`

### Prometheus query returns no data

**Problem**: Metrics show 0 nodes for one or more collectors

**Solution**:
- Ensure the monitoring stack is healthy: `oc get pods -n openshift-monitoring`
- Check that `prometheus-k8s-0` is running (or use `--prom-pod` to specify another)
- Try a wider rate interval: `--rate-interval 15m`
- Verify promtool is available: `oc exec -n openshift-monitoring prometheus-k8s-0 -- promtool --version`

### Command timed out

**Problem**: One or more `oc exec` commands time out

**Solution**:
- Check network connectivity to the cluster API
- The Prometheus pod may be under heavy load -- retry later
- For large log volumes, the grep on compaction logs may take longer than 30 seconds

### No compaction log entries

**Problem**: Compaction log analysis shows 0 entries for all pods

**Solution**:
- Increase `--log-lines` (default 2000 may not cover enough history)
- Verify etcd is performing compactions: `oc logs -n openshift-etcd <pod> -c etcd | grep compaction | tail -5`
- On low-activity clusters, compactions may be infrequent

## Requirements

- `oc` CLI must be installed and authenticated to the target cluster
- The OpenShift monitoring stack (Prometheus) must be running
- Network access from the machine running the script to the cluster API
