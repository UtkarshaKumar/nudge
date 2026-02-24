# nudge — AI context

## What this is
`nudge` is a privacy-first meeting scribe for macOS. It captures meeting audio silently via BlackHole virtual audio routing, transcribes locally with Whisper, extracts action items with a local LLM (Ollama), pushes them to macOS Reminders, and generates a Word document meeting note — all 100% on-device.

## Key facts for AI assistance
- **Language**: Python 3.11+
- **Entry point**: `nudge.py` → `src/cli/app.py`
- **Audio**: `sounddevice` reads from BlackHole 2ch; saved as 30s WAV chunks
- **Transcription**: `faster-whisper` with VAD filter; model loaded once per session
- **LLM**: `ollama` Python SDK; `ActionExtractor` and `MeetingAnalyzer` in `src/extraction/ollama_client.py`
- **Reminders**: AppleScript via `subprocess` in `src/integrations/reminders.py`
- **Word docs**: `python-docx` in `src/integrations/word_notes.py`
- **Storage**: SQLite at `~/.nudge/data/nudge.db`; audio chunks at `~/.nudge/data/sessions/<id>/`
- **Config**: `~/.nudge/config.yaml` — loaded by `src/config.py`

## Directory structure
```
src/
  audio/        capture.py (3-thread model), devices.py
  transcription/ whisper_engine.py
  extraction/   ollama_client.py, prompts.py, dedup.py
  integrations/ reminders.py (AppleScript), word_notes.py (python-docx)
  storage/      db.py (SQLite), models.py (dataclasses)
  cli/          app.py (typer commands), display.py (rich output)
  config.py     layered config (yaml → env vars)
```

## Threading model (critical)
sounddevice callback → audio_queue → CollectionThread (saves WAV chunks)
                                   → on_chunk_saved callback → TranscriptionQueue
                                                             → TranscriptionThread (single, Whisper not thread-safe)

Never call Whisper from multiple threads simultaneously.

## Commands
```
nudge start [-t "Title"]   # record (blocks, Ctrl+C to stop + process)
nudge list                 # recent sessions
nudge process [session-id] # re-process a session
nudge recover              # process incomplete sessions
nudge search "query"       # search transcripts
nudge digest [--days 7]    # weekly summary
nudge cleanup [--days N]   # delete old audio
nudge doctor               # check all dependencies
nudge config show/edit     # configuration
```

## Adding new LLM prompts
Add versioned prompts to `src/extraction/prompts.py`. Update `CURRENT_*` constants.

## Adding new integrations
Create a new module in `src/integrations/`. Register it in `_process_session()` in `src/cli/app.py`.

## Known constraints
- macOS only (AppleScript, CoreAudio, macOS Reminders)
- Requires BlackHole 2ch to be installed AND a Multi-Output Device configured
- Whisper model loaded on first chunk — first 30s of transcript delayed
- Ollama must be running (auto-started if not)
