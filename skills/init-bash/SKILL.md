---
name: init-bash
description: Scaffold a new Bash script from the standard template in templates.md
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
   - If no arguments are expected, **strip the template's input-file path
     end-to-end — it threads through six sites, and each one masks the next.**
     `set -euo pipefail` turns any survivor into an unbound-variable crash on
     every run, so a partial strip looks fixed right up until the site above it
     is removed. Take all six together:
     1. the header `# Usage:` comment — drop `<input-file>`;
     2. **`show_help`'s heredoc `Usage:` line** — a *second* occurrence, easy to
        miss, and it leaves the help text advertising an argument the script no
        longer takes;
     3. `parse_arguments`' `[[ $# -eq 0 ]] && show_help` guard — it exists to
        catch a *missing required* argument, so with none required it prints
        help and exits on every ordinary run;
     4. `parse_arguments`' required-arg check, and the **body** of its positional
        `*)` case — but **keep the `*)` case itself**, replacing its body with
        `log_error "Unexpected argument: $1"; exit 1`. Deleting the case outright
        is a trap: `-*)` only catches unknown *options*, so a bare word matches
        `*)` alone — the only branch a stray positional can ever reach, and so
        the only `shift` it will ever hit (`-v`/`-n` shift too, but a bare word
        never gets to them). Delete the case and nothing shifts on that input:
        `while [[ $# -gt 0 ]]` spins forever. That is a
        **hang, not a crash** — `set -euo pipefail` cannot catch it, so it hides
        from the reasoning that finds every other site on this list;
     5. `main`'s argument in `process_file "$INPUT_FILE"` — note this one is
        *unguarded*, unlike `parse_arguments`' `${INPUT_FILE:-}`, so it is the
        first thing to crash;
     6. `process_file` itself — **replace it, don't trim it.** Its signature
        (`local input="$1"`) *and its whole body* are file-oriented: the `[[ -f
        "$input" ]]` check, `mv "$temp_file" "${input%.txt}.out"`, and all three
        log calls use `$input`. Deleting only the signature leaves those four
        references dangling and crashes one function deeper. Write the script's
        real core logic in its place and rename it to match
3. Before writing, apply the Rules checks: the filename is `kebab-case.sh` (else propose the corrected name and confirm) and the target doesn't already exist (else stop and ask). Then write the file to the specified path — if the parent directory doesn't exist, create it (`mkdir -p`) before writing
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
