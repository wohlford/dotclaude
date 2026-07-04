---
name: init-python
description: Scaffold a new Python module from the standard template in templates.md
disable-model-invocation: true
---

# /init-python — Scaffold a Python Module

Create a new Python module from the standard template.

## Instructions

The user wants to create a new Python module. Use the template from `~/.claude/templates.md` (Python Module Template section).

### Arguments

The user must provide:
- A filename or path (e.g., `data_processor.py`, `scripts/validate.py`)

The user may optionally provide:
- A brief description of the module's purpose
- Key classes or functions to stub out
- Third-party dependencies to import

### Process

1. Read `~/.claude/templates.md` for the full Python module template
2. Customize the template:
   - Set the module docstring to the user's description (or a placeholder)
   - Add requested classes/functions as stubs with docstrings and type hints
   - Add specified imports (maintaining stdlib > third-party > local order)
   - Keep `main()` entry point and `if __name__ == "__main__":` block
3. Write the file to the specified path
4. Settle the shebang/exec-bit pairing per STYLE.md's script rule: if the module is an entry-point
   script run by path (the user's description implies a standalone CLI), keep the template's
   shebang and `chmod +x <file>`; if it is only ever imported or run via `python3 module.py`,
   remove the shebang line instead. Never leave a shebang on a non-executable file — the
   exec-bit-guard hook blocks committing a new 644 shebang file.
5. Run `/sync-docs` to regenerate any `<!-- sync:scripts -->` index tables in the repo (no-op if no such markers exist).
6. Confirm creation and summarize the module structure

### Template Requirements (from STYLE.md)

- **4-space indentation** (PEP 8)
- Line length: 88 characters (ruff/PEP 8 default); `ruff format` is authoritative — lines it leaves long (unsplittable strings, comment/docstring lines) are tolerated over 88
- Import order: stdlib, third-party, local (blank line between groups)
- `pathlib.Path` not `os.path`
- f-strings for string formatting
- Type hints on all function signatures
- Google-style docstrings — a one-liner is fine when the behavior is obvious; add Args/Returns/Raises when a signature or contract isn't self-evident
- `logging` for diagnostics in libraries and long-running services; CLI tools send program output to stdout and diagnostics to stderr (`print(..., file=sys.stderr)` is fine)
- Specific exceptions, never bare `except:`
- No mutable default arguments

### Naming

- Filename must be `lower_snake_case.py` (e.g., `data_processor.py`, not `DataProcessor.py`)
- Variables/functions: `lower_snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private: `_leading_underscore`

### Rules

- Never overwrite an existing file — if the target path already exists, stop and ask.
- If the filename isn't `lower_snake_case.py`, propose the corrected `lower_snake_case.py` name and confirm with the user before writing.
- Preserve the `main()` / `if __name__ == "__main__":` entry point and 4-space indentation.
- Only add the imports and stubs the user requested — don't scaffold speculative code.
- Once the script is fleshed out beyond the template, `/vet <path>` dispatches `style-reviewer` to check it against STYLE.md (non-blocking).
