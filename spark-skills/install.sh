#!/usr/bin/env bash
# spark-skills installer - copies skills into Claude Code / Claude Desktop dirs.
# Idempotent: re-running overwrites skill content under --force, otherwise skips.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
SRC_DIR="${SCRIPT_DIR}/skills"
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1 ; shift ;;
        -h|--help)
            echo "Usage: $0 [--force]"
            echo "  --force  Overwrite existing skill files without prompting."
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2 ; exit 1 ;;
    esac
done

install_one() {
    local target_base="$1"
    mkdir -p "$target_base"
    shopt -s nullglob
    for skill_dir in "$SRC_DIR"/*/; do
        local name
        name="$(basename "$skill_dir")"
        local dest="$target_base/$name"
        if [ -e "$dest" ] && [ "$FORCE" -ne 1 ]; then
            echo "[=] $dest exists; pass --force to overwrite"
        else
            rm -rf "$dest"
            cp -R "$skill_dir" "$dest"
            echo "[+] installed $name -> $dest"
        fi
    done
    shopt -u nullglob
}

install_one "$HOME/.claude/skills"
install_one "$HOME/.claude-code/skills"

echo
echo "Done. Restart Claude Code / Claude Desktop to pick up the new skills."
