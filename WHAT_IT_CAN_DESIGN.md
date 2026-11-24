# What Gerber Bear can and cannot design

The AI never decides what is buildable: a deterministic analysis does (`engineering/design/spec.py`),
and the AI is told these limits word for word. Requests outside them are declined with the reason and,
where one exists, the closest supported alternative.

## Supported

| Area | Supported | Limits and why |
|---|---|---|
| DC input | screw terminal or 5.5×2.1 mm barrel jack, 5–24 V | TVS + bulk capacitor; rails with KiCad power symbols |
| USB-C power | 5 V sink (5.1 kΩ CC), fuse, ESD on data | ≤ 1.5 A total; no USB Power Delivery |
| Battery | 1 Li-ion/LiPo cell, MCP73831 charger from USB-C, load sharing, voltage sense | controller + sensors only; use a protected cell |
| Controller | ESP32-WROOM-32 (USB-C programming with CH340C + auto-reset optional), or I2C host header | |
| Switched outputs | 0–32 low-side AO3400A channels, PWM, flyback, on-LED | ≤ 1 A each; load supply = input or a separate terminal (≤ 24 V) |
| Motors | 1–4 brushed DC, forward/reverse/brake, PWM (DRV8871) | 6.5–24 V, ≤ 2 A continuous |
| Relays | 1–8 SPDT (Sanyou SRD), NPN driver, flyback, LED | coil = input (5/12/24 V); contacts ≤ 10 A and ≤ 30 V |
| Sensors | DS18B20, SHT31, BME280, ADS1115 (4 filtered inputs) | one of each (I2C addresses) |
| Interfaces | RS-485 (MAX3485), CAN (SN65HVD230), I2C OLED connector | termination by jumper |
| Board | rectangular, 2 or 4 layers, 0/2/4 M3 holes, auto or fixed size | |

## Not supported, on purpose
- **Mains (120/230 V AC)** of any kind, including relay contacts: creepage, isolation and safety
  approval are not engineered here, and a mistake can kill.
- **High-speed / impedance-controlled** signals (DDR, PCIe, Ethernet PHYs, HDMI, USB 3), FPGAs,
  RF design beyond the certified ESP32 module: they need stack-up control and simulation.
- Stepper drivers, displays other than the OLED connector, audio, cameras, multi-cell packs,
  USB Power Delivery, custom board shapes: not in the block library yet.

## How correctness is established
Every block uses parts from KiCad's standard libraries with pin numbers checked against both
KiCad 7 and KiCad 10. Values from datasheet application circuits are marked `ai_suggested` until
checked against the datasheet. Each generated circuit passes the design checks
(`engineering/validation/functional.py`) before layout, then KiCad ERC/DRC and the width audit after.
No SPICE simulation is performed.
