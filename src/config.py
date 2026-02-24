"""
Layered configuration for nudge.
Priority: defaults → ~/.nudge/config.yaml → environment variables
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

CONFIG_DIR = Path.home() / ".nudge"
CONFIG_FILE = CONFIG_DIR / "config.yaml"


class AudioConfig(BaseModel):
    device: str = "BlackHole 2ch"
    sample_rate: int = 16000
    chunk_duration: int = 30
    channels: int = 1


class WhisperConfig(BaseModel):
    model: str = "small.en"
    compute_type: str = "int8"
    vad_filter: bool = True
    language: str = "en"


class OllamaConfig(BaseModel):
    model: str = "llama3.2:3b"
    host: str = "http://localhost:11434"
    temperature: float = 0.1
    max_retries: int = 3


class RemindersConfig(BaseModel):
    list_name: str = "Meeting Actions"
    min_confidence: float = 0.6
    include_context: bool = True
    include_source_quote: bool = True


class NotesConfig(BaseModel):
    output_dir: str = "~/Documents/Meeting Notes"
    date_folders: bool = True

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir).expanduser()


class StorageConfig(BaseModel):
    data_dir: str = "~/.nudge/data"
    keep_audio: bool = True
    auto_delete_audio_days: int = 30

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def sessions_path(self) -> Path:
        return self.data_path / "sessions"

    @property
    def db_path(self) -> Path:
        return self.data_path / "nudge.db"


class DisplayConfig(BaseModel):
    live_transcript: bool = True
    log_level: str = "INFO"


class Config(BaseModel):
    audio: AudioConfig = Field(default_factory=AudioConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    reminders: RemindersConfig = Field(default_factory=RemindersConfig)
    notes: NotesConfig = Field(default_factory=NotesConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)

    # Convenience proxy
    @property
    def data_path(self) -> Path:
        return self.storage.data_path

    @property
    def sessions_path(self) -> Path:
        return self.storage.sessions_path


def load_config() -> Config:
    """Load config from file with safe defaults for any missing key."""
    raw: dict = {}

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                raw = loaded

    # Environment variable overrides (NUDGE_SECTION_KEY format)
    _apply_env_overrides(raw)

    return Config(**raw)


def _apply_env_overrides(raw: dict) -> None:
    mappings = {
        "NUDGE_WHISPER_MODEL": ("whisper", "model"),
        "NUDGE_OLLAMA_MODEL": ("ollama", "model"),
        "NUDGE_OLLAMA_HOST": ("ollama", "host"),
        "NUDGE_REMINDERS_LIST": ("reminders", "list_name"),
        "NUDGE_NOTES_DIR": ("notes", "output_dir"),
        "NUDGE_DATA_DIR": ("storage", "data_dir"),
        "NUDGE_LOG_LEVEL": ("display", "log_level"),
    }
    for env_key, (section, key) in mappings.items():
        val = os.getenv(env_key)
        if val:
            raw.setdefault(section, {})[key] = val
