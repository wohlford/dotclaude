---
name: init-js
description: Scaffold a new JavaScript module from the standard template in templates.md
disable-model-invocation: true
---

# /init-js — Scaffold a JavaScript Module

Create a new JavaScript module from the standard template.

## Instructions

The user wants to create a new JavaScript module. Use the template from `~/.claude/templates.md` (JavaScript Module Template section).

### Arguments

The user must provide:
- A filename or path (e.g., `file-processor.js`, `src/validate.js`)

The user may optionally provide:
- A brief description of the module's purpose
- Key functions or classes to stub out
- Third-party dependencies to import

### Process

1. Read `~/.claude/templates.md` for the full JavaScript module template
2. Customize the template:
   - Set the JSDoc header to the user's description (or a placeholder)
   - Add requested functions/classes as stubs with JSDoc and parameter names
   - Add specified `require`/`import` lines
   - Keep the `main()` entry point and `if (require.main === module)` guard
3. Write the file to the specified path
4. Run `/sync-docs` to regenerate any `<!-- sync:scripts -->` index tables in the repo (no-op if no such markers exist).
5. Confirm creation and summarize the module structure

### Template Requirements (from STYLE.md)

- 2-space indentation, semicolons required
- Single quotes for strings
- `const` by default, `let` only when reassignment is needed
- Arrow functions for callbacks; `async`/`await` over callback chains
- Trailing commas in multiline arrays/objects
- JSDoc comments on exported functions
- `module.exports` (or `export`) for the public surface

### Naming

- Filename must be `kebab-case.js` (e.g., `file-processor.js`, not `fileProcessor.js`)
- Variables/functions: `camelCase`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`

### Rules

- Never overwrite an existing file — if the target path already exists, stop and ask.
- Reject a filename that isn't `kebab-case.js`; correct it or confirm with the user first.
- Preserve the `main()` entry point and the `if (require.main === module)` guard.
- Only add the imports and stubs the user requested — don't scaffold speculative code.
