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
4. Confirm creation and summarize the module structure

### Template Requirements (from STYLE.md)

- **2-space indentation** (overrides PEP 8's 4 spaces)
- Line length: 88 characters (Black default)
- Import order: stdlib, third-party, local (blank line between groups)
- `pathlib.Path` not `os.path`
- f-strings for string formatting
- Type hints on all function signatures
- Google-style docstrings (Args, Returns, Raises)
- `logging` not `print` for diagnostics
- Specific exceptions, never bare `except:`
- No mutable default arguments

### Naming

- Filename must be `lower_snake_case.py` (e.g., `data_processor.py`, not `DataProcessor.py`)
- Variables/functions: `lower_snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private: `_leading_underscore`
