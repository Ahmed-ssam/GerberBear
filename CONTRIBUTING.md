# Contributing / building from source

Bug reports and ideas: [GitHub Issues](../../issues). Pull requests are welcome; by submitting one
you agree to license your contribution under the GPL-3.0-or-later.

## Run from source (Windows)
Requirements: KiCad 8-10, Python 3.12 (python.org, "Add to PATH"), Java 21 (Eclipse Temurin JRE).

```
check_install.bat          creates .venv, installs packages, checks KiCad/Java/Freerouting and
                           builds five real test boards (reports\check_report.txt)
run_app.bat                starts the app   (run_app_console.bat shows errors)
run_benchmark.bat          21 realistic requests through your AI key (reports\benchmark\)
installer\build_installer.bat   builds the Windows installer (needs Inno Setup 6 and internet)
```

Options: `check_install.bat --quick | --full | --only T2`, `run_benchmark.bat --group core | --only X03 | --generate`.

## Tests
```
.venv\Scripts\python -m pytest tests             (set PCB_SLOW=1 for the GUI and 209-part tests)
```

## Layout
```
app/ai_pcb_designer/
  ai/            conversation, prompts, AI providers
  engineering/   design spec + analysis, compiler, circuit blocks, placement, routing,
                 validation (DRC parsing, design checks, repair), fabrication package, pipeline
  kicad/         KiCad detection, schematic writer, PCB worker (runs in KiCad's Python), exporters
  ui/            PySide6 desktop app
app/tools/       Freerouting 1.9.0 (GPL-3.0)
installer/       Inno Setup script, build script, third-party notices
tests/           unit and integration tests
```

## Principles
- The AI never decides what is buildable and never invents electrical values; the deterministic
  analysis (`engineering/design/spec.py`) does.
- Every part comes from KiCad's standard libraries with pin numbers checked in KiCad 7 and 10.
- Results are reported as KiCad and the checks produce them - never "validated" unless they ran clean.
