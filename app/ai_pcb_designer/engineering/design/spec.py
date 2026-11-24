"""Structured design requirements and deterministic completeness/feasibility analysis.

The AI fills a DesignSpec from conversation; it never decides on its own whether a
design is ready. `analyze()` does, with explicit rules, and the result tells the AI
exactly which questions remain and which requests are outside what the engine can
build. Critical electrical parameters are never defaulted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

SUPPORTED_INPUT_V = [5.0, 6.0, 7.5, 8.0, 9.0, 10.0, 12.0, 15.0, 24.0]   # rails with KiCad power symbols
MAX_CHANNELS = 32
MAX_CHANNEL_A = 1.0          # SS14 flyback 1 A; AO3400A in SOT-23 kept conservative
TWO_LAYER_MAX_TOTAL_A = 3.0  # load rail as a 1.5 mm outer trace; above this use a plane
ESP32_DIRECT_CHANNELS = 4    # GPIO-driven gates; beyond this a PCA9685 is used
INDUCTIVE = {"dc_fan", "solenoid", "relay_coil", "motor_dc"}

LoadKind = Literal["dc_fan", "led_strip", "solenoid", "relay_coil", "motor_dc", "heater", "lamp", "generic_dc"]
SensorKind = Literal["ds18b20", "sht31", "ads1115", "bme280"]
PowerInput = Literal["screw_terminal", "barrel_jack", "usb_c"]
Interface = Literal["rs485", "can", "oled_i2c"]
RELAY_COIL_V = (5.0, 12.0, 24.0)        # Sanyou SRD coil variants
RELAY_MAX_CONTACT_V = 30.0               # no mains: isolation/creepage is not engineered here
RELAY_MAX_CONTACT_A = 10.0
MAX_RELAYS = 8
MOTOR_V = (6.5, 24.0)                    # DRV8871 is 6.5-45 V; capacitors here are sized up to 24 V rails
MOTOR_MAX_A = 2.0                        # continuous; DRV8871 limits at 3.6 A peak
MAX_MOTORS = 4
USB_MAX_TOTAL_A = 1.5                    # loads + logic from a USB-C 5 V source (needs a 3 A-capable source)
DIRECT_OUTPUTS_MAX = 6                   # ESP32 GPIO-driven outputs before a PCA9685 is used
ESP32_IO_POOL = ["IO25", "IO26", "IO27", "IO32", "IO33", "IO13", "IO14", "IO16", "IO17", "IO18", "IO19", "IO23"]


class LoadSpec(BaseModel):
    count: int | None = Field(None, ge=0)
    kind: LoadKind | None = None
    voltage: float | None = Field(None, gt=0)
    current_per_channel_a: float | None = Field(None, gt=0)
    pwm: bool = True


class SensorSpec(BaseModel):
    kind: SensorKind
    count: int = Field(1, ge=1, le=1)


class BatterySpec(BaseModel):
    kind: Literal["li_ion_1s"] = "li_ion_1s"
    capacity_mah: float | None = Field(None, gt=0)


class MotorSpec(BaseModel):
    count: int | None = Field(None, ge=0)
    voltage: float | None = Field(None, gt=0)
    current_a: float | None = Field(None, gt=0)


class RelaySpec(BaseModel):
    count: int | None = Field(None, ge=0)
    contact_current_a: float | None = Field(None, gt=0)
    contact_voltage: float | None = Field(None, gt=0)


class DesignSpec(BaseModel):
    project_name: str | None = None
    description: str | None = None
    controller: Literal["esp32", "external_i2c"] | None = None
    input_voltage: float | None = Field(None, gt=0)
    power_input: PowerInput | None = None                 # None = screw terminal
    battery: BatterySpec | None = None
    usb_programming: bool | None = None                    # ESP32 programmed over USB-C (CH340C)
    loads: LoadSpec = Field(default_factory=LoadSpec)
    motors: MotorSpec = Field(default_factory=MotorSpec)
    relays: RelaySpec = Field(default_factory=RelaySpec)
    interfaces: list[Interface] = Field(default_factory=list)
    sensors: list[SensorSpec] = Field(default_factory=list)
    power_led: bool = True
    status_led: bool = True
    board_width_mm: float | None = Field(None, gt=10, le=400)
    board_height_mm: float | None = Field(None, gt=10, le=400)
    layers: Literal[2, 4] | None = None
    mounting_holes: Literal[0, 2, 4] | None = None
    unsupported_requests: list[str] = Field(default_factory=list)   # things the user asked for we can't build
    notes: list[str] = Field(default_factory=list)

    def merged(self, updates: dict) -> "DesignSpec":
        """Deep-merge a partial update (from the AI) and re-validate."""
        base = self.model_dump()

        def merge(a, b):
            for k, v in b.items():
                if isinstance(v, dict) and isinstance(a.get(k), dict):
                    merge(a[k], v)
                elif isinstance(v, list) and k in ("unsupported_requests", "notes"):
                    a[k] = list(dict.fromkeys((a.get(k) or []) + v))
                else:
                    a[k] = v
        merge(base, updates or {})
        return DesignSpec.model_validate(base)


@dataclass
class Question:
    field: str
    tier: Literal["critical", "recommended", "optional"]
    why: str


@dataclass
class Analysis:
    ready: bool
    missing: list[Question] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    defaults: dict[str, object] = field(default_factory=dict)      # proposed, shown for approval
    assumptions: list[str] = field(default_factory=list)
    derived: dict[str, object] = field(default_factory=dict)

    def to_dict(self):
        return {"ready": self.ready, "missing": [q.__dict__ for q in self.missing], "conflicts": self.conflicts,
                "unsupported": self.unsupported, "defaults": self.defaults, "assumptions": self.assumptions,
                "derived": self.derived}


def effective_input_v(spec: DesignSpec) -> float | None:
    """USB-C power is 5 V by definition; otherwise the stated input voltage."""
    return 5.0 if spec.power_input == "usb_c" else spec.input_voltage


def io_plan(spec: DesignSpec) -> dict:
    """Which controller signals the design needs, and whether outputs are driven directly
    from ESP32 GPIOs or through PCA9685 PWM drivers. Shared by analysis and compiler."""
    esp = spec.controller == "esp32"
    n = spec.loads.count or 0
    m = spec.motors.count or 0
    r = spec.relays.count or 0
    outputs = n + 2 * m + r
    comm = (3 if "rs485" in spec.interfaces else 0) + (2 if "can" in spec.interfaces else 0)
    pool = len(ESP32_IO_POOL) - comm
    direct = esp and outputs <= min(DIRECT_OUTPUTS_MAX, pool)
    chips = 0 if direct else math.ceil(outputs / 16)
    i2c = chips > 0 or any(x.kind in ("sht31", "ads1115", "bme280") for x in spec.sensors) or \
        "oled_i2c" in spec.interfaces or (not esp)
    return {"outputs": outputs, "direct": direct, "pca9685_chips": chips, "i2c": i2c, "comm_pins": comm,
            "free_gpio": pool - (outputs if direct else 0)}


def analyze(spec: DesignSpec) -> Analysis:
    a = Analysis(ready=False)
    L, M, R = spec.loads, spec.motors, spec.relays
    usb = spec.power_input == "usb_c"
    bat = spec.battery is not None
    if spec.controller is None:
        a.missing.append(Question("controller", "critical",
                                  "On-board ESP32 (Wi-Fi/BLE), or driven over I2C by an external controller?"))
    if spec.input_voltage is None and not usb:
        if bat:
            a.defaults["power_input"] = "usb_c"
            a.assumptions.append("Battery boards charge from USB-C (5 V).")
        else:
            a.missing.append(Question("input_voltage", "critical",
                                      "Supply voltage decides regulator, protection and load rail."))
    if L.count is None and not (M.count or R.count or bat or spec.sensors or spec.interfaces):
        a.missing.append(Question("loads.count", "critical", "How many switched outputs (0 is allowed)."))
    if L.count:
        if L.voltage is None:
            a.missing.append(Question("loads.voltage", "critical",
                                      "Load voltage (the input rail, or a separate load supply)."))
        if L.current_per_channel_a is None:
            a.missing.append(Question("loads.current_per_channel_a", "critical",
                                      "Maximum current per output sizes the MOSFET, diode and copper."))
        if L.kind is None:
            a.missing.append(Question("loads.kind", "recommended",
                                      "Load type (fan, LED strip, solenoid...); every channel gets a flyback diode either way."))
    if M.count:
        if M.voltage is None:
            a.missing.append(Question("motors.voltage", "critical", "Motor supply voltage."))
        if M.current_a is None:
            a.missing.append(Question("motors.current_a", "critical",
                                      "Running current per motor sets the driver current limit and copper width."))
    if R.count:
        if R.contact_current_a is None:
            a.missing.append(Question("relays.contact_current_a", "critical",
                                      "Current through each relay contact sizes the contact traces."))
        if R.contact_voltage is None:
            a.missing.append(Question("relays.contact_voltage", "critical",
                                      "Voltage the relay contacts switch (this tool handles up to 30 V, no mains)."))

    # ---- envelope -------------------------------------------------------
    v = effective_input_v(spec)
    if usb and spec.input_voltage not in (None, 5.0):
        a.conflicts.append(f"USB-C power is 5 V, but the input was given as {spec.input_voltage:g} V.")
    if v is not None and not usb and v not in SUPPORTED_INPUT_V:
        a.unsupported.append(f"Input {v:g} V is not a supported rail; choose one of "
                             f"{', '.join(f'{x:g}' for x in SUPPORTED_INPUT_V)} V.")
    if L.count is not None and L.count > MAX_CHANNELS:
        a.unsupported.append(f"{L.count} outputs requested; this engine builds up to {MAX_CHANNELS}.")
    if L.current_per_channel_a and L.current_per_channel_a > MAX_CHANNEL_A:
        a.unsupported.append(f"{L.current_per_channel_a:g} A per output exceeds the {MAX_CHANNEL_A:g} A channel design "
                             "(SS14 flyback diode, SOT-23 MOSFET).")
    sep_load = bool(L.count and L.voltage and v and abs(L.voltage - v) > 1e-6)
    if sep_load:
        if usb or bat:
            a.unsupported.append("A separate load supply is supported only on boards powered from a DC input.")
        elif L.voltage not in SUPPORTED_INPUT_V or L.voltage > 24:
            a.unsupported.append(f"Load supply {L.voltage:g} V: separate load supplies of "
                                 f"{', '.join(f'{x:g}' for x in SUPPORTED_INPUT_V)} V are supported.")
        else:
            a.assumptions.append(f"Loads get their own {L.voltage:g} V screw terminal (separate supply, common ground).")
    if usb and L.count and L.voltage and abs(L.voltage - 5.0) > 1e-6:
        a.conflicts.append(f"Loads are {L.voltage:g} V but a USB-C board only has 5 V.")
    if bat and (L.count or M.count or R.count):
        a.unsupported.append("Switched loads, motors or relays on a battery-powered board are not supported yet "
                             "(the battery path is sized for the controller and sensors).")
    if bat and spec.power_input not in (None, "usb_c"):
        a.unsupported.append("Battery charging is supported from USB-C only.")
    if spec.usb_programming and spec.controller != "esp32":
        a.conflicts.append("Programming over USB needs the on-board ESP32.")
    if M.count:
        if M.count > MAX_MOTORS:
            a.unsupported.append(f"{M.count} motors requested; up to {MAX_MOTORS} H-bridges are supported.")
        if M.voltage and not (MOTOR_V[0] <= M.voltage <= MOTOR_V[1]):
            a.unsupported.append(f"Motor supply {M.voltage:g} V is outside {MOTOR_V[0]:g}-{MOTOR_V[1]:g} V "
                                 "(DRV8871 H-bridge).")
        if M.current_a and M.current_a > MOTOR_MAX_A:
            a.unsupported.append(f"{M.current_a:g} A per motor exceeds the {MOTOR_MAX_A:g} A continuous rating used "
                                 "for the DRV8871 here.")
        if M.voltage and v and abs(M.voltage - v) > 1e-6 and not (sep_load and abs(M.voltage - L.voltage) < 1e-6):
            a.unsupported.append(f"Motors at {M.voltage:g} V need to run from the input ({v:g} V)"
                                 + (f" or the load supply ({L.voltage:g} V)." if sep_load else "."))
        if usb or bat:
            a.unsupported.append("Motors need a DC input of 6.5-24 V, not USB or battery power.")
    if R.count:
        if R.count > MAX_RELAYS:
            a.unsupported.append(f"{R.count} relays requested; up to {MAX_RELAYS} are supported.")
        if R.contact_voltage and R.contact_voltage > RELAY_MAX_CONTACT_V:
            a.unsupported.append(f"Relay contacts switching {R.contact_voltage:g} V: only up to "
                                 f"{RELAY_MAX_CONTACT_V:g} V is supported (mains needs isolation and creepage "
                                 "design that this tool does not do).")
        if R.contact_current_a and R.contact_current_a > RELAY_MAX_CONTACT_A:
            a.unsupported.append(f"{R.contact_current_a:g} A per relay contact exceeds the {RELAY_MAX_CONTACT_A:g} A "
                                 "relay rating.")
        if v is not None and v not in RELAY_COIL_V:
            a.unsupported.append(f"Relay coils run from the input: it must be 5, 12 or 24 V (input is {v:g} V).")
    for i in spec.interfaces:
        if i in ("rs485", "can") and spec.controller != "esp32":
            a.unsupported.append(f"{i.upper()} needs the on-board ESP32 (it provides the UART/CAN controller).")
    if spec.controller == "external_i2c" and any(x.kind == "ds18b20" for x in spec.sensors):
        a.unsupported.append("DS18B20 (1-Wire) needs the on-board ESP32; use the SHT31 for temperature over I2C instead.")
    if spec.controller == "external_i2c" and not (L.count or M.count or R.count or spec.sensors or spec.interfaces):
        a.conflicts.append("An external-controller board with no outputs and no sensors has nothing to do.")
    if (spec.board_width_mm is None) != (spec.board_height_mm is None):
        a.missing.append(Question("board size", "critical", "Only one board dimension was given; give both or neither."))
    kinds = [x.kind for x in spec.sensors]
    dup = sorted({k for k in kinds if kinds.count(k) > 1})
    if dup:
        a.unsupported.append("Only one of each sensor is supported (same I2C address): " + ", ".join(dup))
    a.unsupported += [f"Requested but not supported: {u}" for u in spec.unsupported_requests]

    # ---- derived values and proposed defaults ------------------------------
    plan = io_plan(spec)
    if spec.controller == "esp32" and plan["direct"] is False and plan["outputs"] and plan["free_gpio"] < 0:
        a.unsupported.append("Not enough ESP32 pins for these outputs and interfaces.")
    total = (L.count or 0) * (L.current_per_channel_a or 0)
    motor_total = (M.count or 0) * (M.current_a or 0)
    a.derived["total_load_a"] = round(total, 3)
    a.derived["total_motor_a"] = round(motor_total, 3)
    a.derived["pca9685_chips"] = plan["pca9685_chips"]
    a.derived["io"] = plan
    if usb and total + 0.5 > USB_MAX_TOTAL_A:
        a.unsupported.append(f"{total:g} A of loads plus about 0.5 A for the logic exceeds the {USB_MAX_TOTAL_A:g} A "
                             "this tool allows from USB-C; use a DC input instead.")
    rail_a = total + (motor_total if not sep_load or (M.voltage and v and abs(M.voltage - v) < 1e-6) else 0)
    need_planes = rail_a > TWO_LAYER_MAX_TOTAL_A or (L.count or 0) > 16
    if spec.layers is None:
        a.defaults["layers"] = 4 if need_planes else 2
        a.assumptions.append(f"{a.defaults['layers']} layers proposed "
                             f"({'load current ' + format(rail_a, 'g') + ' A needs a power plane' if need_planes else 'sufficient for this density'}).")
    elif spec.layers == 2 and rail_a > TWO_LAYER_MAX_TOTAL_A:
        a.conflicts.append(f"2 layers requested, but {rail_a:g} A total load is more than a 1.5 mm trace should carry "
                           f"({TWO_LAYER_MAX_TOTAL_A:g} A limit used here). Choose 4 layers or reduce the current.")
    if spec.mounting_holes is None:
        a.defaults["mounting_holes"] = 4
        a.assumptions.append("Four M3 mounting holes in the corners.")
    if spec.board_width_mm is None and spec.board_height_mm is None:
        a.defaults["board_size"] = "auto"
        a.assumptions.append("Board size chosen automatically as the smallest size the placement fits (5 mm steps).")
    if L.count and L.kind is None:
        a.assumptions.append("Load type unknown: flyback diodes fitted on every channel (safe for inductive loads).")
    if usb and total:
        a.assumptions.append("USB-C loads need a USB-C power adapter rated 5 V / 3 A; a PC port may supply only 0.5 A.")
    a.ready = not [q for q in a.missing if q.tier == "critical"] and not a.conflicts and not a.unsupported
    return a
