#!/usr/bin/env bash
# nudge installer — run once to set everything up
# Usage: bash install.sh

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "  ${RED}✗${NC}  $*"; }
step() { echo -e "\n${BOLD}$*${NC}"; }
info() { echo -e "  ${DIM}$*${NC}"; }
ask()  { echo -e "  ${BLUE}?${NC}  $*"; }

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NUDGE_HOME="$HOME/.nudge"
CONFIG_FILE="$NUDGE_HOME/config.yaml"

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  nudge — your silent meeting scribe  ${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  This installs:"
echo "    • BlackHole 2ch  — virtual audio loopback (routes speaker audio)"
echo "    • Ollama          — local LLM runner"
echo "    • faster-whisper  — local speech-to-text"
echo "    • nudge           — this tool"
echo ""
echo "  All processing is 100% local. Nothing leaves your Mac."
echo ""

# ── Platform check ────────────────────────────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
    fail "nudge requires macOS. Detected: $(uname)"
    exit 1
fi
ok "macOS $(sw_vers -productVersion) detected"

# ── Homebrew ──────────────────────────────────────────────────────────────────
step "Step 1/7  Homebrew"
if ! command -v brew &>/dev/null; then
    warn "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi
ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"

# ── BlackHole ─────────────────────────────────────────────────────────────────
step "Step 2/7  BlackHole virtual audio driver"

# Check if BlackHole audio device is already visible to the system (post-reboot)
BLACKHOLE_ACTIVE=false
if system_profiler SPAudioDataType 2>/dev/null | grep -q "BlackHole 2ch"; then
    BLACKHOLE_ACTIVE=true
fi

if brew list --cask blackhole-2ch &>/dev/null 2>&1 || brew list blackhole-2ch &>/dev/null 2>&1; then
    if [[ "$BLACKHOLE_ACTIVE" == true ]]; then
        ok "BlackHole 2ch installed and active"
    else
        warn "BlackHole 2ch is installed but not yet active — it needs a reboot to load the audio driver."
        echo ""
        echo -e "  ${BOLD}${RED}⚠  ACTION REQUIRED: Restart your Mac before continuing.${NC}"
        echo ""
        echo "  BlackHole is a kernel audio driver. macOS must restart before it"
        echo "  appears in Audio MIDI Setup. Without this step, you won't be able"
        echo "  to check the BlackHole 2ch box in the Multi-Output Device."
        echo ""
        echo "  After restarting:"
        echo "    1. Come back to this folder"
        echo "    2. Run: ${BOLD}bash install.sh${NC}"
        echo "    3. The installer will skip BlackHole (already installed) and"
        echo "       continue from the Audio MIDI Setup step."
        echo ""
        read -rp "  Press Enter to exit and restart your Mac, or type 'skip' to continue anyway: " REBOOT_CHOICE
        if [[ "${REBOOT_CHOICE:-}" != "skip" ]]; then
            echo ""
            echo "  Restart your Mac, then re-run: bash install.sh"
            echo ""
            exit 0
        fi
        warn "Continuing without reboot — BlackHole may not appear in Audio MIDI Setup yet."
    fi
else
    info "Installing BlackHole 2ch (may require admin password)..."
    brew install blackhole-2ch || brew install --cask blackhole-2ch
    echo ""
    echo -e "  ${BOLD}${RED}⚠  REBOOT REQUIRED before continuing.${NC}"
    echo ""
    echo "  BlackHole was just installed. macOS needs a restart to load the"
    echo "  audio driver before it appears in Audio MIDI Setup."
    echo ""
    echo "  After restarting:"
    echo "    1. Come back to this folder"
    echo "    2. Run: ${BOLD}bash install.sh${NC}"
    echo "    3. The installer will pick up from where it left off."
    echo ""
    read -rp "  Press Enter to exit — restart your Mac, then re-run the installer: " _
    exit 0
fi

# ── Audio MIDI Setup guide ────────────────────────────────────────────────────
step "Step 3/7  Configure audio routing"
echo ""
echo "  BlackHole captures digital audio from your speakers."
echo "  You need a Multi-Output Device so audio plays normally AND routes to nudge."
echo ""
echo "  ${BOLD}Follow these steps (takes ~1 minute):${NC}"
echo ""
echo "  1. I'll open Audio MIDI Setup now. Look for it in your Dock."
echo "  2. Click the ${BOLD}+${NC} button at the bottom-left"
echo "  3. Choose ${BOLD}\"Create Multi-Output Device\"${NC}"
echo "  4. In the right panel, check both:"
echo "       ☑  ${BOLD}BlackHole 2ch${NC}"
echo "       ☑  ${BOLD}Your speakers or headphones${NC}  (e.g. MacBook Pro Speakers)"
echo "  5. Right-click the new device → ${BOLD}\"Use This Device For Sound Output\"${NC}"
echo "  6. Optionally rename it to ${BOLD}\"nudge output\"${NC}"
echo ""
echo -e "  ${DIM}Tip: nudge will still capture audio even if you use headphones.${NC}"
echo ""
read -rp "  Press Enter to open Audio MIDI Setup (or 's' to skip if already done): " SKIP_AUDIO

if [[ "${SKIP_AUDIO:-}" != "s" ]]; then
    open -a "Audio MIDI Setup"
    echo ""
    read -rp "  Press Enter once you've created the Multi-Output Device: " _
fi
ok "Audio routing step complete"

# ── Ollama ────────────────────────────────────────────────────────────────────
step "Step 4/7  Ollama (local LLM runner)"
if ! command -v ollama &>/dev/null; then
    info "Installing Ollama..."
    brew install ollama
fi
ok "Ollama $(ollama --version 2>/dev/null | head -1 || echo 'installed')"

# ── Questions ─────────────────────────────────────────────────────────────────
step "Step 5/7  Configuration"
echo ""

# LLM model
echo "  ${BOLD}Which LLM model for action item extraction?${NC}"
echo "    1) llama3.2:3b   — fast, 2 GB RAM, great quality       ${DIM}(recommended)${NC}"
echo "    2) phi3:mini     — fastest, 2.3 GB, good quality"
echo "    3) llama3.1:8b   — best quality, 4.7 GB RAM (needs 16 GB Mac)"
echo ""
read -rp "  Choice [1]: " LLM_CHOICE
LLM_CHOICE="${LLM_CHOICE:-1}"
case "$LLM_CHOICE" in
    2) LLM_MODEL="phi3:mini" ;;
    3) LLM_MODEL="llama3.1:8b" ;;
    *) LLM_MODEL="llama3.2:3b" ;;
