# Changelog

## 0.9.0 (beta) - first public release
- Conversation -> proposal -> KiCad schematic and routed PCB, KiCad ERC/DRC, automatic repairs
- Design blocks: ESP32 (USB-C programming optional), I2C host header, DC input (screw terminal,
  barrel jack), USB-C power, 1-cell Li-ion charging, MOSFET outputs (separate load supply
  possible), DRV8871 motor drivers, relays, RS-485, CAN, DS18B20, SHT31, BME280, ADS1115, OLED connector
- Design checks: supply ranges, voltage ratings, LED currents, resistor power, rail sources,
  I2C pull-ups and addresses, ESP32 boot pins; trace-width audit (IPC-2221); decoupling and
  regulator-loop distances
- Order package for JLCPCB / PCBWay with catalogue-confirmed LCSC part numbers
- Edits keep designators and the placement of unchanged blocks
- Windows installer with bundled Python, Java and Freerouting
- Redesigned interface: start screen with templates, motion (can be reduced in Settings), lit 3D viewer
- AI provider: retries with back-off when the service is busy, then switches to another Gemini Flash model
- Fixed board sizes: when a design does not fit, the tool says which options (mounting holes, USB-C
  programming) would make it fit at that size
- ERC: datasheet-intended connections (CH340 V3 = VCC, BME280 address strap) are listed as waived with
  their reason instead of being counted as errors
