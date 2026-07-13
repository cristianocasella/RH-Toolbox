# ImageSet Update Checker

Python utility to check for available updates in OpenShift ImageSetConfiguration files. Queries container registries to find newer versions of images, operator catalogs, and OpenShift platform releases.

## Overview

This tool parses an OpenShift `ImageSetConfiguration` YAML file and checks all referenced container images against their respective registries to identify available updates. It's particularly useful for:

- Identifying outdated container images in your mirror configuration
- Finding newer operator catalog versions
- Discovering available patch releases for OpenShift platform
- Auditing image configurations before mirroring

## Features

- **Multi-Registry Support**: Works with Docker Hub, Quay.io, Red Hat registries, and generic OCI registries
- **Authentication**: Supports Docker config authentication for private registries
- **Semantic Versioning**: Intelligently detects newer versions using semantic version comparison
- **Operator Catalogs**: Identifies Red Hat and Community operator catalog versions
- **OpenShift Versions**: Extracts min/max OpenShift versions for reference
- **Formatted Output**: Clean tabular output showing current vs. available versions

## Prerequisites

- Python 3.6+
- Network access to container registries
- Optional: Docker config file with registry credentials (for private registries)

## Quick Start

### Basic Usage (Public Images Only)

```bash
cd imageset-check-update
python3 check_updates.py /path/to/imageset-config.yaml
```

### With Authentication (Private Registries)

```bash
python3 check_updates.py /path/to/imageset-config.yaml --docker-config ~/.docker/config.json
```

### List Available ImageSet Files

```bash
python3 check_updates.py --list
```

## Authentication Setup

For private registries (Red Hat, private Quay repositories), you need to provide registry credentials.

### Option 1: Docker Config File

Create a `.docker-config.json` file in the same directory:

```json
{
  "auths": {
    "registry.redhat.io": {
      "auth": "BASE64_ENCODED_USERNAME:PASSWORD"
    },
    "quay.io": {
      "auth": "BASE64_ENCODED_USERNAME:PASSWORD"
    }
  }
}
```

To generate the base64 auth string:
```bash
echo -n "username:password" | base64
```

### Option 2: Use Existing Docker Config

If you already have Docker/Podman configured:

```bash
# Copy from Docker
cp ~/.docker/config.json .docker-config.json

# Or from Podman
cp ${XDG_RUNTIME_DIR}/containers/auth.json .docker-config.json
```

### Option 3: Extract from pull-secret

If you have an OpenShift pull-secret:

```bash
cat pull-secret.json | jq . > .docker-config.json
```

## Usage Examples

### Check Single ImageSet File

```bash
python3 check_updates.py imagesets/imageset-config-4.20.yaml
```

### Check with Custom Docker Config Location

```bash
python3 check_updates.py imageset.yaml --docker-config /path/to/auth.json
```

### Discover ImageSet Files

```bash
python3 check_updates.py --list
```

## Output Explanation

### Example Output

```
======================================================================
Checking for updates: imageset-config-v2-4.20.21.yaml
======================================================================

✓ Loaded credentials for 3 registries

OpenShift Platform Versions:
----------------------------------------------------------------------
  minVersion: 4.20.0
    → Check: https://access.redhat.com/downloads/content/290
  maxVersion: 4.20.21
    → Check: https://access.redhat.com/downloads/content/290

Operator Catalogs:
----------------------------------------------------------------------
  [Red Hat] v4.20
  [Community] v4.20

Container Images (15 total):
------------------------------------------------------------------------------------------------------------------------
IMAGE                                                        CURRENT         STATUS
------------------------------------------------------------------------------------------------------------------------
docker.io/grafana/grafana                                    11.4.0          ✓ UPDATE: 11.5.2
quay.io/openshift/origin-coredns                             4.20            ✓ Up to date
registry.redhat.io/rhel9/support-tools                       latest          ⚠ :latest tag - consider pinning
docker.io/library/nginx                                      1.27            ℹ [AUTH REQUIRED]
------------------------------------------------------------------------------------------------------------------------
Summary: 1 updates found out of 14 images checked
========================================================================================================================
```

