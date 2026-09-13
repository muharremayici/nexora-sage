import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path
from tools.core.config import LOGS_DIR
from tools.core.unmanaged_atomic_io import native_filesystem_path

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Set up logger once
logger = logging.getLogger("CodeMaps")
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter(
    fmt='%(asctime)s [%(levelname)s] %(module)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Console diagnostics must not corrupt stdio protocols such as MCP JSON-RPC.
ch = logging.StreamHandler(sys.stderr)
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)

# Ensure output dir exists before file handler is created
Path(native_filesystem_path(LOGS_DIR)).mkdir(parents=True, exist_ok=True)

# Rotating File Handler
# Max 5 MB per file, keep 5 backups (pipeline.log.1, pipeline.log.2, etc.)
fh = RotatingFileHandler(
    native_filesystem_path(LOGS_DIR / "pipeline.log"),
    maxBytes=5 * 1024 * 1024, 
    backupCount=5, 
    encoding="utf-8"
)
fh.setLevel(logging.DEBUG)
fh.setFormatter(formatter)
logger.addHandler(fh)
