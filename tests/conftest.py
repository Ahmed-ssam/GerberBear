import sys
from pathlib import Path

# the application package lives in app/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
