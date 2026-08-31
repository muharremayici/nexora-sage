from __future__ import annotations

import sys
from pathlib import Path

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.generate_system_health_check import main


if __name__ == "__main__":
    raise SystemExit(main())
