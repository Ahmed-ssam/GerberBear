"""Reference circuits used by the POC and tests."""
from .model import BoardSpec, Circuit, Component, MountingHole, Net, NetKind


def attiny_led_blinker() -> Circuit:
    """ATtiny85 driving an LED, 5 V header input, decoupling cap, two M3 holes."""
    return Circuit(
        name="attiny_led_poc",
        title="POC: ATtiny85 LED blinker",
        components=[
            Component(ref="J1", symbol="Connector_Generic:Conn_01x02",
                      footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
                      value="5V_IN", description="5 V power input header", group="power",
                      pin_nets={"1": "+5V", "2": "GND"}),
            Component(ref="U1", symbol="MCU_Microchip_ATtiny:ATtiny85-20S", value="ATtiny85-20S",
                      description="8-bit AVR microcontroller", group="mcu",
                      pin_nets={"8": "+5V", "4": "GND", "5": "LED_DRV"}),
            Component(ref="C1", symbol="Device:C", footprint="Capacitor_SMD:C_0805_2012Metric",
                      value="100nF", description="U1 decoupling", group="mcu",
                      pin_nets={"1": "+5V", "2": "GND"}),
            Component(ref="R1", symbol="Device:R", footprint="Resistor_SMD:R_0805_2012Metric",
                      value="330", description="LED current limit (~9 mA at 5 V, red LED)", group="led",
                      pin_nets={"1": "LED_DRV", "2": "LED_A"}),
            Component(ref="D1", symbol="Device:LED", footprint="LED_SMD:LED_0805_2012Metric",
                      value="RED", description="Status LED", group="led",
                      pin_nets={"2": "LED_A", "1": "GND"}),
        ],
        nets=[
            Net(name="+5V", kind=NetKind.power, power_symbol="power:+5V", track_width_mm=0.5),
            Net(name="GND", kind=NetKind.ground, power_symbol="power:GND", track_width_mm=0.5),
            Net(name="LED_DRV"),
            Net(name="LED_A"),
        ],
        board=BoardSpec(width_mm=40, height_mm=30, mounting_holes=[
            MountingHole(x_mm=4, y_mm=4), MountingHole(x_mm=36, y_mm=26)]),
    )