esac
ok "LLM model: $LLM_MODEL"

# Whisper model
echo ""
echo "  ${BOLD}Which Whisper model for transcription?${NC}"
echo "    1) small.en   — balanced accuracy, 500 MB    ${DIM}(recommended)${NC}"
echo "    2) tiny.en    — fastest, 150 MB, lower accuracy"
echo "    3) medium.en  — best accuracy, 1.5 GB, slower"
echo "    4) large-v3   — multilingual, best overall, 3 GB"
echo ""
read -rp "  Choice [1]: " WHISPER_CHOICE
WHISPER_CHOICE="${WHISPER_CHOICE:-1}"
case "$WHISPER_CHOICE" in
    2) WHISPER_MODEL="tiny.en" ;;
    3) WHISPER_MODEL="medium.en" ;;
    4) WHISPER_MODEL="large-v3" ;;
    *) WHISPER_MODEL="small.en" ;;
esac
ok "Whisper model: $WHISPER_MODEL"

# Meeting notes folder
echo ""
echo "  ${BOLD}Where should meeting notes (Word docs) be saved?${NC}"
DEFAULT_NOTES="$HOME/Documents/Meeting Notes"
read -rp "  Folder [$DEFAULT_NOTES]: " NOTES_DIR
NOTES_DIR="${NOTES_DIR:-$DEFAULT_NOTES}"
ok "Meeting notes: $NOTES_DIR"

# Reminders list
echo ""
echo "  ${BOLD}Reminders list name for action items?${NC}"
read -rp "  List name [Meeting Actions]: " REMINDERS_LIST
REMINDERS_LIST="${REMINDERS_LIST:-Meeting Actions}"
ok "Reminders list: $REMINDERS_LIST"

