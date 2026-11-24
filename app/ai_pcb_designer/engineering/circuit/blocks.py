"""Reusable functional blocks built from verified KiCad library parts.

Each block wires real symbols by their real pin numbers (checked against the
installed libraries). Values that come from a datasheet's typical application
and have not been checked against the datasheet in this codebase are marked
`ai_suggested` so the UI never presents them as verified.
"""
from __future__ import annotations

from .builder import CircuitBuilder
from .model import VerificationStatus

UNVERIFIED = {"verification_status": VerificationStatus.ai_suggested}

E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]
LED_VF = {"RED": 2.0, "GREEN": 2.2, "YELLOW": 2.1, "BLUE": 2.9, "WHITE": 2.9}


def led_resistor(supply_v: float, color: str = "RED", ma: float = 2.0) -> str:
    """Smallest E12 resistor giving at most `ma` through an indicator LED."""
    import math
    r = max(10.0, (supply_v - LED_VF.get(color.upper(), 2.0)) / (ma / 1000))
    dec = 10 ** math.floor(math.log10(r))
    val = next((e * dec for e in E12 if e * dec >= r - 1e-9), 10 * dec)
    return f"{val / 1000:g}k" if val >= 1000 else f"{val:g}"


def screw_input(b: CircuitBuilder, vin: str, gnd: str = "GND", edge="left", name="pwr_in",
                volts: float = 12.0, tvs: str = "SMAJ15CA", cap: str = "100uF 25V"):
    """Screw-terminal DC input with TVS clamp and bulk capacitor on the load rail."""
    with b.block(name):
        b.part("Connector:Screw_Terminal_01x02", f"VIN {volts:g}V", {"1": vin, "2": gnd}, "TERM2", prefix="J",
               description=f"DC input ({volts:g} V nominal)", edge=edge, anchor=True)
        b.part("Device:D_TVS", tvs, {"1": vin, "2": gnd}, "SMA", prefix="D",
               description="Input transient clamp (standoff >= 1.15 x nominal input)", **UNVERIFIED)
        b.part("Device:C_Polarized", cap, {"1": vin, "2": gnd}, "CP6.3", description="Bulk input")


def protected_branch(b: CircuitBuilder, vin: str, vout: str, gnd: str = "GND", hold="500mA", name="pwr_prot",
                     cap_v: int = 25, fuse_v: int = 16):
    """Reverse-polarity diode + resettable fuse feeding the logic supply only."""
    mid = b.net(f"{vout}_D")
    with b.block(name):
        b.part("Diode:SS14", "SS14", {"2": vin, "1": mid}, "SMA", prefix="D", description="Reverse polarity (logic)")
        b.part("Device:Polyfuse", f"{hold} {fuse_v}V", {"1": mid, "2": vout}, "F1206",
               description=f"Logic supply fuse (rated >= {fuse_v} V)", **UNVERIFIED)
        b.C(f"10uF {cap_v}V", vout, gnd, fp="C1206", description="Logic branch input")


def buck_3v3(b: CircuitBuilder, vin: str, v3: str, gnd: str = "GND", name="buck", cap_v: int = 25):
    """AP63203 fixed 3.3 V synchronous buck (3.8-32 V in)."""
    sw, bst = b.net(f"{name}_SW"), b.net(f"{name}_BST")
    with b.block(name):
        b.part("Regulator_Switching:AP63203WU", "AP63203WU",
               {"3": vin, "2": vin, "4": gnd, "5": sw, "6": bst, "1": v3}, prefix="U",
               description="3.3 V buck; EN tied to VIN, FB is the fixed-output sense", anchor=True)
        b.C(f"10uF {cap_v}V", vin, gnd, fp="C1206", description="Buck input", **UNVERIFIED)
        b.C("100nF", vin, gnd, description="Buck input HF")
        b.C("100nF", bst, sw, description="Bootstrap", **UNVERIFIED)
        b.part("Device:L", "4.7uH", {"1": sw, "2": v3}, "L6045", description="Buck inductor", **UNVERIFIED)
        b.C("22uF", v3, gnd, fp="C1206", description="Buck output", **UNVERIFIED)
        b.C("22uF", v3, gnd, fp="C1206", description="Buck output", **UNVERIFIED)


