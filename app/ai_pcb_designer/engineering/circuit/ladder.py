"""Complexity ladder of reference designs used to stress the pipeline (~30 to ~200 parts)."""
from . import blocks as B
from .builder import CircuitBuilder
from .model import BoardSpec, Circuit, DesignRules, MountingHole, ZoneSpec


def _holes(w, h, inset=4.0):
    return [MountingHole(x_mm=x, y_mm=y) for x, y in
            ((inset, inset), (w - inset, inset), (inset, h - inset), (w - inset, h - inset))]


def esp32_fan_controller() -> Circuit:
    """L1 (~32 parts): the spec's example. 12 V in, buck 3.3 V, ESP32, DS18B20, PWM fan output."""
    W, H = 100, 70
    b = CircuitBuilder("l1_esp32_fan", "L1: ESP32 temperature-controlled fan",
                       BoardSpec(width_mm=W, height_mm=H, mounting_holes=_holes(W, H)))
    v12, v3, gnd = b.power("+12V", width=1.0), b.power("+3V3", width=0.5), b.power("GND", width=0.5)
    with b.sheet("Power"):
        B.screw_input(b, v12, edge="left")
        B.protected_branch(b, v12, b.net("VLOGIC", width=0.5), hold="750mA")
        B.buck_3v3(b, "VLOGIC", v3)
        B.power_led(b, v3)
    with b.sheet("MCU"):
        io = B.esp32_core(b, v3, edge="top", use=("IO2", "IO4", "IO25"))
        B.status_led(b, io["IO2"])
    with b.sheet("IO"):
        B.ds18b20(b, io["IO4"], v3)
        B.mosfet_channel(b, 1, io["IO25"], v12, edge="right")
    return b.build()


def pwm8_driver() -> Circuit:
    """L2 (~75 parts): PCA9685 with 8 MOSFET channels, LDO logic, I2C host header."""
    W, H = 110, 75
    b = CircuitBuilder("l2_pwm8", "L2: 8-channel I2C PWM load driver",
                       BoardSpec(width_mm=W, height_mm=H, mounting_holes=_holes(W, H)))
    v12, v3, gnd = b.power("+12V", width=1.5), b.power("+3V3", width=0.4), b.power("GND", width=0.6)
    sda, scl, oe = b.net("SDA"), b.net("SCL"), b.net("OE_N")
    with b.sheet("Power"):
        B.screw_input(b, v12, edge="left")
        B.protected_branch(b, v12, b.net("VLOGIC", width=0.4), hold="200mA")
        B.ldo_3v3(b, "VLOGIC", v3)
        B.power_led(b, v3)
    with b.sheet("Control"):
        B.i2c_header(b, sda, scl, oe, v3, edge="left")
        B.i2c_pullups(b, sda, scl, v3)
        with b.block("oe_pu"):
            b.R("10k", v3, oe, description="OE pull-up: outputs disabled until host drives low")
        outs, _ = B.pca9685(b, sda, scl, oe, v3, address=0, used=8)
    with b.sheet("Channels"):
        for i in range(8):
            B.mosfet_channel(b, i + 1, outs[i], v12, edge="bottom" if i < 4 else "top")
    return b.build()


def pwm16_sensor_board_4l() -> Circuit:
    """L3 on 4 layers (In1 GND, In2 +12V planes): the escalation a user would approve."""
    c = pwm16_sensor_board()
    c.name, c.title = "l3b_pwm16_sense_4l", "L3b: 16-ch PWM driver with sensing (4 layers)"
    c.board.layers = 4
    c.board.zones = [ZoneSpec(net="GND", layer="In1.Cu", plane=True), ZoneSpec(net="+12V", layer="In2.Cu", plane=True),
                     ZoneSpec(net="GND", layer="F.Cu"), ZoneSpec(net="GND", layer="B.Cu")]
    return c


