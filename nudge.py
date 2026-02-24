#!/usr/bin/env python3
"""
nudge — your silent meeting scribe.

Entry point. Adds the project directory to sys.path so the src
package resolves correctly whether run directly or via an alias.

Usage:
  python nudge.py start
  python nudge.py list
  python nudge.py doctor
"""
import sys
from pathlib import Path

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from src.cli.app import app

if __name__ == "__main__":
    app()
