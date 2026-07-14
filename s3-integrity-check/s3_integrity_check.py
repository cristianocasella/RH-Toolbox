#!/usr/bin/env python3
"""
S3 Integrity Check

Identifies corrupted objects in S3-compatible buckets where object metadata
exists (HEAD succeeds) but the underlying data is missing (GET fails).

Supports direct S3 connection parameters for any endpoint, as well as
preset shortcuts that auto-discover credentials from known platforms.

Usage:
    ./s3_integrity_check.py --preset noobaa
    ./s3_integrity_check.py --preset velero
    ./s3_integrity_check.py --endpoint URL --access-key KEY --secret-key KEY --bucket NAME
"""

import argparse
import configparser
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SCAN_MODE_ULID = "ulid_blocks"
SCAN_MODE_PREFIX_GROUPS = "prefix_groups"
SCAN_MODE_ALL = "all_objects"

PRESET_DEFAULTS = {
    "noobaa": {"namespace": "openshift-storage"},
    "velero": {"namespace": "open-cluster-management-backup"},
}


class ConnectionInfo:
    """Resolved S3 connection parameters."""

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        endpoint: str,
        bucket: str,
        no_verify_ssl: bool = False,
        cleanup_suffix: Optional[str] = None,
        prefix: Optional[str] = None,
        scan_mode: str = SCAN_MODE_ALL,
        probe_file: Optional[str] = None,
    ):
        self.access_key = access_key
        self.secret_key = secret_key
        self.endpoint = endpoint
        self.bucket = bucket
        self.no_verify_ssl = no_verify_ssl
        self.cleanup_suffix = cleanup_suffix
        self.prefix = prefix
        self.scan_mode = scan_mode
        self.probe_file = probe_file


