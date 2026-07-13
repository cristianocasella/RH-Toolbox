#!/usr/bin/env python3
"""
oc-mirror Sync Automation Script

Automates the three-stage oc-mirror workflow for disconnected environments:
1. Mirror from upstream to local file storage
2. Mirror from file to primary registry
3. Mirror from file to secondary registry
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML module not found. Install with: pip3 install PyYAML")
    sys.exit(1)


class OCMirrorConfig:
    """Configuration parser and validator for oc-mirror sync."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()
        self._validate_config()

    def _load_config(self) -> Dict:
        """Load YAML configuration file."""
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            print(f"ERROR: Config file not found: {self.config_path}")
            sys.exit(1)
        except yaml.YAMLError as e:
            print(f"ERROR: Invalid YAML in config file: {e}")
            sys.exit(1)

    def _validate_config(self):
        """Validate required configuration fields."""
        required_fields = ['imageset_config', 'working_folder', 'registries']
        for field in required_fields:
            if field not in self.config:
                print(f"ERROR: Missing required field in config: {field}")
                sys.exit(1)

        # Validate registries is a list with at least one entry
        if not isinstance(self.config['registries'], list):
            print("ERROR: 'registries' must be a list")
            sys.exit(1)

        if len(self.config['registries']) == 0:
            print("ERROR: At least one registry must be configured")
            sys.exit(1)

        # Validate each registry has required fields
        for idx, registry in enumerate(self.config['registries']):
            if 'url' not in registry:
                print(f"ERROR: Registry at index {idx} missing 'url' field")
                sys.exit(1)
            if 'name' not in registry:
                print(f"WARNING: Registry at index {idx} missing 'name' field, using 'Registry-{idx}'")
                registry['name'] = f'Registry-{idx}'

        if not os.path.exists(self.config['imageset_config']):
            print(f"ERROR: ImageSet config file not found: {self.config['imageset_config']}")
            sys.exit(1)

    @property
    def imageset_config(self) -> str:
        """Get imageset configuration file path."""
        return self.config['imageset_config']

    @property
    def working_folder(self) -> str:
        """Get working folder path."""
        return self.config['working_folder']

    @property
    def registries(self) -> List[Dict]:
        """Get list of registries to mirror to."""
        return self.config['registries']

    @property
    def v2_mode(self) -> bool:
        """Check if v2 mode should be used."""
        return self.config.get('options', {}).get('v2_mode', True)

    @property
    def parallel_images(self) -> int:
        """Get parallel images setting."""
        return self.config.get('options', {}).get('parallel_images', 10)

    @property
    def parallel_layers(self) -> int:
        """Get parallel layers setting."""
        return self.config.get('options', {}).get('parallel_layers', 10)

    @property
    def retry_times(self) -> int:
        """Get retry times setting."""
        return self.config.get('options', {}).get('retry_times', 5)

    @property
    def remove_signatures(self) -> bool:
        """Get remove signatures setting (v2 only - workaround for manifest issues)."""
        return self.config.get('options', {}).get('remove_signatures', False)

    @property
    def ignore_release_signature(self) -> bool:
        """Get ignore release signature setting (v2 only)."""
        return self.config.get('options', {}).get('ignore_release_signature', False)

    @property
    def skopeo_fallback(self) -> bool:
        """Get skopeo fallback setting (workaround for oc-mirror manifest format issues)."""
        return self.config.get('options', {}).get('skopeo_fallback', False)

    @property
    def log_level(self) -> str:
        """Get logging level."""
        return self.config.get('logging', {}).get('level', 'INFO')

    @property
    def log_file(self) -> Optional[str]:
        """Get log file path."""
        return self.config.get('logging', {}).get('log_file')


