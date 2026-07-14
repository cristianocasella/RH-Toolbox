# RH-Toolbox Development Guide

This document provides guidelines for maintaining and extending the RH-Toolbox repository.

## Project Overview

RH-Toolbox is a collection of **whitelabel, community-driven scripts** for working with Red Hat products. These scripts are **not official Red Hat tools** and are **not supported by Red Hat** in any way.

## Repository Structure

```
RH-Toolbox/
├── README.md                    # Main repository README with tool catalog
├── LICENSE                      # AGPL-3.0 license
├── .pylintrc                    # Pylint configuration
├── requirements-dev.txt         # Development dependencies
├── oc-mirror-wrapper/           # Tool 1: oc-mirror automation
│   ├── README.md               # Tool-specific documentation
│   ├── oc_mirror_sync.py       # Main script
│   └── oc_mirror_config.yaml.example  # Configuration example
├── imageset-check-update/       # Tool 2: ImageSet update checker
│   ├── README.md               # Tool-specific documentation
│   └── check_updates.py        # Main script
└── [future-tool]/               # Future tools follow same pattern
    ├── README.md
    └── script.py
```

## Whitelabeling Guidelines

**CRITICAL**: All scripts must be fully whitelabel before being committed to this repository.

### What to Remove

1. **Customer/Company Names**:
   - Remove ALL specific customer/company names
   - Remove internal project codenames
   - Remove customer-specific registry URLs (replace with `example.com` domains)

2. **Internal Infrastructure**:
   - Replace specific hostnames with generic examples (e.g., `quay.example.com`)
   - Replace specific paths with generic placeholders (e.g., `/path/to/your/...`)
   - Remove internal network references

3. **Proprietary Information**:
   - Remove any proprietary configuration details
   - Remove internal documentation links
   - Remove customer-specific environment details

### What to Use Instead

1. **Generic Examples**:
   - `example.com`, `example.org` for domain names
   - `Primary`, `Secondary`, `DR-Site` for registry names
   - `/path/to/your/...` for file paths
   - `Registry-1`, `Registry-2` for numbered resources

2. **Descriptive Placeholders**:
   - Use UPPERCASE for environment variables: `YOUR_REGISTRY_URL`
   - Use descriptive names: `datacenter-1`, `datacenter-2`
   - Include comments explaining what should be replaced

3. **Configuration Examples**:
   - Always provide `.example` config files
   - Include detailed comments in example files
   - Document required vs. optional fields

## Code Quality Standards

### Pylint Score Target: 10.00/10

All Python scripts must achieve a perfect pylint score before merging.

### Pre-commit Validation

**IMPORTANT**: Before completing any task that modifies Python files, you MUST validate your changes using pre-commit:

```bash
# Run pre-commit on all changed files
pre-commit run --all-files

# Or run on specific files
pre-commit run --files path/to/file.py
```

This ensures:
- Black formatting is correct
- Pylint score is 10.00/10
- YAML files are valid
- No trailing whitespace or file hygiene issues

**DO NOT proceed with reporting task completion if pre-commit checks fail.**

#### Pylint Configuration

The project uses `.pylintrc` with the following settings:

```ini
[MESSAGES CONTROL]
disable=C0301,R0903

[DESIGN]
max-branches=15
max-locals=25
max-statements=75
```

