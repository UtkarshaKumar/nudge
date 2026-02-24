#!/usr/bin/env bash
# nudge uninstaller
# Usage: bash uninstall.sh

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
step() { echo -e "\n${BOLD}$*${NC}"; }
ask()  { echo -e "  ${YELLOW}?${NC}  $*"; }

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NUDGE_HOME="$HOME/.nudge"

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  nudge uninstaller${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  This will remove nudge and its components."
echo "  You will be asked before anything is deleted."
echo ""

# ── Keep data? ────────────────────────────────────────────────────────────────
step "Your meeting data"
echo ""
echo "  You have sessions stored at: $NUDGE_HOME/data/"
echo ""
echo "  ${BOLD}What should happen to your transcripts and meeting notes?${NC}"
echo "    1) Keep everything   ${DIM}(just remove the app, keep all transcripts/notes)${NC}"
echo "    2) Delete data too   ${DIM}(remove everything including transcripts and SQLite)${NC}"
echo ""
read -rp "  Choice [1]: " DATA_CHOICE
DATA_CHOICE="${DATA_CHOICE:-1}"

# ── Remove Python venv ────────────────────────────────────────────────────────
step "Python virtual environment"
VENV_DIR="$INSTALL_DIR/.venv"
if [[ -d "$VENV_DIR" ]]; then
    read -rp "  Remove .venv at $VENV_DIR? [Y/n]: " REMOVE_VENV
    REMOVE_VENV="${REMOVE_VENV:-Y}"
    if [[ "$REMOVE_VENV" =~ ^[Yy] ]]; then
        rm -rf "$VENV_DIR"
        ok "Removed .venv"
    else
        warn "Kept .venv"
    fi
else
    ok "No .venv found"
fi

# ── Remove config and optionally data ─────────────────────────────────────────
step "nudge config and data"
if [[ -d "$NUDGE_HOME" ]]; then
    if [[ "$DATA_CHOICE" == "2" ]]; then
        read -rp "  Permanently delete $NUDGE_HOME (all transcripts, notes, database)? [y/N]: " CONFIRM_DELETE
        if [[ "${CONFIRM_DELETE:-N}" =~ ^[Yy] ]]; then
            rm -rf "$NUDGE_HOME"
            ok "Deleted $NUDGE_HOME"
        else
            warn "Kept $NUDGE_HOME (no data deleted)"
        fi
    else
        # Only remove config file, keep data
        if [[ -f "$NUDGE_HOME/config.yaml" ]]; then
            rm -f "$NUDGE_HOME/config.yaml"
            ok "Removed config.yaml (data kept at $NUDGE_HOME/data/)"
        fi
    fi
fi

# ── Remove lock file if present ───────────────────────────────────────────────
rm -f "$NUDGE_HOME/nudge.lock" 2>/dev/null || true

# ── Remove shell alias ────────────────────────────────────────────────────────
step "Shell alias"
REMOVED_ALIAS=false
for RC_FILE in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile"; do
    if [[ -f "$RC_FILE" ]] && grep -q "alias nudge=" "$RC_FILE" 2>/dev/null; then
        # Remove the alias line and the comment above it
        sed -i '' '/# nudge — meeting scribe/d' "$RC_FILE" 2>/dev/null || true
        sed -i '' '/alias nudge=/d' "$RC_FILE" 2>/dev/null || true
        ok "Removed nudge alias from $RC_FILE"
        REMOVED_ALIAS=true
    fi
done
if [[ "$REMOVED_ALIAS" == false ]]; then
    ok "No alias found in shell config"
fi

# ── Optional: remove BlackHole ────────────────────────────────────────────────
step "BlackHole audio driver (optional)"
echo ""
echo "  ${DIM}BlackHole is a system audio driver. Other apps might use it.${NC}"
echo "  ${DIM}It's safe to keep it installed.${NC}"
echo ""
read -rp "  Remove BlackHole 2ch? [y/N]: " REMOVE_BH
if [[ "${REMOVE_BH:-N}" =~ ^[Yy] ]]; then
    if brew list --cask blackhole-2ch &>/dev/null 2>&1; then
        brew uninstall --cask blackhole-2ch
        ok "Removed BlackHole 2ch"
    elif brew list blackhole-2ch &>/dev/null 2>&1; then
        brew uninstall blackhole-2ch
        ok "Removed BlackHole 2ch"
    else
        warn "BlackHole 2ch not installed via Homebrew"
    fi
else
    ok "Kept BlackHole 2ch"
fi

# ── Optional: remove Ollama ───────────────────────────────────────────────────
step "Ollama (optional)"
echo ""
echo "  ${DIM}Ollama may be used by other tools on your Mac.${NC}"
echo ""
read -rp "  Remove Ollama? [y/N]: " REMOVE_OLLAMA
if [[ "${REMOVE_OLLAMA:-N}" =~ ^[Yy] ]]; then
    if command -v ollama &>/dev/null; then
        brew uninstall ollama 2>/dev/null || warn "Remove Ollama manually: brew uninstall ollama"
        ok "Removed Ollama"
    else
        ok "Ollama not found"
    fi
else
    ok "Kept Ollama"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  nudge uninstalled.${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [[ "$DATA_CHOICE" == "1" ]]; then
    echo "  Your meeting data is preserved at:"
    echo "    Transcripts: $NUDGE_HOME/data/sessions/"
    echo "    Database:    $NUDGE_HOME/data/nudge.db"
    echo ""
    echo "  To delete it manually: rm -rf $NUDGE_HOME"
fi

echo "  Restart your terminal for the alias change to take effect."
echo ""
