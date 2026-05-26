from __future__ import annotations

import logging
from pathlib import Path
from .settings import get_settings


def setup_logging() -> None:
    s = get_settings()
    s.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = s.logs_dir / 'app.log'

    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(logging.INFO if not s.debug else logging.DEBUG)

    formatter = logging.Formatter(
        fmt='%(asctime)s %(levelname)s %(name)s %(message)s'
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