class OCMirrorExecutor:
    """Executor for oc-mirror commands."""

    def __init__(self, config: OCMirrorConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.logger = logging.getLogger(__name__)

    def _backup_working_dir(self, stage_name: str):
        """
        Backup working-dir before mirroring to registries.

        oc-mirror v2 modifies working-dir/publish during registry mirroring, which can
        cause subsequent registry mirrors to fail with "no tar archives found" error.
        """
        import shutil
        import time

        working_dir = os.path.join(self.config.working_folder, 'working-dir')
        backup_dir = os.path.join(self.config.working_folder, f'working-dir-backup-{stage_name}')

        if not os.path.exists(working_dir):
            self.logger.debug(f"No working-dir to backup for {stage_name}")
            return

        try:
            if os.path.exists(backup_dir):
                shutil.rmtree(backup_dir)
            shutil.copytree(working_dir, backup_dir)
            self.logger.info(f"Backed up working-dir to {backup_dir}")
        except Exception as e:
            self.logger.warning(f"Failed to backup working-dir: {e}")

    def _restore_working_dir(self, stage_name: str):
        """
        Restore working-dir from backup before registry mirroring.

        This ensures each registry mirror uses clean metadata not modified by previous registry mirrors.
        """
        import shutil

        working_dir = os.path.join(self.config.working_folder, 'working-dir')
        backup_dir = os.path.join(self.config.working_folder, f'working-dir-backup-{stage_name}')

        if not os.path.exists(backup_dir):
            self.logger.debug(f"No backup to restore for {stage_name}")
            return

        try:
            if os.path.exists(working_dir):
                shutil.rmtree(working_dir)
            shutil.copytree(backup_dir, working_dir)
            self.logger.info(f"Restored working-dir from {backup_dir}")
        except Exception as e:
            self.logger.warning(f"Failed to restore working-dir: {e}")

    def _build_base_command(self) -> List[str]:
        """Build base oc-mirror command with common flags."""
        cmd = ['oc-mirror']

        # Version selection (v1 or v2)
        if self.config.v2_mode:
            cmd.append('--v2')

            # V2-specific flags
            cmd.extend(['--parallel-images', str(self.config.parallel_images)])
            cmd.extend(['--parallel-layers', str(self.config.parallel_layers)])
            cmd.extend(['--retry-times', str(self.config.retry_times)])

            # V2 workaround flags for manifest issues
            if self.config.remove_signatures:
                cmd.append('--remove-signatures')

            if self.config.ignore_release_signature:
                cmd.append('--ignore-release-signature')
        else:
            # V1 mode (legacy)
            cmd.append('--v1')

            # V1-specific flags (uses different parameter names)
            cmd.extend(['--max-per-registry', str(self.config.parallel_images)])

        return cmd

    def _execute_command(self, cmd: List[str], stage_name: str) -> Tuple[bool, str, str]:
        """
        Execute oc-mirror command with real-time output.

        Returns:
            Tuple of (success: bool, stdout: str, stderr: str)
        """
        cmd_str = ' '.join(cmd)
        self.logger.info(f"[{stage_name}] Executing: {cmd_str}")

        if self.dry_run:
            self.logger.info(f"[{stage_name}] DRY RUN - Command not executed")
            return True, "", ""

        try:
            # Run with direct output to preserve oc-mirror's progress display
            # Use line buffering and flush to ensure progress updates are visible
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout for unified progress
                universal_newlines=True,
                bufsize=0  # Unbuffered for real-time output
            )

            output_lines = []

            # Read output character by character to handle progress bars and carriage returns
            while True:
                char = process.stdout.read(1)
                if not char:
                    if process.poll() is not None:
                        break
                    continue

                # Print immediately for real-time display
                print(char, end='', flush=True)
                output_lines.append(char)

            output = ''.join(output_lines)

            if process.returncode == 0:
                print()  # New line after progress completes
                self.logger.info(f"[{stage_name}] Command completed successfully")
                return True, output, ""
            else:
                print()  # New line after progress completes
                self.logger.error(f"[{stage_name}] Command failed with exit code {process.returncode}")
                return False, output, ""

        except FileNotFoundError:
            self.logger.error(f"[{stage_name}] oc-mirror command not found. Ensure oc-mirror is installed and in PATH.")
            return False, "", "oc-mirror command not found"
        except Exception as e:
            self.logger.error(f"[{stage_name}] Unexpected error: {e}")
            return False, "", str(e)

    def _parse_failed_images(self, working_folder: str) -> List[str]:
        """
        Parse oc-mirror error logs to extract failed images.

        Returns:
            List of source image URIs that failed
        """
        import glob
        import re

        failed_images = []

        # oc-mirror v2 stores logs in working-dir/logs/
        error_log_pattern = os.path.join(working_folder, 'working-dir', 'logs', 'mirroring_errors_*.txt')
        error_logs = glob.glob(error_log_pattern)

        if not error_logs:
            self.logger.warning(f"No mirroring error logs found at: {error_log_pattern}")
            return failed_images

        # Use the most recent error log
        latest_log = max(error_logs, key=os.path.getmtime)
        self.logger.info(f"Parsing error log: {latest_log}")

        try:
            with open(latest_log, 'r') as f:
                for line in f:
                    # Match lines like:
                    # [ERROR]  : [Worker] error mirroring image docker://quay.io/validatedpatterns/patterns-operator@sha256:...
                    # Also match: [ERROR]  : [Worker] error mirroring image quay.io/validatedpatterns/utility-container:latest error:
                    match = re.search(r'error mirroring image (?:docker://)?([^\s]+)', line)
                    if match:
                        source_image = match.group(1)
                        # Remove 'error:' suffix if present
                        source_image = source_image.replace('error:', '').strip()
                        # Skip if already in list (deduplicate)
                        if source_image and source_image not in failed_images:
                            failed_images.append(source_image)
                            self.logger.debug(f"Found failed image: {source_image}")
        except Exception as e:
            self.logger.error(f"Failed to parse error log: {e}")

        self.logger.info(f"Parsed {len(failed_images)} failed images from error log")
        return failed_images

    def _retry_with_skopeo(self, failed_images: List[str], destination_registry: str) -> Tuple[int, int]:
        """
        Retry failed images using skopeo with two-level fallback approach.

        Level 1: Direct copy without digest preservation
        Level 2: OCI format conversion for manifest compatibility

        Args:
            failed_images: List of source image URIs that failed with oc-mirror
            destination_registry: Destination registry URL (e.g., quay.example.com/ocp)

        Returns:
            Tuple of (successful_count, failed_count)
        """
        if not failed_images:
            return 0, 0

        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"Retrying {len(failed_images)} failed images with skopeo fallback")
        self.logger.info(f"{'='*80}\n")

        successful = 0
        failed = 0

        for source_image in failed_images:
            # Convert source to destination format
            # Example: quay.io/validatedpatterns/image:tag -> quay.example.com/ocp/validatedpatterns/image:tag
            dest_image = source_image.replace('quay.io', destination_registry)
            dest_image = dest_image.replace('docker.io', destination_registry)
            dest_image = dest_image.replace('registry.redhat.io', destination_registry)
            dest_image = dest_image.replace('registry.access.redhat.com', destination_registry)

            self.logger.info(f"Skopeo retry: {source_image}")

            # Attempt 1: Direct copy without --preserve-digests (Red Hat KB solution)
            cmd = [
                'skopeo', 'copy',
                f'docker://{source_image}',
                f'docker://{dest_image}'
            ]

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0:
                    self.logger.info(f"  ✓ Success (direct copy)")
                    successful += 1
                    continue
                elif 'manifest invalid' in result.stderr:
                    # Attempt 2: OCI format conversion for manifest incompatibility
                    self.logger.info(f"  → Manifest invalid, trying OCI conversion...")

                    # Extract image name for temp path (remove registry and digest/tag)
                    import hashlib
                    temp_name = hashlib.md5(source_image.encode()).hexdigest()[:12]
                    oci_path = f'/tmp/oc-mirror-oci-{temp_name}'

                    try:
                        # Step 1: Pull to OCI format (converts manifest)
                        cmd_oci_pull = [
                            'skopeo', 'copy',
                            f'docker://{source_image}',
                            f'oci:{oci_path}'
                        ]
                        result_pull = subprocess.run(cmd_oci_pull, capture_output=True, text=True, timeout=600)

                        if result_pull.returncode != 0:
                            self.logger.error(f"  ✗ OCI pull failed: {result_pull.stderr}")
                            failed += 1
                            continue

                        # Step 2: Push from OCI to destination
                        # Convert @sha256: to :sha256- (tag format) since digest changes during OCI conversion
                        dest_image_tag = dest_image.replace('@sha256:', ':sha256-')

                        cmd_oci_push = [
                            'skopeo', 'copy',
                            f'oci:{oci_path}',
                            f'docker://{dest_image_tag}'
                        ]
                        result_push = subprocess.run(cmd_oci_push, capture_output=True, text=True, timeout=600)

                        if result_push.returncode == 0:
                            self.logger.info(f"  ✓ Success (OCI conversion)")
                            successful += 1
                        else:
                            self.logger.error(f"  ✗ OCI push failed: {result_push.stderr}")
                            failed += 1

                    finally:
                        # Cleanup OCI temp directory
                        import shutil
                        if os.path.exists(oci_path):
                            shutil.rmtree(oci_path, ignore_errors=True)
                else:
                    self.logger.error(f"  ✗ Failed: {result.stderr}")
                    failed += 1
            except subprocess.TimeoutExpired:
                self.logger.error(f"  ✗ Timeout after 10 minutes")
                failed += 1
            except Exception as e:
                self.logger.error(f"  ✗ Error: {e}")
                failed += 1

        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"Skopeo fallback results: {successful} successful, {failed} failed")
        self.logger.info(f"{'='*80}\n")

        return successful, failed

    def stage1_mirror_to_file(self) -> bool:
        """Stage 1: Mirror from upstream to local file storage."""
        cmd = self._build_base_command()
        cmd.extend([
            '--config', self.config.imageset_config,
            f'file://{self.config.working_folder}'
        ])

        success, _, _ = self._execute_command(cmd, "STAGE 1")
        return success

    def mirror_to_registry(self, registry: Dict, registry_index: int, total_registries: int) -> bool:
        """
        Mirror from file storage to a specific registry.

        Args:
            registry: Registry configuration dict with 'url' and 'name'
            registry_index: Index of this registry (0-based)
            total_registries: Total number of registries to mirror to

        Returns:
            True if successful, False otherwise
        """
        stage_num = registry_index + 1  # Stage 1, 2, 3, etc. (registry stages)
        stage_name = f'stage{stage_num}'

        # Restore working-dir from backup (if not the first registry)
        if registry_index > 0:
            self._restore_working_dir('stage1')
        else:
            # Backup working-dir before first registry mirror
            self._backup_working_dir('stage1')

        cmd = self._build_base_command()
        cmd.extend([
            '--config', self.config.imageset_config,
            f'--from=file://{self.config.working_folder}',
            f'docker://{registry["url"]}'
        ])

        registry_name = registry.get('name', f'Registry-{registry_index + 1}')
        success, _, _ = self._execute_command(cmd, f"STAGE {stage_num} ({registry_name})")

        # Apply skopeo fallback if enabled and oc-mirror had failures
        if not success and self.config.skopeo_fallback:
            self.logger.info("Attempting skopeo fallback for failed images")
            failed_images = self._parse_failed_images(self.config.working_folder)
            if failed_images:
                successful, failed = self._retry_with_skopeo(failed_images, registry["url"])
                # Consider stage successful if oc-mirror partial + skopeo fallback covered all images
                if failed == 0:
                    self.logger.info("All failed images recovered via skopeo fallback")
                    return True

        return success


