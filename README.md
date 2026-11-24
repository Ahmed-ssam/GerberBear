# Gerber Bear

**Describe a circuit board in plain language - get a real, checked KiCad project and the files to order it.**

<p align="center">
  <a href="https://github.com/Ahmed-ssam/GerberBear/releases/latest/download/GerberBear-Setup.exe">
    <img src="https://img.shields.io/badge/Download%20for%20Windows-GerberBear--Setup.exe-F5C518?style=for-the-badge&logo=windows&logoColor=black&labelColor=0E1525" alt="Download Gerber Bear for Windows">
  </a>
  <br>
  <sub>Free · Windows 10/11 (64-bit) · needs <a href="https://www.kicad.org/download/windows/">KiCad 8, 9 or 10</a> ·
  <a href="https://github.com/Ahmed-ssam/GerberBear/releases">all versions</a></sub>
</p>

Gerber Bear is a free Windows desktop app that turns a conversation into a printed-circuit-board
design. You say what the board should do; an AI engineer asks for anything that is missing
(it never guesses voltages or currents), proposes an architecture you approve, and the app then
builds the design with [KiCad](https://www.kicad.org):

- a hierarchical **schematic**, checked against KiCad's own netlist and **ERC**
- a placed and **routed PCB** (2 or 4 layers), checked with KiCad **DRC**
- **design checks** KiCad does not do: datasheet supply ranges, component voltage ratings, LED
  currents, resistor power, rails without a source, I2C address clashes, ESP32 boot pins, trace
  widths against the current each net carries
- **fabrication files** (Gerbers, drill, pick-and-place, BOM, STEP/GLB 3D model, PDF schematic)
- an **order package** for JLCPCB / PCBWay: Gerber zip, assembly BOM and placement files, supplier
  part numbers confirmed in the JLCPCB catalogue, and a README with the exact order-form values

Everything is reported as it is: if a check fails, the app says so and does not call the board ready.

> **Beta.** No circuit simulation is performed. Review every design (it opens directly in KiCad)
> before you order boards.

## What it can design
ESP32 Wi-Fi controllers or I2C-driven boards with: up to 32 switched outputs (fans, LED strips,
solenoids, heaters...), up to 4 brushed DC motors (forward/reverse), up to 8 relays (low voltage),
USB-C power and programming, a Li-ion battery with USB-C charging, DS18B20 / SHT31 / BME280 /
ADS1115 sensors, RS-485, CAN and an OLED connector. Mains-voltage and high-speed designs are
declined on purpose. Full list: [WHAT_IT_CAN_DESIGN.md](WHAT_IT_CAN_DESIGN.md).

## Install
1. Install **KiCad 8, 9 or 10** (free): <https://www.kicad.org/download/windows/> - default options.
2. Download **[GerberBear-Setup.exe](https://github.com/Ahmed-ssam/GerberBear/releases/latest/download/GerberBear-Setup.exe)**
   (latest version) and run it. Leave "Create a desktop shortcut" ticked to get a desktop icon.
   Windows may show "Windows protected your PC" because the installer is not code-signed:
   click **More info -> Run anyway**.
3. Start Gerber Bear. The welcome screen checks KiCad and asks for an **AI key**: a free Google
   Gemini key from <https://aistudio.google.com/apikey> works (OpenAI or a local
   OpenAI-compatible server also work). The key is stored only in Windows Credential Manager.

Python, Java and the Freerouting autorouter are included in the installer.

## Use
See the [user guide](docs/USER_GUIDE.md). In short: describe the board -> answer the questions ->
approve the proposal -> wait a few minutes -> review the Checks view -> **Export fabrication files**.

## Problems and ideas
Use [GitHub Issues](../../issues). In the app, **... -> Save diagnostic report** creates a zip
(logs, system details, the run record - never your API key) to attach to the issue.

## Privacy
Your conversation goes to the AI provider you chose, with your key; part values go to a public
JLCPCB parts index when the order package is built. Nothing else leaves your PC. Details:
[PRIVACY.md](PRIVACY.md).

## Build from source
See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence
Gerber Bear is free software: GNU General Public License v3.0 or later ([LICENSE](LICENSE)).
It uses KiCad (GPL-3.0) as an external program and bundles Freerouting (GPL-3.0), Eclipse Temurin,
Python, Qt/PySide6 (LGPL-3.0) and others under their own licences:
[installer/THIRD_PARTY_NOTICES.txt](installer/THIRD_PARTY_NOTICES.txt).
Boards you design with Gerber Bear are yours, under any terms you choose.
