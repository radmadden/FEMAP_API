"""Where the GUI remembers its last-used form. Kept out of gui.py so setup_env.py can
pre-fill it without importing tkinter."""
from pathlib import Path

LAST = Path.home() / ".fem_checks_last.json"
