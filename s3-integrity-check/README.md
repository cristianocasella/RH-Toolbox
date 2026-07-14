# S3 Integrity Check

Identifies corrupted objects in S3-compatible buckets where object metadata exists but the underlying data is missing.

## Overview

In object storage systems backed by multiple layers (e.g. NooBaa over Ceph RGW), a failure in the backing store can leave objects in an inconsistent state: the metadata layer still reports the object exists (HEAD succeeds), but the actual data is gone (GET fails). These phantom objects cause consumers — such as Thanos Store Gateway or Velero — to fail or crash-loop.

This tool scans objects in a bucket, performs a HEAD + GET probe on each, and reports which objects are corrupted. It also generates ready-to-run cleanup commands.

**This tool is read-only** — it never modifies or deletes any data. When corrupted objects are found, it prints cleanup commands for you to review and run manually.

The tool supports two modes:
- **Generic mode** — connect to any S3-compatible endpoint by providing credentials directly
- **Preset mode** — auto-discover credentials from a known platform (NooBaa, Velero)

## Prerequisites

- **Python 3.6+**
- **`aws` CLI** — used for S3 API calls
- **`oc` CLI** — required only when using presets (must be logged in to the target cluster)

## Quick Start

```bash
# NooBaa preset — scans Thanos TSDB blocks in the observability bucket
./s3_integrity_check.py --preset noobaa

# Velero preset — scans all objects in a Velero backup bucket
./s3_integrity_check.py --preset velero

# Velero with a specific BackupStorageLocation
./s3_integrity_check.py --preset velero --bsl-name aap

# Generic S3 endpoint
./s3_integrity_check.py \
    --endpoint https://s3.example.com \
    --access-key AKIAIOSFODNN7EXAMPLE \
    --secret-key wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \
    --bucket my-bucket
```

## Usage

```
usage: s3_integrity_check.py [-h] [--preset {noobaa,velero}]
                               [--endpoint ENDPOINT] [--access-key KEY]
                               [--secret-key KEY] [--bucket BUCKET]
                               [--prefix PREFIX]
                               [--namespace NS]
                               [--observability-namespace NS]
                               [--bsl-name NAME]
                               [--no-verify-ssl] [--dry-run] [--debug]
```

### Preset Mode

Use `--preset` to auto-discover connection parameters from a known platform. This flag is mutually exclusive with the generic connection parameters (`--endpoint`, `--access-key`, `--secret-key`, `--bucket`, `--prefix`).

| Preset | Scan Mode | Description |
|---|---|---|
| `noobaa` | ULID blocks | Reads the `noobaa-admin` secret and `s3` route from OpenShift, discovers the bucket from `obc-observability`. Scans Thanos TSDB blocks by probing `meta.json` per block. |
| `velero` | Backup groups | Reads credentials, endpoint, bucket, and prefix from a Velero BackupStorageLocation (BSL). Lists backup directories and probes `velero-backup.json` per backup — if corrupted, the entire backup is marked as broken. |

### Generic Connection Parameters

When not using a preset, all four connection parameters are required:

| Parameter | Description |
|---|---|
| `--endpoint` | S3 endpoint URL (e.g. `https://s3.example.com`) |
| `--access-key` | AWS/S3 access key ID |
| `--secret-key` | AWS/S3 secret access key |
| `--bucket` | Target bucket name |
| `--prefix` | Optional S3 key prefix to scope the scan (e.g. `backups/`) |

### Shared Preset Options

| Parameter | Description |
|---|---|
| `--namespace` | OpenShift namespace for preset resources (default: `openshift-storage` for noobaa, `open-cluster-management-backup` for velero) |

### NooBaa Preset Options

These options are only valid with `--preset noobaa`:

| Parameter | Default | Description |
|---|---|---|
| `--observability-namespace` | `open-cluster-management-observability` | Namespace for ACM observability (used in cleanup commands) |

### Velero Preset Options

These options are only valid with `--preset velero`:

| Parameter | Default | Description |
|---|---|---|
| `--bsl-name` | *(auto-detected)* | Name of the BackupStorageLocation to use. If not specified, the BSL marked as `default` in the namespace is used. |

### General Options

| Parameter | Description |
|---|---|
| `--no-verify-ssl` | Skip TLS certificate verification (always on for presets) |
| `--dry-run` | Report results only, suppress the cleanup commands block at the end (the script never executes cleanup — it only prints commands for manual use) |
| `--debug` | Enable debug logging |

## Scan Modes

The tool uses different scanning strategies depending on the context:

### ULID Block Scan (NooBaa preset)

Lists top-level prefixes in the bucket and filters for valid ULID-format directories (Thanos TSDB blocks). For each block, probes `<ULID>/meta.json` as a representative check. If the block is corrupted, the cleanup command removes the entire block directory recursively.

### Backup Group Scan (Velero preset)

