"""
Versioned LLM prompts for action extraction and meeting analysis.
Keep versions so existing sessions can be reprocessed with the same prompt.
"""

# ── Action item extraction ─────────────────────────────────────────────────────

ACTION_EXTRACTION_V1 = """You are an expert meeting analyst. Extract action items from this meeting transcript.

An action item is a CLEAR commitment or task that someone explicitly agreed to do.

Return ONLY a valid JSON array — no explanation, no markdown fences.
Each element must be:
{{
  "task": "verb-first description (e.g. Update the roadmap, Send recap email)",
  "assignee": "person's name, or null if truly unclear",
  "deadline": "natural language deadline as stated, or null if not mentioned",
  "context": "one sentence explaining why this task exists",
  "confidence": 0.0,
  "source_quote": "the exact words that indicate this commitment"
}}

Confidence guide:
  0.9–1.0  Person explicitly committed: "Yes, I'll do that by Friday"
  0.7–0.9  Strong implication: "Can you handle the client email?" + affirmative
  0.5–0.7  Weak implication: vague ownership, no clear acceptance

Rules:
- Only include items with confidence >= 0.5
- Do NOT invent tasks. Ground every item in the transcript.
- If no action items exist, return: []
- Start every task with a verb

Transcript:
{transcript}

JSON array:"""


# ── Meeting summary and decisions ─────────────────────────────────────────────

MEETING_ANALYSIS_V1 = """Analyze this meeting transcript and return JSON with these fields:

{{
  "title": "short, descriptive meeting title (max 8 words)",
  "summary": "2-3 sentence plain-English summary of what was discussed",
  "decisions": ["list of key decisions that were made"],
  "participants": ["names of people who spoke"],
  "topics": ["main topics covered, 1-5 words each"]
}}

Rules:
- Return ONLY valid JSON, no other text
- Keep summary concise — it appears in the Word document header
- Decisions are things the group agreed on (not tasks)
- If a field has no data, use an empty list or empty string

Transcript:
{transcript}

JSON:"""


# ── Weekly digest ─────────────────────────────────────────────────────────────

WEEKLY_DIGEST_V1 = """You are summarizing a person's meeting week. Here are their meeting notes:

{meeting_summaries}

Write a weekly digest with:
{{
  "week_summary": "2-3 sentences: what did this person focus on this week?",
  "key_themes": ["2-4 recurring themes across meetings"],
  "critical_actions": ["top 3 most important uncompleted action items"],
  "wins": ["things that got resolved or completed this week"]
}}

Return ONLY valid JSON.

JSON:"""


# Current versions (use these in production code)
CURRENT_ACTIONS_PROMPT = ACTION_EXTRACTION_V1
CURRENT_ANALYSIS_PROMPT = MEETING_ANALYSIS_V1
CURRENT_DIGEST_PROMPT = WEEKLY_DIGEST_V1
