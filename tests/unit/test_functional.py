"""The design checks must catch real faults (injected here) and stay quiet on good designs."""
from ai_pcb_designer.engineering.circuit.builder import CircuitBuilder
from ai_pcb_designer.engineering.circuit.model import BoardSpec
from ai_pcb_designer.engineering.validation.functional import check


def _base(vin="+12V"):
    b = CircuitBuilder("t", "t", BoardSpec(width_mm=50, height_mm=50))
    b.power(vin)
    b.power("+3V3")
    b.power("GND")
    b.part("Connector:Screw_Terminal_01x02", "IN", {"1": vin, "2": "GND"}, "TERM2", prefix="J")
    return b


def _msgs(r, chk):
    return [f["message"] for f in r["findings"] if f["check"] == chk]


def test_regulator_input_over_limit():
    b = _base("+12V")
    b.part("Regulator_Linear:AP2112K-3.3", "AP2112K-3.3", {"1": "+12V", "2": "GND", "3": "+12V", "5": "+3V3"},
           prefix="U")
    r = check(b.build())
    assert any("outside 3.5-6 V" in m for m in _msgs(r, "supply"))


def test_capacitor_rating_and_unrated_cap():
    b = _base("+24V")
    b.C("10uF 16V", "+24V", "GND", fp="C1206")
    b.C("100nF", "+24V", "GND")
    r = check(b.build())
    assert any("rating below" in m for m in _msgs(r, "capacitor rating"))
    assert any("no voltage rating" in m for m in _msgs(r, "capacitor rating"))


def test_tvs_standoff_below_rail():
    b = _base("+24V")
    b.part("Device:D_TVS", "SMAJ15CA", {"1": "+24V", "2": "GND"}, "SMA", prefix="D")
    assert check(b.build())["errors"] >= 1


def test_led_too_dim_and_resistor_power():
    b = _base("+24V")
    b.R("4.7k", "+24V", "LA")
    b.LED("RED", "LA", "GND")
    b.R("1k", "+3V3", "LB")
    b.LED("BLUE", "LB", "GND")
    r = check(b.build())
    assert any("dissipates" in m for m in _msgs(r, "resistor power"))
    assert any("too dim" in m for m in _msgs(r, "LED current"))


def test_rail_without_source():
    b = CircuitBuilder("t", "t", BoardSpec(width_mm=50, height_mm=50))
    b.power("+3V3")
    b.power("GND")
    b.part("Sensor_Humidity:SHT31-DIS", "SHT31-DIS", {"5": "+3V3", "2": "GND"}, prefix="U")
    r = check(b.build())
    assert any("+3V3" in m for m in _msgs(r, "power source"))


def test_i2c_conflict_and_missing_pullup():
    b = _base()
    b.part("Sensor_Humidity:SHT31-DIS", "SHT31-DIS", {"1": "SDA", "4": "SCL", "5": "+3V3", "2": "GND"}, prefix="U")
    b.part("Sensor_Humidity:SHT31-DIS", "SHT31-DIS", {"1": "SDA", "4": "SCL", "5": "+3V3", "2": "GND"}, prefix="U")
    r = check(b.build())
    assert any("0x44" in m for m in _msgs(r, "I2C"))
    assert any("pull-up" in m for m in _msgs(r, "I2C"))


def test_esp32_boot_straps():
    b = _base()
    b.part("RF_Module:ESP32-WROOM-32", "ESP32-WROOM-32", {"2": "+3V3", "1": "GND", "3": "EN", "14": "IO12"}, prefix="U")
    b.R("10k", "+3V3", "IO12")
    r = check(b.build())
    msgs = _msgs(r, "ESP32 boot")
    assert any("IO12" in m for m in msgs) and any("EN has no pull-up" in m for m in msgs)


def test_generated_designs_are_clean():
    """Every design the compiler can produce for the standard test specs passes its own checks."""
    import pytest
    from ai_pcb_designer.engineering.design.compiler import build_circuit
    from ai_pcb_designer.engineering.design.spec import DesignSpec
    from ai_pcb_designer.kicad.detection import detect
    from ai_pcb_designer.kicad.library import LibraryManager
    caps = detect()
    if not caps.symbol_dir:
        pytest.skip("KiCad libraries not installed")
    libs = LibraryManager(caps.symbol_dir, caps.footprint_dir, caps.models3d_dir)
    specs = [
        {"controller": "esp32", "input_voltage": 24, "loads": {"count": 8, "kind": "led_strip", "voltage": 24,
                                                               "current_per_channel_a": 0.35},
         "sensors": [{"kind": "sht31"}, {"kind": "ads1115"}]},
        {"controller": "esp32", "power_input": "usb_c", "battery": {"capacity_mah": 1000}, "loads": {"count": 0},
         "sensors": [{"kind": "bme280"}], "interfaces": ["oled_i2c"]},
        {"controller": "esp32", "input_voltage": 12, "motors": {"count": 2, "voltage": 12, "current_a": 1},
         "relays": {"count": 2, "contact_current_a": 3, "contact_voltage": 24}, "interfaces": ["rs485", "can"],
         "usb_programming": True, "loads": {"count": 0}},
        {"controller": "esp32", "input_voltage": 24, "power_input": "barrel_jack",
         "loads": {"count": 6, "kind": "led_strip", "voltage": 12, "current_per_channel_a": 0.5}},
    ]
    for sp in specs:
        c = build_circuit(DesignSpec.model_validate(sp), 200, 150, 2, 4, libs)
        r = check(c)
        assert r["errors"] == 0 and r["warnings"] == 0, (sp, r["findings"])
        assert not r["not_checked"], r["not_checked"]
