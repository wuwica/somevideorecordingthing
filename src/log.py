"""Central logging configuration.

Call setup_logging() once at startup (main.py).  Every other module then does:

    import logging
    log = logging.getLogger(__name__)

Set LOG_LEVEL=DEBUG in the environment for verbose output.
"""
import logging
import logging.handlers
import os
from pathlib import Path

_LOG_DIR = Path(__file__).parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "app.log"
_FMT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def setup_logging():
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    _LOG_DIR.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    if root.handlers:
        return

    fmt = logging.Formatter(_FMT, datefmt=_DATE_FMT)

    # Rotating file: 5 MB per file, keep 5 backups
    fh = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Suppress noisy third-party loggers unless debugging
    if level > logging.DEBUG:
        logging.getLogger("uvicorn").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("fastapi").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging started — level=%s file=%s", level_name, _LOG_FILE
    )
