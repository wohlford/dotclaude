#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Script: install.sh
# Purpose: Symlink source-controlled dotclaude files into ~/.claude
# Usage: ./install.sh
# ============================================================================

# ---------- Configuration ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

# Files symlinked into ~/.claude
readonly CLAUDE_FILES=(
  CLAUDE.md
  CONTRIBUTING.md
  STYLE.md
  templates.md
  workflows.md
  README.md
  LICENSE
)

# Directories symlinked into ~/.claude
readonly CLAUDE_DIRS=(
  agents
  skills
  scripts
)

# ---------- Helper Functions ----------
log_info()  { printf '[INFO] %s\n' "$*"; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }

link_file() {
  local src="$1"
  local dest="$2"

  if [[ -L "$dest" ]]; then
    local current
    current="$(readlink "$dest")"
    if [[ "$current" == "$src" ]]; then
      return 0
    fi
    rm "$dest"
  elif [[ -e "$dest" ]]; then
    local bak
    bak="${dest}.$(date +%Y%m%d-%H%M%S).bak"
    log_info "Backing up $dest → $bak"
    mv "$dest" "$bak"
  fi

  ln -sf "$src" "$dest"
  log_info "Linked $dest → $src"
}

# ---------- Main ----------
main() {
  mkdir -p "$HOME/.claude"

  for f in "${CLAUDE_FILES[@]}"; do
    link_file "$SCRIPT_DIR/$f" "$HOME/.claude/$f"
  done

  for d in "${CLAUDE_DIRS[@]}"; do
    link_file "$SCRIPT_DIR/$d" "$HOME/.claude/$d"
  done

  log_info "Done"
  log_info "Note: settings.json was NOT linked (Claude Code rewrites it at runtime; manage manually if needed)."
}

main "$@"
