# RH-Toolbox
A set of scripts that can help dealing with Red Hat products.

## Disclaimer

These scripts are provided **as-is** without any warranty or guarantee. They are **not official Red Hat tools** and are **not supported by Red Hat** in any way. Use at your own risk. Always test in a non-production environment before deploying to production systems.

## Tools

### [oc-mirror Sync Automation](./oc-mirror-wrapper/)
Python wrapper for automating multi-stage oc-mirror workflows in disconnected OpenShift environments with support for unlimited target registries. Includes automatic working directory management, skopeo fallback for failed images, and comprehensive error handling.

### [ImageSet Update Checker](./imageset-check-update/)
Python utility to check for available updates in OpenShift ImageSetConfiguration files. Queries container registries (Docker Hub, Quay.io, Red Hat registries) to identify newer versions of images, operator catalogs, and platform releases before mirroring.

### [etcd Performance Check](./etcd-performance-check/)
Python utility to collect and analyze etcd performance metrics on OpenShift clusters. Queries Prometheus for WAL fsync, compaction pause, backend commit durations, and DB fragmentation, then rates each metric against best-practice thresholds and generates a color-coded terminal report plus a markdown file.

### [S3 Integrity Check](./s3-integrity-check/)
Python utility to detect corrupted objects in S3-compatible buckets where object metadata exists but underlying data is missing. Supports any S3 endpoint via direct connection parameters, with built-in presets for auto-discovering credentials from known platforms like NooBaa and Velero on OpenShift.

### [RHOKP MCP Server](./rhokp-mcp-server/)
MCP (Model Context Protocol) server that exposes a local Red Hat Offline Knowledge Portal (RHOKP) instance as AI-consumable tools. Enables LLM-powered assistants to search Red Hat knowledgebase solutions, articles, documentation, CVEs, and product lifecycle data offline via full-text, semantic, and hybrid search.

## Development Setup

### Prerequisites

This project uses [asdf](https://asdf-vm.com/) for version management and [pre-commit](https://pre-commit.com/) for automated code quality checks.

### Quick Start

1. **Install asdf** (if not already installed):
   ```bash
   # macOS
   brew install asdf

   # Linux - follow instructions at https://asdf-vm.com/guide/getting-started.html
   ```

2. **Install required asdf plugins**:
   ```bash
   asdf plugin add python
   asdf plugin add pre-commit
   ```

3. **Install tools from `.tool-versions`**:
   ```bash
   asdf install
   ```

4. **Install Python dependencies**:
   ```bash
   pip3 install -r requirements-dev.txt
   ```

5. **Install pre-commit hooks**:
   ```bash
   pre-commit install
   ```

### Code Quality

All Python code must pass the following checks before committing:

- **Black** - Code formatter (88 character line length)
- **Pylint** - Linter (must achieve 10.00/10 score)
- **YAML validation** - Syntax checks for YAML files
- **File hygiene** - Trailing whitespace, end-of-file fixers

These checks run automatically on `git commit` via pre-commit hooks.

#### Manual Testing

```bash
# Format code
black .

# Run linter
pylint $(git ls-files '*.py')

# Run all pre-commit checks manually
pre-commit run --all-files
```

### Tool Versions

The project uses `.tool-versions` (asdf) to pin specific versions:
- **Python**: 3.14.2t
- **pre-commit**: 4.0.1

This ensures consistent development environments across contributors.

## Contributing

Contributions are welcome! If you have improvements, bug fixes, or new tools to add:
- Fork the repository
- Create a feature branch
- Submit a pull request with a clear description of your changes

Please ensure your contributions follow the same structure and documentation standards as existing tools.

## License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0) - see the [LICENSE](LICENSE) file for details.