# Auto-delete audio
echo ""
echo "  ${BOLD}Auto-delete audio recordings after how many days?${NC}"
echo "  ${DIM}(Transcripts and Word docs are kept. Only the audio wav files are deleted.)${NC}"
echo "  ${DIM}Enter 0 to keep audio forever.${NC}"
read -rp "  Days [30]: " DELETE_DAYS
DELETE_DAYS="${DELETE_DAYS:-30}"
ok "Auto-delete audio after: ${DELETE_DAYS} days"

# Auto-watch
echo ""
echo "  ${BOLD}Start recording automatically when you join a meeting?${NC}"
echo "  ${DIM}nudge will watch for Zoom, Google Meet, and Teams in the background.${NC}"
echo "  ${DIM}Starts at login. You can disable it later: nudge watch uninstall${NC}"
echo ""
read -rp "  Enable auto-detect? [Y/n]: " AUTO_WATCH
AUTO_WATCH="${AUTO_WATCH:-Y}"
if [[ "$AUTO_WATCH" =~ ^[Yy] ]]; then
    INSTALL_WATCH=true
    ok "Auto-detect: enabled (will install at login)"
else
    INSTALL_WATCH=false
    ok "Auto-detect: disabled (use 'nudge start' manually, or enable later with 'nudge watch install')"
fi

# ── Python environment ────────────────────────────────────────────────────────
step "Step 6/7  Python environment"

# Prefer Python 3.11+
PYTHON_BIN=""
for py in python3.13 python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null; then
        PY_VERSION=$("$py" --version 2>&1 | awk '{print $2}')
        MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 10 ]]; then
            PYTHON_BIN="$py"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    warn "Python 3.10+ not found. Installing via Homebrew..."
    brew install python@3.12
    PYTHON_BIN="python3.12"
fi
ok "Python: $PYTHON_BIN ($("$PYTHON_BIN" --version))"

VENV_DIR="$INSTALL_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment at .venv..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
ok "Virtual environment: $VENV_DIR"

PIP="$VENV_DIR/bin/pip"
PYTHON="$VENV_DIR/bin/python"

info "Installing Python dependencies (this may take 2-3 minutes)..."
"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet -r "$INSTALL_DIR/requirements.txt"
ok "Python dependencies installed"

# ── Pull Ollama model ─────────────────────────────────────────────────────────
info "Starting Ollama and pulling $LLM_MODEL..."
ollama serve &>/dev/null &
OLLAMA_PID=$!
sleep 3

ollama pull "$LLM_MODEL"
ok "Ollama model $LLM_MODEL ready"

# Pre-download Whisper model
info "Pre-downloading Whisper model $WHISPER_MODEL (happens once)..."
"$PYTHON" -c "
from faster_whisper import WhisperModel
print('  Downloading Whisper $WHISPER_MODEL...')
WhisperModel('$WHISPER_MODEL', device='auto', compute_type='int8')
print('  Done.')
" || warn "Whisper model download will happen on first use"
ok "Whisper model $WHISPER_MODEL ready"

kill "$OLLAMA_PID" 2>/dev/null || true

# ── Write config ──────────────────────────────────────────────────────────────
step "Step 7/7  Writing configuration"

mkdir -p "$NUDGE_HOME"
cat > "$CONFIG_FILE" <<EOF
# nudge configuration — generated by install.sh
# Edit with: nudge config edit

audio:
  device: "BlackHole 2ch"
  sample_rate: 16000
  chunk_duration: 30
  channels: 1

whisper:
  model: "$WHISPER_MODEL"
  compute_type: "int8"
  vad_filter: true
  language: "en"

ollama:
  model: "$LLM_MODEL"
  host: "http://localhost:11434"
  temperature: 0.1
  max_retries: 3

reminders:
  list_name: "$REMINDERS_LIST"
  min_confidence: 0.6
  include_context: true
  include_source_quote: true

notes:
  output_dir: "$NOTES_DIR"
  date_folders: true

storage:
  data_dir: "~/.nudge/data"
  keep_audio: true
  auto_delete_audio_days: $DELETE_DAYS