def ldo_3v3(b: CircuitBuilder, vin: str, v3: str, gnd: str = "GND", name="ldo", cap_v: int = 25):
    """AMS1117-3.3 (VIN <= 15 V). Only for low logic current: dissipation = (VIN-3.3) x I."""
    with b.block(name):
        b.part("Regulator_Linear:AMS1117-3.3", "AMS1117-3.3", {"3": vin, "2": v3, "1": gnd}, prefix="U",
               description="3.3 V LDO for logic (<30 mA budget)", anchor=True)
        b.C(f"10uF {cap_v}V", vin, gnd, fp="C1206", description="LDO input")
        b.C("22uF", v3, gnd, fp="C1206", description="LDO output (stability)")


def power_led(b: CircuitBuilder, v: str, gnd: str = "GND", name="pwr_led"):
    a = b.net(f"{name}_A")
    with b.block(name):
        b.R(led_resistor(3.3, "GREEN"), v, a, description="Power LED current limit (~2 mA)")
        b.LED("GREEN", a, gnd, description="Power indicator")


ESP32_GPIO_PIN = {"IO34": "6", "IO35": "7", "IO2": "24", "IO4": "26", "IO5": "29", "IO13": "16", "IO14": "13", "IO16": "27",
                  "IO17": "28", "IO18": "30", "IO19": "31", "IO21": "33", "IO22": "36", "IO23": "37",
                  "IO25": "10", "IO26": "11", "IO27": "12", "IO32": "8", "IO33": "9"}


def esp32_core(b: CircuitBuilder, v3: str, gnd: str = "GND", edge="top", name="mcu",
               use=("IO2",), extra_inputs=()) -> dict[str, str]:
    """ESP32-WROOM-32 with EN RC reset, BOOT/EN buttons and a UART programming header.
    Only the GPIOs listed in `use` are connected; all others get no-connect flags."""
    en, io0 = b.net("ESP_EN"), b.net("ESP_IO0")
    tx, rx = b.net("ESP_TXD0"), b.net("ESP_RXD0")
    io = {k: b.net(f"ESP_{k}") for k in tuple(use) + tuple(extra_inputs)}
    pins = {"1": gnd, "15": gnd, "38": gnd, "39": gnd, "2": v3, "3": en, "25": io0, "35": tx, "34": rx}
    pins.update({ESP32_GPIO_PIN[k]: n for k, n in io.items()})
    with b.block(name):
        b.part("RF_Module:ESP32-WROOM-32", "ESP32-WROOM-32", pins,
               prefix="U", description="Wi-Fi/BLE MCU module", edge=edge, anchor=True)
        b.C("10uF", v3, gnd, fp="C0805", description="Module bulk")
        b.C("100nF", v3, gnd, description="Module HF")
        b.R("10k", v3, en, description="EN pull-up")
        b.C("1uF", en, gnd, description="EN power-on delay")
        b.R("10k", v3, io0, description="IO0 pull-up (run mode)")
        b.part("Switch:SW_Push", "RESET", {"1": en, "2": gnd}, "BTN", description="Reset")
        b.part("Switch:SW_Push", "BOOT", {"1": io0, "2": gnd}, "BTN", description="Boot mode")
        b.part("Connector_Generic:Conn_01x06", "PROG", {"1": gnd, "2": v3, "3": tx, "4": rx, "5": en, "6": io0},
               "HDR1x6", prefix="J", description="UART programming (3.3 V)")
    return io


def status_led(b: CircuitBuilder, gpio: str, gnd: str = "GND", color="BLUE", name="stat_led"):
    a = b.net(f"{name}_A")
    with b.block(name):
        b.R(led_resistor(3.3, color), gpio, a, description="Status LED current limit (~2 mA)")
        b.LED(color, a, gnd, description="Status indicator")


