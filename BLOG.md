# I Built a Tool That Listens to My Meetings So I Don't Have To

*And it runs entirely on my Mac. No cloud. No subscriptions. No bots.*

---

Here's a scenario I lived through every week for two years.

A 45-minute call ends. There were three clear action items. Someone said "I'll follow up by Friday." Someone else said "let me loop in the team on that." You said "sure, I'll get that doc updated." By the time you open Notion to write it down, you've already taken the next call, answered two Slack messages, and you can only remember one of them.

The Friday comes. Nobody followed up. The doc didn't get updated. The project slips a week.

This isn't a discipline problem. It's a physics problem. You cannot fully participate in a meeting and take perfect notes at the same time.

---

## The existing tools all have the same problem

Otter, Fireflies, Granola, Notion AI meeting recorder — they're all useful, but they all require one of three things you might not be willing to give:

1. **Your audio leaving your machine.** Every word, every client name, every sensitive strategy discussion — uploaded to someone's cloud.
2. **A bot joining your meeting.** Everyone sees "Otter.ai has joined." Some hosts disable bots. Some clients ask uncomfortable questions.
3. **A monthly subscription** for a problem that should be solved once.

I didn't want any of that. So I built **nudge**.

---

## What nudge does

nudge is a free, open-source meeting scribe that runs entirely on your Mac. No cloud APIs. No recording bots. No subscriptions. Your audio never leaves your machine.

Here's the full flow, automatically, for every meeting you join:

1. **You join a Zoom, Google Meet, or Teams call.** nudge detects it and starts recording — silently, invisibly, without joining the call.
2. **While the meeting runs**, nudge transcribes the audio in real time using Whisper running locally on your Mac.
3. **When the call ends**, nudge extracts every action item using a local LLM (Ollama + Llama 3.2), assigns owners, parses deadlines like "end of week" into actual dates, and scores each item by confidence.
4. **Action items land in your macOS Reminders** automatically, inside a list called "Meeting Actions." No review needed. They're just there.
5. **A Word document appears** in `~/Documents/Meeting Notes/2026/02 February/` — with a summary, key decisions, action items table, and full transcript — before you've closed the meeting window.

That's it. You talk. You listen. nudge handles the rest.

---

## The part that makes it different

> **Everything runs on your Mac. Locally. Offline.**

- Transcription: [Whisper](https://github.com/openai/whisper) by OpenAI, running on your Apple Silicon chip via Metal acceleration
- Action extraction: [Ollama](https://ollama.ai) with Llama 3.2 (a 2 GB model that runs entirely in RAM)
- Meeting notes: `python-docx` generates the Word file on your machine
- Reminders: AppleScript — talks directly to your macOS Reminders app

No API keys. No internet required during meetings. If you're on a flight, it still works.

---

## The meeting notes you never wrote

One thing I added that turned out to be more valuable than expected: **every session generates a dated Word document.**

```
~/Documents/Meeting Notes/
└── 2026/
    └── 02 February/
        ├── 2026-02-24 Daily Standup.docx
        └── 2026-02-25 Q1 Planning.docx
```

You know that moment three months later when someone asks "wait, who agreed to own that?" and nobody can remember? Now you open Finder, search for the date, and it's there. Summary, decisions, full transcript, action items. Every meeting, automatically, forever.

---

## Nudge in one screenshot

```
● Recording · Q1 Planning
  Device: BlackHole 2ch  ·  Ctrl+C to stop

[00:18] Let's review the roadmap before we get into the weeds
[01:42] Sarah, can you get the client deck ready for the Thursday call?
[01:45] Yeah, I can do that by Wednesday evening
[04:12] We need someone to loop in procurement this week
[07:33] John, are you okay to own the budget sign-off?
[07:35] Sure, I'll get it done by Friday

────────────────────────────────────────────────
Processing: Q1 Planning  ·  00:45:12

  Transcribing...          ████████████ 100%
  Extracting action items  ████████████ 100%
  Analyzing meeting...     ████████████ 100%
  Adding to Reminders...   ████████████ 100%
  Writing meeting notes... ████████████ 100%

  Conf   Task                              Owner    Due
  ──────────────────────────────────────────────────────
  98%    Prepare client deck for Thursday  Sarah    Wed
  92%    Get budget sign-off               John     Fri
  74%    Loop in procurement               —        This wk

✓ 3 items added to Reminders → "Meeting Actions"
✓ Notes saved → Meeting Notes/2026/02 February/2026-02-24 Q1 Planning.docx
```

---

## How the audio routing works (the clever part)

You might be wondering: *how does it capture meeting audio without joining the call?*

It uses **BlackHole** — a free virtual audio driver for macOS. During installation, you create a "Multi-Output Device" in macOS that routes your system audio to two places at once:

- Your speakers/headphones (so you hear everything normally)
- BlackHole (so nudge can read the digital audio stream)

From your meeting participants' perspective, nothing has changed. No bot. No recording notification. Just you, listening.

---

## It's free. It's open source. One command to install.

```bash
git clone https://github.com/UtkarshaKumar/nudge
cd nudge
bash install.sh
```

The installer walks you through everything in about 5 minutes — BlackHole setup, model downloads, audio routing — and asks 5 questions. After that, `nudge watch install` makes it start automatically every time you log in.

**GitHub:** [github.com/UtkarshaKumar/nudge](https://github.com/UtkarshaKumar/nudge)

---

## What's next

nudge is a v1. It works. I use it daily. There are things I want to add:

- **Speaker diarization** — identify who said what (currently transcribes without speaker labels)
- **Jira integration** — one-click ticket creation from action items (hook is already in the code)
- **Slack summary** — post a meeting recap to a channel after the call ends
- **Windows support** — the architecture supports it, needs testing

If you use it and something breaks, open an issue. If you want to contribute, PRs are very welcome.

---

*Built with Python, faster-whisper, Ollama, python-docx, BlackHole, and too many back-to-back meetings.*
