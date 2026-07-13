#!/usr/bin/env python3
"""
Check for updates in OpenShift ImageSetConfiguration files.
Queries container registries to find newer versions.

Usage:
    ./check_updates.py <imageset-file.yaml>
"""

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import urllib.request
import urllib.error
from urllib.parse import quote


class RegistryClient:
    """Client for querying container registries."""

    def __init__(self, docker_config_path: Optional[Path] = None):
        """Initialize with Docker config for authentication."""
        self.auths = {}
        if docker_config_path and docker_config_path.exists():
            with open(docker_config_path) as f:
                config = json.load(f)
                self.auths = config.get('auths', {})
                print(f"✓ Loaded credentials for {len(self.auths)} registries")
        else:
            print("⚠ No Docker config found - proceeding without authentication")

    def get_auth_header(self, registry: str) -> Dict[str, str]:
        """Get authentication header for a registry."""
        headers = {}

        # Find matching auth entry
        auth_entry = None
        if registry in self.auths:
            auth_entry = self.auths[registry]
        else:
            # Try partial matches
            for key in self.auths:
                if registry in key or key in registry:
                    auth_entry = self.auths[key]
                    break

        if auth_entry and 'auth' in auth_entry:
            headers['Authorization'] = f'Basic {auth_entry["auth"]}'

        return headers

    def parse_image(self, image_str: str) -> Tuple[str, str, str]:
        """Parse image string into registry, repository, and tag."""
        # Examples:
        # docker.io/grafana/grafana:latest -> docker.io, grafana/grafana, latest
        # quay.io/openshift/origin-coredns:4.20 -> quay.io, openshift/origin-coredns, 4.20
        # registry.redhat.io/rhel9/ubi:latest -> registry.redhat.io, rhel9/ubi, latest

        parts = image_str.split(':', 1)
        image_path = parts[0]
        tag = parts[1] if len(parts) > 1 else 'latest'

        # Split registry from repository
        path_parts = image_path.split('/', 1)
        if '.' in path_parts[0] or path_parts[0] in ['docker', 'registry']:
            registry = path_parts[0]
            repository = path_parts[1] if len(path_parts) > 1 else ''
        else:
            registry = 'docker.io'
            repository = image_path

        return registry, repository, tag

    def get_redhat_bearer_token(self, registry: str, repository: str) -> Optional[str]:
        """Get bearer token for Red Hat registry."""
        # Get basic auth credentials
        auth_entry = self.auths.get(registry) or self.auths.get('registry.redhat.io')
        if not auth_entry or 'auth' not in auth_entry:
            return None

        try:
            # Red Hat uses sso.redhat.com for authentication
            auth_url = f'https://sso.redhat.com/auth/realms/rhcc/protocol/redhat-docker-v2/auth?service=docker-registry&client_id=curl&scope=repository:{repository}:pull'
            headers = {'Authorization': f'Basic {auth_entry["auth"]}'}

            req = urllib.request.Request(auth_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                return data.get('token') or data.get('access_token')
        except Exception as e:
            # Fallback: try direct basic auth
            return None

    def get_tags(self, registry: str, repository: str, current_tag: str) -> List[str]:
        """Get available tags for an image from the registry."""
        try:
            # Construct registry API URL
            if registry == 'docker.io':
                # Docker Hub API
                api_url = f'https://registry.hub.docker.com/v2/repositories/{repository}/tags?page_size=100'
                headers = {}
            elif 'redhat.io' in registry or 'redhat.com' in registry:
                # Red Hat registry API - requires bearer token
                bearer_token = self.get_redhat_bearer_token(registry, repository)
                if bearer_token:
                    headers = {'Authorization': f'Bearer {bearer_token}'}
                else:
                    headers = self.get_auth_header(registry)
                api_url = f'https://{registry}/v2/{repository}/tags/list'
            elif registry == 'quay.io':
                # Quay.io API
                api_url = f'https://quay.io/api/v1/repository/{repository}/tag/?limit=100&onlyActiveTags=true'
                headers = {}
            else:
                # Generic registry v2 API
                api_url = f'https://{registry}/v2/{repository}/tags/list'
                headers = self.get_auth_header(registry)

            req = urllib.request.Request(api_url, headers=headers)

            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())

                # Parse based on registry type
                if registry == 'docker.io':
                    tags = [result['name'] for result in data.get('results', [])]
                elif registry == 'quay.io':
                    tags = [tag['name'] for tag in data.get('tags', [])]
                else:
                    tags = data.get('tags', [])

                return tags

        except urllib.error.HTTPError as e:
            if e.code == 401:
                return ['[AUTH REQUIRED]']
            elif e.code == 404:
                return ['[NOT FOUND]']
            else:
                return [f'[HTTP {e.code}]']
        except Exception as e:
            return [f'[ERROR: {type(e).__name__}]']

    def find_newer_versions(self, registry: str, repository: str, current_tag: str) -> Optional[str]:
        """Find if there's a newer version available."""
        # Skip :latest tags
        if current_tag == 'latest':
            return None

        tags = self.get_tags(registry, repository, current_tag)

        # Handle error cases
        if not tags or (len(tags) == 1 and tags[0].startswith('[')):
            return tags[0] if tags else '[NO TAGS]'

        # Filter out error/status tags
        if len(tags) > 0 and all(t.startswith('[') for t in tags):
            return tags[0]

        # Try to find semantic version tags similar to current
        # Example: current is "1.72", look for "1.73", "1.74", etc.
        # Also handles: v4.20, 4.9, v2.16, etc.
        current_pattern = re.match(r'^v?(\d+)\.?(\d+)?(?:\.(\d+))?(?:\.(\d+))?', current_tag)

        if current_pattern:
            current_parts = [
                int(current_pattern.group(1)),
                int(current_pattern.group(2)) if current_pattern.group(2) else 0,
                int(current_pattern.group(3)) if current_pattern.group(3) else 0,
                int(current_pattern.group(4)) if current_pattern.group(4) else 0
            ]

            newer_tags = []
            for tag in tags:
                # Skip tags with non-version info (sha256, latest, build timestamps, etc.)
                if any(skip in tag.lower() for skip in ['sha256', 'latest', 'source', 'temp']):
                    continue

                # Skip pure numeric timestamps (10+ digits)
                if re.match(r'^\d{10,}', tag):
                    continue

                tag_match = re.match(r'^v?(\d+)\.?(\d+)?(?:\.(\d+))?(?:\.(\d+))?', tag)
                if tag_match:
                    tag_parts = [
                        int(tag_match.group(1)),
                        int(tag_match.group(2)) if tag_match.group(2) else 0,
                        int(tag_match.group(3)) if tag_match.group(3) else 0,
                        int(tag_match.group(4)) if tag_match.group(4) else 0
                    ]

                    # Only consider tags with same major.minor pattern
                    # e.g., for v4.20, only look at v4.20.x, not v4.21 or v5.0
                    if tag_parts[0] == current_parts[0] and tag_parts[1] == current_parts[1]:
                        # Compare versions (patch level)
                        if tag_parts > current_parts:
                            newer_tags.append((tag, tag_parts))
                    # Also check for next minor version in same major
                    elif tag_parts[0] == current_parts[0] and tag_parts[1] > current_parts[1]:
                        newer_tags.append((tag, tag_parts))

            if newer_tags:
                # Return the latest newer version
                newest = sorted(newer_tags, key=lambda x: x[1])[-1]
                return newest[0]
            else:
                # No newer version found - up to date
                return None

        # If no semantic versioning, just return the number of tags
        return f'[{len(tags)} tags available]'


