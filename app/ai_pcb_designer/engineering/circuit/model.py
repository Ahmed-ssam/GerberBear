"""Structured circuit representation: the single source the generators read.

The AI produces/edits this model; deterministic generators turn it into KiCad
files. Validation here catches structural errors before any file is written.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class VerificationStatus(str, Enum):
    verified_library = "verified_library"      # symbol+footprint exist, pins match pads
    datasheet = "datasheet"
    ai_suggested = "ai_suggested"
    unverified = "unverified"


class NetKind(str, Enum):
    signal = "signal"
    power = "power"
    ground = "ground"


class Net(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_+\-./]+$")
    kind: NetKind = NetKind.signal
    power_symbol: str | None = None             # e.g. "power:+5V"; required for power/ground
    track_width_mm: float | None = None

    @model_validator(mode="after")
    def _power_symbol(self):
        if self.kind != NetKind.signal and not self.power_symbol:
            raise ValueError(f"net {self.name}: power/ground nets need power_symbol")
        return self


class Component(BaseModel):
    ref: str = Field(pattern=r"^[A-Z]+[0-9]+$")
    symbol: str                                  # "Lib:Name"
    footprint: str | None = None                 # None -> use symbol's default
    value: str
    description: str = ""
    datasheet: str | None = None
    pin_nets: dict[str, str] = Field(default_factory=dict)   # pin number -> net name
    group: str | None = None                    # placement hint ("power", "mcu", "led")
    block: str | None = None                    # functional block instance, e.g. "ch07"
    sheet: str | None = None                    # schematic sheet name (None = root)
    edge: Literal["left", "right", "top", "bottom"] | None = None   # keep on this board edge
    anchor: bool = False                        # block's reference part for local placement
    manufacturer: str | None = None             # sourcing; never invented by the app
    mpn: str | None = None                      # manufacturer part number
    lcsc: str | None = Field(default=None, pattern=r"^C[0-9]{1,9}$")   # JLCPCB/LCSC part, e.g. "C25804"
    dnp: bool = False                           # do not populate (kept on the board, left off the BOM/CPL)
    erc_waive: dict[str, str] = Field(default_factory=dict)  # pin -> why the datasheet wiring trips ERC
    verification_status: VerificationStatus = VerificationStatus.unverified


class ZoneSpec(BaseModel):
    net: str
    layer: str                                   # "F.Cu", "In1.Cu", ...
    plane: bool = False                          # inner power plane: present before routing


class MountingHole(BaseModel):
    x_mm: float
    y_mm: float
    footprint: str = "MountingHole:MountingHole_3.2mm_M3"


class BoardSpec(BaseModel):
    width_mm: float = Field(gt=5, le=500)
    height_mm: float = Field(gt=5, le=500)
    layers: Literal[2, 4] = 2
    thickness_mm: float = 1.6
    mounting_holes: list[MountingHole] = Field(default_factory=list)
    ground_pour: bool = True
    zones: list[ZoneSpec] = Field(default_factory=list)    # explicit pours/planes (overrides ground_pour)
    auto_size: bool = False                                 # size chosen by the tool: may grow if routing needs room


class DesignRules(BaseModel):
    clearance_mm: float = 0.2
    track_width_mm: float = 0.25
    power_track_width_mm: float = 0.5
    via_diameter_mm: float = 0.8
    via_drill_mm: float = 0.4
    min_drill_mm: float = 0.2          # some library footprints (e.g. ESP32 thermal vias) need 0.2 mm
    edge_clearance_mm: float = 0.5


class Circuit(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    title: str = ""
    revision: str = "A"
    components: list[Component]
    nets: list[Net]
    board: BoardSpec
    rules: DesignRules = Field(default_factory=DesignRules)

    @model_validator(mode="after")
    def _consistency(self):
        refs = [c.ref for c in self.components]
        dup = {r for r in refs if refs.count(r) > 1}
        if dup:
            raise ValueError(f"duplicate references: {sorted(dup)}")
        names = {n.name for n in self.nets}
        for c in self.components:
            for pin, net in c.pin_nets.items():
                if net not in names:
                    raise ValueError(f"{c.ref} pin {pin} uses undefined net {net!r}")
        return self

    def net(self, name: str) -> Net:
        return next(n for n in self.nets if n.name == name)

    def connections(self) -> dict[str, set[tuple[str, str]]]:
        out: dict[str, set[tuple[str, str]]] = {n.name: set() for n in self.nets}
        for c in self.components:
            for pin, net in c.pin_nets.items():
                out[net].add((c.ref, pin))
        return out

    def warnings(self) -> list[str]:
        w = []
        for net, nodes in self.connections().items():
            if len(nodes) < 2:
                w.append(f"net {net} has {len(nodes)} connection(s)")
        return w