def setup_logging(level: str) -> None:
    """Configure logging with the specified level."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    log_format = "%(asctime)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def run_oc_cmd(cmd: List[str]) -> Optional[str]:
    """Run an oc CLI command and return its stdout, or None on failure."""
    logger.debug("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Command failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.debug("Command returned %d: %s", result.returncode, result.stderr)
        return None
    return result.stdout.strip().strip("'")


def _base64_decode(encoded: str) -> Optional[str]:
    """Decode a base64-encoded string using the base64 CLI."""
    try:
        result = subprocess.run(
            ["base64", "-d"],
            input=encoded,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("base64 decode failed: %s", exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _parse_ini_credentials(content: str) -> Tuple[str, str]:
    """Parse INI-format credential file and return (access_key, secret_key)."""
    parser = configparser.ConfigParser()
    parser.read_file(io.StringIO(content))
    section = parser.sections()[0] if parser.sections() else "default"
    access_key = parser.get(section, "aws_access_key_id")
    secret_key = parser.get(section, "aws_secret_access_key")
    return access_key, secret_key


def run_aws_cmd(
    cmd: List[str], env: Dict[str, str], timeout: int = 30
) -> subprocess.CompletedProcess:
    """Run an AWS CLI command with the given environment and timeout."""
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=timeout, check=False
    )


# ---------------------------------------------------------------------------
# Preset resolvers
# ---------------------------------------------------------------------------


def resolve_noobaa_preset(args: argparse.Namespace) -> ConnectionInfo:
    """Auto-discover S3 credentials, endpoint, and bucket from OpenShift/NooBaa."""
    namespace = args.namespace or PRESET_DEFAULTS["noobaa"]["namespace"]
    logger.info("Fetching NooBaa credentials from namespace %s", namespace)

    access_key_b64 = run_oc_cmd(
        [
            "oc",
            "get",
            "secret",
            "noobaa-admin",
            "-n",
            namespace,
            "-o",
            "jsonpath={.data.AWS_ACCESS_KEY_ID}",
        ]
    )
    secret_key_b64 = run_oc_cmd(
        [
            "oc",
            "get",
            "secret",
            "noobaa-admin",
            "-n",
            namespace,
            "-o",
            "jsonpath={.data.AWS_SECRET_ACCESS_KEY}",
        ]
    )

    access_key = _base64_decode(access_key_b64) if access_key_b64 else None
    secret_key = _base64_decode(secret_key_b64) if secret_key_b64 else None

    s3_host = run_oc_cmd(
        [
            "oc",
            "get",
            "route",
            "s3",
            "-n",
            namespace,
            "-o",
            "jsonpath={.spec.host}",
        ]
    )

    if not all([access_key, secret_key, s3_host]):
        logger.error(
            "Failed to retrieve NooBaa credentials. "
            "Is oc logged in with access to namespace %s?",
            namespace,
        )
        sys.exit(1)

    endpoint = f"https://{s3_host}"

    bucket = run_oc_cmd(
        [
            "oc",
            "get",
            "cm",
            "obc-observability",
            "-n",
            namespace,
            "-o",
            "jsonpath={.data.BUCKET_NAME}",
        ]
    )
    if not bucket:
        bucket = run_oc_cmd(
            [
                "oc",
                "get",
                "obc",
                "obc-observability",
                "-n",
                namespace,
                "-o",
                "jsonpath={.spec.bucketName}",
            ]
        )
    if not bucket:
        logger.error("Could not determine bucket name from namespace %s", namespace)
        sys.exit(1)

    obs_ns = args.observability_namespace
    cleanup_suffix = (
        "\n# After cleanup, restart Thanos Store shards:\n"
        f"oc delete pod -n {obs_ns} \\\n"
        "  -l app.kubernetes.io/component=object-store-gateway"
    )

    return ConnectionInfo(
        access_key=access_key,
        secret_key=secret_key,
        endpoint=endpoint,
        bucket=bucket,
        no_verify_ssl=True,
        cleanup_suffix=cleanup_suffix,
        scan_mode=SCAN_MODE_ULID,
    )


def _find_default_bsl(namespace: str) -> Optional[str]:
    """Find the BackupStorageLocation marked as default in the namespace."""
    raw = run_oc_cmd(
        [
            "oc",
            "get",
            "backupstoragelocation",
            "-n",
            namespace,
            "-o",
            "json",
        ]
    )
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    for item in data.get("items", []):
        if item.get("spec", {}).get("default") is True:
            return item["metadata"]["name"]

    return None


def resolve_velero_preset(args: argparse.Namespace) -> ConnectionInfo:
    """Auto-discover S3 credentials, endpoint, and bucket from a Velero BSL."""
    namespace = args.namespace or PRESET_DEFAULTS["velero"]["namespace"]
    bsl_name = args.bsl_name

    if not bsl_name:
        logger.info(
            "No --bsl-name specified, looking for default BSL in %s", namespace
        )
        bsl_name = _find_default_bsl(namespace)
        if not bsl_name:
            logger.error(
                "No default BackupStorageLocation found in namespace %s. "
                "Specify one with --bsl-name",
                namespace,
            )
            sys.exit(1)

    logger.info(
        "Fetching Velero BSL '%s' from namespace %s", bsl_name, namespace
    )

    raw = run_oc_cmd(
        [
            "oc",
            "get",
            "backupstoragelocation",
            bsl_name,
            "-n",
            namespace,
            "-o",
            "json",
        ]
    )
    if not raw:
        logger.error(
            "Failed to retrieve BSL '%s' from namespace %s. "
            "Is oc logged in with access to this resource?",
            bsl_name,
            namespace,
        )
        sys.exit(1)

    try:
        bsl = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.error("Failed to parse BSL JSON for '%s'", bsl_name)
        sys.exit(1)

    spec = bsl.get("spec", {})
    config = spec.get("config", {})
    obj_storage = spec.get("objectStorage", {})
    cred_ref = spec.get("credential", {})

    endpoint = config.get("s3Url")
    bucket = obj_storage.get("bucket")
    prefix = obj_storage.get("prefix")
    cred_secret_name = cred_ref.get("name")
    cred_secret_key = cred_ref.get("key", "cloud")

    if not all([endpoint, bucket, cred_secret_name]):
        logger.error(
            "BSL '%s' is missing required fields (s3Url, bucket, or credential)",
            bsl_name,
        )
        sys.exit(1)

    cred_b64 = run_oc_cmd(
        [
            "oc",
            "get",
            "secret",
            cred_secret_name,
            "-n",
            namespace,
            "-o",
            f"jsonpath={{.data.{cred_secret_key}}}",
        ]
    )
    if not cred_b64:
        logger.error(
            "Failed to read credential secret '%s' in namespace %s",
            cred_secret_name,
            namespace,
        )
        sys.exit(1)

    cred_content = _base64_decode(cred_b64)
    if not cred_content:
        logger.error("Failed to decode credential secret '%s'", cred_secret_name)
        sys.exit(1)

    try:
        access_key, secret_key = _parse_ini_credentials(cred_content)
    except (configparser.Error, IndexError, KeyError) as exc:
        logger.error(
            "Failed to parse credentials from secret '%s': %s",
            cred_secret_name,
            exc,
        )
        sys.exit(1)

    backup_prefix = f"{prefix}/backups/" if prefix else "backups/"

    return ConnectionInfo(
        access_key=access_key,
        secret_key=secret_key,
        endpoint=endpoint,
        bucket=bucket,
        no_verify_ssl=True,
        prefix=backup_prefix,
        scan_mode=SCAN_MODE_PREFIX_GROUPS,
        probe_file="velero-backup.json",
    )


PRESETS = {
    "noobaa": resolve_noobaa_preset,
    "velero": resolve_velero_preset,
}

PRESET_ONLY_PARAMS = {
    "noobaa": {
        "observability_namespace": "open-cluster-management-observability",
    },
    "velero": {
        "bsl_name": None,
    },
}


# ---------------------------------------------------------------------------
# Object scanning
# ---------------------------------------------------------------------------


def list_block_ulids(
    bucket: str, endpoint: str, env: Dict[str, str], no_verify_ssl: bool
) -> List[str]:
    """List all TSDB block ULIDs in the bucket by querying top-level prefixes."""
    logger.info("Listing TSDB blocks in bucket: %s", bucket)
    cmd = [
        "aws",
        "s3api",
        "list-objects-v2",
        "--bucket",
        bucket,
        "--delimiter",
        "/",
        "--query",
        "CommonPrefixes[].Prefix",
        "--output",
        "json",
        "--endpoint-url",
        endpoint,
    ]
    if no_verify_ssl:
        cmd.append("--no-verify-ssl")

    try:
        result = run_aws_cmd(cmd, env, timeout=120)
    except subprocess.TimeoutExpired:
        logger.error("Timed out listing bucket objects")
        return []

    if result.returncode != 0:
        logger.error("Error listing objects: %s", result.stderr)
        return []

    try:
        prefixes = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        logger.error("Could not parse bucket listing response")
        return []

    if not prefixes:
        return []

    ulid_re = re.compile(r"^[0-9A-Z]{26}/$")
    blocks = sorted([p.rstrip("/") for p in prefixes if ulid_re.match(p)])
    return blocks


def list_sub_prefixes(
    bucket: str,
    endpoint: str,
    env: Dict[str, str],
    no_verify_ssl: bool,
    prefix: str,
) -> List[str]:
    """List sub-prefixes (directories) under a given prefix."""
    logger.info("Listing groups under %s in bucket: %s", prefix, bucket)
    cmd = [
        "aws",
        "s3api",
        "list-objects-v2",
        "--bucket",
        bucket,
        "--prefix",
        prefix,
        "--delimiter",
        "/",
        "--query",
        "CommonPrefixes[].Prefix",
        "--output",
        "json",
        "--endpoint-url",
        endpoint,
    ]
    if no_verify_ssl:
        cmd.append("--no-verify-ssl")

    try:
        result = run_aws_cmd(cmd, env, timeout=120)
    except subprocess.TimeoutExpired:
        logger.error("Timed out listing prefixes")
        return []

    if result.returncode != 0:
        logger.error("Error listing prefixes: %s", result.stderr)
        return []

    try:
        prefixes = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        logger.error("Could not parse prefix listing response")
        return []

    if not prefixes:
        return []

    return sorted(prefixes)


def list_all_objects(
    bucket: str,
    endpoint: str,
    env: Dict[str, str],
    no_verify_ssl: bool,
    prefix: Optional[str] = None,
) -> List[str]:
    """List all object keys in the bucket, optionally filtered by prefix."""
    label = f"bucket: {bucket}"
    if prefix:
        label += f" (prefix: {prefix})"
    logger.info("Listing all objects in %s", label)

    all_keys: List[str] = []
    continuation_token: Optional[str] = None

    while True:
        cmd = [
            "aws",
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--output",
            "json",
            "--endpoint-url",
            endpoint,
        ]
        if prefix:
            cmd.extend(["--prefix", prefix])
        if no_verify_ssl:
            cmd.append("--no-verify-ssl")
        if continuation_token:
            cmd.extend(["--continuation-token", continuation_token])

        try:
            result = run_aws_cmd(cmd, env, timeout=120)
        except subprocess.TimeoutExpired:
            logger.error("Timed out listing bucket objects")
            return all_keys

        if result.returncode != 0:
            logger.error("Error listing objects: %s", result.stderr)
            return all_keys

        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            logger.error("Could not parse bucket listing response")
            return all_keys

        for obj in data.get("Contents", []):
            all_keys.append(obj["Key"])

        if data.get("IsTruncated"):
            continuation_token = data.get("NextContinuationToken")
        else:
            break

    return sorted(all_keys)


def check_object(
    key: str,
    bucket: str,
    endpoint: str,
    env: Dict[str, str],
    no_verify_ssl: bool,
) -> Tuple[str, Optional[str]]:
    """Check whether an object is readable or corrupted via HEAD + GET probe.

    Returns:
        A tuple of (status, detail) where status is one of:
        - "ok": object is readable
        - "corrupted": HEAD succeeds but GET fails (data missing)
        - "missing": object does not exist
        - "error": unexpected error
    """
    head_cmd = [
        "aws",
        "s3api",
        "head-object",
        "--bucket",
        bucket,
        "--key",
        key,
        "--endpoint-url",
        endpoint,
    ]
    if no_verify_ssl:
        head_cmd.append("--no-verify-ssl")

    try:
        head = run_aws_cmd(head_cmd, env, timeout=15)
    except subprocess.TimeoutExpired:
        return "error", "HEAD request timed out"

    if head.returncode != 0:
        combined = head.stdout + head.stderr
        if "404" in combined or "Not Found" in combined:
            return "missing", "object not found"
        return "error", combined.strip()[:200]

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=True) as tmp:
        get_cmd = [
            "aws",
            "s3api",
            "get-object",
            "--bucket",
            bucket,
            "--key",
            key,
            "--range",
            "bytes=0-3",
            tmp.name,
            "--endpoint-url",
            endpoint,
        ]
        if no_verify_ssl:
            get_cmd.append("--no-verify-ssl")

        try:
            get = run_aws_cmd(get_cmd, env, timeout=15)
        except subprocess.TimeoutExpired:
            return "corrupted", "GET timed out (backing data unreachable)"

    if get.returncode != 0:
        return "corrupted", "HEAD ok but GET failed (data missing)"

    return "ok", None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_cleanup_commands(
    corrupted: List[str],
    conn: ConnectionInfo,
) -> None:
    """Print AWS CLI cleanup commands for corrupted objects."""
    print()
    print("=" * 60)
    print("CLEANUP COMMANDS")
    print("=" * 60)
    print()
    print(f'export AWS_ACCESS_KEY_ID="{conn.access_key}"')
    print(f'export AWS_SECRET_ACCESS_KEY="{conn.secret_key}"')
    print(f'BUCKET="{conn.bucket}"')
    print(f'ENDPOINT="{conn.endpoint}"')
    print()

    ssl_flag = " --no-verify-ssl" if conn.no_verify_ssl else ""
    if conn.scan_mode in (SCAN_MODE_ULID, SCAN_MODE_PREFIX_GROUPS):
        for group in corrupted:
            print(
                f'aws s3 rm --recursive "s3://${{BUCKET}}/{group}/" '
                f'--endpoint-url "${{ENDPOINT}}"{ssl_flag}'
            )
    else:
        for key in corrupted:
            print(
                f'aws s3 rm "s3://${{BUCKET}}/{key}" '
                f'--endpoint-url "${{ENDPOINT}}"{ssl_flag}'
            )

    if conn.cleanup_suffix:
        print(conn.cleanup_suffix)


# ---------------------------------------------------------------------------
# Argument parsing and validation
# ---------------------------------------------------------------------------


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Identify corrupted objects in S3-compatible buckets where "
            "object metadata exists but underlying data is missing"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Presets auto-discover credentials from known platforms:
  noobaa    Reads NooBaa admin secret and S3 route from OpenShift
  velero    Reads credentials and endpoint from a Velero BackupStorageLocation

Examples:
  # NooBaa preset (auto-discovers credentials from OpenShift)
  %(prog)s --preset noobaa

  # Velero preset (uses default BSL)
  %(prog)s --preset velero

  # Velero with specific BSL and namespace
  %(prog)s --preset velero --bsl-name aap --namespace open-cluster-management-backup

  # Generic S3 endpoint
  %(prog)s --endpoint https://s3.example.com \\
      --access-key AKIAIOSFODNN7EXAMPLE \\
      --secret-key wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \\
      --bucket my-bucket

  # Generic with prefix filter and TLS skip
  %(prog)s --endpoint https://minio.local:9000 \\
      --access-key admin --secret-key secret \\
      --bucket data --prefix backups/ --no-verify-ssl
        """,
    )

    preset_group = parser.add_argument_group("preset mode")
    preset_group.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        help="Use a preset to auto-discover connection parameters",
    )

    conn_group = parser.add_argument_group("connection parameters (generic mode)")
    conn_group.add_argument(
        "--endpoint",
        help="S3 endpoint URL (e.g. https://s3.example.com)",
    )
    conn_group.add_argument(
        "--access-key",
        help="AWS/S3 access key ID",
    )
    conn_group.add_argument(
        "--secret-key",
        help="AWS/S3 secret access key",
    )
    conn_group.add_argument(
        "--bucket",
        help="Target bucket name",
    )
    conn_group.add_argument(
        "--prefix",
        help="S3 key prefix to scope the scan (e.g. velero/)",
    )

    noobaa_group = parser.add_argument_group("noobaa preset options")
    noobaa_group.add_argument(
        "--observability-namespace",
        default="open-cluster-management-observability",
        help=(
            "OpenShift namespace for ACM observability "
            "(default: open-cluster-management-observability)"
        ),
    )

    velero_group = parser.add_argument_group("velero preset options")
    velero_group.add_argument(
        "--bsl-name",
        help=(
            "Name of the BackupStorageLocation to use "
            "(default: the BSL marked as default)"
        ),
    )

    shared_group = parser.add_argument_group("shared preset options")
    shared_group.add_argument(
        "--namespace",
        help=(
            "OpenShift namespace for preset resources "
            "(default: openshift-storage for noobaa, "
            "open-cluster-management-backup for velero)"
        ),
    )

    general_group = parser.add_argument_group("general options")
    general_group.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Skip TLS certificate verification (always on for presets)",
    )
    general_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only, suppress cleanup commands (the script never executes cleanup)",
    )
    general_group.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    return parser.parse_args()


