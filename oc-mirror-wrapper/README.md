# oc-mirror Sync Automation

Python wrapper script that automates the multi-stage oc-mirror workflow for disconnected OpenShift environments with multiple registry synchronization.

## Overview

This script orchestrates the complete oc-mirror mirroring process:

1. **Stage 0**: Mirror from upstream registries to local file storage
2. **Stage 1+**: Mirror from file storage to each configured registry (supports unlimited registries)

The script supports both oc-mirror v1 and v2 modes, with advanced features including:
- Support for unlimited number of target registries
- Automatic working directory backup/restore between registry mirrors
- Skopeo fallback for failed images
- Parallel processing configuration
- Granular stage execution control
- Comprehensive error handling and logging

## Prerequisites

- Python 3.6+
- `oc-mirror` CLI tool installed and in PATH
- `skopeo` CLI tool (optional, for fallback mode)
- PyYAML: `pip3 install PyYAML`
- Authentication configured for all registries (via pull-secret or podman/docker login)

## Quick Start

1. **Copy the example configuration**:
   ```bash
   cd oc-mirror-wrapper
   cp oc_mirror_config.yaml.example oc_mirror_config.yaml
   ```

2. **Edit the configuration** with your paths and registry URLs:
   ```bash
   vim oc_mirror_config.yaml
   ```

3. **Run the full workflow**:
   ```bash
   python3 oc_mirror_sync.py --config oc_mirror_config.yaml
   ```

## Configuration

### Basic Configuration

Edit `oc_mirror_config.yaml` with your environment details:

```yaml
# Path to your oc-mirror ImageSetConfiguration file
imageset_config: "/path/to/your/imageset-config-v2.yaml"

# Local working folder for file-based mirror
working_folder: "/path/to/your/mirror/working-folder"

# Registry endpoints (array - add as many as needed)
registries:
  - url: "quay.example.com/ocp"
    name: "Primary"
  - url: "quay-dr.example.com/ocp"
    name: "DR-Site"
```

**Multiple Registries Example:**
```yaml
registries:
  - url: "quay.dc1.example.com/ocp"
    name: "Datacenter-1"
  - url: "quay.dc2.example.com/ocp"
    name: "Datacenter-2"
  - url: "quay.dr.example.com/ocp"
    name: "DR-Site"
  - url: "quay.edge.example.com/ocp"
    name: "Edge-Location"
```

### Advanced Options

```yaml
options:
  v2_mode: true              # Use oc-mirror v2 (recommended)
  parallel_images: 10        # Concurrent image downloads (adjust for network)
  parallel_layers: 10        # Concurrent layer downloads
  retry_times: 5             # Number of retries for failed transfers
  remove_signatures: false   # Remove signatures (v2 workaround for manifest issues)
  ignore_release_signature: false  # Ignore release signatures (v2)
  skopeo_fallback: false     # Enable skopeo retry for failed images

logging:
  level: "INFO"              # DEBUG, INFO, WARNING, ERROR
  log_file: null             # Optional: path to log file
```

## Usage Examples

### Run Full Workflow
```bash
python3 oc_mirror_sync.py --config oc_mirror_config.yaml
```

### Dry Run (Preview Commands)
```bash
python3 oc_mirror_sync.py --config oc_mirror_config.yaml --dry-run
```

### Run Specific Stages

Stage 0 only (mirror to file):
```bash
python3 oc_mirror_sync.py --config oc_mirror_config.yaml --stage 0
```

Stages 1 and 2 only (mirror to first two registries, assumes Stage 0 completed previously):
```bash
python3 oc_mirror_sync.py --config oc_mirror_config.yaml --stage 1 2
```

All stages for 4 registries:
```bash
python3 oc_mirror_sync.py --config oc_mirror_config.yaml --stage 0 1 2 3 4
```

**Note**: Stage numbering:
- Stage 0 = Mirror to file
- Stage 1 = First registry in list
- Stage 2 = Second registry in list
- Stage N = Nth registry in list

## Workflow Details

### Stage 0: Mirror to File
Downloads container images from upstream registries (Red Hat, Quay.io, etc.) to local file storage. This creates a portable mirror archive.

### Stage 1+: Mirror to Registries
Pushes images from local file storage to each configured registry in sequence:
- The working directory is backed up before the first registry mirror (Stage 1)
- Before each subsequent registry mirror, the working directory is restored from backup
- This ensures each registry receives clean metadata and prevents "no tar archives found" errors

## Advanced Features

### Skopeo Fallback
When `skopeo_fallback: true` is enabled, the script automatically:
1. Parses oc-mirror error logs to identify failed images
2. Retries failed images using skopeo with two-level fallback:
   - **Level 1**: Direct copy without digest preservation
   - **Level 2**: OCI format conversion for manifest compatibility

This is useful for handling manifest format issues that oc-mirror cannot resolve.

### Working Directory Management
The script automatically backs up and restores the working directory between registry mirrors to prevent oc-mirror v2 metadata corruption that can cause "no tar archives found" errors. Each registry receives a clean copy of the working directory metadata.

## Exit Codes

- `0` - Success
- `1` - Configuration error
- `2` - Stage 0 failed (cannot proceed to registries)
- `3` - One or more registry mirrors failed (partial failure)

## Troubleshooting

### No tar archives found error
This is automatically handled by the working directory backup/restore feature. If you still encounter this, ensure the working directory exists and contains the mirror data from Stage 0.

### Manifest invalid errors
Enable skopeo fallback mode:
```yaml
options:
  skopeo_fallback: true
```

For oc-mirror v2 manifest issues, try:
```yaml
options:
  remove_signatures: true
  ignore_release_signature: true
```

### Slow network
Reduce parallelism and increase retries:
```yaml
options:
  parallel_images: 5
  parallel_layers: 5
  retry_times: 10
```

### Fast network
Increase parallelism:
```yaml
options:
  parallel_images: 20
  parallel_layers: 20
```

## Example Output

```
================================================================================
oc-mirror Sync Workflow Starting
================================================================================
ImageSet Config: /path/to/imageset-config-v2.yaml
Working Folder: /path/to/working-folder
Registry 1: Primary (quay.example.com/ocp)
Registry 2: DR-Site (quay-dr.example.com/ocp)
V2 Mode: True
Stages to run: [0, 1, 2]
================================================================================

### STAGE 0: Mirror to Local File Storage ###
[Progress output...]

### STAGE 1: Mirror to Primary (1/2) ###
[Progress output...]

### STAGE 2: Mirror to DR-Site (2/2) ###
[Progress output...]

================================================================================
Workflow Summary
================================================================================
✓ STAGE0: SUCCESS (45m 23s)
✓ STAGE1: SUCCESS (32m 15s)
✓ STAGE2: SUCCESS (31m 48s)
================================================================================
Total execution time: 1h 49m
================================================================================
```