def i2c_pullups(b: CircuitBuilder, sda: str, scl: str, v3: str, name="i2c"):
    with b.block(name):
        b.R("4.7k", v3, sda, description="I2C SDA pull-up")
        b.R("4.7k", v3, scl, description="I2C SCL pull-up")


def i2c_header(b: CircuitBuilder, sda: str, scl: str, oe: str, v3: str, gnd="GND", edge="left", name="i2c_hdr"):
    with b.block(name):
        b.part("Connector_Generic:Conn_01x06", "I2C", {"1": gnd, "2": v3, "3": sda, "4": scl, "5": oe, "6": gnd},
               "HDR1x6", prefix="J", description="I2C host + output enable", edge=edge, anchor=True)


def pca9685(b: CircuitBuilder, sda: str, scl: str, oe: str, v3: str, gnd="GND", address=0, name="pwm",
            used=16):
    """PCA9685 16-ch PWM. Address bits A0..A5 strapped; OE active-low (pull-up => outputs off at reset).
    Outputs beyond `used` are left unconnected (no-connect flags)."""
    outs = [b.net(f"{name}_OUT{i}") for i in range(used)]
    pins = {"28": v3, "14": gnd, "26": scl, "27": sda, "23": oe, "25": gnd}
    for bit, pin in enumerate(("1", "2", "3", "4", "5", "24")):
        pins[pin] = v3 if address >> bit & 1 else gnd
    led_pins = ["6", "7", "8", "9", "10", "11", "12", "13", "15", "16", "17", "18", "19", "20", "21", "22"]
    pins.update(dict(zip(led_pins, outs)))
    with b.block(name):
        b.part("Driver_LED:PCA9685PW", "PCA9685PW", pins, prefix="U",
               description=f"16-ch PWM, I2C addr 0x{0x40 | address:02X}", anchor=True)
        b.C("100nF", v3, gnd, description="PCA9685 decoupling")
        b.C("10uF", v3, gnd, fp="C0805", description="PCA9685 bulk")
    return outs, led_pins


def mosfet_channel(b: CircuitBuilder, idx: int, pwm: str, vload: str, gnd="GND", edge="bottom",
                   out_width=0.6, sheet=None, load_v: float = 12.0):
    """Low-side switch for a 12 V load up to 0.5 A (assumed), with flyback diode and on-indicator."""
    g, out, la = b.net(f"CH{idx}_G"), b.net(f"CH{idx}_OUT", width=out_width), b.net(f"CH{idx}_LED")
    with b.block(f"ch{idx:02d}", sheet=sheet):
        b.part("Connector:Screw_Terminal_01x02", f"OUT{idx}", {"1": vload, "2": out}, "TERM2", prefix="J",
               description=f"Load {idx}: + and switched -", edge=edge, anchor=True)
        b.part("Transistor_FET:AO3400A", "AO3400A", {"1": g, "2": gnd, "3": out}, prefix="Q",
               description="Logic-level N-MOSFET low-side switch")
        b.R("100", pwm, g, description="Gate resistor")
        b.R("100k", g, gnd, description="Gate pull-down (off when PWM is hi-Z)")
        b.part("Diode:SS14", "SS14", {"1": vload, "2": out}, "SMA", prefix="D", description="Flyback")
        b.R(led_resistor(load_v), vload, la, description=f"Indicator current limit (~2 mA at {load_v:g} V)")
        b.LED("RED", la, out, description=f"Channel {idx} on")


def ds18b20(b: CircuitBuilder, dq: str, v3: str, gnd="GND", name="temp"):
    with b.block(name):
        b.part("Sensor_Temperature:DS18B20", "DS18B20", {"1": gnd, "2": dq, "3": v3}, "TO92", prefix="U",
               description="1-Wire temperature sensor", anchor=True)
        b.R("4.7k", v3, dq, description="1-Wire pull-up")


