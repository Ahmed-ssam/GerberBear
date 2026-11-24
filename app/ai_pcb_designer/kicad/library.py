"""Resolve symbols and footprints from the installed KiCad libraries.

Everything returned here is read from real library files. Pins come from the
symbol definition, pads from the footprint; `verify_pin_pad_mapping` checks
they agree so a component can never be wired to a pad that does not exist.
"""
from __future__ import annotations

import re

import copy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import sexpr
from .sexpr import Sym, child, children


class LibraryError(LookupError):
    pass


@dataclass
class SymbolPin:
    number: str
    name: str
    etype: str          # power_in, passive, bidirectional, ...
    x: float            # library coords (Y up), connection point
    y: float
    angle: float        # direction from connection point toward the body
    length: float
    unit: int


@dataclass
class ResolvedSymbol:
    lib_id: str
    node: list          # flattened symbol s-expr, renamed to full lib_id
    pins: list[SymbolPin]
    properties: dict[str, str]
    is_power: bool
    units: int
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)  # minx,miny,maxx,maxy lib coords

    def pin(self, number: str) -> SymbolPin:
        for p in self.pins:
            if p.number == number:
                return p
        raise LibraryError(f"{self.lib_id} has no pin {number!r}")


@dataclass
class ResolvedFootprint:
    lib_id: str
    path: Path
    pads: set[str] = field(default_factory=set)
    has_3d_model: bool = False


class LibraryManager:
    def __init__(self, symbol_dir: str, footprint_dir: str, models3d_dir: str | None = None):
        self.symbol_dir = Path(symbol_dir)
        self.footprint_dir = Path(footprint_dir)
        self.models3d_dir = Path(models3d_dir) if models3d_dir else None

    # -------------------------------------------------------------- symbols
    @lru_cache(maxsize=64)
    def _symbol_lib(self, lib: str) -> dict[str, list]:
        path = self.symbol_dir / f"{lib}.kicad_sym"
        if not path.exists():
            raise LibraryError(f"Symbol library not found: {lib}")
        root = sexpr.parse(path.read_text(encoding="utf-8"))
        return {str(s[1]): s for s in children(root, "symbol")}

    def resolve_symbol(self, lib_id: str) -> ResolvedSymbol:
        lib, _, name = lib_id.partition(":")
        syms = self._symbol_lib(lib)
        if name not in syms:
            raise LibraryError(f"Symbol {name!r} not in library {lib}")
        node = copy.deepcopy(syms[name])
        ext = child(node, "extends")
        if ext is not None:
            parent = copy.deepcopy(syms[str(ext[1])])
            parent_name = str(ext[1])
            # child's properties override parent's; graphics/pins come from parent
            child_props = {str(p[1]): p for p in children(node, "property")}
            flat = [Sym("symbol"), name]
            for item in parent[2:]:
                if sexpr.tag(item) == "property" and str(item[1]) in child_props:
                    flat.append(child_props.pop(str(item[1])))
                elif sexpr.tag(item) == "symbol":
                    sub = copy.deepcopy(item)
                    sub[1] = str(sub[1]).replace(parent_name, name, 1)
                    flat.append(sub)
                else:
                    flat.append(item)
            # properties only present on child go before the unit sub-symbols
            first_sub = next(i for i, it in enumerate(flat) if sexpr.tag(it) == "symbol" and i > 0)
            for p in child_props.values():
                flat.insert(first_sub, p)
                first_sub += 1
            node = flat
        props = {str(p[1]): str(p[2]) for p in children(node, "property")}
        pins: list[SymbolPin] = []
        xs, ys = [], []
        for sub in children(node, "symbol"):
            unit = int(str(sub[1]).rsplit("_", 2)[-2])
            for g in sub[2:]:
                pts = child(g, "pts") if isinstance(g, list) else None
                for xy in (children(pts, "xy") if pts else []):
                    xs.append(float(xy[1])); ys.append(float(xy[2]))
                for a in ("start", "end", "at", "center"):
                    pt = child(g, a) if isinstance(g, list) else None
                    if pt is not None and sexpr.tag(g) != "pin":
                        xs.append(float(pt[1])); ys.append(float(pt[2]))
            for p in children(sub, "pin"):
                at = child(p, "at")
                pins.append(SymbolPin(
                    number=str(child(p, "number")[1]), name=str(child(p, "name")[1]),
                    etype=str(p[1]), x=float(at[1]), y=float(at[2]),
                    angle=float(at[3]) if len(at) > 3 else 0.0,
                    length=float(child(p, "length")[1]), unit=unit))
                xs.append(float(at[1])); ys.append(float(at[2]))
        units = max([p.unit for p in pins] or [1]) or 1
        node[1] = lib_id  # embedded lib_symbols use the full lib id
        bbox = (min(xs, default=0), min(ys, default=0), max(xs, default=0), max(ys, default=0))
        return ResolvedSymbol(lib_id, node, pins, props, child(node, "power") is not None,
                              units, bbox)

    # ------------------------------------------------------------ footprints
    def resolve_footprint(self, lib_id: str) -> ResolvedFootprint:
        lib, _, name = lib_id.partition(":")
        path = self.footprint_dir / f"{lib}.pretty" / f"{name}.kicad_mod"
        if not path.exists():
            raise LibraryError(f"Footprint not found: {lib_id}")
        root = sexpr.parse(path.read_text(encoding="utf-8"))
        pads = {str(p[1]) for p in children(root, "pad") if str(p[1]) != ""}
        has3d = False
        for m in children(root, "model"):
            rel = re.sub(r"\$\{KICAD\d+_3DMODEL_DIR\}", "", str(m[1])).lstrip("/\\")
            if self.models3d_dir and (self.models3d_dir / rel).exists():
                has3d = True
        return ResolvedFootprint(lib_id, path, pads, has3d)

    def footprint_lib_path(self, lib_id: str) -> tuple[str, str]:
        lib, _, name = lib_id.partition(":")
        return str(self.footprint_dir / f"{lib}.pretty"), name


def verify_pin_pad_mapping(sym: ResolvedSymbol, fp: ResolvedFootprint) -> list[str]:
    """Return a list of problems; empty means every symbol pin has a pad."""
    problems = []
    pin_numbers = {p.number for p in sym.pins}
    for n in sorted(pin_numbers - fp.pads):
        problems.append(f"{sym.lib_id} pin {n} has no pad in {fp.lib_id}")
    return problems
