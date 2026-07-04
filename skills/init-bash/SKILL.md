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
   - Update `check_dependencies` with any specified dependencies; if none were
     specified, remove the template's placeholder `jq`/`curl` entries rather than
     shipping them
   - If no arguments are expected, simplify `parse_arguments`: remove the
     positional `INPUT_FILE` case and its required-arg check, keeping only the
     `-h`/`-v`/`-n` option handling
3. Write the file to the specified path — if the parent directory doesn't exist, create it (`mkdir -p`) before writing
4. Make it executable: `chmod +x <file>`
5. Run `/sync-docs` to regenerate any `<!-- sync:scripts -->` index tables in the repo (no-op if no such markers exist).
6. Confirm creation and remind the user of the script structure

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

### Rules

- Never overwrite an existing file — if the target path already exists, stop and ask.
- If the filename isn't `kebab-case.sh`, propose the corrected `kebab-case.sh` name and confirm with the user before writing.
- Always `chmod +x` the new script and keep the `main "$@"` entry point intact.
- Only add `check_dependencies` entries the user actually named — don't invent dependencies.
- Once the script is fleshed out beyond the template, `/vet <path>` dispatches `style-reviewer` to check it against STYLE.md (non-blocking).