class WorkflowOrchestrator:
    """Orchestrates the multi-stage oc-mirror workflow."""

    def __init__(self, config: OCMirrorConfig, dry_run: bool = False, stages: Optional[List[int]] = None):
        self.config = config
        self.executor = OCMirrorExecutor(config, dry_run)
        self.logger = logging.getLogger(__name__)
        # If no stages specified, run stage 0 (file) + all registry stages (1, 2, 3, ...)
        self.stages = stages or list(range(0, len(config.registries) + 1))
        self.results = {}

    def run(self) -> int:
        """
        Execute the mirror workflow.

        Returns:
            Exit code (0 = success, 1 = config error, 2 = stage 0 failed, 3 = registry stage failed)
        """
        workflow_start_time = time.time()

        self.logger.info("=" * 80)
        self.logger.info("oc-mirror Sync Workflow Starting")
        self.logger.info("=" * 80)
        self.logger.info(f"ImageSet Config: {self.config.imageset_config}")
        self.logger.info(f"Working Folder: {self.config.working_folder}")

        # Log all configured registries
        for idx, registry in enumerate(self.config.registries):
            registry_name = registry.get('name', f'Registry-{idx + 1}')
            self.logger.info(f"Registry {idx + 1}: {registry_name} ({registry['url']})")

        self.logger.info(f"V2 Mode: {self.config.v2_mode}")
        self.logger.info(f"Stages to run: {self.stages}")
        self.logger.info("=" * 80)

        registry_failed = False
        stage_times = {}

        # Stage 0: Mirror to file
        if 0 in self.stages:
            self.logger.info("\n### STAGE 0: Mirror to Local File Storage ###\n")
            stage_start = time.time()
            if not self.executor.stage1_mirror_to_file():
                self.logger.error("STAGE 0 FAILED - Cannot proceed to registry mirrors")
                self.results['stage0'] = False
                stage_times['stage0'] = time.time() - stage_start
                self._print_summary(stage_times, workflow_start_time)
                return 2
            stage_times['stage0'] = time.time() - stage_start
            self.results['stage0'] = True

        # Stages 1+: Mirror to each registry
        total_registries = len(self.config.registries)
        for idx, registry in enumerate(self.config.registries):
            stage_num = idx + 1
            if stage_num in self.stages:
                registry_name = registry.get('name', f'Registry-{idx + 1}')
                self.logger.info(f"\n### STAGE {stage_num}: Mirror to {registry_name} ({idx + 1}/{total_registries}) ###\n")

                stage_start = time.time()
                stage_key = f'stage{stage_num}'

                if not self.executor.mirror_to_registry(registry, idx, total_registries):
                    self.logger.warning(f"STAGE {stage_num} FAILED - Continuing to next registry")
                    registry_failed = True
                    self.results[stage_key] = False
                else:
                    self.results[stage_key] = True

                stage_times[stage_key] = time.time() - stage_start

        workflow_total_time = time.time() - workflow_start_time
        self._print_summary(stage_times, workflow_total_time)

        if registry_failed:
            return 3
        return 0

    def _print_summary(self, stage_times: Dict[str, float], total_time: float):
        """Print workflow execution summary with timing information."""
        self.logger.info("\n" + "=" * 80)
        self.logger.info("Workflow Summary")
        self.logger.info("=" * 80)

        for stage, success in self.results.items():
            status = "SUCCESS" if success else "FAILED"
            symbol = "✓" if success else "✗"
            elapsed = stage_times.get(stage, 0)
            elapsed_str = self._format_time(elapsed)
            self.logger.info(f"{symbol} {stage.upper()}: {status} ({elapsed_str})")

        self.logger.info("=" * 80)
        self.logger.info(f"Total execution time: {self._format_time(total_time)}")
        self.logger.info("=" * 80)

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format seconds into human-readable time string."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"