def validate_arguments(args: argparse.Namespace) -> None:
    """Validate mutual exclusivity and required combinations."""
    conn_params = {
        "--endpoint": args.endpoint,
        "--access-key": args.access_key,
        "--secret-key": args.secret_key,
        "--bucket": args.bucket,
    }
    conn_provided = {k for k, v in conn_params.items() if v is not None}

    if args.preset and conn_provided:
        logger.error(
            "--preset cannot be combined with %s",
            ", ".join(sorted(conn_provided)),
        )
        sys.exit(1)

    if args.preset and args.prefix:
        logger.error("--prefix cannot be used with --preset (prefix is auto-discovered)")
        sys.exit(1)

    if not args.preset:
        missing = {k for k, v in conn_params.items() if v is None}
        if missing:
            logger.error(
                "Generic mode requires %s (or use --preset)",
                ", ".join(sorted(missing)),
            )
            sys.exit(1)

    for preset_name, params in PRESET_ONLY_PARAMS.items():
        for param, default_val in params.items():
            actual = getattr(args, param, default_val)
            if actual != default_val and args.preset != preset_name:
                flag = f"--{param.replace('_', '-')}"
                logger.error(
                    "%s can only be used with --preset %s", flag, preset_name
                )
                sys.exit(1)

    if args.namespace is not None and not args.preset:
        logger.error("--namespace can only be used with a preset")
        sys.exit(1)