display:
  live_transcript: true
  log_level: "INFO"
EOF
ok "Config written to $CONFIG_FILE"

# ── Shell wrapper script ──────────────────────────────────────────────────────
# Use a wrapper script at ~/.local/bin/nudge instead of an alias.
# Aliases break when the install path contains spaces; wrapper scripts do not.
WRAPPER_DIR="$HOME/.local/bin"
WRAPPER="$WRAPPER_DIR/nudge"
mkdir -p "$WRAPPER_DIR"

cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/zsh
exec '$VENV_DIR/bin/python' '$INSTALL_DIR/nudge.py' "\$@"
WRAPPER_EOF
chmod +x "$WRAPPER"
ok "nudge command installed at $WRAPPER"

# Ensure ~/.local/bin is in PATH (add once to shell rc if missing)
SHELL_RC=""
if [[ -f "$HOME/.zshrc" ]]; then
    SHELL_RC="$HOME/.zshrc"
elif [[ -f "$HOME/.bashrc" ]]; then
    SHELL_RC="$HOME/.bashrc"
elif [[ -f "$HOME/.bash_profile" ]]; then
    SHELL_RC="$HOME/.bash_profile"
fi

if [[ -n "$SHELL_RC" ]]; then
    # Remove any old alias lines that may have stale paths
    sed -i '' '/^# nudge — meeting scribe/d' "$SHELL_RC" 2>/dev/null || true
    sed -i '' '/^alias nudge=/d' "$SHELL_RC" 2>/dev/null || true

    if ! grep -q 'local/bin' "$SHELL_RC" 2>/dev/null; then
        echo '' >> "$SHELL_RC"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
        ok "Added ~/.local/bin to PATH in $SHELL_RC"
    fi
fi

# ── App bundle + icon for macOS Login Items ───────────────────────────────────
APP_BUNDLE="$HOME/Applications/nudge.app"
APP_EXECUTABLE="$APP_BUNDLE/Contents/MacOS/nudge-watcher"
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"

info "Building nudge.app for Login Items display..."
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$RESOURCES_DIR"

# Generate app icon (512×512 PNG → icns) using pure Python stdlib
"$PYTHON" - <<'PY_ICON'
import struct, zlib, math, subprocess, os, tempfile, shutil

def make_png(path, size=512):
    """Generate a dark gradient circle with white audio waveform bars."""
    cx = cy = size // 2
    r = size // 2 - 4
    data = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = math.sqrt(dx*dx + dy*dy)
            if dist > r:
                row += bytes([0, 0, 0, 0])
                continue
            # Background: deep indigo → dark navy
            t = dist / r
            bg = (int(38 + 22*t), int(24 + 18*t), int(92 + 28*t), 255)
            # Waveform: 3 bars (left shorter, center tall, right shorter)
            bar_w  = max(int(size * 0.05), 1)
            gap    = int(size * 0.12)
            bh     = [int(size*0.32), int(size*0.52), int(size*0.32)]
            bx     = [cx - gap, cx, cx + gap]
            in_bar = False
            for i in range(3):
                if abs(x - bx[i]) <= bar_w:
                    top = cy - bh[i]//2
                    bot = cy + bh[i]//2
                    # Rounded ends: clip corners
                    cap = bar_w
                    if (y < top + cap and abs(x - bx[i]) + abs(y - (top + cap)) > cap + bar_w) or \
                       (y > bot - cap and abs(x - bx[i]) + abs(y - (bot - cap)) > cap + bar_w):
                        continue
                    if top <= y <= bot:
                        in_bar = True
                        break
            pixel = (255, 255, 255, 255) if in_bar else bg
            row += bytes(pixel)
        data.append(bytes(row))

    def chunk(t, d):
        c = t + d
        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    raw = b''.join(b'\x00' + row for row in data)
    with open(path, 'wb') as f:
        f.write(
            b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 6)) +
            chunk(b'IEND', b'')
        )

# Write source PNG
src = '/tmp/nudge_icon_512.png'
make_png(src)

