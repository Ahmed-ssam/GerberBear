"""Programmatic circuit construction with automatic reference designators.

Blocks (see blocks.py) are plain functions that call `part()` inside a
`with b.block(...)` context, so a 200-part design is a few dozen lines and
every part carries its block/sheet for schematic grouping and placement.
"""
from __future__ import annotations

from contextlib import contextmanager

from .model import BoardSpec, Circuit, Component, DesignRules, Net, NetKind

# Footprint shorthands (all verified to exist in KiCad 7 standard libraries)
FP = {
    "R0603": "Resistor_SMD:R_0603_1608Metric",
    "C0603": "Capacitor_SMD:C_0603_1608Metric",
    "C0805": "Capacitor_SMD:C_0805_2012Metric",
    "C1206": "Capacitor_SMD:C_1206_3216Metric",
    "LED0603": "LED_SMD:LED_0603_1608Metric",
    "CP6.3": "Capacitor_SMD:CP_Elec_6.3x7.7",
    "SMA": "Diode_SMD:D_SMA",
    "F1206": "Fuse:Fuse_1206_3216Metric",
    "TERM2": "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
    "JACK": "Connector_BarrelJack:BarrelJack_Horizontal",
    "BTN": "Button_Switch_SMD:SW_SPST_TL3342",
    "L6045": "Inductor_SMD:L_Bourns_SRN6045TA",
    "HDR1x4": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
    "HDR1x6": "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
    "TO92": "Package_TO_SOT_THT:TO-92_Inline",
}

PREFIX = {"Device:R": "R", "Device:C": "C", "Device:C_Polarized": "C", "Device:LED": "D",
          "Device:L": "L", "Device:Polyfuse": "F", "Switch:SW_Push": "SW"}


class CircuitBuilder:
    def __init__(self, name: str, title: str, board: BoardSpec, rules: DesignRules | None = None):
        self.name, self.title, self.board = name, title, board
        self.rules = rules or DesignRules()
        self._nets: dict[str, Net] = {}
        self._comps: list[Component] = []
        self._counters: dict[str, int] = {}
        self._block: str | None = None
        self._sheet: str | None = None

    # ---- nets -------------------------------------------------------------
    def power(self, name: str, symbol: str | None = None, width: float | None = None) -> str:
        kind = NetKind.ground if name.upper() in ("GND", "GNDA", "GNDPWR") else NetKind.power
        self._nets.setdefault(name, Net(name=name, kind=kind, power_symbol=symbol or f"power:{name}",
                                        track_width_mm=width))
        return name

    def net(self, name: str, width: float | None = None) -> str:
        if name not in self._nets:
            self._nets[name] = Net(name=name, track_width_mm=width)
        return name

    # ---- context ----------------------------------------------------------
    @contextmanager
    def block(self, name: str, sheet: str | None = None):
        prev = (self._block, self._sheet)
        self._block = name
        if sheet is not None:
            self._sheet = sheet
        try:
            yield self
        finally:
            self._block, self._sheet = prev

    @contextmanager
    def sheet(self, name: str):
        prev = self._sheet
        self._sheet = name
        try:
            yield self
        finally:
            self._sheet = prev

    # ---- parts ------------------------------------------------------------
    def part(self, symbol: str, value: str, pins: dict[str, str], footprint: str | None = None,
             prefix: str | None = None, description: str = "", **kw) -> str:
        pre = prefix or PREFIX.get(symbol) or ("U" if not symbol.startswith("Connector") else "J")
        self._counters[pre] = self._counters.get(pre, 0) + 1
        ref = f"{pre}{self._counters[pre]}"
        for net in pins.values():
            if net not in self._nets:
                self.net(net)
        fp = FP.get(footprint, footprint) if footprint else None
        self._comps.append(Component(ref=ref, symbol=symbol, footprint=fp, value=value,
                                     description=description, pin_nets=dict(pins),
                                     block=kw.pop("block", self._block),
                                     sheet=kw.pop("sheet", self._sheet), **kw))
        return ref

    # common passives
    def R(self, value, a, b, fp="R0603", **kw):
        return self.part("Device:R", value, {"1": a, "2": b}, fp, **kw)

    def C(self, value, a, b, fp="C0603", **kw):
        return self.part("Device:C", value, {"1": a, "2": b}, fp, **kw)

    def LED(self, color, anode, cathode, fp="LED0603", **kw):
        return self.part("Device:LED", color, {"2": anode, "1": cathode}, fp, **kw)

    def build(self) -> Circuit:
        return Circuit(name=self.name, title=self.title, components=self._comps,
                       nets=list(self._nets.values()), board=self.board, rules=self.rules)
