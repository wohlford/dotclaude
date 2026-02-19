# Code Templates

Full-length templates for reference. See [STYLE.md](./.claude/STYLE.md) for rules.

## Bash Script Template

```bash
#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Script: example-processor.sh
# Purpose: Process files with validation and error handling
# Usage: ./example-processor.sh [options] <input-file>
# ============================================================================

# ---------- Configuration ----------
readonly SCRIPT_DIR="$(dirname "$(realpath "$0")")"
readonly DEFAULT_TIMEOUT=30

# ---------- Global Variables ----------
verbose=0
dry_run=0
temp_file=""

# ---------- Cleanup Handler ----------
cleanup() {
  local exit_code=$?
  [[ -n "$temp_file" && -f "$temp_file" ]] && rm --force "$temp_file"
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

# ---------- Helper Functions ----------
show_help() {
  cat << EOF
Usage: $(basename "$0") [options] <input-file>

Options:
  -h, --help          Show this help message
  -v, --verbose       Enable verbose output
  -n, --dry-run       Show what would be done without doing it
EOF
  exit 0
}

# ---------- Validation ----------
check_dependencies() {
  local missing=0
  for cmd in "jq" "curl"; do
    if ! command -v "$cmd" &>/dev/null; then
      printf '[ERROR] %s\n' "$cmd not found (sudo port install $cmd)"
      missing=1
    fi
  done
  [[ $missing -eq 0 ]] || exit 1
}

# ---------- Core Logic ----------
process_file() {
  local input="$1"
  [[ -f "$input" ]] || { printf '[ERROR] %s\n' "Not found: $input"; return 1; }

  if [[ $dry_run -eq 1 ]]; then
    printf '[INFO] %s\n' "Would process: $input"
    return 0
  fi

  temp_file=$(mktemp)
  # ... processing ...
  mv "$temp_file" "${input%.txt}.out"
  temp_file=""
  printf '[INFO] %s\n' "Processed: $input"
}

# ---------- Argument Parsing ----------
parse_arguments() {
  [[ $# -eq 0 ]] && show_help
  while [[ $# -gt 0 ]]; do
    case $1 in
      -h|--help)    show_help ;;
      -v|--verbose) verbose=1; shift ;;
      -n|--dry-run) dry_run=1; shift ;;
      -*)           printf '[ERROR] %s\n' "Unknown option: $1"; exit 1 ;;
      *)            readonly INPUT_FILE="$1"; shift ;;
    esac
  done
  [[ -n "${INPUT_FILE:-}" ]] || { printf '[ERROR] %s\n' "Input file required"; exit 1; }
}

# ---------- Main ----------
main() {
  parse_arguments "$@"
  check_dependencies
  process_file "$INPUT_FILE"
}

main "$@"
```

## Python Module Template

```python
#!/usr/bin/env python3
"""Brief module description.

Longer description explaining purpose and usage.
"""

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ExampleProcessor:
  """Process data with validation.

  Attributes:
    name: Processor identifier
    value: Current value
  """

  def __init__(self, name: str, value: int = 0) -> None:
    if not name:
      raise ValueError("name cannot be empty")
    self.name = name
    self.value = value

  def process(self) -> dict[str, Any]:
    """Process and return results.

    Returns:
      Dict with 'status' and 'value' keys.

    Raises:
      RuntimeError: If processing fails.
    """
    try:
      return {'status': 'success', 'value': self.value * 2}
    except Exception as e:
      raise RuntimeError(f"Failed: {self.name}") from e


def main() -> int:
  """Main entry point."""
  try:
    result = ExampleProcessor("demo", 21).process()
    print(f"Result: {result}")
    return 0
  except Exception as e:
    logger.error(f"Fatal: {e}")
    return 1


if __name__ == "__main__":
  sys.exit(main())
```

## JavaScript Module Template

```javascript
#!/usr/bin/env node
'use strict';

const fs = require('fs').promises;

const DEFAULT_TIMEOUT = 30000;

/**
 * Process data from input file.
 * @param {string} inputPath - Path to input file
 * @param {Object} [options] - Processing options
 * @returns {Promise<Object>} Processing results
 */
async function processData(inputPath, options = {}) {
  const { timeout = DEFAULT_TIMEOUT, validate = true } = options;
  const data = await fs.readFile(inputPath, 'utf8');
  return { status: 'success', records: data.length };
}

async function main() {
  try {
    const result = await processData('input.txt');
    console.log('Result:', result);
  } catch (error) {
    console.error('Fatal:', error);
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}

module.exports = { processData };
```