Lists backup directories under `<prefix>/backups/` and probes `velero-backup.json` in each one as a representative check. A Velero backup is an atomic unit — if its metadata file is corrupted, the entire backup is unusable. The cleanup command removes the entire backup directory recursively. This is much faster than scanning every individual object (e.g. ~50 backups probed vs ~10,000 objects listed).

### All Objects Scan (generic mode)

Lists every object in the bucket (optionally filtered by prefix), handling S3 pagination automatically. Each object is individually probed via HEAD + GET. Corrupted objects are cleaned up individually.

## Usage Examples

```bash
# NooBaa with custom storage namespace
./s3_integrity_check.py --preset noobaa --namespace my-storage-ns

# NooBaa dry-run (report only)
./s3_integrity_check.py --preset noobaa --dry-run

# Velero with default BSL
./s3_integrity_check.py --preset velero

# Velero with specific BSL and namespace
./s3_integrity_check.py --preset velero --bsl-name rhbk \
    --namespace open-cluster-management-backup

# Generic MinIO endpoint with self-signed cert
./s3_integrity_check.py \
    --endpoint https://minio.local:9000 \
    --access-key admin --secret-key secret \
    --bucket data --no-verify-ssl

# Generic endpoint, scan only under a prefix
./s3_integrity_check.py \
    --endpoint https://s3.example.com \
    --access-key AKIA... --secret-key ... \
    --bucket my-bucket --prefix backups/2026/
```

## How It Works

1. **Connection Setup** — Either auto-discovers credentials via a preset or uses the explicitly provided parameters.
2. **Object Enumeration** — Depending on the scan mode:
   - **ULID blocks** (noobaa): Lists top-level prefixes and filters for 26-character ULID directories
   - **Backup groups** (velero): Lists backup directories under the BSL prefix and probes one representative file per backup
   - **All objects** (generic): Lists every object key in the bucket (with optional prefix filter), handling pagination
3. **Health Check** — For each object, performs:
   - `HEAD` — checks if metadata reports the object exists
   - `GET` of the first 4 bytes — checks if the backing store can serve actual data
4. **Reporting** — Summarizes healthy, corrupted, and errored objects.
5. **Cleanup Commands** — Prints ready-to-run `aws s3 rm` commands (unless `--dry-run` is set). Preset-specific commands (e.g. Thanos pod restart for NooBaa) are appended automatically.

### Object Status Classification

| Status | HEAD | GET | Meaning |
|---|---|---|---|
| `ok` | 200 | 200 | Object is healthy |
| `corrupted` | 200 | Fail/Timeout | Metadata exists but data is missing |
| `missing` | 404 | — | Object does not exist or already cleaned up |
| `error` | Other | — | Unexpected error, investigate manually |

## Example Output

### NooBaa Preset

```
2026-07-14 10:30:01 - INFO - Fetching NooBaa credentials from namespace openshift-storage
2026-07-14 10:30:02 - INFO - Endpoint: https://s3-openshift-storage.apps.cluster.example.com
2026-07-14 10:30:02 - INFO - Bucket:   nb.1234567890.observability
2026-07-14 10:30:05 - INFO - Listing TSDB blocks in bucket: nb.1234567890.observability
2026-07-14 10:30:08 - INFO - Found 142 objects. Testing each...
[!] CORRUPTED: 01HWXYZ1234567890ABCDEFGH (HEAD ok but GET failed (data missing))
[!] CORRUPTED: 01HWXYZ1234567890ABCDEFGJ (HEAD ok but GET failed (data missing))
2026-07-14 10:32:15 - INFO - Results: 142 total, 2 corrupted, 0 errors
2026-07-14 10:32:15 - WARNING - Corrupted objects (2):
2026-07-14 10:32:15 - WARNING -   - 01HWXYZ1234567890ABCDEFGH
2026-07-14 10:32:15 - WARNING -   - 01HWXYZ1234567890ABCDEFGJ

============================================================
CLEANUP COMMANDS
============================================================

export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
BUCKET="nb.1234567890.observability"
ENDPOINT="https://s3-openshift-storage.apps.cluster.example.com"

aws s3 rm --recursive "s3://${BUCKET}/01HWXYZ1234567890ABCDEFGH/" --endpoint-url "${ENDPOINT}" --no-verify-ssl
aws s3 rm --recursive "s3://${BUCKET}/01HWXYZ1234567890ABCDEFGJ/" --endpoint-url "${ENDPOINT}" --no-verify-ssl

# After cleanup, restart Thanos Store shards:
oc delete pod -n open-cluster-management-observability \
  -l app.kubernetes.io/component=object-store-gateway
```

### Velero Preset