def ads1115_frontend(b: CircuitBuilder, sda, scl, v3, gnd="GND", edge="right", name="adc"):
    """ADS1115 with 4 RC-filtered inputs on two screw terminals (inputs must stay within 0..VDD)."""
    ain = [b.net(f"AIN{i}") for i in range(4)]
    raw = [b.net(f"AIN{i}_IN") for i in range(4)]
    with b.block(name):
        b.part("Analog_ADC:ADS1115IDGS", "ADS1115", {"1": gnd, "3": gnd, "8": v3, "9": sda, "10": scl,
                                                     "4": ain[0], "5": ain[1], "6": ain[2], "7": ain[3]},
               prefix="U", description="16-bit ADC, I2C 0x48", anchor=True)
        b.C("100nF", v3, gnd, description="ADC decoupling")
        for i in range(4):
            b.R("1k", raw[i], ain[i], description=f"AIN{i} series/filter")
            b.C("100nF", ain[i], gnd, description=f"AIN{i} filter (fc ~1.6 kHz)")
    for k in range(2):
        with b.block(f"{name}_in{k}"):
            b.part("Connector:Screw_Terminal_01x02", f"AIN{2*k}-{2*k+1}",
                   {"1": raw[2 * k], "2": raw[2 * k + 1]}, "TERM2", prefix="J",
                   description="Analog inputs", edge=edge, anchor=True)


def sht31(b: CircuitBuilder, sda, scl, v3, gnd="GND", name="rh"):
    rst = b.net(f"{name}_RST")
    with b.block(name):
        b.part("Sensor_Humidity:SHT31-DIS", "SHT31-DIS", {"1": sda, "2": gnd, "4": scl, "5": v3, "6": rst, "7": gnd,
                                                          "8": gnd, "9": gnd}, prefix="U",
               description="Humidity/temperature, I2C 0x44", anchor=True)
        b.C("100nF", v3, gnd, description="SHT31 decoupling")
        b.R("10k", v3, rst, description="nRESET pull-up")


# =====================================================================================
# Expanded block library. Pin numbers checked against the KiCad 7 and KiCad 10 standard
# libraries (identical in both, except where a pin is given by name with "@NAME", which
# the compiler resolves per installed KiCad version, e.g. the USB-C shield "S1" / "SH").
# =====================================================================================

FP_EXTRA = {
    "USBC": "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
    "TERM3": "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-3-5.08_1x03_P5.08mm_Horizontal",
    "JST_PH2": "Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
    "BARREL": "Connector_BarrelJack:BarrelJack_Horizontal",
    "SOIC8": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "SOD123": "Diode_SMD:D_SOD-123",
    "HDR1x2": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
    "C0805": "Capacitor_SMD:C_0805_2012Metric",
}


def usb_c_port(b: CircuitBuilder, vbus: str, gnd: str = "GND", dp: str | None = None, dm: str | None = None,
               edge="left", name="usb", fuse_hold="1.5A"):
    """USB-C receptacle as a 5 V sink (5.1 k CC pull-downs = USB default / 1.5 A / 3 A source
    capability), resettable fuse on VBUS, optional USB 2.0 data with ESD protection.
    Returns the fused 5 V net."""
    raw = b.net(f"{name}_VBUS_RAW")
    cc1, cc2 = b.net(f"{name}_CC1"), b.net(f"{name}_CC2")
    pins = {"A4": raw, "A9": raw, "B4": raw, "B9": raw, "A1": gnd, "A12": gnd, "B1": gnd, "B12": gnd,
            "A5": cc1, "B5": cc2, "@SHIELD": gnd}
    if dp and dm:
        pins.update({"A6": dp, "B6": dp, "A7": dm, "B7": dm})
    with b.block(name):
        b.part("Connector:USB_C_Receptacle_USB2.0_16P", "USB-C", pins, FP_EXTRA["USBC"], prefix="J",
               description="USB-C receptacle (power sink" + (", USB 2.0 data)" if dp else " only)"),
               edge=edge, anchor=True)
        b.R("5.1k", cc1, gnd, description="CC1 pull-down (identifies a sink)")
        b.R("5.1k", cc2, gnd, description="CC2 pull-down (identifies a sink)")
        b.part("Device:Polyfuse", f"{fuse_hold} 6V", {"1": raw, "2": vbus}, "F1206",
               description="VBUS resettable fuse", **UNVERIFIED)
        b.C("10uF", vbus, gnd, fp=FP_EXTRA["C0805"], description="VBUS bulk")
        if dp and dm:
            b.part("Power_Protection:USBLC6-2SC6", "USBLC6-2SC6",
                   {"1": dp, "6": dp, "3": dm, "4": dm, "5": vbus, "2": gnd}, prefix="U",
                   description="USB data ESD protection")
    return vbus


