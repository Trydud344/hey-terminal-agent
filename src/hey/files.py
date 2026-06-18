from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, content: str, *, mode: int, prefix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=prefix,
        delete=False,
    ) as tmp:
        tmp.write(content)
        temp_path = Path(tmp.name)

    try:
        temp_path.chmod(mode)
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
