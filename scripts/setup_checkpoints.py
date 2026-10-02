"""Initialize LangGraph's versioned tables in app_state with app credentials."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings
from app.query_checkpoint import postgres_checkpointer

if __name__ == "__main__":
    with postgres_checkpointer(get_settings(), setup=True):
        print("Checkpoint tables ready in app_state; no analytics credentials used.")
