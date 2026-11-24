"""Path safety helpers (no traversal outside a root)."""
from __future__ import annotations

from pathlib import Path


class UnsafePath(ValueError):
    pass


def safe_join(root: Path, *parts: str) -> Path:
    root = Path(root).resolve()
    p = root.joinpath(*parts).resolve()
    if p != root and root not in p.parents:
        raise UnsafePath(f"path escapes {root}: {'/'.join(parts)}")
    return p