```
2026-07-14 11:00:01 - INFO - No --bsl-name specified, looking for default BSL in open-cluster-management-backup
2026-07-14 11:00:02 - INFO - Fetching Velero BSL 'default' from namespace open-cluster-management-backup
2026-07-14 11:00:03 - INFO - Endpoint: https://s3-openshift-storage.apps.cluster.example.com
2026-07-14 11:00:03 - INFO - Bucket:   acm-backup
2026-07-14 11:00:03 - INFO - Prefix:   velero/backups/
2026-07-14 11:00:05 - INFO - Listing groups under velero/backups/ in bucket: acm-backup
2026-07-14 11:00:06 - INFO - Found 47 objects. Testing each...
[!] CORRUPTED: velero/backups/acm-schedule-20260524080018 (HEAD ok but GET failed (data missing))
2026-07-14 11:01:10 - INFO - Results: 47 total, 1 corrupted, 0 errors
2026-07-14 11:01:10 - WARNING - Corrupted objects (1):
2026-07-14 11:01:10 - WARNING -   - velero/backups/acm-schedule-20260524080018

============================================================
CLEANUP COMMANDS
============================================================

export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
BUCKET="acm-backup"
ENDPOINT="https://s3-openshift-storage.apps.cluster.example.com"

aws s3 rm --recursive "s3://${BUCKET}/velero/backups/acm-schedule-20260524080018/" --endpoint-url "${ENDPOINT}" --no-verify-ssl
```

### Generic Endpoint

```
2026-07-14 12:00:01 - INFO - Endpoint: https://minio.local:9000
2026-07-14 12:00:01 - INFO - Bucket:   thanos-data
2026-07-14 12:00:03 - INFO - Listing all objects in bucket: thanos-data
2026-07-14 12:00:05 - INFO - Found 58 objects. Testing each...
2026-07-14 12:01:10 - INFO - Results: 58 total, 0 corrupted, 0 errors
2026-07-14 12:01:10 - INFO - All objects are healthy!
```

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | All objects are healthy (or no objects found) |
| `1` | Script error (credentials, network, interrupted) |
| `2` | Corrupted objects detected |

## Troubleshooting

### Cannot retrieve credentials (NooBaa preset)

**Problem**: Script fails with "Failed to retrieve NooBaa credentials"

**Solution**:
- Verify you are logged in to the correct cluster: `oc whoami --show-server`
- Check namespace access: `oc get secret noobaa-admin -n openshift-storage`
- Ensure the `s3` route exists: `oc get route s3 -n openshift-storage`

### No default BSL found (Velero preset)

**Problem**: Script fails with "No default BackupStorageLocation found"

**Solution**:
- List available BSLs: `oc get backupstoragelocation -n open-cluster-management-backup`
- Specify one explicitly: `--bsl-name <name>`
- Check that at least one BSL has `spec.default: true`

### Failed to retrieve BSL (Velero preset)

**Problem**: Script fails with "Failed to retrieve BSL"

**Solution**:
- Verify the BSL name: `oc get backupstoragelocation -n <namespace>`
- Check namespace access: `oc auth can-i get backupstoragelocation -n <namespace>`
- Ensure the credential secret referenced by the BSL exists

### Preset-specific options rejected

**Problem**: An option like `--bsl-name` or `--observability-namespace` is rejected

**Solution**: These options are tied to specific presets. `--bsl-name` requires `--preset velero`, `--observability-namespace` requires `--preset noobaa`. `--namespace` requires any preset.

### Timeout listing objects

**Problem**: Listing objects takes too long or times out

**Solution**:
- Check network connectivity to the S3 endpoint
- The endpoint may be overloaded — retry later
- For large buckets, the all-objects scan may take several minutes due to pagination
- Run with `--debug` to see the exact AWS CLI commands being executed

### All objects show as "error"

**Problem**: Every object returns an error status instead of ok/corrupted

**Solution**:
- Verify the `aws` CLI version: `aws --version`
- Check TLS connectivity — try adding `--no-verify-ssl` for self-signed certificates
- Run with `--debug` to inspect error details per object

### Cleanup commands don't work

**Problem**: The generated `aws s3 rm` commands fail

**Solution**:
- Make sure you export the environment variables printed above the commands
- Check that the S3 endpoint is still reachable
- Verify the bucket name hasn't changed (re-run the check)

## Design Notes

- **Read-only by design** — the script never modifies or deletes bucket data. Cleanup commands are printed to stdout for the operator to review, adapt, and execute manually. This is intentional to prevent accidental data loss.
- Both the NooBaa and Velero presets use representative-file probing (one file per block/backup) for efficiency. Other objects within the same group could theoretically also be corrupted, but if the representative file is affected, the entire group is unusable.
- The all-objects scan (generic mode) may take several minutes for very large buckets due to pagination and per-object probing.

## Requirements

- `aws` CLI must be installed and on the `PATH`
- Presets additionally require `oc` CLI logged in to the target cluster
- The S3 endpoint must be reachable from the machine running the script
