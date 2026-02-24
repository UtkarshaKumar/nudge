"""
Audio device discovery and system health checks.
"""
from __future__ import annotations

import shutil
from typing import Optional

import sounddevice as sd


def list_input_devices() -> list[dict]:
    """Return all available audio input devices with their properties."""
    devices = sd.query_devices()
    return [
        {
            "id": i,
            "name": d["name"],
            "channels": d["max_input_channels"],
            "sample_rate": int(d["default_samplerate"]),
        }
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]


def find_device_id(device_name: str) -> Optional[int]:
    """Return device index for the given name, or None if not found."""
    for device in list_input_devices():
        if device_name.lower() in device["name"].lower():
            return device["id"]
    return None


def is_blackhole_available(device_name: str = "BlackHole 2ch") -> bool:
    """Check if the BlackHole virtual audio device is installed and visible."""
    return find_device_id(device_name) is not None


def check_disk_space(path, required_gb: float = 2.0) -> tuple[bool, float]:
    """
    Check if enough disk space is available at the given path.
    Returns (is_sufficient, available_gb).
    """
    path.mkdir(parents=True, exist_ok=True)
    stats = shutil.disk_usage(str(path))
    available_gb = stats.free / (1024 ** 3)
    return available_gb >= required_gb, available_gb
