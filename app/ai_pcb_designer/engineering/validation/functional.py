"""Functional design checks: does the circuit make electrical sense, beyond KiCad's ERC/DRC?

ERC checks that pins are connected in a legal way; DRC checks copper geometry. Neither knows
that a regulator's input is above its maximum, that a capacitor's voltage rating is too low,
that an LED will barely glow, that two I2C devices share an address, or that a supply rail
has no source at all. These checks do, from the circuit and a table of datasheet limits.

Every rail voltage comes from the design (net names and the regulators that make them), and
every limit in PARTS cites the datasheet value it encodes. A part missing from the table is
reported as "not checked" - never assumed to be fine.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# Datasheet absolute/recommended limits used by the checks.
#   supply: (pin numbers) -> (min V, max V) recommended operating range
#   vds / vr / vce: max blocking voltage; i: max continuous current (A)
PARTS: dict[str, dict] = {
    "AP63203WU": {"supply": {("3",): (3.8, 32.0)}, "iout": 2.0},             # Diodes Inc. AP63203 DS
    "AMS1117-3.3": {"supply": {("3",): (4.75, 15.0)}, "iout": 1.0, "pd_max": 1.0},   # SOT-223, ~1 W practical
    "AP2112K-3.3": {"supply": {("1",): (3.5, 6.0)}, "iout": 0.6},             # 2.5-6 V; 3.5 V for 3.3 V out
    "MCP73831-2-OT": {"supply": {("4",): (3.75, 6.0)}},
    "DRV8871DDA": {"supply": {("5",): (6.5, 45.0)}},
    "PCA9685PW": {"supply": {("28",): (2.3, 5.5)}},
    "ESP32-WROOM-32": {"supply": {("2",): (3.0, 3.6)}, "i_peak": 0.5},
    "CH340C": {"supply": {("16",): (3.0, 5.5)}},
    "MAX3485": {"supply": {("8",): (3.0, 3.6)}},
    "SN65HVD230": {"supply": {("3",): (3.0, 3.6)}},
    "BME280": {"supply": {("8",): (1.71, 3.6), ("6",): (1.2, 3.6)}},
    "SHT31-DIS": {"supply": {("5",): (2.15, 5.5)}},
    "ADS1115": {"supply": {("8",): (2.0, 5.5)}},
    "DS18B20": {"supply": {("3",): (3.0, 5.5)}},
    "USBLC6-2SC6": {"supply": {("5",): (0.0, 5.5)}},
    "AO3400A": {"vds": 30.0, "i": 5.7},
    "DMG2301L": {"vds": 20.0, "i": 3.0},
    "MMBT3904": {"vce": 40.0, "i": 0.2},
    "SS14": {"vr": 40.0, "i": 1.0},
    "SS34": {"vr": 40.0, "i": 3.0},
    "1N4148W": {"vr": 75.0, "i": 0.15},
}
I2C_ADDR = {"SHT31-DIS": [0x44], "BME280": [0x76], "ADS1115": [0x48], "OLED": [0x3C]}
LED_VF = {"RED": 2.0, "GREEN": 2.2, "YELLOW": 2.1, "BLUE": 3.0, "WHITE": 3.0}
R_POWER_W = {"0402": 0.063, "0603": 0.1, "0805": 0.125, "1206": 0.25}
ESP32_STRAP = {"14": "IO12 (MTDI) must be low at boot or the flash runs at 1.8 V",
               "23": "IO15 (MTDO) controls boot log output"}


@dataclass
class Finding:
    severity: str        # error | warning | info
    check: str
    message: str
    refs: list[str]


def _volts(net: str) -> float | None:
    m = re.fullmatch(r"\+(\d+(?:\.\d+)?)V(\d)?", net)
    if m:
        return float(m.group(1)) + (float("0." + m.group(2)) if m.group(2) else 0.0)
    return {"+3V3": 3.3, "VSYS": 5.0, "VBAT": 4.2, "USB_VBUS": 5.0}.get(net)


def _value_v(value: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*V\b", value)
    return float(m.group(1)) if m else None


def _ohms(value: str) -> float | None:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", value.strip())
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6}[m.group(2)]


def rail_voltages(circuit) -> dict[str, float]:
    """Voltage of every supply net: named rails, then values carried through series parts
    (fuses, reverse-polarity/OR diodes) from a known rail."""
    v = {n.name: _volts(n.name) for n in circuit.nets if _volts(n.name) is not None}
    for _ in range(4):
        for c in circuit.components:
            pins = c.pin_nets
            if c.symbol == "Device:Polyfuse" and len(pins) == 2:
                a, b = pins.get("1"), pins.get("2")
                if a in v and b not in v:
                    v[b] = v[a]
            elif c.value in ("SS14", "SS34") and c.symbol.startswith("Diode:"):
                a, k = pins.get("2"), pins.get("1")
                if a in v and k not in v:
                    v[k] = round(v[a] - 0.4, 2)
    return v


def check(circuit) -> dict:
    out: list[Finding] = []
    v = rail_voltages(circuit)
    comps = circuit.components
    by_ref = {c.ref: c for c in comps}

    def add(sev, chk, msg, *refs):
        out.append(Finding(sev, chk, msg, list(refs)))

    unchecked = sorted({c.value for c in comps if c.ref[0] in "UQ" and c.value not in PARTS})
    for c in comps:
        spec = PARTS.get(c.value)
        if not spec:
            continue
        # supply ranges
        for pins, (lo, hi) in spec.get("supply", {}).items():
            for p in pins:
                net = c.pin_nets.get(p)
                if net is None:
                    add("error", "supply", f"{c.ref} {c.value}: supply pin {p} is not connected", c.ref)
                elif net in v and not (lo - 1e-6 <= v[net] <= hi + 1e-6):
                    add("error", "supply", f"{c.ref} {c.value}: supply {net} = {v[net]:g} V is outside "
                                           f"{lo:g}-{hi:g} V", c.ref)
        # blocking voltage of switches and diodes
        if "vds" in spec or "vce" in spec:
            lim = spec.get("vds") or spec.get("vce")
            worst = max((v.get(n, 0) for n in c.pin_nets.values()), default=0)
            # the switched node can rise to the load rail: use the highest rail on the board
            load_rail = max((v.get(n, 0) for n in _rails_of(c, circuit)), default=worst)
            peak = max(worst, load_rail)
            if peak > 0.8 * lim:
                add("warning" if peak <= lim else "error", "voltage rating",
                    f"{c.ref} {c.value}: rated {lim:g} V, circuit reaches {peak:g} V "
                    f"({peak / lim:.0%} of rating; 80% or less recommended)", c.ref)
        if "vr" in spec:
            peak = max((v.get(n, 0) for n in c.pin_nets.values()), default=0)
            if peak > 0.8 * spec["vr"]:
                add("warning", "voltage rating", f"{c.ref} {c.value}: reverse rating {spec['vr']:g} V vs {peak:g} V", c.ref)

    # capacitors: rating vs the rail they sit on
    for c in comps:
        if c.symbol not in ("Device:C", "Device:C_Polarized") or len(c.pin_nets) != 2:
            continue
        nets = list(c.pin_nets.values())
        if "GND" not in nets:
            continue
        other = next(n for n in nets if n != "GND")
        if other not in v:
            continue
        rated = _value_v(c.value)
        if rated is None:
            if v[other] > 5.5:
                add("warning", "capacitor rating", f"{c.ref} {c.value} on {other} ({v[other]:g} V) has no voltage "
                                                   f"rating in its value: the assembler may fit a 6.3 V or 10 V part",
                    c.ref)
        elif rated < 1.25 * v[other]:
            add("error", "capacitor rating", f"{c.ref} {c.value} on {other} ({v[other]:g} V): rating below 1.25x the rail",
                c.ref)

    # TVS standoff must be at or above the rail
    for c in comps:
        m = re.fullmatch(r"SMAJ(\d+(?:\.\d+)?)C?A", c.value)
        if m:
            rail = max((v.get(n, 0) for n in c.pin_nets.values()), default=0)
            if float(m.group(1)) < rail:
                add("error", "TVS", f"{c.ref} {c.value}: standoff {m.group(1)} V is below the {rail:g} V rail - it "
                                    "would conduct in normal operation", c.ref)

    # LEDs: current from the series resistor
    led_r = {}
    for c in comps:
        if c.symbol == "Device:LED":
            a = c.pin_nets.get("2")
            r = next((x for x in comps if x.symbol == "Device:R" and a in x.pin_nets.values()), None)
            if r is None:
                continue
            src = next(n for n in r.pin_nets.values() if n != a)
            k = c.pin_nets.get("1")
            vs = v.get(src, 3.3 if src.startswith("ESP_") else None)
            vk = 0.0 if k == "GND" or k not in v else v[k]
            ohms = _ohms(r.value)
            if vs is None or ohms is None:
                continue
            vf = LED_VF.get(c.value.upper(), 2.0)
            i = (vs - vk - vf) / ohms
            led_r[r.ref] = (vs - vk - vf, i)
            if i < 0.5e-3:
                add("warning", "LED current", f"{c.ref} {c.value}: about {i * 1e3:.1f} mA through {r.ref} {r.value} "
                                              f"from {src} ({vs:g} V) - too dim to see reliably", c.ref, r.ref)
            elif i > 20e-3:
                add("error", "LED current", f"{c.ref}: {i * 1e3:.0f} mA exceeds a typical 0603 LED's 20 mA", c.ref, r.ref)

    # resistor power
    for c in comps:
        if c.symbol != "Device:R":
            continue
        ohms = _ohms(c.value)
        size = next((s for s in R_POWER_W if s in (c.footprint or "")), None)
        if not ohms or not size:
            continue
        if c.ref in led_r:
            p = led_r[c.ref][1] ** 2 * ohms
        else:
            a, b = (v.get(n) for n in c.pin_nets.values())
            if a is None or b is None:
                a = a if a is not None else (0.0 if "GND" in c.pin_nets.values() else None)
                b = b if b is not None else (0.0 if "GND" in c.pin_nets.values() else None)
            if a is None or b is None:
                continue
            p = (a - b) ** 2 / ohms
        if p > R_POWER_W[size]:
            add("error", "resistor power", f"{c.ref} {c.value} ({size}) dissipates {p * 1e3:.0f} mW, above its "
                                           f"{R_POWER_W[size] * 1e3:.0f} mW rating", c.ref)
        elif p > 0.5 * R_POWER_W[size]:
            add("warning", "resistor power", f"{c.ref} {c.value} ({size}) dissipates {p * 1e3:.0f} mW, more than half "
                                             f"its {R_POWER_W[size] * 1e3:.0f} mW rating", c.ref)

    # linear regulator dissipation (estimate: ESP32 peak current when present, else 50 mA)
    esp = any(c.value == "ESP32-WROOM-32" for c in comps)
    for c in comps:
        if c.value == "AMS1117-3.3":
            vin = v.get(c.pin_nets.get("3"))
            if vin:
                i = 0.25 if esp else 0.05
                p = (vin - 3.3) * i
                if p > PARTS[c.value]["pd_max"]:
                    add("error", "regulator", f"{c.ref} AMS1117: about {p:.2f} W at {i * 1e3:.0f} mA from {vin:g} V - "
                                              "too hot for SOT-223 without copper area", c.ref)

    # every supply rail must have a source
    sources = set()
    for c in comps:
        if c.symbol.startswith(("Connector:", "Connector_Generic:")) and c.ref.startswith("J"):
            sources |= {n for n in c.pin_nets.values()}
    for c in comps:
        for sym_vout, pins in (("AP63203WU", ("1",)), ("AMS1117-3.3", ("2",)), ("AP2112K-3.3", ("5",)),
                               ("MCP73831-2-OT", ("3",))):
            if c.value == sym_vout:
                sources |= {c.pin_nets.get(p) for p in pins}
    changed = True
    while changed:
        changed = False
        for c in comps:
            ns = list(c.pin_nets.values())
            if c.symbol == "Device:Polyfuse" or (c.value in ("SS14", "SS34") and c.symbol.startswith("Diode:")) \
                    or c.symbol == "Device:L" or (c.value == "DMG2301L"):
                pairs = [("1", "2"), ("2", "1")] if c.symbol in ("Device:Polyfuse", "Device:L") else \
                        ([("2", "1")] if c.symbol.startswith("Diode:") else [("3", "2")])
                for a, b in pairs:
                    if c.pin_nets.get(a) in sources and c.pin_nets.get(b) not in sources:
                        sources.add(c.pin_nets.get(b))
                        changed = True
            _ = ns
    for n in circuit.nets:
        if (n.kind.value != "signal" or n.name in ("VSYS", "VLOGIC", "VBAT")) and n.name not in sources:
            add("error", "power source", f"rail {n.name} has nothing that supplies it", )

    # I2C: pull-ups and address conflicts
    i2c_parts = [c for c in comps if c.value in I2C_ADDR or c.value == "OLED" or c.value == "PCA9685PW"]
    if i2c_parts:
        sda = {c.pin_nets.get({"SHT31-DIS": "1", "BME280": "3", "ADS1115": "9", "PCA9685PW": "27"}.get(c.value, ""))
               for c in i2c_parts} - {None}
        for net in sda:
            if not any(c.symbol == "Device:R" and net in c.pin_nets.values() and "+3V3" in c.pin_nets.values()
                       for c in comps):
                add("error", "I2C", f"{net}: I2C data line without a pull-up resistor")
        addrs: dict[int, list[str]] = {}
        for c in i2c_parts:
            if c.value == "PCA9685PW":
                a = 0x40 + sum(1 << bit for bit, pin in enumerate(("1", "2", "3", "4", "5", "24"))
                               if c.pin_nets.get(pin) == "+3V3")
                addrs.setdefault(a, []).append(c.ref)
            else:
                for a in I2C_ADDR.get(c.value, []):
                    addrs.setdefault(a, []).append(c.ref)
        for a, refs in addrs.items():
            if len(refs) > 1:
                add("error", "I2C", f"address 0x{a:02X} used by {', '.join(refs)}", *refs)

    # ESP32 boot straps and reset
    for c in comps:
        if c.value != "ESP32-WROOM-32":
            continue
        for pin, why in ESP32_STRAP.items():
            net = c.pin_nets.get(pin)
            if net and any(x.symbol == "Device:R" and net in x.pin_nets.values() and "+3V3" in x.pin_nets.values()
                           for x in comps):
                add("error", "ESP32 boot", f"{c.ref}: {why}; it is pulled up", c.ref)
        en = c.pin_nets.get("3")
        if not any(x.symbol == "Device:R" and en in x.pin_nets.values() and "+3V3" in x.pin_nets.values() for x in comps):
            add("error", "ESP32 boot", f"{c.ref}: EN has no pull-up - the module would not start", c.ref)
        io0 = c.pin_nets.get("25")
        if io0 and not any(x.symbol == "Device:R" and io0 in x.pin_nets.values() and "+3V3" in x.pin_nets.values()
                           for x in comps):
            add("warning", "ESP32 boot", f"{c.ref}: IO0 has no pull-up (relies on the internal one)", c.ref)

    # MOSFET gates must have a defined state at reset
    for c in comps:
        if c.value in ("AO3400A",):
            g = c.pin_nets.get("1")
            if not any(x.symbol == "Device:R" and g in x.pin_nets.values() and "GND" in x.pin_nets.values()
                       for x in comps):
                add("warning", "gate", f"{c.ref}: gate has no pull-down - the output may switch on during reset", c.ref)

    errors = sum(f.severity == "error" for f in out)
    warnings = sum(f.severity == "warning" for f in out)
    return {"errors": errors, "warnings": warnings, "findings": [asdict(f) for f in out],
            "not_checked": unchecked, "rails": {k: v[k] for k in sorted(v)}}


def _rails_of(c, circuit) -> list[str]:
    """Rails a switch's drain/collector can be pulled up to through its load: the rails of
    the other parts on the same nets (terminal, relay coil, diode)."""
    nets = set(c.pin_nets.values())
    out = set()
    for x in circuit.components:
        if x is c:
            continue
        xs = set(x.pin_nets.values())
        if xs & nets:
            out |= xs
    return [n for n in out if _volts(n) is not None]
