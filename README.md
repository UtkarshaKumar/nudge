# nudge

Your silent meeting scribe. Captures audio from your speakers, transcribes it locally, extracts action items, and sends them straight to your macOS Reminders — all without anyone knowing it's running.

Every session also produces a dated Word document saved to your meeting notes folder, so you have a permanent, searchable archive.

**100% on-device. Nothing ever leaves your Mac.**

---

## What it does

1. **Records** meeting audio from your speakers via BlackHole (invisible to other participants)
2. **Transcribes** in real time using Whisper running locally on Apple Silicon
3. **Extracts** action items using a local LLM (Ollama + Llama 3.2)
4. **Adds** action items directly to macOS Reminders
5. **Writes** a Word document to `~/Documents/Meeting Notes/YYYY/MM Month/`

Works with any meeting platform — Zoom, Google Meet, Microsoft Teams, Slack huddles, or anything else playing audio through your speakers.

---

## Requirements

- macOS (Apple Silicon M1/M2/M3/M4 recommended, Intel works)
- [Homebrew](https://brew.sh) (installer will set it up if missing)
- ~4 GB free disk space (for models)
- 8 GB RAM minimum, 16 GB recommended for the larger LLM

---

## Installation

```bash
bash install.sh
```

The installer will:

1. Install **BlackHole 2ch** — the virtual audio driver that routes speaker audio to nudge
2. Walk you through a 1-minute **Audio MIDI Setup** to create a Multi-Output Device
3. Install **Ollama** and pull your chosen LLM model
4. Install **Python dependencies** in an isolated virtual environment
5. Pre-download the **Whisper** transcription model
6. Write your **config** to `~/.nudge/config.yaml`
7. Add a `nudge` **alias** to your shell (`~/.zshrc` or `~/.bashrc`)

### What the installer asks

| Question | Default |
|---|---|
| LLM model | `llama3.2:3b` (fast, 2 GB) |
| Whisper model | `small.en` (500 MB, good accuracy) |
| Meeting notes folder | `~/Documents/Meeting Notes` |
| Reminders list name | `Meeting Actions` |
| Auto-delete audio after N days | 30 days |

After installation, reload your shell:

```bash
source ~/.zshrc   # or ~/.bashrc
nudge doctor      # verify everything is working
```

---

## Usage

### Option A — Automatic (recommended)

nudge can watch for meetings in the background and start recording the moment you join a call — no manual action needed.

```bash
nudge watch install    # run once — installs as a login item
```

After that, nudge runs silently at login. When it detects you've joined a call, it starts recording automatically. When the call ends, it processes everything and sends action items to Reminders.

**Supported platforms for auto-detect:**

| Platform | Detection method |
|---|---|
| Zoom | `CptHost` process — only spawned during active meetings |
| Google Meet | Browser tab URL + title change (Chrome, Edge, Safari) |
| Microsoft Teams | Window title meeting indicators |
| Webex | Process name detection |

```bash
nudge watch status     # check if watcher is installed and running
nudge watch logs       # see what the watcher has been doing
nudge watch uninstall  # turn off auto-detection
```

---

### Option B — Manual

```bash
nudge start
nudge start -t "Daily standup"    # give it a title
nudge start -t "Q1 planning" -q   # quiet mode (no live transcript)
```

Press **Ctrl+C** to stop. nudge automatically processes the recording after you stop.

### What you'll see

While recording:
```
● Recording · Daily standup
  Device: BlackHole 2ch
  Ctrl+C to stop

[00:18] So let's review the Q1 targets first
[01:42] Sarah, can you update the client deck by Friday?
[01:45] Sure, I'll get that done.
[04:12] We should loop in procurement next week
```

After stopping:
```
────────────────────────────────────────────────
Processing: Daily standup

  Transcribing...          ████████████ 100%  0:04
  Extracting action items  ████████████ 100%  0:12
  Analyzing meeting...     ████████████ 100%  0:08
  Adding to Reminders...   ████████████ 100%  0:01
  Writing meeting notes... ████████████ 100%  0:03

  Conf   Task                              Owner    Due
  ─────────────────────────────────────────────────────
  98%    Update the client deck            Sarah    Friday
  81%    Loop in procurement               —        Next week

✓ 2 items added to Reminders → "Meeting Actions"
✓ Meeting notes saved → ~/Documents/Meeting Notes/2026/02 February/2026-02-24 Daily standup.docx
```

---

## All commands

| Command | Description |
|---|---|
| `nudge start [-t "Title"]` | Start recording (Ctrl+C to stop) |
| `nudge list` | List recent sessions |
| `nudge process [id]` | Re-extract actions from a past session |
| `nudge recover` | Process any incomplete sessions from crashed runs |
| `nudge search "keyword"` | Search across all meeting transcripts |
| `nudge digest [--days 7]` | Weekly summary: themes, open actions, wins |
| `nudge cleanup [--days N]` | Delete old audio files to free space |
| `nudge doctor` | Diagnose setup issues |
| `nudge config show` | Print current configuration |
| `nudge config edit` | Open config in `$EDITOR` |

---

## Meeting notes format

Every session generates a Word document:

```
DAILY STANDUP
February 24, 2026 · 2:30 PM  ·  Duration: 00:45:12
Participants: Sarah, John, Alex
Recorded by nudge

SUMMARY
The team reviewed Q1 targets and agreed to push the client
deck deadline to Friday. Procurement alignment was flagged
as a dependency for the roadmap.

KEY DECISIONS
• Release date moved to March 3rd
• New branding guidelines approved for all deliverables

ACTION ITEMS
┌───┬──────────────────────────┬──────────┬──────────┐
│ # │ Task                     │ Owner    │ Due      │
├───┼──────────────────────────┼──────────┼──────────┤
│ 1 │ Update the client deck   │ Sarah    │ Friday   │
│ 2 │ Loop in procurement      │ —        │ Next wk  │
└───┴──────────────────────────┴──────────┴──────────┘

FULL TRANSCRIPT
[00:00] So let's get started with the Q1 review...
...
```

Files are organized as:
```
~/Documents/Meeting Notes/
└── 2026/
    └── 02 February/
        ├── 2026-02-24 Daily standup.docx
        └── 2026-02-24 Q1 planning.docx
```

---

## Configuration

Config file lives at `~/.nudge/config.yaml`. Edit with:

```bash
nudge config edit
```

Key settings:

```yaml
whisper:
  model: "small.en"        # tiny.en | small.en | medium.en | large-v3
  language: "en"           # set to "auto" for multilingual meetings

ollama:
  model: "llama3.2:3b"     # llama3.2:3b | phi3:mini | llama3.1:8b

reminders:
  list_name: "Meeting Actions"
  min_confidence: 0.6      # only add items above this threshold

notes:
  output_dir: "~/Documents/Meeting Notes"

storage:
  auto_delete_audio_days: 30   # 0 = keep forever
```

---

## How audio routing works

nudge uses **BlackHole 2ch**, a free virtual audio driver. You create a macOS **Multi-Output Device** that routes your system audio to two places simultaneously:

1. Your real speakers/headphones → you hear everything normally
2. BlackHole 2ch → nudge captures the digital audio stream

This is completely transparent to meeting participants. No bot joins the call. No recording indicator appears. No API calls to external services.

---

## Privacy

- All audio processing runs on your Mac
- Whisper runs locally (no OpenAI API)
- Ollama runs locally (no cloud LLM)
- Audio is saved to `~/.nudge/data/sessions/` and auto-deleted after N days
- The only external call is to macOS Reminders (via AppleScript, stays on-device)
- No telemetry, no analytics, no network calls

---

## Uninstall

```bash
bash uninstall.sh
```

The uninstaller asks before removing anything. You can choose to:
- Keep your transcripts and meeting notes (only remove the app)
- Delete everything including your session database
- Keep or remove BlackHole and Ollama independently

---

## Troubleshooting

Run `nudge doctor` first — it checks every dependency and tells you exactly what's missing.

**No audio captured / transcript is empty**
→ Check that your system audio output is set to the Multi-Output Device (not just your speakers directly). Go to System Settings → Sound → Output.

**"BlackHole 2ch not found"**
→ Run `brew install blackhole-2ch`, then restart your Mac.

**"Ollama model not found"**
→ Run `ollama pull llama3.2:3b` (or whichever model is in your config).

**Action items look wrong**
→ Try a larger LLM: edit `~/.nudge/config.yaml`, set `ollama.model: llama3.1:8b`, then run `ollama pull llama3.1:8b`.

**Word doc not opening after meeting**
→ Check that Microsoft Word or Pages is installed. The doc is always saved regardless — find it in your meeting notes folder.

---

## Evaluation & Evals

nudge includes an evaluation suite (`src/evals/`) to benchmark the local Llama model against a golden dataset of challenging transcripts (ambiguous deadlines, no-action meetings, etc.).

You can run the evals to test prompts or try new LLMs:
```bash
python -m src.evals.run
```

**Current Baseline (llama3.2:3b @ zero temperature):**
- **Task F1 Score:** 75.0%
- **Task Recall:** 75.0%  
- **Task Precision:** 75.0%
- **Deadline Extract Accuracy:** 100.0%
- **Owner Extract Accuracy:** 83.3%
