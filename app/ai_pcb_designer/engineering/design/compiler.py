"""Compile a complete DesignSpec into a Circuit using only verified blocks.

Deterministic: the same spec always yields the same circuit. All part/value
selection rules live here and are written into the proposal so the user can see
why each choice was made.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from ..circuit import blocks as B
from ..circuit.builder import CircuitBuilder
from ..circuit.model import BoardSpec, Circuit, DesignRules, MountingHole, ZoneSpec
from ..placement.heuristic import PlacementError, footprint_geom, place
from .spec import ESP32_DIRECT_CHANNELS, INDUCTIVE, DesignSpec, analyze

TVS_STANDOFF = [5.0, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 22, 24, 26, 28, 30, 33]
CAP_RATING = [10, 16, 25, 35, 50]
DIRECT_GPIOS = ["IO25", "IO26", "IO27", "IO32"]


class CompileError(ValueError):
    pass


@dataclass
class CompileResult:
    circuit: Circuit
    proposal: dict
    sizing_log: list[str] = field(default_factory=list)


def _slug(text: str | None) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9]+", "_", (text or "design")).strip("_").lower()
    return (s or "design")[:40]


def _vin_net(v: float) -> str:
    return f"+{v:g}V"


SAFETY = 1.5           # current margin applied before sizing copper


def ipc2221_width_mm(amps: float, rise_c: float = 10.0, oz: float = 1.0, external: bool = True) -> float:
    """Trace width for a current (IPC-2221): A[mil^2] = (I / (k * dT^0.44))^(1/0.725),
    k = 0.048 outer / 0.024 inner layers; 1 oz copper = 1.378 mil thick. Rounded up to 0.05 mm."""
    import math
    if amps <= 0:
        return 0.0
    k = 0.048 if external else 0.024
    area = (amps / (k * rise_c ** 0.44)) ** (1 / 0.725)
    w_mm = area / (1.378 * oz) * 0.0254
    return math.ceil(w_mm / 0.05) * 0.05


def select_tvs(v: float) -> str:
    standoff = next(x for x in TVS_STANDOFF if x >= 1.15 * v)
    return f"SMAJ{standoff:g}CA"


def cap_rating(v: float) -> int:
    return next(x for x in CAP_RATING if x >= 1.5 * v)


def select_cap(v: float) -> str:
    return f"100uF {cap_rating(v)}V"


def fuse_rating(v: float) -> int:
    return 16 if v <= 12 else 30


def _holes(w, h, n, inset=4.0):
    corners = [(inset, inset), (w - inset, h - inset), (w - inset, inset), (inset, h - inset)]
    return [MountingHole(x_mm=x, y_mm=y) for x, y in corners[:n]]


def resolve_named_pins(c: Circuit, libs) -> Circuit:
    """Pins given as "@NAME" (e.g. the USB-C shield, numbered S1 in KiCad 7 and SH in KiCad
    10) are mapped to the installed library's pin numbers."""
    for comp in c.components:
        named = [k for k in comp.pin_nets if k.startswith("@")]
        if not named:
            continue
        sym = libs.resolve_symbol(comp.symbol)
        for k in named:
            net = comp.pin_nets.pop(k)
            nums = [p.number for p in sym.pins if p.name == k[1:]]
            if not nums:
                raise CompileError(f"{comp.ref}: symbol {comp.symbol} has no pin named {k[1:]}")
            for n in nums:
                comp.pin_nets[n] = net
    return c