def parse_imageset(file_path: Path) -> Dict:
    """Parse ImageSetConfiguration YAML file."""
    with open(file_path) as f:
        content = f.read()

    results = {
        'openshift_versions': [],
        'operator_catalogs': [],
        'additional_images': [],
    }

    # Extract OpenShift versions
    min_version_match = re.search(r'minVersion:\s*([0-9]+\.[0-9]+\.[0-9]+)', content)
    max_version_match = re.search(r'maxVersion:\s*([0-9]+\.[0-9]+\.[0-9]+)', content)

    if min_version_match:
        results['openshift_versions'].append({
            'type': 'minVersion',
            'current': min_version_match.group(1)
        })

    if max_version_match:
        results['openshift_versions'].append({
            'type': 'maxVersion',
            'current': max_version_match.group(1)
        })

    # Extract operator catalogs
    for match in re.finditer(r'catalog:\s*(registry\.redhat\.io/redhat/(?:redhat|community)-operator-index):v([0-9]+\.[0-9]+)', content):
        results['operator_catalogs'].append({
            'catalog': match.group(1),
            'version': match.group(2)
        })

    # Extract additional images
    for match in re.finditer(r'-\s*name:\s*([^:\s]+):([^\s]+)', content):
        image_name = match.group(1)
        tag = match.group(2)

        # Skip operator catalogs (already captured)
        if 'operator-index' in image_name:
            continue

        results['additional_images'].append({
            'full': f'{image_name}:{tag}',
            'name': image_name,
            'tag': tag
        })

    return results


