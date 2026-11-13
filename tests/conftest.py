import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
# The built-in theme, whatever desktop palette the machine running the tests has.
os.environ["WORKSHEET_PALETTE"] = os.devnull
