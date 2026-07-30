#!/usr/bin/env python3
"""etcd Performance Analysis Tool for OpenShift clusters.

Collects etcd performance metrics via Prometheus and etcd logs,
analyzes against best-practice thresholds, and generates both
terminal output and a markdown report.

Requires: oc CLI authenticated to target cluster.
No external Python dependencies (stdlib only).

Usage:
    ./etcd_performance_check.py
    ./etcd_performance_check.py --rate-interval 10m
    ./etcd_performance_check.py -o /tmp/report.md
"""

import argparse
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

THRESHOLDS: Dict[str, List[tuple]] = {
    "wal_fsync_ms": [
        (2, "Superb"),
        (5, "Good"),
        (10, "Acceptable"),
        (20, "Warning"),
        (float("inf"), "Critical"),
    ],
    "compaction_pause_ms": [
        (100, "Superb"),
        (500, "Good"),
        (900, "Acceptable"),
        (1500, "Warning"),
        (float("inf"), "Critical"),
    ],
    "backend_commit_ms": [
        (10, "Superb"),
        (25, "Good"),
        (50, "Acceptable"),
        (100, "Warning"),
        (float("inf"), "Critical"),
    ],
    "compaction_time_ms": [
        (500, "Superb"),
        (700, "Good"),
        (900, "Acceptable"),
        (1500, "Warning"),
        (float("inf"), "Critical"),
    ],
    "fragmentation_pct": [
        (20, "Superb"),
        (30, "Good"),
        (40, "Acceptable"),
        (50, "Warning"),
        (float("inf"), "Critical"),
    ],
}

RATING_SYMBOL: Dict[str, str] = {
    "Superb": "\033[32m✅ Superb\033[0m",
    "Good": "\033[32m✅ Good\033[0m",
    "Acceptable": "\033[33m✅ Acceptable\033[0m",
    "Warning": "\033[33m⚠️  Warning\033[0m",
    "Critical": "\033[31m❌ Critical\033[0m",
}

RATING_MD: Dict[str, str] = {
    "Superb": "✅ Superb",
    "Good": "✅ Good",
    "Acceptable": "✅ Acceptable",
    "Warning": "⚠️ Warning",
    "Critical": "❌ Critical",
}

BOLD = "\033[1m"
RESET = "\033[0m"
CYAN = "\033[36m"
GRAY = "\033[90m"

RATING_ORDER = ["Superb", "Good", "Acceptable", "Warning", "Critical"]


def rate(value: float, metric_key: str) -> str:
    """Return a rating label for the given metric value."""
    for limit, label in THRESHOLDS[metric_key]:
        if value < limit:
            return label
    return "Critical"


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------


