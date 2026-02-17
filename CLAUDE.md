# Global Claude Code Instructions

Universal instructions for all projects.

- **Code style and formatting:** [STYLE.md](./.claude/STYLE.md)
- **Code templates:** [templates.md](./.claude/templates.md) (Bash, Python, JavaScript)
- **Development workflows:** [workflows.md](./.claude/workflows.md) (Explore/Plan/Code/Commit, TDD)

## Skills (Slash Commands)

| Command | Purpose |
| :--- | :--- |
| `/commit` | Create a git commit following `<type>: <subject>` format |
| `/init-bash` | Scaffold a new bash script from the standard template |
| `/init-python` | Scaffold a new Python module from the standard template |

## Agents

| Agent | Model | Purpose |
| :--- | :--- | :--- |
| `style-reviewer` | haiku | Review code files for STYLE.md compliance |

## Hooks

- **PostToolUse** (Edit/Write) — `~/.claude/scripts/style-check.sh` validates file format (tabs, newlines, syntax) on every edit

## Environment

- **Platform**: macOS with MacPorts package manager
- **Editor**: BBEdit (primary code editor)
- **Shell**: Prefer MacPorts bash (`/opt/local/bin/bash`) for scripts requiring advanced features
- **Default bash**: `/bin/bash` is the system bash (version 3.x, limited features)
- **GNU Core Utilities**: Installed via MacPorts (`coreutils @9.5_1`)
  - GNU tools are prefixed with `g` (e.g., `gls`, `ggrep`, `gdate`)
  - Use GNU versions for advanced features like `--long-options`

## Language and Tooling Preferences

- **Preferred**: Unix tools orchestrated through Bash scripts
- **Secondary**: Python for complex tasks requiring rich libraries
- Favor command-line tools and shell scripts over GUI methods
- Use Python when Bash becomes unwieldy or complex data structures are needed

### Package Management

#### Python (uv)

- **Version**: Python 3.13 (MacPorts)
- Create venv: `uv venv` — Activate: `source .venv/bin/activate`
- Install: `uv pip install <package>` (NOT standard `pip`)
- Sync: `uv pip sync requirements.txt`

#### Node.js (NVM)

- **NVM**: 0.40.3 — **Node**: v25.2.1
- Initialize: `source /opt/local/share/nvm/init-nvm.sh`
- Install: `npm install <package>`

#### System Tools (MacPorts)

- Install: `sudo port install <package>`
- Check: `port installed | grep <package>`
- Location: `/opt/local/bin/`, `/opt/local/lib/`

## macOS Notes

### Bash Versions

| Version | Location | Use Case |
|---------|----------|----------|
| 3.x | `/bin/bash` | System/POSIX scripts |
| 5.x | `/opt/local/bin/bash` | Modern scripts (associative arrays, `[[`, etc.) |

### GNU vs BSD Tools

macOS ships BSD tools by default. GNU versions (MacPorts) provide more features:

| Tool | BSD | GNU | Key Difference |
|------|-----|-----|----------------|
| grep | `/usr/bin/grep` | `ggrep` | `-P` (Perl regex) |
| sed | `/usr/bin/sed` | `gsed` | Extended features |
| date | `/bin/date` | `gdate` | Better parsing |
| ls | `/bin/ls` | `gls` | `--color`, `--group-directories-first` |

To use GNU by default: `export PATH="/opt/local/libexec/gnubin:$PATH"`