def barrel_input(b: CircuitBuilder, vin: str, gnd: str = "GND", edge="left", name="pwr_in",
                 volts: float = 12.0, tvs: str = "SMAJ15CA", cap: str = "100uF 25V"):
    """5.5 x 2.1 mm barrel jack (centre positive), TVS clamp and bulk capacitor."""
    with b.block(name):
        b.part("Connector:Barrel_Jack_Switch", f"DC {volts:g}V", {"1": vin, "2": gnd, "3": gnd}, FP_EXTRA["BARREL"],
               prefix="J", description=f"DC input {volts:g} V, centre positive", edge=edge, anchor=True)
        b.part("Device:D_TVS", tvs, {"1": vin, "2": gnd}, "SMA", prefix="D",
               description="Input transient clamp (standoff >= 1.15 x nominal input)", **UNVERIFIED)
        b.part("Device:C_Polarized", cap, {"1": vin, "2": gnd}, "CP6.3", description="Bulk input")


def ldo_ap2112(b: CircuitBuilder, vin: str, v3: str, gnd: str = "GND", name="ldo"):
    """AP2112K-3.3: 600 mA LDO, 250 mV typ. dropout at 600 mA (inputs 3.5-6 V)."""
    with b.block(name):
        b.part("Regulator_Linear:AP2112K-3.3", "AP2112K-3.3", {"1": vin, "2": gnd, "3": vin, "5": v3}, prefix="U",
               description="3.3 V LDO, 600 mA (EN tied to VIN)", anchor=True)
        b.C("10uF", vin, gnd, fp=FP_EXTRA["C0805"], description="LDO input")
        b.C("10uF", v3, gnd, fp=FP_EXTRA["C0805"], description="LDO output")


def li_ion_charger(b: CircuitBuilder, vbus: str, vsys: str, gnd: str = "GND", charge_ma: int = 500,
                   name="charger", edge="left"):
    """Single-cell Li-ion/LiPo: MCP73831 linear charger from USB 5 V (4.20 V regulation,
    I = 1000 V / R_PROG), and load sharing: the system runs from USB through a Schottky
    diode when present, otherwise from the cell through a P-MOSFET (DMG2301L) whose gate
    is pulled up by VBUS. Returns the battery net."""
    vbat, stat, prog, led = b.net("VBAT", width=0.5), b.net(f"{name}_STAT"), b.net(f"{name}_PROG"), b.net(f"{name}_LED")
    rprog_k = 1000.0 / charge_ma
    with b.block(name):
        b.part("Battery_Management:MCP73831-2-OT", "MCP73831-2-OT",
               {"1": stat, "2": gnd, "3": vbat, "4": vbus, "5": prog}, prefix="U",
               description=f"Li-ion charger, {charge_ma} mA, 4.20 V", anchor=True)
        b.R(f"{rprog_k:.3g}k", prog, gnd, description=f"PROG: {charge_ma} mA charge current (1000 V / R)")
        b.C("4.7uF", vbus, gnd, fp=FP_EXTRA["C0805"], description="Charger input")
        b.C("4.7uF", vbat, gnd, fp=FP_EXTRA["C0805"], description="Charger output (battery)")
        b.R(led_resistor(5.0), vbus, led, description="Charge LED current limit (~2 mA)")
        b.LED("RED", led, stat, description="Charging (on while charging)")
    with b.block(f"{name}_bat", sheet=None):
        b.part("Connector_Generic:Conn_01x02", "BATTERY", {"1": vbat, "2": gnd}, FP_EXTRA["JST_PH2"], prefix="J",
               description="1-cell Li-ion/LiPo, JST PH 2 mm: pin 1 = +. Check your cell's wiring - "
                           "JST PH polarity is not standardised.", edge=edge, anchor=True)
    with b.block(f"{name}_share"):
        b.part("Diode:SS14", "SS14", {"2": vbus, "1": vsys}, "SMA", prefix="D",
               description="USB -> system (load sharing)")
        b.part("Transistor_FET:DMG2301L", "DMG2301L", {"1": vbus, "2": vsys, "3": vbat}, prefix="Q",
               description="Battery -> system when USB is absent (P-MOSFET, gate = VBUS)")
        b.R("100k", vbus, gnd, description="Gate pull-down: MOSFET on without USB")
        b.C("10uF", vsys, gnd, fp=FP_EXTRA["C0805"], description="System rail")
    return vbat


