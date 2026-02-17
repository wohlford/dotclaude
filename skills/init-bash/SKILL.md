---
name: init-bash
description: Scaffold a new Bash script from the standard template in templates.md
disable-model-invocation: true
---

# /init-bash — Scaffold a Bash Script

Create a new bash script from the standard template.

## Instructions

The user wants to create a new bash script. Use the template from `~/.claude/templates.md` (Bash Script Template section).

### Arguments

The user must provide:
- A filename or path (e.g., `process-data.sh`, `scripts/deploy.sh`)

The user may optionally provide:
- A brief description of the script's purpose
- Dependencies the script needs (e.g., `jq`, `curl`)
- Whether it takes arguments

### Process

1. Read `~/.claude/templates.md` for the full bash script template
2. Customize the template:
   - Set `Script:` to the filename
   - Set `Purpose:` to the user's description (or a placeholder)
   - Set `Usage:` based on expected arguments
   - Update `check_dependencies` with any specified dependencies
   - If no arguments are expected, simplify `parse_arguments`
3. Write the file to the specified path
4. Make it executable: `chmod +x <file>`
5. Confirm creation and remind the user of the script structure

### Template Requirements (from STYLE.md)

- Shebang: `#!/usr/bin/env bash`
- `set -euo pipefail`
- Header comment block with Script, Purpose, Usage
- `trap cleanup EXIT INT TERM` for temp file cleanup
- Helper functions: `log_info`, `log_error`
- `show_help` function
- `check_dependencies` function
- Argument parsing with `-h/--help`, `-v/--verbose`, `-n/--dry-run`
- `main` function as entry point

### Naming

- Filename must be `kebab-case.sh` (e.g., `process-data.sh`, not `process_data.sh`)
- Variables: `lower_snake_case`
- Functions: `lower_snake_case`
- Constants: `UPPER_SNAKE_CASE`