def build_circuit(spec: DesignSpec, width: float, height: float, layers: int, holes: int, libs=None) -> Circuit:
    from .spec import ESP32_IO_POOL, effective_input_v, io_plan
    v = effective_input_v(spec)
    L, M, R = spec.loads, spec.motors, spec.relays
    n, nm, nr = L.count or 0, M.count or 0, R.count or 0
    ichan = L.current_per_channel_a or 0.0
    usb = spec.power_input == "usb_c"
    bat = spec.battery is not None
    esp = spec.controller == "esp32"
    vin_net = _vin_net(v)
    sep_load = bool(n and L.voltage and abs(L.voltage - v) > 1e-6)
    vload_net = _vin_net(L.voltage) if sep_load else vin_net
    zones = []
    if layers == 4:
        zones = [ZoneSpec(net="GND", layer="In1.Cu", plane=True), ZoneSpec(net=vin_net, layer="In2.Cu", plane=True),
                 ZoneSpec(net="GND", layer="F.Cu"), ZoneSpec(net="GND", layer="B.Cu")]
    name = _slug(spec.project_name or spec.description)
    b = CircuitBuilder(name, spec.project_name or "AI PCB Designer project",
                       BoardSpec(width_mm=width, height_mm=height, layers=layers,
                                 mounting_holes=_holes(width, height, holes), zones=zones),
                       DesignRules())
    total = n * ichan
    motor_total = nm * (M.current_a or 0)
    on_vin = total * (not sep_load) + motor_total * (abs((M.voltage or v) - v) < 1e-6)
    # Widths from current (IPC-2221, 1 oz outer copper, 10 °C rise, 1.5x margin), never below
    # the floors that keep rails stiff. On 4 layers the input rail is also a full plane.
    vin_w = (1.0 if layers > 2 else max(1.5, ipc2221_width_mm(on_vin * SAFETY))) if not usb else \
        max(0.5, ipc2221_width_mm((on_vin + 0.5) * SAFETY))      # USB: loads + ~0.5 A logic
    vin = b.power(vin_net, width=round(vin_w, 2))
    vload = vin
    if sep_load:
        vload = b.power(vload_net, width=round(max(1.5, ipc2221_width_mm(
            (total + (motor_total if abs((M.voltage or 0) - L.voltage) < 1e-6 else 0)) * SAFETY)), 2))
    # 3.3 V logic: <= 0.5 A (ESP32 Wi-Fi peaks) x 1.5 margin = 0.75 A -> 0.25 mm (IPC-2221, 10 °C),
    # i.e. the default width, which also enters every 0.5 mm-pitch pin without a neck-down
    v3, gnd = b.power("+3V3", width=0.25), b.power("GND", width=0.5)
    out_w = max(0.25, ipc2221_width_mm(ichan * SAFETY))
    # an ESP32 on a USB-C powered board is programmed over that port unless the user said otherwise
    usb_data = bool(esp and (spec.usb_programming or (usb and spec.usb_programming is None)))
    dp = b.net("USB_D+") if usb_data else None
    dm = b.net("USB_D-") if usb_data else None
    vbat = None

    with b.sheet("Power"):
        cv, fv = cap_rating(max(v, 5.0)), fuse_rating(v)
        if usb:
            vbus = B.usb_c_port(b, vin, dp=dp, dm=dm, edge="left",
                                fuse_hold="1.5A" if total else "750mA")
            if bat:
                vsys = b.net("VSYS", width=0.5)
                charge_ma = 500
                if spec.battery.capacity_mah:
                    charge_ma = int(max(100, min(500, 0.5 * spec.battery.capacity_mah)) // 50 * 50)
                vbat = B.li_ion_charger(b, vbus, vsys, charge_ma=charge_ma, edge="right")
                B.ldo_ap2112(b, vsys, v3)
            else:
                B.ldo_ap2112(b, vin, v3)
        else:
            if spec.power_input == "barrel_jack":
                B.barrel_input(b, vin, volts=v, tvs=select_tvs(v), cap=select_cap(v), edge="left")
            else:
                B.screw_input(b, vin, volts=v, tvs=select_tvs(v), cap=select_cap(v), edge="left")
            if sep_load:
                B.load_input(b, vload, volts=L.voltage, tvs=select_tvs(L.voltage), cap=select_cap(L.voltage),
                             edge="left")
            logic = b.net("VLOGIC", width=0.5)
            if esp or v > 15:
                B.protected_branch(b, vin, logic, hold="750mA", cap_v=cv, fuse_v=fv)
                B.buck_3v3(b, logic, v3, cap_v=cv)
            else:
                B.protected_branch(b, vin, logic, hold="200mA", cap_v=cv, fuse_v=fv)
                B.ldo_3v3(b, logic, v3, cap_v=cv)
            if usb_data:                       # the programming port can also power the logic
                pv = b.net("USB_VBUS", width=0.5)
                B.usb_c_port(b, pv, dp=dp, dm=dm, edge="top", name="usb", fuse_hold="500mA")
                with b.block("usb_or"):
                    b.part("Diode:SS14", "SS14", {"2": pv, "1": logic}, "SMA", prefix="D",
                           description="USB 5 V -> logic supply (diode-OR with the DC input)")
        if spec.power_led:
            B.power_led(b, v3)

    plan = io_plan(spec)
    chips = plan["pca9685_chips"]
    outs_needed = plan["outputs"]
    sigs: list[str] = []
    io: dict[str, str] = {}
    sda = scl = oe = None
    with b.sheet("Control"):
        if esp:
            pool = list(ESP32_IO_POOL)
            use = ["IO2"] + (["IO4"] if any(x.kind == "ds18b20" for x in spec.sensors) else [])
            if plan["i2c"]:
                use += ["IO21", "IO22"]
            if chips:
                use += ["IO5"]
            comm = {}
            for key, k in (("rs485", ("RS485_TX", "RS485_RX", "RS485_DE")), ("can", ("CAN_TX", "CAN_RX"))):
                if key in spec.interfaces:
                    for sig in k:
                        comm[sig] = pool.pop()               # from the end: outputs keep IO25.. order
            direct = pool[:outs_needed] if plan["direct"] else []
            use += direct + list(comm.values())
            io = B.esp32_core(b, v3, edge="right" if n > 2 else "top", use=tuple(dict.fromkeys(use)),
                              extra_inputs=("IO35",) if bat else ())
            if spec.status_led:
                B.status_led(b, io["IO2"])
            sda, scl, oe = io.get("IO21"), io.get("IO22"), io.get("IO5")
            sigs = [io[g] for g in direct]
            comm = {k: io[g] for k, g in comm.items()}
            if usb_data:
                B.ch340_usb_uart(b, dp, dm, v3, "ESP_TXD0", "ESP_RXD0", "ESP_EN", "ESP_IO0")
            if bat:
                B.battery_sense(b, vbat, io["IO35"])
        else:
            comm = {}
            sda, scl, oe = b.net("SDA"), b.net("SCL"), b.net("OE_N")
            B.i2c_header(b, sda, scl, oe, v3, edge="left")
        if plan["i2c"]:
            B.i2c_pullups(b, sda, scl, v3)
        if chips:
            with b.block("oe_pu"):
                b.R("10k", v3, oe, description="OE pull-up: outputs off until the controller enables them")
            for c in range(chips):
                used = min(16, outs_needed - 16 * c)
                outs, _ = B.pca9685(b, sda, scl, oe, v3, address=c, name=f"pwm{chr(65 + c)}", used=used)
                sigs += outs
    if spec.sensors or spec.interfaces:
        with b.sheet("Sensors"):
            for x in spec.sensors:
                if x.kind == "ds18b20":
                    B.ds18b20(b, io["IO4"], v3)
                elif x.kind == "sht31":
                    B.sht31(b, sda, scl, v3)
                elif x.kind == "bme280":
                    B.bme280(b, sda, scl, v3)
                elif x.kind == "ads1115":
                    B.ads1115_frontend(b, sda, scl, v3, edge="right" if not esp or n <= 2 else "left")
            if "oled_i2c" in spec.interfaces:
                B.oled_header(b, sda, scl, v3, edge="top")
            if "rs485" in spec.interfaces:
                B.rs485(b, comm["RS485_TX"], comm["RS485_RX"], comm["RS485_DE"], v3, edge="left")
            if "can" in spec.interfaces:
                B.can_bus(b, comm["CAN_TX"], comm["CAN_RX"], v3, edge="left")
    k = 0
    for g in range(0, n, 8):
        with b.sheet(f"Channels {g + 1}-{min(n, g + 8)}"):
            for i in range(g, min(n, g + 8)):
                if n <= 2:
                    edge = "right" if not esp else "bottom"
                else:
                    edge = "bottom" if i < math.ceil(n / 2) else "top"
                B.mosfet_channel(b, i + 1, sigs[k], vload, edge=edge, out_width=out_w, load_v=L.voltage or v)
                k += 1
    if nm:
        vm = vload if sep_load and abs((M.voltage or 0) - L.voltage) < 1e-6 else vin
        mw = max(0.4, ipc2221_width_mm((M.current_a or 0) * SAFETY))
        ilim_k = round(64.0 / max(0.5, min(3.5, 1.5 * (M.current_a or 1.0))), 1)
        with b.sheet("Motors"):
            for i in range(nm):
                B.drv8871_motor(b, i + 1, sigs[k], sigs[k + 1], vm, ilim_k=ilim_k, out_width=mw,
                                cap_v=cap_rating(M.voltage or v), edge="bottom")
                k += 2
    if nr:
        cw = max(0.5, ipc2221_width_mm((R.contact_current_a or 0) * SAFETY))
        with b.sheet("Relays"):
            for i in range(nr):
                B.relay_channel(b, i + 1, sigs[k], vin, v, edge="bottom" if i % 2 == 0 else "top", contact_width=cw)
                k += 1
    c = b.build()
    if libs is not None:
        resolve_named_pins(c, libs)
    _rate_capacitors(c)
    return c


def _rate_capacitors(c: Circuit):
    """Put a voltage rating in every capacitor value on a rail above 5.5 V, so the BOM
    cannot be filled with a 6.3 V or 10 V part (>= 1.5x the rail, standard steps)."""
    from ..validation.functional import _value_v, rail_voltages
    v = rail_voltages(c)
    for comp in c.components:
        if comp.symbol not in ("Device:C", "Device:C_Polarized") or _value_v(comp.value) is not None:
            continue
        nets = list(comp.pin_nets.values())
        rail = max((v.get(n, 0.0) for n in nets), default=0.0)
        if "GND" in nets and rail > 5.5:
            comp.value = f"{comp.value} {cap_rating(rail)}V"


def _fits(circuit: Circuit, libs) -> bool:
    geoms = {}
    for comp in circuit.components:
        fp = comp.footprint or libs.resolve_symbol(comp.symbol).properties["Footprint"]
        geoms[comp.ref] = footprint_geom(libs.resolve_footprint(fp).path, fp)
    try:
        place(circuit, geoms, spacing=1.5)      # a little slack so re-layouts still fit
        return True
    except PlacementError:
        return False


def _size_alternatives(spec: DesignSpec, w: float, h: float, layers: int, holes: int, libs) -> str:
    """The user fixed the board size and it is too small: find which optional extras could
    be dropped to keep that size, so the assistant can offer real alternatives."""
    usb = bool(spec.usb_programming)
    what = [f"{holes} mounting holes"] + (["USB-C programming"] if usb else [])
    msg = f"The design does not fit on {w:g} x {h:g} mm with {' and '.join(what)}."
    options = []
    for u in ([True, False] if usb else [False]):
        for hs in (x for x in (4, 2, 0) if x <= holes):
            if (u, hs) == (usb, holes):
                continue
            variant = spec.model_copy(update={"usb_programming": u if usb else spec.usb_programming,
                                              "mounting_holes": hs})
            if _fits(build_circuit(variant, w, h, layers, hs, libs), libs):
                options.append(f"{hs} mounting holes" + ("" if u or not usb else " and no USB-C programming"))
                break            # fewest changes first: keep the largest hole count that fits
    if options:
        msg += " It does fit at that size with " + "; or with ".join(options) + "."
    else:
        msg += " Dropping mounting holes or USB-C programming does not make it fit either."
    return msg + " Otherwise allow a larger board, or let the size be chosen automatically."


def compile_spec(spec: DesignSpec, libs, prefer_size: tuple[float, float] | None = None) -> CompileResult:
    """`prefer_size`: the previous revision's board, tried first when no size was specified,
    so an edit that still fits keeps the outline (and with it the unchanged layout)."""
    an = analyze(spec)
    if not an.ready:
        raise CompileError("spec not ready: " + "; ".join(
            [q.field for q in an.missing if q.tier == "critical"] + an.conflicts + an.unsupported))
    layers = spec.layers or an.defaults["layers"]
    holes = spec.mounting_holes if spec.mounting_holes is not None else an.defaults["mounting_holes"]
    log = []
    if spec.board_width_mm and spec.board_height_mm:
        w, h = spec.board_width_mm, spec.board_height_mm
        c = build_circuit(spec, w, h, layers, holes, libs)
        if not _fits(c, libs):
            raise CompileError(_size_alternatives(spec, w, h, layers, holes, libs))
        log.append(f"user size {w:g}x{h:g} mm fits")
    elif prefer_size and _fits(c := build_circuit(spec, prefer_size[0], prefer_size[1], layers, holes, libs), libs):
        log.append(f"previous size {prefer_size[0]:g}x{prefer_size[1]:g} mm still fits")
    else:
        probe = build_circuit(spec, 300, 200, layers, holes, libs)
        n = spec.loads.count or 0
        side = math.ceil(n / 2) if n > 2 else 0
        w = max(70.0, side * 12.8 + 26 + (48 if spec.controller == "esp32" and n <= 2 else 0))
        h = max(55.0, w / 1.6)
        area_need = 0.0
        for comp in probe.components:
            fp = comp.footprint or libs.resolve_symbol(comp.symbol).properties["Footprint"]
            g = footprint_geom(libs.resolve_footprint(fp).path, fp)
            area_need += (g.box.w + 1.5) * (g.box.h + 1.5)
        while w * h < 2.2 * area_need:
            w, h = w * 1.05, h * 1.05
        for _ in range(25):
            w, h = 5 * math.ceil(w / 5), 5 * math.ceil(h / 5)
            c = build_circuit(spec, w, h, layers, holes, libs)
            if _fits(c, libs):
                log.append(f"auto size {w:g}x{h:g} mm fits")
                break
            log.append(f"{w:g}x{h:g} mm does not fit")
            w, h = w * 1.08, h * 1.08
        else:
            raise CompileError("Could not find a board size that fits (tried up to "
                               f"{w:.0f} x {h:.0f} mm).")
    if not (spec.board_width_mm and spec.board_height_mm):
        c.board.auto_size = True
    from ..validation.functional import check as functional_check
    prop = make_proposal(spec, c, an, layers, holes)
    fc = functional_check(c)
    prop["functional_checks"] = {"errors": fc["errors"], "warnings": fc["warnings"],
                                 "findings": [f["message"] for f in fc["findings"]]}
    if fc["errors"]:
        prop["risks"] = [f"Design check: {f['message']}" for f in fc["findings"] if f["severity"] == "error"] + prop["risks"]
    return CompileResult(c, prop, log)


def make_proposal(spec: DesignSpec, c: Circuit, an, layers: int, holes: int) -> dict:
    from .spec import effective_input_v
    L, M, R = spec.loads, spec.motors, spec.relays
    n, nm, nr = L.count or 0, M.count or 0, R.count or 0
    v = effective_input_v(spec)
    usb, bat, esp = spec.power_input == "usb_c", spec.battery is not None, spec.controller == "esp32"
    sep_load = bool(n and L.voltage and abs(L.voltage - v) > 1e-6)
    usb_data = any(x.symbol == "Interface_USB:CH340C" for x in c.components)
    counts = Counter(x.value if x.symbol.split(":")[0] not in ("Device",) else x.symbol.split(":")[1]
                     for x in c.components)
    key_parts, seen = [], set()
    for x in c.components:
        if x.ref[0] in "UQJK" or x.symbol.startswith(("RF_Module", "Driver_", "Regulator", "Sensor", "Analog_ADC")):
            k = (x.value, x.description)
            if k in seen:
                continue
            seen.add(k)
            qty = sum(1 for y in c.components if (y.value, y.description) == k)
            key_parts.append({"part": x.value, "qty": qty, "role": x.description,
                              "footprint": (x.footprint or "").split(":")[-1],
                              "status": x.verification_status.value})
    chips = an.derived["pca9685_chips"]
    total = an.derived["total_load_a"]
    io = an.derived.get("io") or {}

    # ---- overview + architecture ------------------------------------------------
    what = []
    if n:
        what.append(f"{n} switched output{'s' if n > 1 else ''}")
    if nm:
        what.append(f"{nm} motor H-bridge{'s' if nm > 1 else ''}")
    if nr:
        what.append(f"{nr} relay{'s' if nr > 1 else ''}")
    what += [x.kind.upper() for x in spec.sensors]
    what += [{"rs485": "RS-485", "can": "CAN", "oled_i2c": "OLED display connector"}[i] for i in spec.interfaces]
    power = ("USB-C" + (" + 1-cell Li-ion battery" if bat else "") if usb else
             f"{v:g} V {'barrel jack' if spec.power_input == 'barrel_jack' else 'screw terminal'}")
    overview = (f"{'ESP32 Wi-Fi' if esp else 'I2C-controlled'} board, {power} input"
                + (": " + ", ".join(what) if what else "") + ".")
    arch = []
    if usb:
        arch.append("USB-C receptacle (5.1 k CC pull-downs = 5 V sink), resettable fuse on VBUS"
                    + (", USBLC6 ESD protection on the data lines" if usb_data else ""))
        if bat:
            arch.append("MCP73831 Li-ion charger from USB; load sharing: USB through a Schottky diode, battery "
                        "through a P-MOSFET when USB is absent; battery voltage divider to ESP32 IO35")
        arch.append("AP2112K 3.3 V LDO (600 mA) for the logic")
    else:
        arch.append(f"{v:g} V {'barrel jack' if spec.power_input == 'barrel_jack' else 'screw-terminal input'} -> "
                    f"TVS clamp + bulk capacitor -> rail {_vin_net(v)}")
        if sep_load:
            arch.append(f"Separate {L.voltage:g} V load supply terminal (TVS + bulk), common ground")
        arch.append("Logic: reverse-polarity diode + resettable fuse -> " +
                    ("AP63203 3.3 V buck" if (esp or v > 15) else "AMS1117-3.3 LDO (low logic current)"))
    if esp:
        arch.append("ESP32-WROOM-32 module, antenna at the board edge"
                    + ("; programmed over USB-C via CH340C with auto-reset" if usb_data else
                       "; 6-pin UART programming header"))
    else:
        arch.append("6-pin I2C host header (SDA, SCL, OE)")
    if io.get("outputs"):
        arch.append(f"{chips}x PCA9685 PWM driver(s) on I2C, outputs off at reset (OE pull-up)" if chips
                    else f"{io['outputs']} ESP32 GPIOs drive the outputs directly")
    if n:
        arch.append(f"{n}x low-side AO3400A channel ({_vin_net(L.voltage or v)}): gate resistor, pull-down, "
                    "SS14 flyback, on-LED, screw terminal")
    if nm:
        arch.append(f"{nm}x DRV8871 H-bridge at {M.voltage or v:g} V (forward / reverse / brake, PWM speed), "
                    "current limit resistor, bulk + HF capacitors, 2-pin terminal")
    if nr:
        arch.append(f"{nr}x SRD relay ({v:g} V coil) with NPN driver, flyback diode, on-LED, 3-pin terminal NO/COM/NC")
    for i in spec.interfaces:
        arch.append({"rs485": "MAX3485 RS-485 transceiver, termination jumper, fail-safe bias, A/B/GND terminal",
                     "can": "SN65HVD230 CAN transceiver, termination jumper, CANH/CANL/GND terminal",
                     "oled_i2c": "4-pin I2C OLED connector (GND, VCC, SCL, SDA)"}[i])

    # ---- risks and rules ---------------------------------------------------------
    risks = [
        "No circuit simulation: values follow datasheet application rules; check the 'ai_suggested' parts.",
        "Values marked 'ai_suggested' follow typical application rules and are not verified against datasheets here.",
    ]
    if n and (L.kind in INDUCTIVE or L.kind is None):
        risks.append("Inductive loads: flyback diodes are fitted, but check motor stall/inrush current.")
    lv = L.voltage or v
    if n and lv and lv >= 20:
        risks.append("AO3400A MOSFETs are rated 30 V: little margin on a 24 V rail; surges above 30 V are "
                     "clamped by the TVS only approximately.")
    if total > 0:
        risks.append(f"Total output current {total:g} A flows through the "
                     f"{'load' if sep_load else 'input'} terminal and "
                     + ("the inner power plane." if layers == 4 and not sep_load else
                        f"a {next((nn.track_width_mm for nn in c.nets if nn.name == _vin_net(lv)), 1.5):g} mm rail "
                        "trace (IPC-2221, 10 °C rise, 1.5x margin)."))
    if nm:
        risks.append(f"Motor current limit set by R_ILIM = {next((x.value for x in c.components if x.description.startswith('Current limit')), '?')}"
                     " from the DRV8871 datasheet relation I_TRIP ≈ 64 kΩ·A / R_ILIM; confirm against the datasheet "
                     "and your motor's stall current.")
    if nr:
        risks.append("Relay contacts are used for <= 30 V only. The board has no mains isolation or creepage design.")
    if usb:
        risks.append("USB-C: only 5 V is negotiated (no USB Power Delivery). Loads need a 5 V / 3 A USB-C adapter.")
    if bat:
        risks.append("Battery: no protection IC on the board - use a cell with its own protection circuit "
                     "(over-discharge, short circuit). Check the JST PH polarity of your cell.")
    if "rs485" in spec.interfaces or "can" in spec.interfaces:
        risks.append("Bus lines have no surge/ESD protection beyond the transceiver's own; add TVS diodes for long "
                     "or outdoor cables.")
    rules = []
    if not usb:
        rules += [f"TVS {select_tvs(v)}: smallest SMAJ standoff >= 1.15 x {v:g} V",
                  f"Bulk capacitor {select_cap(v)}; ceramic input capacitors {cap_rating(v)} V: rating >= 1.5 x input",
                  f"Logic fuse rated {fuse_rating(v)} V for a {v:g} V rail"]
    if n:
        rules.append(f"Output trace width {next((nn.track_width_mm for nn in c.nets if nn.name == 'CH1_OUT'), '-')} mm "
                     f"for {L.current_per_channel_a or 0:g} A per channel")
    if bat:
        rules.append(f"Charge current {next((x.description for x in c.components if x.description.startswith('Li-ion charger')), '')}"
                     ": 0.5 C of the stated capacity, at most 500 mA")
    rules.append("Layer count: 4 with planes when the rail current > 3 A or more than 16 outputs")
    limitations = ["Placement and routing are automatic; a human review in KiCad is recommended before ordering."]
    if n and not sep_load:
        limitations.insert(0, "Loads run from the input rail voltage.")
    return {
        "title": c.title, "name": c.name, "overview": overview, "architecture": arch,
        "key_parts": key_parts,
        "part_counts": dict(counts.most_common()),
        "totals": {"components": len(c.components) + len(c.board.mounting_holes), "nets": len(c.nets),
                   "sheets": len({x.sheet for x in c.components if x.sheet})},
        "board": {"width_mm": c.board.width_mm, "height_mm": c.board.height_mm, "layers": layers,
                  "mounting_holes": holes, "planes": [f"{z.net} on {z.layer}" for z in c.board.zones if z.plane]},
        "selection_rules": rules,
        "assumptions": an.assumptions,
        "risks": risks,
        "limitations": limitations,
        "complexity": ("low" if len(c.components) < 50 else "medium" if len(c.components) < 120 else "high"),
    }
