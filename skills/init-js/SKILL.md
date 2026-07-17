---
name: init-js
description: Scaffold a new JavaScript module from the standard template in templates.md
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
   - Set the template's top-of-file `/** … */` module description block to the user's description (or a placeholder)
   - Add requested functions/classes as stubs with JSDoc and parameter names
   - Add specified `import` lines (`require` only when scaffolding explicit CommonJS). Installing
     the packages (`npm install <package>`) is out of scope — note any uninstalled imports to the
     user at the end
   - Keep the `main()` entry point and the template's `import.meta.url` entry-point guard; swap it for `if (require.main === module)` only when the user explicitly asked for CommonJS
3. Write the file to the specified path — if the parent directory doesn't exist, create it (`mkdir -p`) before writing
4. Settle the shebang/exec-bit pairing per STYLE.md's script rule: if the module is an entry-point
   run by path (a standalone CLI), keep the template's shebang and `chmod +x <file>`; if it is only
   ever imported — the template exports `processData`, so this is a real case — remove the shebang
   and do not set the exec bit. Keep `main()` and its run-directly guard either way: the guard still
   fires on `node <path>`, the ESM counterpart of `python3 module.py`. When the user's description is
   absent or does not settle which it is, ask; do not guess. Never leave a shebang on a
   non-executable file — the exec-bit-guard hook blocks committing a new 644 shebang file.
5. Run `/sync-docs` to regenerate any `<!-- sync:scripts -->` index tables in the repo (no-op if no such markers exist).
6. Confirm creation and summarize the module structure

### Template Requirements (from STYLE.md)

- 2-space indentation, semicolons required
- Single quotes for strings
- `const` by default, `let` only when reassignment is needed
- Arrow functions for callbacks; `async`/`await` over callback chains
- Trailing commas in multiline arrays/objects
- JSDoc comments on exported functions
- **Modules: ESM (`import`/`export`)** — the template in templates.md is ESM; produce a CommonJS scaffold (`require`/`module.exports`, `require.main` guard) only if the user explicitly asks for it
- `export` for the public surface (`module.exports` only in an explicit-CommonJS scaffold)

### Naming

- Filename must be `kebab-case.js` (e.g., `file-processor.js`, not `fileProcessor.js`)
- Variables/functions: `camelCase`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`

### Rules

- Never overwrite an existing file — if the target path already exists, stop and ask.
- If the filename isn't `kebab-case.js`, propose the corrected `kebab-case.js` name and confirm with the user before writing.
- Shebang and exec bit travel together — set both on an entry-point module, neither on an import-only one. Never one without the other.
- Preserve the `main()` entry point and its guard — the template's `import.meta.url` entry-point check, or `if (require.main === module)` in an explicit-CommonJS scaffold.
- Only add the imports and stubs the user requested — don't scaffold speculative code.
- Once the module is fleshed out beyond the template, `/vet <path>` dispatches `style-reviewer` to check it against STYLE.md (non-blocking).