def run_cmd(cmd: str, timeout: int = 60) -> Optional[str]:
    """Run a shell command and return its stdout, or None on failure."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,  # pylint: disable=subprocess-run-check
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            print(f"\033[31mCommand failed:\033[0m {cmd}", file=sys.stderr)
            print(result.stderr.strip(), file=sys.stderr)
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"\033[31mCommand timed out:\033[0m {cmd}", file=sys.stderr)
        return None


def detect_cluster() -> str:
    """Derive a short cluster name from the current oc login context."""
    out = run_cmd("oc whoami --show-server")
    if not out:
        return "unknown-cluster"
    match = re.search(r"api[.-]([^:]+)", out)
    return match.group(1) if match else out


def discover_etcd_pods(namespace: str) -> List[Dict[str, str]]:
    """List etcd pods in the given namespace via oc."""
    out = run_cmd(
        f"oc get pods -n {namespace} -l app=etcd "
        f"-o jsonpath='{{range .items[*]}}{{.metadata.name}}"
        f'|{{.status.podIP}}{{"\\n"}}{{end}}\''
    )
    if not out:
        return []
    pods: List[Dict[str, str]] = []
    for line in out.strip().splitlines():
        parts = line.strip().split("|")
        if len(parts) == 2:
            pods.append({"name": parts[0], "ip": parts[1]})
    return pods


# ---------------------------------------------------------------------------
# Metric collection
# ---------------------------------------------------------------------------


def promtool_query(query: str, monitoring_ns: str, prom_pod: str) -> Optional[str]:
    """Execute an instant PromQL query via promtool inside the Prometheus pod."""
    cmd = (
        f"oc exec -n {monitoring_ns} {prom_pod} -- "
        f"promtool query instant http://localhost:9090 '{query}'"
    )
    return run_cmd(cmd, timeout=30)


def parse_promtool_output(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Parse promtool text output into a list of instance/pod/value dicts."""
    if not raw:
        return []
    results: List[Dict[str, Any]] = []
    for line in raw.strip().splitlines():
        match = re.match(
            r'\{.*?instance="([^"]+)".*?pod="([^"]*)".*?\}\s*=>\s*([0-9.eE+-]+)',
            line,
        )
        if not match:
            match2 = re.match(
                r'\{.*?instance="([^"]+)".*?\}\s*=>\s*([0-9.eE+-]+)', line
            )
            if match2:
                results.append(
                    {
                        "instance": match2.group(1),
                        "pod": "",
                        "value": float(match2.group(2)),
                    }
                )
            continue
        results.append(
            {
                "instance": match.group(1),
                "pod": match.group(2),
                "value": float(match.group(3)),
            }
        )
    return results


def parse_db_size_output(raw: Optional[str]) -> List[Dict[str, Any]]:
    """Parse the etcd_mvcc_db_total_size_in_bytes metric output."""
    if not raw:
        return []
    results: List[Dict[str, Any]] = []
    for line in raw.strip().splitlines():
        match = re.match(
            r"etcd_mvcc_db_total_size_in_bytes\{"
            r'.*?instance="([^"]+)".*?pod="([^"]*)".*?\}'
            r"\s*=>\s*([0-9.eE+-]+)",
            line,
        )
        if match:
            results.append(
                {
                    "instance": match.group(1),
                    "pod": match.group(2),
                    "value": float(match.group(3)),
                }
            )
    return results


def collect_prometheus_metrics(
    args: argparse.Namespace,
) -> Dict[str, List[Dict[str, Any]]]:
    """Collect all Prometheus-based etcd metrics."""
    interval = args.rate_interval
    monitoring_ns = args.monitoring_ns
    prom_pod = args.prom_pod

    metrics: Dict[str, List[Dict[str, Any]]] = {}

    print("  Collecting WAL fsync duration ...", end=" ", flush=True)
    raw = promtool_query(
        "histogram_quantile(0.99, "
        f"rate(etcd_disk_wal_fsync_duration_seconds_bucket[{interval}]))",
        monitoring_ns,
        prom_pod,
    )
    metrics["wal_fsync"] = parse_promtool_output(raw)
    print(f"({len(metrics['wal_fsync'])} nodes)")

    print("  Collecting compaction pause duration ...", end=" ", flush=True)
    raw = promtool_query(
        "histogram_quantile(0.99, sum by(le, instance) ("
        "rate(etcd_debugging_mvcc_db_compaction_pause_duration"
        f"_milliseconds_bucket[{interval}])))",
        monitoring_ns,
        prom_pod,
    )
    metrics["compaction_pause"] = parse_promtool_output(raw)
    print(f"({len(metrics['compaction_pause'])} nodes)")

    print("  Collecting backend commit duration ...", end=" ", flush=True)
    raw = promtool_query(
        "histogram_quantile(0.99, "
        f"rate(etcd_disk_backend_commit_duration_seconds_bucket[{interval}]))",
        monitoring_ns,
        prom_pod,
    )
    metrics["backend_commit"] = parse_promtool_output(raw)
    print(f"({len(metrics['backend_commit'])} nodes)")

    print("  Collecting DB size ...", end=" ", flush=True)
    raw = promtool_query("etcd_mvcc_db_total_size_in_bytes", monitoring_ns, prom_pod)
    metrics["db_size"] = parse_db_size_output(raw)
    print(f"({len(metrics['db_size'])} nodes)")

    return metrics