def battery_sense(b: CircuitBuilder, vbat: str, adc: str, gnd: str = "GND", name="vbat_sense"):
    with b.block(name):
        b.R("100k", vbat, adc, description="Battery voltage divider (1/2)")
        b.R("100k", adc, gnd, description="Battery voltage divider (1/2)")
        b.C("100nF", adc, gnd, description="ADC input filter")


def ch340_usb_uart(b: CircuitBuilder, dp: str, dm: str, v3: str, esp_tx: str, esp_rx: str, en: str, io0: str,
                   gnd: str = "GND", name="usb_uart"):
    """CH340C USB-UART at 3.3 V (V3 tied to VCC, internal oscillator) with the usual
    two-transistor auto-reset (DTR/RTS -> EN/IO0) so the ESP32 is programmed over USB."""
    dtr, rts = b.net(f"{name}_DTR"), b.net(f"{name}_RTS")
    b1, b2 = b.net(f"{name}_B1"), b.net(f"{name}_B2")
    with b.block(name):
        b.part("Interface_USB:CH340C", "CH340C",
               {"1": gnd, "2": esp_rx, "3": esp_tx, "4": v3, "5": dp, "6": dm, "13": dtr, "14": rts, "16": v3},
               prefix="U", description="USB-UART (3.3 V supply, V3 = VCC)", anchor=True,
               erc_waive={"4": "CH340 datasheet: at a 3.3 V supply V3 is tied to VCC (KiCad types V3 "
                               "as a power output, so ERC sees two outputs on +3V3)"})
        b.C("100nF", v3, gnd, description="CH340C decoupling")
        b.R("10k", dtr, b1, description="Auto-reset base resistor")
        b.R("10k", rts, b2, description="Auto-reset base resistor")
        b.part("Transistor_BJT:MMBT3904", "MMBT3904", {"1": b1, "2": rts, "3": en}, prefix="Q",
               description="Auto-reset: DTR/RTS -> EN")
        b.part("Transistor_BJT:MMBT3904", "MMBT3904", {"1": b2, "2": dtr, "3": io0}, prefix="Q",
               description="Auto-reset: DTR/RTS -> IO0")