def resolve_connection(args: argparse.Namespace) -> ConnectionInfo:
    """Build ConnectionInfo from either a preset or explicit arguments."""
    if args.preset:
        return PRESETS[args.preset](args)

    return ConnectionInfo(
        access_key=args.access_key,
        secret_key=args.secret_key,
        endpoint=args.endpoint,
        bucket=args.bucket,
        no_verify_ssl=args.no_verify_ssl,
        prefix=args.prefix,
        scan_mode=SCAN_MODE_ALL,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _enumerate_objects(
    conn: ConnectionInfo, env: Dict[str, str]
) -> List[Tuple[str, str]]:
    """Enumerate objects to check based on scan mode.

    Returns a list of (label, key) tuples where label is the display name
    and key is the full S3 object key to probe.
    """
    if conn.scan_mode == SCAN_MODE_ULID:
        blocks = list_block_ulids(
            conn.bucket, conn.endpoint, env, conn.no_verify_ssl
        )
        return [(ulid, f"{ulid}/meta.json") for ulid in blocks]

    if conn.scan_mode == SCAN_MODE_PREFIX_GROUPS:
        prefixes = list_sub_prefixes(
            conn.bucket, conn.endpoint, env, conn.no_verify_ssl, conn.prefix
        )
        return [
            (p.rstrip("/"), f"{p}{conn.probe_file}")
            for p in prefixes
        ]

    keys = list_all_objects(
        conn.bucket, conn.endpoint, env, conn.no_verify_ssl, conn.prefix
    )
    return [(key, key) for key in keys]


def main() -> None:
    """Main entry point."""
    args = parse_arguments()

    setup_logging("DEBUG" if args.debug else "INFO")
    validate_arguments(args)

    conn = resolve_connection(args)

    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = conn.access_key
    env["AWS_SECRET_ACCESS_KEY"] = conn.secret_key

    logger.info("Endpoint: %s", conn.endpoint)
    logger.info("Bucket:   %s", conn.bucket)
    if conn.prefix:
        logger.info("Prefix:   %s", conn.prefix)

    objects = _enumerate_objects(conn, env)
    if not objects:
        logger.info("No objects found in bucket")
        sys.exit(0)

    logger.info("Found %d objects. Testing each...", len(objects))

    corrupted: List[str] = []
    errors: List[Tuple[str, str]] = []

    for i, (label, key) in enumerate(objects, 1):
        sys.stdout.write(f"\r    Testing {i}/{len(objects)}: {label} ...")
        sys.stdout.flush()

        status, detail = check_object(
            key, conn.bucket, conn.endpoint, env, conn.no_verify_ssl
        )

        if status == "corrupted":
            corrupted.append(label)
            sys.stdout.write(f"\r[!] CORRUPTED: {label} ({detail})\n")
        elif status == "error":
            errors.append((label, detail))
            sys.stdout.write(f"\r[?] ERROR:     {label} ({detail})\n")

    sys.stdout.write(f"\r{'':80}\n")

    logger.info(
        "Results: %d total, %d corrupted, %d errors",
        len(objects),
        len(corrupted),
        len(errors),
    )

    if not corrupted:
        logger.info("All objects are healthy!")
        if errors:
            logger.warning(
                "%d objects had unexpected errors (investigate manually):",
                len(errors),
            )
            for label, detail in errors:
                logger.warning("  %s: %s", label, detail)
        sys.exit(0)

    logger.warning("Corrupted objects (%d):", len(corrupted))
    for label in corrupted:
        logger.warning("  - %s", label)

    if not args.dry_run:
        print_cleanup_commands(corrupted, conn)

    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Unexpected error: %s", exc)
        sys.exit(1)