def pwm16_sensor_board() -> Circuit:
    """L3 (~150 parts): 16 PWM channels, 4-ch ADC front end, humidity sensor."""
    W, H = 150, 100
    b = CircuitBuilder("l3_pwm16_sense", "L3: 16-ch PWM driver with sensing",
                       BoardSpec(width_mm=W, height_mm=H, mounting_holes=_holes(W, H)))
    v12, v3, gnd = b.power("+12V", width=1.5), b.power("+3V3", width=0.4), b.power("GND", width=0.6)
    sda, scl, oe = b.net("SDA"), b.net("SCL"), b.net("OE_N")
    with b.sheet("Power"):
        B.screw_input(b, v12, edge="left")
        B.protected_branch(b, v12, b.net("VLOGIC", width=0.4), hold="200mA")
        B.ldo_3v3(b, "VLOGIC", v3)
        B.power_led(b, v3)
    with b.sheet("Control"):
        B.i2c_header(b, sda, scl, oe, v3, edge="left")
        B.i2c_pullups(b, sda, scl, v3)
        with b.block("oe_pu"):
            b.R("10k", v3, oe, description="OE pull-up: outputs disabled until host drives low")
        outs, _ = B.pca9685(b, sda, scl, oe, v3, address=0)
    with b.sheet("Sensors"):
        B.ads1115_frontend(b, sda, scl, v3, edge="right")
        B.sht31(b, sda, scl, v3)
    for half in range(2):
        with b.sheet(f"Channels {8*half+1}-{8*half+8}"):
            for i in range(8 * half, 8 * half + 8):
                B.mosfet_channel(b, i + 1, outs[i], v12, edge="bottom" if half == 0 else "top")
    return b.build()


def esp32_pwm24_controller() -> Circuit:
    """L4 (~205 parts): ESP32 + 2x PCA9685 + 24 channels, 4 layers with GND and +12V planes."""
    W, H = 190, 120
    b = CircuitBuilder(
        "l4_esp32_pwm24", "L4: ESP32 24-channel PWM controller",
        BoardSpec(width_mm=W, height_mm=H, layers=4, mounting_holes=_holes(W, H),
                  zones=[ZoneSpec(net="GND", layer="In1.Cu", plane=True),
                         ZoneSpec(net="+12V", layer="In2.Cu", plane=True),
                         ZoneSpec(net="GND", layer="F.Cu"), ZoneSpec(net="GND", layer="B.Cu")]),
        DesignRules(power_track_width_mm=0.5))
    v12, v3, gnd = b.power("+12V", width=1.0), b.power("+3V3", width=0.4), b.power("GND", width=0.5)
    with b.sheet("Power"):
        B.screw_input(b, v12, edge="left")
        B.protected_branch(b, v12, b.net("VLOGIC", width=0.5), hold="750mA")
        B.buck_3v3(b, "VLOGIC", v3)
        B.power_led(b, v3)
    with b.sheet("MCU"):
        io = B.esp32_core(b, v3, edge="right", use=("IO2", "IO4", "IO5", "IO21", "IO22"))
        B.status_led(b, io["IO2"])
        B.ds18b20(b, io["IO4"], v3)
        sda, scl, oe = io["IO21"], io["IO22"], io["IO5"]
        B.i2c_pullups(b, sda, scl, v3)
        with b.block("oe_pu"):
            b.R("10k", v3, oe, description="OE pull-up: outputs disabled until firmware drives low")
    with b.sheet("PWM Drivers"):
        outs_a, _ = B.pca9685(b, sda, scl, oe, v3, address=0, name="pwmA", used=12)
        outs_b, _ = B.pca9685(b, sda, scl, oe, v3, address=1, name="pwmB", used=12)
    pwm = outs_a[:12] + outs_b[:12]
    for q in range(4):
        with b.sheet(f"Channels {6*q+1}-{6*q+6}"):
            for i in range(6 * q, 6 * q + 6):
                B.mosfet_channel(b, i + 1, pwm[i], v12, edge="bottom" if i < 12 else "top")
    return b.build()


LADDER = {"L1": esp32_fan_controller, "L2": pwm8_driver, "L3": pwm16_sensor_board,
          "L3b": pwm16_sensor_board_4l, "L4": esp32_pwm24_controller}