### Status Indicators

- `✓ UPDATE: x.y.z` - Newer version available
- `✓ Up to date` - Current version is the latest
- `⚠ :latest tag - consider pinning` - Image uses `:latest` tag (not recommended for production)
- `ℹ [AUTH REQUIRED]` - Authentication needed to check this registry
- `ℹ [NOT FOUND]` - Image or repository not found in registry
- `ℹ [HTTP 4xx]` - HTTP error accessing registry
- `ℹ [N tags available]` - Multiple tags found but version comparison not possible

## Version Detection Logic

The tool uses intelligent semantic versioning comparison:

- **Patch-level updates**: For `v4.20.5`, finds `v4.20.6`, `v4.20.7`, etc.
- **Minor version updates**: For `v4.20`, also shows `v4.21` if available
- **Same major.minor**: Prioritizes updates within the same minor version series
- **Skips non-versions**: Ignores SHA tags, timestamps, and `latest`

## Supported Registry APIs

| Registry | API Support | Authentication |
|----------|-------------|----------------|
| Docker Hub | ✓ | Optional (public images work without auth) |
| Quay.io | ✓ | Optional (public repos work without auth) |
| Red Hat (registry.redhat.io) | ✓ | Required (uses SSO bearer token) |
| Red Hat (registry.access.redhat.com) | ✓ | Optional |
| Generic OCI v2 | ✓ | Optional |

## Troubleshooting

### Authentication Errors

**Problem**: `[AUTH REQUIRED]` for Red Hat images

**Solution**: Ensure you have valid Red Hat registry credentials in your Docker config:
```bash
# Login to Red Hat registry
podman login registry.redhat.io

# Copy the auth file
cp ${XDG_RUNTIME_DIR}/containers/auth.json .docker-config.json
```

### Rate Limiting

**Problem**: Getting rate-limited on Docker Hub

**Solution**: Authenticate with Docker Hub to increase rate limits:
```bash
docker login
cp ~/.docker/config.json .docker-config.json
```

### Image Not Found

**Problem**: `[NOT FOUND]` for an image that exists

**Possible Causes**:
- Image repository was moved or renamed
- Registry URL changed
- Typo in ImageSet configuration
- Image is in a different registry than specified

### Network Timeouts

**Problem**: Script hangs or times out

**Solution**:
- Check network connectivity to registries
- Verify firewall/proxy settings allow HTTPS to registries
- Some registries may be slow - the timeout is set to 10 seconds per image

## Limitations

- Only checks images explicitly listed in the `additionalImages` section
- OpenShift platform version checking is informational only (links to Red Hat portal)
- Version comparison works best with semantic versioning (x.y.z format)
- Cannot detect updates for digest-pinned images (`@sha256:...`)
- Rate limits may apply for unauthenticated registry queries

## Best Practices

1. **Pin Versions**: Avoid `:latest` tags in production ImageSet configurations
2. **Regular Checks**: Run update checks before each mirror sync
3. **Test First**: Test updated images in a non-production environment before mirroring
4. **Credential Security**: Keep `.docker-config.json` out of version control (add to `.gitignore`)
5. **Document Changes**: Keep a changelog of ImageSet updates and reasons
6. **Version Consistency**: Ensure operator catalog versions match your OpenShift version

## Example ImageSetConfiguration

```yaml
kind: ImageSetConfiguration
apiVersion: mirror.openshift.io/v1alpha2
storageConfig:
  registry:
    imageURL: localhost:5000/metadata
mirror:
  platform:
    channels:
    - name: stable-4.20
      minVersion: 4.20.0
      maxVersion: 4.20.21
  operators:
  - catalog: registry.redhat.io/redhat/redhat-operator-index:v4.20
  additionalImages:
  - name: docker.io/grafana/grafana:11.4.0
  - name: quay.io/openshift/origin-coredns:4.20
  - name: registry.redhat.io/rhel9/support-tools:latest
```