def setup_logging(level: str, log_file: Optional[str] = None):
    """Configure logging."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    handlers = [logging.StreamHandler(sys.stdout)]

    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        handlers=handlers
    )


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Automate oc-mirror sync workflow for disconnected registries',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Run full workflow with config file
  %(prog)s --config oc_mirror_config.yaml

  # Dry-run to see commands without executing
  %(prog)s --config oc_mirror_config.yaml --dry-run

  # Run only specific stages (0=file, 1=1st registry, 2=2nd registry, etc.)
  %(prog)s --config oc_mirror_config.yaml --stage 0
  %(prog)s --config oc_mirror_config.yaml --stage 1 2
  %(prog)s --config oc_mirror_config.yaml --stage 0 1 2 3
        '''
    )

    parser.add_argument(
        '--config',
        required=True,
        help='Path to YAML configuration file'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show commands without executing them'
    )

    parser.add_argument(
        '--stage',
        type=int,
        nargs='+',
        help='Run specific stages only (0=file mirror, 1=1st registry, 2=2nd registry, etc.)'
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_arguments()

    config = OCMirrorConfig(args.config)

    setup_logging(config.log_level, config.log_file)

    orchestrator = WorkflowOrchestrator(config, dry_run=args.dry_run, stages=args.stage)

    exit_code = orchestrator.run()

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