# Build iconset
iconset = '/tmp/nudge.iconset'
os.makedirs(iconset, exist_ok=True)
sizes = [16, 32, 64, 128, 256, 512]
for s in sizes:
    subprocess.run(['sips', '-z', str(s), str(s), src, '--out', f'{iconset}/icon_{s}x{s}.png'],
                   capture_output=True)
    subprocess.run(['sips', '-z', str(s*2), str(s*2), src, '--out', f'{iconset}/icon_{s}x{s}@2x.png'],
                   capture_output=True)

# Convert to icns
resources = os.path.expanduser('~/Applications/nudge.app/Contents/Resources')
subprocess.run(['iconutil', '-c', 'icns', iconset, '-o', f'{resources}/nudge.icns'],
               capture_output=True)

# Cleanup
shutil.rmtree(iconset, ignore_errors=True)
os.remove(src)
print('  Icon generated.')
PY_ICON

# Info.plist
cat > "$APP_BUNDLE/Contents/Info.plist" <<'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>nudge</string>
    <key>CFBundleDisplayName</key>
    <string>nudge</string>
    <key>CFBundleIdentifier</key>
    <string>com.nudge.watcher</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>nudge-watcher</string>
    <key>CFBundleIconFile</key>
    <string>nudge</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
PLIST_EOF

# Launcher executable
cat > "$APP_EXECUTABLE" <<EXE_EOF
#!/bin/zsh
exec '$VENV_DIR/bin/python' '$INSTALL_DIR/nudge.py' watch run
EXE_EOF
chmod +x "$APP_EXECUTABLE"

# Ad-hoc sign so Gatekeeper allows execution on Apple Silicon
codesign --force --deep --sign - "$APP_BUNDLE" 2>/dev/null
ok "nudge.app built and signed → $APP_BUNDLE"

# ── Install LaunchAgent (auto-watch) ──────────────────────────────────────────
if [[ "$INSTALL_WATCH" == true ]]; then
    PLIST_LABEL="com.nudge.watcher"
    PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
    PLIST_TEMPLATE="$INSTALL_DIR/com.nudge.watcher.plist.template"

    if [[ -f "$PLIST_TEMPLATE" ]]; then
        mkdir -p "$HOME/Library/LaunchAgents"
        sed \
            -e "s|APP_EXECUTABLE_PATH|$APP_EXECUTABLE|g" \
            -e "s|NUDGE_HOME|$NUDGE_HOME|g" \
            -e "s|HOME_PATH|$HOME|g" \
            "$PLIST_TEMPLATE" > "$PLIST_DEST"

        # Unload first in case it's already loaded
        launchctl unload -w "$PLIST_DEST" 2>/dev/null || true
        launchctl load -w "$PLIST_DEST" 2>/dev/null
        ok "Auto-watch login item installed — nudge starts at login"
    else
        warn "Plist template not found — skipping LaunchAgent install"
        warn "You can enable later: nudge watch install"
    fi
fi

# ── Final check ───────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  Setup complete!${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Reload your shell, then verify:"
echo ""
echo "    source $SHELL_RC"
echo "    nudge doctor"
echo ""

if [[ "$INSTALL_WATCH" == true ]]; then
    echo -e "  ${GREEN}${BOLD}Auto-detect is ON.${NC} nudge will start recording automatically"
    echo "  whenever you join a Zoom, Google Meet, or Teams call."
    echo ""
    echo "  To disable:   nudge watch uninstall"
    echo "  To see logs:  nudge watch logs"
else
    echo "  To record manually:"
    echo "    nudge start               # begin recording"
    echo "    nudge start -t \"Standup\" # record with a title"
    echo ""
    echo "  To enable auto-detect later:"
    echo "    nudge watch install"
fi

echo ""
echo "  Other commands:"
echo "    nudge list                # recent sessions"
echo "    nudge search 'keyword'    # search transcripts"
echo "    nudge digest              # weekly summary"
echo "    nudge cleanup             # free disk space"
echo ""
echo "  Config: nudge config edit"
echo "  Help:   nudge --help"
echo ""
echo -e "  ${DIM}Meeting notes: $NOTES_DIR${NC}"
echo -e "  ${DIM}Reminders:     \"$REMINDERS_LIST\"${NC}"
echo ""
