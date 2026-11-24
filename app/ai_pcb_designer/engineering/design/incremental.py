"""Incremental changes: keep what did not change between two revisions.

The compiler numbers parts in order, so adding one sensor would renumber everything after
it and the placer would lay the whole board out again. Here parts are matched by what
they are - the functional block they belong to, their symbol, value, footprint, role and
connections - so that

* every unchanged part keeps its reference designator (R12 stays R12 on the new board,
  in the BOM and on the silkscreen), new parts get new numbers, and
* every unchanged block keeps its exact position and orientation when the board size is
  the same; only new or changed blocks are placed into the free space.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..circuit.model import Circuit


def _sig(c) -> tuple:
    return (c.symbol, c.value, c.footprint or "", c.description, tuple(sorted(c.pin_nets.items())))


def _blocks(circuit: Circuit) -> dict[str, list]:
    out: dict[str, list] = {}
    for c in circuit.components:
        out.setdefault(c.block or c.ref, []).append(c)
    return out


def stabilize(new: Circuit, old: Circuit) -> dict:
    """Rename parts of `new` to their designators in `old` where they match; returns the
    set of blocks that are identical in both (same parts, same connections)."""
    ob, nb = _blocks(old), _blocks(new)
    rename: dict[str, str] = {}
    same_blocks = []
    for name, comps in nb.items():
        olds = ob.get(name)
        if not olds:
            continue
        pool = {}
        for c in olds:
            pool.setdefault(_sig(c)[:4], []).append(c.ref)       # match on identity, not wiring
        for c in comps:
            k = _sig(c)[:4]
            if pool.get(k):
                rename[c.ref] = pool[k].pop(0)
        if sorted(map(_sig, comps)) == sorted(map(_sig, olds)):
            same_blocks.append(name)
    taken = set(rename.values())
    counters: dict[str, int] = {}
    for c in old.components + new.components:
        m = re.fullmatch(r"([A-Z]+)(\d+)", c.ref)
        if m:
            counters[m.group(1)] = max(counters.get(m.group(1), 0), int(m.group(2)))
    final: dict[str, str] = {}
    for c in new.components:
        if c.ref in rename:
            final[c.ref] = rename[c.ref]
        else:
            pre = re.match(r"[A-Z]+", c.ref).group(0)
            counters[pre] += 1
            while f"{pre}{counters[pre]}" in taken:
                counters[pre] += 1
            final[c.ref] = f"{pre}{counters[pre]}"
            taken.add(final[c.ref])
    for c in new.components:
        c.ref = final[c.ref]
    new.components.sort(key=lambda c: (re.match(r"[A-Z]+", c.ref).group(0), int(re.search(r"\d+", c.ref).group(0))))
    return {"kept_refs": sum(1 for k, v in final.items() if k in rename), "renamed": len(final),
            "unchanged_blocks": same_blocks}


def previous_positions(rev_dir: Path) -> dict[str, list] | None:
    """Final part positions of a generated revision (its last placement step)."""
    try:
        run = json.loads((Path(rev_dir) / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pos = None
    for st in run.get("steps", []):
        if st.get("name", "").startswith("placement") and st.get("status") == "ok":
            pos = (st.get("detail") or {}).get("positions") or pos
    return pos


def keep_map(new: Circuit, old: Circuit, positions: dict, unchanged_blocks: list[str]) -> dict:
    """{block: {ref: (x, y, rotation)}} for unchanged blocks, when the board outline is the same."""
    if (new.board.width_mm, new.board.height_mm) != (old.board.width_mm, old.board.height_mm):
        return {}
    out = {}
    nb = _blocks(new)
    for b in unchanged_blocks:
        refs = [c.ref for c in nb.get(b, [])]
        if refs and all(r in positions for r in refs):
            out[b] = {r: tuple(positions[r]) for r in refs}
    return out


def load_circuit(rev_dir: Path) -> Circuit | None:
    try:
        return Circuit.model_validate_json((Path(rev_dir) / "circuit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