def collect_compaction_logs(
    pods: List[Dict[str, str]], namespace: str, log_lines: int
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch and parse etcd compaction log entries from each pod."""
    print(
        f"  Collecting compaction logs ({log_lines} lines per pod) ...",
        flush=True,
    )
    logs: Dict[str, List[Dict[str, Any]]] = {}
    for pod in pods:
        pod_name = pod["name"]
        print(f"    {pod_name} ...", end=" ", flush=True)
        raw = run_cmd(
            f"oc logs -n {namespace} {pod_name} -c etcd "
            f"--tail={log_lines} 2>/dev/null | grep compaction",
            timeout=30,
        )
        entries = _parse_compaction_entries(raw)
        logs[pod_name] = entries
        print(f"({len(entries)} entries)")
    return logs


def _parse_compaction_entries(
    raw: Optional[str],
) -> List[Dict[str, Any]]:
    """Parse compaction duration entries from raw log text."""
    if not raw:
        return []
    entries: List[Dict[str, Any]] = []
    for line in raw.strip().splitlines():
        match = re.search(r'"took":"([0-9.]+)(ms|s|µs)"', line)
        db_match = re.search(r'"current-db-size-bytes":(\d+)', line)
        inuse_match = re.search(r'"current-db-size-in-use-bytes":(\d+)', line)
        if match:
            val = float(match.group(1))
            unit = match.group(2)
            if unit == "s":
                val *= 1000
            elif unit == "µs":
                val /= 1000
            entry: Dict[str, Any] = {"took_ms": val}
            if db_match:
                entry["db_bytes"] = int(db_match.group(1))
            if inuse_match:
                entry["inuse_bytes"] = int(inuse_match.group(1))
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Node label helper
# ---------------------------------------------------------------------------


def short_node(pod_or_instance: str) -> str:
    """Shorten a pod or instance identifier for display."""
    if not pod_or_instance:
        return pod_or_instance
    return pod_or_instance.replace("etcd-", "").split(":")[0]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyze_metrics(
    metrics: Dict[str, List[Dict[str, Any]]],
    compaction_logs: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Build display rows and ratings from collected metrics."""
    all_ratings: List[str] = []

    wal_rows = _build_simple_rows(
        metrics.get("wal_fsync", []),
        "wal_fsync_ms",
        1000,
        "<10ms",
        all_ratings,
    )
    cp_rows = _build_simple_rows(
        metrics.get("compaction_pause", []),
        "compaction_pause_ms",
        1,
        "<900ms",
        all_ratings,
    )
    bc_rows = _build_simple_rows(
        metrics.get("backend_commit", []),
        "backend_commit_ms",
        1000,
        "<100ms",
        all_ratings,
    )
    db_rows = _build_db_rows(metrics.get("db_size", []), compaction_logs, all_ratings)
    comp_rows = _build_compaction_rows(compaction_logs, all_ratings)

    return {
        "wal_rows": wal_rows,
        "cp_rows": cp_rows,
        "bc_rows": bc_rows,
        "db_rows": db_rows,
        "comp_rows": comp_rows,
        "all_ratings": all_ratings,
    }


def _build_simple_rows(
    data: List[Dict[str, Any]],
    metric_key: str,
    multiplier: float,
    target: str,
    all_ratings: List[str],
) -> List[list]:
    """Build rated table rows for a simple numeric metric."""
    rows: List[list] = []
    for record in data:
        val_ms = record["value"] * multiplier
        rating = rate(val_ms, metric_key)
        all_ratings.append(rating)
        node = short_node(record.get("pod") or record["instance"])
        fmt = f"{val_ms:.1f}ms" if multiplier == 1 else f"{val_ms:.2f}ms"
        rows.append([node, fmt, target, RATING_SYMBOL[rating]])
    return rows


def _build_db_rows(
    data: List[Dict[str, Any]],
    compaction_logs: Dict[str, List[Dict[str, Any]]],
    all_ratings: List[str],
) -> List[list]:
    """Build table rows for DB size and fragmentation."""
    rows: List[list] = []
    for record in data:
        total_mb = record["value"] / (1024 * 1024)
        node = short_node(record.get("pod") or record["instance"])
        pod_name = record.get("pod", "")
        inuse_mb = None
        frag_pct = None
        if pod_name in compaction_logs and compaction_logs[pod_name]:
            last = compaction_logs[pod_name][-1]
            if "inuse_bytes" in last:
                inuse_mb = last["inuse_bytes"] / (1024 * 1024)
                frag_pct = (1 - last["inuse_bytes"] / record["value"]) * 100
        if inuse_mb is not None:
            frag_rating = rate(frag_pct, "fragmentation_pct")
            all_ratings.append(frag_rating)
            rows.append(
                [
                    node,
                    f"{total_mb:.0f} MB",
                    f"~{inuse_mb:.0f} MB",
                    f"~{frag_pct:.0f}%",
                    RATING_SYMBOL[frag_rating],
                ]
            )
        else:
            rows.append([node, f"{total_mb:.0f} MB", "N/A", "N/A", "N/A"])
    return rows


def _build_compaction_rows(
    compaction_logs: Dict[str, List[Dict[str, Any]]],
    all_ratings: List[str],
) -> List[list]:
    """Build table rows from compaction log statistics."""
    rows: List[list] = []
    for pod_name, entries in compaction_logs.items():
        if not entries:
            continue
        times = [e["took_ms"] for e in entries]
        avg_t = statistics.mean(times)
        min_t = min(times)
        max_t = max(times)
        rating = rate(avg_t, "compaction_time_ms")
        all_ratings.append(rating)
        node = short_node(pod_name)
        rows.append(
            [
                node,
                f"{avg_t:.0f}ms",
                f"{min_t:.0f}ms",
                f"{max_t:.0f}ms",
                str(len(entries)),
                RATING_SYMBOL[rating],
            ]
        )
    return rows


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------


def print_header(cluster: str, timestamp: str) -> None:
    """Print the report header banner."""
    width = 60
    print()
    print(f"{BOLD}{'═' * width}{RESET}")
    print(f"{BOLD}  etcd Performance Report — {cluster}{RESET}")
    print(f"{GRAY}  {timestamp}{RESET}")
    print(f"{BOLD}{'═' * width}{RESET}")
    print()


def print_table(title: str, rows: List[list], columns: List[str]) -> None:
    """Print a bordered table with ANSI color support."""
    print(f"{BOLD}{title}{RESET}")
    col_widths = _compute_column_widths(rows, columns)

    border_top = "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
    border_mid = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
    border_bot = "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

    print(border_top)
    print(_fmt_row(columns, col_widths))
    print(border_mid)
    for row in rows:
        print(_fmt_row(row, col_widths))
    print(border_bot)
    print()


def _compute_column_widths(rows: List[list], columns: List[str]) -> List[int]:
    """Compute column widths accounting for ANSI escape sequences."""
    col_widths: List[int] = []
    for i, col in enumerate(columns):
        max_w = len(col)
        for row in rows:
            cell = str(row[i]) if i < len(row) else ""
            visible = re.sub(r"\033\[[0-9;]*m", "", cell)
            max_w = max(max_w, len(visible))
        col_widths.append(max_w)
    return col_widths


def _fmt_row(cells: list, col_widths: List[int]) -> str:
    """Format one row of the table with proper padding."""
    parts: List[str] = []
    for i, cell in enumerate(cells):
        visible = re.sub(r"\033\[[0-9;]*m", "", str(cell))
        pad = col_widths[i] - len(visible)
        parts.append(f" {cell}{' ' * pad} ")
    return f"│{'│'.join(parts)}│"


def print_summary(worst_rating: str) -> None:
    """Print the overall health verdict."""
    idx = RATING_ORDER.index(worst_rating) if worst_rating in RATING_ORDER else 4
    if idx <= 2:
        print(f"{BOLD}Overall: \033[32m✅ ALL METRICS HEALTHY\033[0m{RESET}")
    elif idx == 3:
        print(
            f"{BOLD}Overall: \033[33m⚠️  SOME METRICS NEED" f" ATTENTION\033[0m{RESET}"
        )
    else:
        print(f"{BOLD}Overall: \033[31m❌ CRITICAL ISSUES" f" DETECTED\033[0m{RESET}")
    print()


def print_terminal_report(
    cluster: str, timestamp: str, analysis: Dict[str, Any]
) -> None:
    """Render the full analysis to the terminal."""
    print_header(cluster, timestamp)

    if analysis["wal_rows"]:
        print_table(
            "WAL Fsync Duration (99th percentile)",
            analysis["wal_rows"],
            ["Node", "Value", "Target", "Status"],
        )
    if analysis["cp_rows"]:
        print_table(
            "Compaction Pause Duration (99th percentile)",
            analysis["cp_rows"],
            ["Node", "Value", "Target", "Status"],
        )
    if analysis["bc_rows"]:
        print_table(
            "Backend Commit Duration (99th percentile)",
            analysis["bc_rows"],
            ["Node", "Value", "Target", "Status"],
        )
    if analysis["db_rows"]:
        _print_db_table(analysis["db_rows"])
    if analysis["comp_rows"]:
        print_table(
            "Compaction Log Analysis",
            analysis["comp_rows"],
            ["Node", "Avg", "Min", "Max", "Samples", "Status"],
        )

    if analysis["all_ratings"]:
        worst_idx = max(RATING_ORDER.index(r) for r in analysis["all_ratings"])
        print_summary(RATING_ORDER[worst_idx])
    else:
        print(f"{BOLD}No metrics collected.{RESET}\n")


def _print_db_table(db_rows: List[list]) -> None:
    """Print DB size table, choosing columns based on data availability."""
    has_frag = any(r[2] != "N/A" for r in db_rows)
    if has_frag:
        print_table(
            "DB Size & Fragmentation",
            db_rows,
            ["Node", "Total", "In-Use", "Frag%", "Status"],
        )
    else:
        simple = [[r[0], r[1], "✅ Stable"] for r in db_rows]
        print_table("DB Size", simple, ["Node", "Total", "Status"])


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------


def md_table(headers: List[str], rows: List[list]) -> str:
    """Generate a markdown table string."""
    lines: List[str] = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("------" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def generate_markdown(
    cluster: str,
    timestamp: str,
    metrics: Dict[str, List[Dict[str, Any]]],
    compaction_logs: Dict[str, List[Dict[str, Any]]],
    all_ratings: List[str],
) -> str:
    """Build the full markdown report."""
    lines: List[str] = []
    lines.append(f"# etcd Performance Report — {cluster}")
    lines.append("")
    lines.append(f"**Date:** {timestamp}  ")
    lines.append(f"**Cluster:** {cluster}")
    lines.append("")
    lines.append("---")
    lines.append("")

    _md_wal_fsync(lines, metrics)
    _md_compaction_pause(lines, metrics)
    _md_backend_commit(lines, metrics)
    _md_db_fragmentation(lines, metrics, compaction_logs)
    _md_compaction_logs(lines, compaction_logs)
    _md_overall(lines, all_ratings)

    return "\n".join(lines)


def _md_wal_fsync(lines: List[str], metrics: Dict[str, List[Dict[str, Any]]]) -> None:
    """Append the WAL fsync markdown section."""
    lines.append("## WAL Fsync Duration (99th percentile)")
    lines.append("")
    rows: List[list] = []
    for record in metrics.get("wal_fsync", []):
        val_ms = record["value"] * 1000
        rating = rate(val_ms, "wal_fsync_ms")
        node = short_node(record.get("pod") or record["instance"])
        rows.append(
            [
                f"{node} ({record['instance']})",
                f"~{val_ms:.2f}ms",
                "<10ms",
                RATING_MD[rating],
            ]
        )
    lines.append(md_table(["Node", "Value", "Target", "Status"], rows))
    lines.append("")


def _md_compaction_pause(
    lines: List[str], metrics: Dict[str, List[Dict[str, Any]]]
) -> None:
    """Append the compaction pause markdown section."""
    lines.append("## Compaction Pause Duration (99th percentile)")
    lines.append("")
    rows: List[list] = []
    for record in metrics.get("compaction_pause", []):
        val_ms = record["value"]
        rating = rate(val_ms, "compaction_pause_ms")
        node = short_node(record.get("pod") or record["instance"])
        rows.append([node, f"{val_ms:.1f}ms", "<900ms", RATING_MD[rating]])
    lines.append(md_table(["Node", "Value", "Target", "Status"], rows))
    lines.append("")


def _md_backend_commit(
    lines: List[str], metrics: Dict[str, List[Dict[str, Any]]]
) -> None:
    """Append the backend commit markdown section."""
    lines.append("## Backend Commit Duration (99th percentile)")
    lines.append("")
    rows: List[list] = []
    for record in metrics.get("backend_commit", []):
        val_ms = record["value"] * 1000
        rating = rate(val_ms, "backend_commit_ms")
        node = short_node(record.get("pod") or record["instance"])
        rows.append(
            [
                f"{node} ({record['instance']})",
                f"~{val_ms:.2f}ms",
                "<100ms",
                RATING_MD[rating],
            ]
        )
    lines.append(md_table(["Node", "Value", "Target", "Status"], rows))
    lines.append("")


def _md_db_fragmentation(
    lines: List[str],
    metrics: Dict[str, List[Dict[str, Any]]],
    compaction_logs: Dict[str, List[Dict[str, Any]]],
) -> None:
    """Append the DB size and fragmentation markdown section."""
    lines.append("## DB Size & Fragmentation")
    lines.append("")
    rows: List[list] = []
    for record in metrics.get("db_size", []):
        total_mb = record["value"] / (1024 * 1024)
        node = short_node(record.get("pod") or record["instance"])
        inuse_mb = None
        frag_pct = None
        pod_name = record.get("pod", "")
        if pod_name in compaction_logs and compaction_logs[pod_name]:
            last = compaction_logs[pod_name][-1]
            if "inuse_bytes" in last:
                inuse_mb = last["inuse_bytes"] / (1024 * 1024)
                frag_pct = (1 - last["inuse_bytes"] / record["value"]) * 100
        if inuse_mb is not None:
            frag_rating = rate(frag_pct, "fragmentation_pct")
            rows.append(
                [
                    f"{node} ({record['instance']})",
                    f"{total_mb:.0f} MB",
                    f"~{inuse_mb:.0f} MB",
                    f"~{frag_pct:.0f}%",
                    RATING_MD[frag_rating],
                ]
            )
        else:
            rows.append(
                [
                    f"{node} ({record['instance']})",
                    f"{total_mb:.0f} MB",
                    "N/A",
                    "N/A",
                    "N/A",
                ]
            )
    if any(len(r) == 5 and r[2] != "N/A" for r in rows):
        lines.append(
            md_table(
                ["Node", "Total", "In-Use", "Fragmentation", "Status"],
                rows,
            )
        )
    else:
        simple_rows = [[r[0], r[1], "✅ Stable"] for r in rows]
        lines.append(md_table(["Node", "Total", "Status"], simple_rows))
    lines.append("")


def _md_compaction_logs(
    lines: List[str],
    compaction_logs: Dict[str, List[Dict[str, Any]]],
) -> None:
    """Append the compaction log analysis markdown section."""
    has_logs = any(len(v) > 0 for v in compaction_logs.values())
    if not has_logs:
        return
    lines.append("## Compaction Log Analysis")
    lines.append("")
    for pod_name, entries in compaction_logs.items():
        if not entries:
            continue
        times = [e["took_ms"] for e in entries]
        avg_t = statistics.mean(times)
        min_t = min(times)
        max_t = max(times)
        rating = rate(avg_t, "compaction_time_ms")
        node = short_node(pod_name)
        lines.append(f"**{node}** ({len(entries)} samples):")
        lines.append("")
        lines.append(
            md_table(
                ["Metric", "Value"],
                [
                    ["Average", f"{avg_t:.0f}ms"],
                    ["Min", f"{min_t:.0f}ms"],
                    ["Max", f"{max_t:.0f}ms"],
                    ["Samples", str(len(entries))],
                    ["Rating", RATING_MD[rating]],
                ],
            )
        )
        lines.append("")


def _md_overall(lines: List[str], all_ratings: List[str]) -> None:
    """Append the overall assessment to the markdown report."""
    worst_idx = max(RATING_ORDER.index(r) for r in all_ratings) if all_ratings else 0
    lines.append("---")
    lines.append("")
    if worst_idx <= 2:
        lines.append("**Overall Assessment:** ✅ All metrics healthy")
    elif worst_idx == 3:
        lines.append("**Overall Assessment:** ⚠️ Some metrics need attention")
    else:
        lines.append("**Overall Assessment:** ❌ Critical issues detected")
    lines.append("")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Collect and analyze etcd performance metrics "
        "on an OpenShift cluster.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s
  %(prog)s --rate-interval 10m
  %(prog)s -n openshift-etcd --log-lines 5000
  %(prog)s -o /tmp/etcd-report.md
        """,
    )
    parser.add_argument(
        "-n",
        "--namespace",
        default="openshift-etcd",
        help="etcd namespace (default: openshift-etcd)",
    )
    parser.add_argument(
        "--monitoring-ns",
        default="openshift-monitoring",
        help="Monitoring namespace (default: openshift-monitoring)",
    )
    parser.add_argument(
        "--prom-pod",
        default="prometheus-k8s-0",
        help="Prometheus pod name (default: prometheus-k8s-0)",
    )
    parser.add_argument(
        "--log-lines",
        type=int,
        default=2000,
        help="Number of log lines to fetch per pod (default: 2000)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Markdown output file path " "(default: etcd-report-<cluster>-<date>.md)",
    )
    parser.add_argument(
        "--rate-interval",
        default="5m",
        help="Rate interval for Prometheus queries (default: 5m)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point: collect metrics, analyze, and generate report."""
    args = parse_arguments()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"\n{BOLD}etcd Performance Check{RESET}")
    print(f"{GRAY}{'─' * 40}{RESET}")

    print("\n[1/4] Detecting cluster ...")
    cluster = detect_cluster()
    print(f"  Cluster: {CYAN}{cluster}{RESET}")

    print("\n[2/4] Discovering etcd pods ...")
    pods = discover_etcd_pods(args.namespace)
    if not pods:
        print(
            "\033[31mNo etcd pods found. " + "Check namespace and connectivity.\033[0m"
        )
        sys.exit(1)
    for pod in pods:
        print(f"  {pod['name']} ({pod['ip']})")

    print("\n[3/4] Collecting Prometheus metrics ...")
    metrics = collect_prometheus_metrics(args)

    print("\n[4/4] Collecting compaction logs ...")
    compaction_logs = collect_compaction_logs(pods, args.namespace, args.log_lines)

    analysis = analyze_metrics(metrics, compaction_logs)
    print_terminal_report(cluster, timestamp, analysis)

    md_content = generate_markdown(
        cluster, timestamp, metrics, compaction_logs, analysis["all_ratings"]
    )
    if args.output:
        outfile = args.output
    else:
        safe_cluster = re.sub(r"[^a-zA-Z0-9._-]", "_", cluster)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        outfile = f"etcd-report-{safe_cluster}-{date_str}.md"

    with open(outfile, "w", encoding="utf-8") as report_file:
        report_file.write(md_content)
    print(f"Report saved to: {CYAN}{outfile}{RESET}\n")


if __name__ == "__main__":
    main()