def relay_channel(b: CircuitBuilder, idx: int, ctrl: str, vcoil: str, coil_v: float, gnd="GND", edge="bottom",
                  contact_width=1.0, sheet=None):
    """Sanyou SRD relay (SPDT, contacts 10 A at 30 V DC per datasheet; used here only for
    <= 30 V loads) driven by an NPN transistor with flyback diode and on-LED. Pin roles
    from the KiCad symbol: 1 = COM, 2/5 = coil, 3 = NO, 4 = NC."""
    base, coil, led = b.net(f"RL{idx}_B"), b.net(f"RL{idx}_COIL"), b.net(f"RL{idx}_LED")
    com = b.net(f"RL{idx}_COM", width=contact_width)
    no, nc = b.net(f"RL{idx}_NO", width=contact_width), b.net(f"RL{idx}_NC", width=contact_width)
    with b.block(f"rl{idx:02d}", sheet=sheet):
        b.part("Connector:Screw_Terminal_01x03", f"RELAY{idx}", {"1": no, "2": com, "3": nc}, FP_EXTRA["TERM3"],
               prefix="J", description=f"Relay {idx} contacts: NO, COM, NC (<= 30 V)", edge=edge, anchor=True)
        b.part("Relay:SANYOU_SRD_Form_C", f"SRD-{coil_v:02.0f}VDC-SL-C",
               {"1": com, "2": coil, "5": vcoil, "3": no, "4": nc}, prefix="K",
               description=f"Relay {idx}, {coil_v:g} V coil")
        b.part("Transistor_BJT:MMBT3904", "MMBT3904", {"1": base, "2": gnd, "3": coil}, prefix="Q",
               description="Coil driver (NPN low side)")
        b.R("1k", ctrl, base, description="Base resistor")
        b.R("100k", base, gnd, description="Base pull-down (off at reset)")
        b.part("Diode:1N4148W", "1N4148W", {"1": vcoil, "2": coil}, FP_EXTRA["SOD123"], prefix="D",
               description="Coil flyback")
        b.R(led_resistor(coil_v), vcoil, led, description=f"Indicator current limit (~2 mA at {coil_v:g} V)")
        b.LED("RED", led, coil, description=f"Relay {idx} on")


def drv8871_motor(b: CircuitBuilder, idx: int, in1: str, in2: str, vm: str, gnd="GND", ilim_k: float = 30.0,
                  edge="bottom", out_width=0.6, cap_v: int = 35, sheet=None):
    """DRV8871 brushed-DC H-bridge (6.5-45 V, 3.6 A peak). Current limit set by R_ILIM."""
    o1, o2 = b.net(f"M{idx}_OUT1", width=out_width), b.net(f"M{idx}_OUT2", width=out_width)
    ilim = b.net(f"M{idx}_ILIM")
    with b.block(f"m{idx:02d}", sheet=sheet):
        b.part("Connector:Screw_Terminal_01x02", f"MOTOR{idx}", {"1": o1, "2": o2}, "TERM2", prefix="J",
               description=f"Motor {idx}", edge=edge, anchor=True)
        b.part("Driver_Motor:DRV8871DDA", "DRV8871DDA",
               {"1": gnd, "7": gnd, "9": gnd, "2": in2, "3": in1, "4": ilim, "5": vm, "6": o1, "8": o2},
               prefix="U", description=f"Motor {idx} H-bridge (IN1/IN2 PWM: forward, reverse, brake, coast)")
        b.R(f"{ilim_k:g}k", ilim, gnd, description="Current limit (see datasheet I_TRIP vs R_ILIM)", **UNVERIFIED)
        b.C("100nF", vm, gnd, description="VM decoupling")
        b.C(f"10uF {cap_v}V", vm, gnd, fp="C1206", description="VM bulk", **UNVERIFIED)
        b.R("100k", in1, gnd, description="IN1 pull-down (coast at reset)")
        b.R("100k", in2, gnd, description="IN2 pull-down (coast at reset)")


def rs485(b: CircuitBuilder, tx: str, rx: str, de: str, v3: str, gnd="GND", edge="left", name="rs485"):
    """MAX3485 half-duplex RS-485 (3.3 V), DE and /RE driven together, 120 ohm termination
    selected with a jumper, fail-safe bias resistors, screw terminal A/B/GND."""
    a, bb_, term = b.net("RS485_A"), b.net("RS485_B"), b.net("RS485_TERM")
    with b.block(name):
        b.part("Connector:Screw_Terminal_01x03", "RS485", {"1": a, "2": bb_, "3": gnd}, FP_EXTRA["TERM3"],
               prefix="J", description="RS-485: A, B, GND", edge=edge, anchor=True)
        b.part("Interface_UART:MAX3485", "MAX3485", {"1": rx, "2": de, "3": de, "4": tx, "5": gnd, "6": a,
                                                     "7": bb_, "8": v3}, FP_EXTRA["SOIC8"], prefix="U",
               description="RS-485 transceiver, 3.3 V")
        b.C("100nF", v3, gnd, description="MAX3485 decoupling")
        b.R("100k", de, gnd, description="DE/RE pull-down: receive at reset")
        b.R("120", a, term, description="Bus termination (fit jumper at the line ends)")
        b.part("Connector_Generic:Conn_01x02", "TERM", {"1": term, "2": bb_}, FP_EXTRA["HDR1x2"], prefix="J",
               description="Termination jumper")
        b.R("680", v3, a, description="Fail-safe bias A", **UNVERIFIED)
        b.R("680", bb_, gnd, description="Fail-safe bias B", **UNVERIFIED)