def check_updates(imageset_path: Path, docker_config: Optional[Path] = None):
    """Check for updates in an imageset file."""
    print(f"\n{'='*70}")
    print(f"Checking for updates: {imageset_path.name}")
    print(f"{'='*70}\n")

    # Initialize registry client
    client = RegistryClient(docker_config)
    print()

    # Parse imageset
    results = parse_imageset(imageset_path)

    # Check OpenShift versions
    if results['openshift_versions']:
        print("OpenShift Platform Versions:")
        print("-" * 70)
        for ver in results['openshift_versions']:
            print(f"  {ver['type']}: {ver['current']}")
            print(f"    → Check: https://access.redhat.com/downloads/content/290")
        print()

    # Check operator catalogs
    if results['operator_catalogs']:
        print(f"Operator Catalogs:")
        print("-" * 70)
        seen = set()
        for cat in results['operator_catalogs']:
            key = f"{cat['catalog']}:v{cat['version']}"
            if key not in seen:
                catalog_type = "Red Hat" if "redhat-operator" in cat['catalog'] else "Community"
                print(f"  [{catalog_type}] v{cat['version']}")
                seen.add(key)
        print()

    # Check additional images
    if results['additional_images']:
        print(f"Container Images ({len(results['additional_images'])} total):")
        print("-" * 120)

        # Table header
        print(f"{'IMAGE':<60} {'CURRENT':<15} {'STATUS':<45}")
        print("-" * 120)

        updates_found = 0
        checked = 0

        for img in results['additional_images']:
            registry, repository, tag = client.parse_image(img['full'])

            # Truncate long image names for table formatting
            image_display = img['name']
            if len(image_display) > 58:
                image_display = image_display[:55] + "..."

            # Skip :latest tags
            if tag == 'latest':
                status = "⚠ :latest tag - consider pinning"
                print(f"{image_display:<60} {tag:<15} {status:<45}")
                continue

            newer = client.find_newer_versions(registry, repository, tag)
            checked += 1

            if newer and not newer.startswith('['):
                updates_found += 1
                status = f"✓ UPDATE: {newer}"
                print(f"{image_display:<60} {tag:<15} {status:<45}")
            elif newer and newer.startswith('['):
                status = f"ℹ {newer}"
                print(f"{image_display:<60} {tag:<15} {status:<45}")
            else:
                status = "✓ Up to date"
                print(f"{image_display:<60} {tag:<15} {status:<45}")

        print("-" * 120)
        print(f"Summary: {updates_found} updates found out of {checked} images checked")
        print("=" * 120)
        print()


def main():
    parser = argparse.ArgumentParser(
        description='Check for updates in OpenShift ImageSetConfiguration files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s imagesets/imageset-config-v2-4.20.21.yaml
  %(prog)s imagesets/imageset-config-v2-4.20.21.yaml --docker-config .docker-config.json
        '''
    )

    parser.add_argument(
        'imageset_file',
        type=Path,
        nargs='?',
        help='Path to ImageSetConfiguration YAML file'
    )

    parser.add_argument(
        '--docker-config',
        type=Path,
        default=Path('.docker-config.json'),
        help='Path to Docker config JSON file (default: .docker-config.json)'
    )

    parser.add_argument(
        '--list',
        action='store_true',
        help='List available imageset files and exit'
    )

    args = parser.parse_args()

    # List available files if requested
    if args.list:
        # Search for imageset files in common locations
        search_patterns = [
            'imagesets/**/*.yaml',
            '**/imagesets/*.yaml',
            '**/*imageset*.yaml',
            './*.yaml',
        ]

        found_files = []
        for pattern in search_patterns:
            found_files.extend(Path('.').glob(pattern))

        # Remove duplicates and sort
        found_files = sorted(set(found_files))

        if found_files:
            print("Available imageset files:")
            for f in found_files:
                print(f"  {f}")
        else:
            print("No imageset files found in current directory")
            print("Searched patterns: imagesets/**/*.yaml, **/*imageset*.yaml")
        sys.exit(0)

    if not args.imageset_file:
        parser.print_help()
        sys.exit(1)

    # Validate imageset file exists
    if not args.imageset_file.exists():
        print(f"ERROR: File not found: {args.imageset_file}")
        print("\nRun with --list to see available files")
        sys.exit(1)

    # Check for updates
    try:
        check_updates(args.imageset_file, args.docker_config)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