**Disabled Rules**:
- `C0301`: Line-too-long (we don't enforce strict line length)
- `R0903`: Too-few-public-methods (acceptable for orchestrator classes)

**Complexity Thresholds**:
- Max branches per function: 15 (default: 12)
- Max local variables: 25 (default: 15)
- Max statements per function: 75 (default: 50)

#### Running Pylint

```bash
# Install dependencies
pip3 install -r requirements-dev.txt

# Check all Python files
pylint $(git ls-files '*.py')

# Check specific file
pylint path/to/script.py
```

#### Common Pylint Fixes

1. **W0718 (broad-exception-caught)**:
   - Use specific exceptions: `(OSError, ValueError)` instead of `Exception`
   - For intentional catch-all: add `# pylint: disable=broad-exception-caught`

2. **W1514 (unspecified-encoding)**:
   - Always specify encoding: `open(file, encoding='utf-8')`

3. **W1203 (logging-fstring-interpolation)**:
   - Use lazy formatting: `logger.info("Value: %s", value)`
   - Not: `logger.info(f"Value: {value}")`

4. **W0611 (unused-import)**:
   - Remove unused imports
   - Move all imports to module top (no inline imports unless necessary)

5. **R1732 (consider-using-with)**:
   - Use `with` for file operations and subprocess when possible
   - If not possible (e.g., real-time streaming), add inline disable comment

### Black Code Formatting

All Python code should be formatted with Black before committing.

```bash
# Format all Python files
black .

# Check without modifying
black --check .

# Format specific file
black path/to/script.py
```

**Black Configuration**: Uses default settings (88 character line length).

## Python Script Standards

### Required Elements

1. **Shebang and Docstring**:
   ```python
   #!/usr/bin/env python3
   """
   Brief description of what the script does.

   Usage:
       ./script.py [options]
   """
   ```

2. **Imports Organization**:
   ```python
   # Standard library imports
   import argparse
   import json
   import sys

   # Third-party imports
   import yaml

   # Local imports (if any)
   from .module import something
   ```

3. **Type Hints**:
   ```python
   from typing import Dict, List, Optional, Tuple

   def function(param: str) -> Dict[str, str]:
       """Docstring."""
       return {}
   ```

4. **File Encoding**:
   ```python
   with open(file_path, 'r', encoding='utf-8') as f:
       content = f.read()
   ```

5. **Exception Handling**:
   ```python
   # Good: Specific exceptions
   try:
       operation()
   except (OSError, ValueError) as e:
       logger.error("Operation failed: %s", e)

   # Acceptable: Top-level catch-all with comment
   try:
       main()
   except Exception as e:  # pylint: disable=broad-exception-caught
       # Catch all exceptions at top-level for graceful error reporting
       print(f"ERROR: {e}")
       sys.exit(1)
   ```

6. **Logging**:
   ```python
   import logging

   logger = logging.getLogger(__name__)

   # Use lazy % formatting for performance
   logger.info("Processing %s items", count)
   logger.error("Failed to process %s: %s", item, error)
   ```

## Documentation Standards

### Tool README Template

Each tool directory must include a `README.md` with:

1. **Title and Overview**:
   - Clear, concise description
   - Key features (bullet points)

2. **Prerequisites**:
   - Python version
   - Required packages
   - External dependencies (CLIs, authentication)

3. **Quick Start**:
   - Minimal working example
   - Basic usage commands

4. **Configuration**:
   - Configuration file format
   - Required vs. optional fields
   - Examples for common scenarios

5. **Usage Examples**:
   - Multiple realistic examples
   - Cover common use cases
   - Show different options/flags

6. **Advanced Features** (if applicable):
   - Optional features
   - Performance tuning
   - Integration examples

7. **Troubleshooting**:
   - Common errors and solutions
   - Debug tips
   - Known limitations

8. **No License/Contributing Sections**:
   - These belong in main README only
   - Avoid duplication

### Main README Structure

The main README should:

1. **Project Description**:
   - Brief overview
   - Purpose and scope

2. **Disclaimer**:
   - Clearly state these are not official Red Hat tools
   - No support or warranty

3. **Tools Catalog**:
   - One entry per tool with link to tool README
   - Brief one-line description

4. **Contributing**:
   - How to contribute
   - PR guidelines
   - Code standards reference

5. **License**:
   - Reference to LICENSE file
   - License type (AGPL-3.0)

## Adding a New Tool

### Checklist

- [ ] Create tool directory: `tool-name/`
- [ ] Add main script: `tool-name/script.py`
- [ ] Add tool README: `tool-name/README.md`
- [ ] Add configuration example (if needed): `tool-name/config.yaml.example`
- [ ] **Whitelabel all content** (remove customer-specific info)
- [ ] Add type hints to all functions
- [ ] Add docstrings to all classes/functions
- [ ] Use `encoding='utf-8'` for all file operations
- [ ] Use specific exceptions (not bare `Exception`)
- [ ] Format with Black: `black tool-name/`
- [ ] Verify pylint score: `pylint tool-name/*.py` → 10.00/10
- [ ] **Run pre-commit validation**: `pre-commit run --all-files` → ALL CHECKS MUST PASS
- [ ] Update main README with tool entry
- [ ] Test script functionality
- [ ] Inform user that changes are ready for review (DO NOT commit or push)

### Script Template

```python
#!/usr/bin/env python3
"""
Brief description of the tool.

Usage:
    ./script.py [options]
"""

import argparse
import logging
import sys
from typing import Dict, List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ToolClass:
    """Main class for the tool."""

    def __init__(self, config: Dict):
        """Initialize the tool."""
        self.config = config

    def run(self) -> bool:
        """Execute the main logic."""
        logger.info("Starting process")
        # Implementation here
        return True


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Tool description',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s --option value
  %(prog)s --dry-run
        '''
    )

    parser.add_argument(
        '--option',
        required=True,
        help='Option description'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without executing'
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_arguments()

    try:
        tool = ToolClass(args)
        success = tool.run()
        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Catch all exceptions at top-level for graceful error reporting
        logger.error("Error: %s", e)
        sys.exit(1)


if __name__ == '__main__':
    main()
```

## Refreshing Existing Scripts

When updating existing scripts:

1. **Review for Customer-Specific Content**:
   - Check docstrings, comments, examples
   - Verify configuration examples are generic
   - Check error messages for internal references

2. **Code Quality**:
   - Run Black: `black .`
   - Run pylint: `pylint $(git ls-files '*.py')`
   - Fix any new warnings/errors
   - **REQUIRED**: Run `pre-commit run --all-files` and ensure all checks pass

3. **Update Documentation**:
   - Update README if functionality changed
   - Update configuration examples if options changed
   - Update usage examples if syntax changed

4. **Test Thoroughly**:
   - Test all major code paths
   - Verify configuration examples work
   - Check dry-run mode if applicable

5. **Final Steps**:
   - Use `git status` and `git diff` to review all changes
   - Inform user about completed changes
   - **NEVER commit or push** - user will review and commit themselves

## Git Workflow

**CRITICAL GIT RESTRICTIONS**:
- ✅ **READ-ONLY git operations are ALLOWED**: `git status`, `git diff`, `git log`, `git ls-files`, `git branch`, `git show`
- ❌ **WRITE git operations are FORBIDDEN**: `git commit`, `git push`, `git merge`, `git rebase`, `git add`, `git stash`
- ❌ **NEVER commit code on behalf of the user**
- ❌ **NEVER push changes to remote repositories**

You may use git commands to:
- Check repository status
- View diffs and changes
- List files for processing (e.g., `git ls-files '*.py'` for pylint)
- View commit history for context
- Check current branch

### Branch Naming

- Feature: `feature/tool-name` or `feature/description`
- Bugfix: `bugfix/issue-description`
- Refactor: `refactor/description`

### Commit Messages

Follow conventional commits format:

```
type(scope): brief description

Longer description if needed.

- Details
- More details
```

**Types**: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

**Examples**:
```
feat(oc-mirror): add support for unlimited registries

Changed registry configuration from fixed primary/secondary to array-based
approach. Now supports any number of target registries.

- Refactored stage numbering: Stage 0 for file, 1+ for registries
- Updated README with new configuration format
- Added configuration examples for 3+ registries
```

```
fix(imageset-check): handle authentication errors gracefully

Added specific exception handling for registry authentication failures
instead of generic Exception catch.
```

```
docs(readme): add imageset-check-update tool to catalog

Added cross-link and description for the new ImageSet update checker tool.
```

## CI/CD Integration

The repository includes GitHub Actions workflow for pylint checks.

**Workflow**: `.github/workflows/pylint.yml`

Expected behavior:
- Runs on all PRs and pushes to main
- Executes: `pylint $(git ls-files '*.py')`
- Must achieve 10.00/10 to pass
- Blocks merge if pylint fails

## Common Patterns

### Configuration Files

Use YAML for configuration with clear structure:

```yaml
# Configuration for Tool Name

# Required: Description of required field
required_field: "value"

# Optional: Description of optional field (default: value)
# optional_field: "value"

# List of items
items:
  - name: "Item 1"
    option: "value"
  - name: "Item 2"
    option: "value"
```

### Argument Parsing

Always include:
- Required vs. optional arguments clearly marked
- Help text for every argument
- Examples in epilog
- Sensible defaults

### Error Handling

Use specific error codes:
```python
# 0 = success
# 1 = generic error
# 2 = configuration/validation error
# 3 = partial failure (some operations succeeded)
sys.exit(0)
```

### Logging Levels

- `DEBUG`: Detailed diagnostic information
- `INFO`: General informational messages
- `WARNING`: Warning messages (script continues)
- `ERROR`: Error messages (script may continue or exit)

## Testing Guidelines

While formal unit tests are not required, all scripts should be manually tested:

1. **Happy path**: Normal successful execution
2. **Error handling**: Missing files, invalid config, network errors
3. **Edge cases**: Empty inputs, maximum values, special characters
4. **Dry-run mode**: Verify no changes made when applicable

## Security Considerations

1. **Credentials**:
   - Never hardcode credentials in scripts
   - Use config files (excluded from git via `.gitignore`)
   - Document authentication setup in README

2. **File Paths**:
   - Validate user-provided paths
   - Use `os.path.exists()` before operations
   - Don't blindly delete user data

3. **Command Injection**:
   - Use list form for subprocess: `['cmd', 'arg1']` not `'cmd arg1'`
   - Validate/sanitize user inputs
   - Don't use `shell=True` unless absolutely necessary

4. **YAML Loading**:
   - Use `yaml.safe_load()` not `yaml.load()`
   - Validate loaded data structure

## Resources

- **Pylint Documentation**: https://pylint.pycqa.org/
- **Black Documentation**: https://black.readthedocs.io/
- **Python Type Hints**: https://docs.python.org/3/library/typing.html
- **Conventional Commits**: https://www.conventionalcommits.org/

## Questions?

For questions or clarifications about these guidelines:
1. Check existing tools for reference implementations
2. Review this document thoroughly
3. Open a GitHub issue for discussion
4. Tag maintainers in PR comments

---

*Last updated: 2026-07-13*