def can_bus(b: CircuitBuilder, tx: str, rx: str, v3: str, gnd="GND", edge="left", name="can"):
    """SN65HVD230 3.3 V CAN transceiver (Rs to GND: high-speed mode), jumper-selected
    120 ohm termination, screw terminal CANH/CANL/GND."""
    h, l, term = b.net("CANH"), b.net("CANL"), b.net("CAN_TERM")
    with b.block(name):
        b.part("Connector:Screw_Terminal_01x03", "CAN", {"1": h, "2": l, "3": gnd}, FP_EXTRA["TERM3"],
               prefix="J", description="CAN: H, L, GND", edge=edge, anchor=True)
        b.part("Interface_CAN_LIN:SN65HVD230", "SN65HVD230", {"1": tx, "2": gnd, "3": v3, "4": rx, "6": l, "7": h,
                                                              "8": gnd}, prefix="U",
               description="CAN transceiver, 3.3 V, high-speed mode")
        b.C("100nF", v3, gnd, description="SN65HVD230 decoupling")
        b.R("120", h, term, description="Bus termination (fit jumper at the line ends)")
        b.part("Connector_Generic:Conn_01x02", "TERM", {"1": term, "2": l}, FP_EXTRA["HDR1x2"], prefix="J",
               description="Termination jumper")


def bme280(b: CircuitBuilder, sda, scl, v3, gnd="GND", name="env"):
    with b.block(name):
        b.part("Sensor:BME280", "BME280", {"1": gnd, "7": gnd, "2": v3, "3": sda, "4": scl, "5": gnd, "6": v3,
                                           "8": v3}, prefix="U",
               description="Pressure/humidity/temperature, I2C 0x76 (CSB high = I2C, SDO low)", anchor=True,
               erc_waive={"5": "BME280 datasheet: SDO is the I2C address strap, tied to GND for 0x76"})
        b.C("100nF", v3, gnd, description="BME280 VDD decoupling")
        b.C("100nF", v3, gnd, description="BME280 VDDIO decoupling")


def oled_header(b: CircuitBuilder, sda, scl, v3, gnd="GND", edge="top", name="oled"):
    with b.block(name):
        b.part("Connector_Generic:Conn_01x04", "OLED", {"1": gnd, "2": v3, "3": scl, "4": sda}, "HDR1x4", prefix="J",
               description="I2C OLED module (SSD1306 0.96\"): GND, VCC, SCL, SDA - check your module's order",
               edge=edge, anchor=True)
        b.C("10uF", v3, gnd, fp=FP_EXTRA["C0805"], description="Display supply bulk")


def load_input(b: CircuitBuilder, vload: str, gnd: str = "GND", edge="left", name="load_in",
               volts: float = 12.0, tvs: str = "SMAJ15CA", cap: str = "100uF 25V"):
    """Separate supply terminal for the loads (common ground with the logic)."""
    with b.block(name):
        b.part("Connector:Screw_Terminal_01x02", f"LOAD {volts:g}V", {"1": vload, "2": gnd}, "TERM2", prefix="J",
               description=f"Load supply input ({volts:g} V)", edge=edge, anchor=True)
        b.part("Device:D_TVS", tvs, {"1": vload, "2": gnd}, "SMA", prefix="D",
               description="Load supply transient clamp", **UNVERIFIED)
        b.part("Device:C_Polarized", cap, {"1": vload, "2": gnd}, "CP6.3", description="Load supply bulk")
