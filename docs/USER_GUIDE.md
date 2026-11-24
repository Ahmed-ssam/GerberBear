# Gerber Bear user guide

## 1. First start
The welcome screen shows two things:
- **KiCad** - found, or a link to download it (install with default options, then restart Gerber Bear).
- **AI key** - open **Settings -> AI**, choose a provider (Gemini is free to start: get a key at
  <https://aistudio.google.com/apikey>), paste the key, **Save**. The key is kept in Windows Credential
  Manager only.

## 2. Describe the board
Click **New** and write what the board should do, as you would to an engineer, for example:

> An ESP32 board for my greenhouse: 12 V supply, two 12 V solenoid valves at 0.4 A, a humidity sensor
> and four analog inputs for soil probes.

Say what powers it, what it switches or measures, and any size limit. The assistant asks about
anything critical that is missing - supply voltage, how many outputs, how much current - and never
fills those in by itself. If you ask for something it cannot build (mains voltage, high-speed
interfaces...), it says so and offers the closest supported option.

## 3. Review the proposal
The proposal card shows the architecture, key parts (parts marked *unverified* follow datasheet
application rules but were not checked against the datasheet), board size and layers, the result of
the design checks, assumptions to confirm, and risks. **Approve and generate**, or ask for changes in
the chat.

## 4. Generation (a few minutes)
The app writes the schematic, runs KiCad ERC, places the parts, routes the board with Freerouting,
pours copper, runs KiCad DRC and repairs what it safely can. Progress is shown in the chat.

## 5. Read the results
Status at the top of the **Board** pane:
- **Validated** - KiCad ERC and DRC clean, everything routed, trace widths and design checks OK.
- **Partially validated** - DRC clean but ERC could not run on this KiCad version.
- **Validation failed** - something is wrong; the **Checks** view lists exactly what.

The **Checks** view shows KiCad's findings, ERC, trace widths, design checks, decoupling distances and
the order package status. **2D / 3D / Schematic** show the real files. **Open in KiCad** opens the
project for your own review or edits.

## 6. Change the design
Ask in the chat ("add a BME280", "make it 4 layers"). A new revision is created; parts that did not
change keep their reference designators and, if the board size is the same, their position.
Earlier revisions can be restored from the **Board** pane.

## 7. Order
**Export fabrication files** saves the order package folder:
- `*_gerbers.zip` - upload as the PCB
- `*_BOM_JLCPCB.csv`, `*_CPL_JLCPCB.csv` - JLCPCB assembly files
- `*_BOM.csv` - BOM for other assemblers, with the source of every part number
- `ORDER_README.txt` - the values for the order form and a checklist

Before paying: look at the board in the manufacturer's viewer, check the orientation of diodes,
LEDs, electrolytic capacitors and IC pin 1 in the assembly preview, and pick parts for any BOM line
listed as needing sourcing.

## 8. If something goes wrong
**... -> Save diagnostic report** and open an issue on GitHub with the zip attached.
To check the installation: Start menu -> Gerber Bear -> **Check installation**.
