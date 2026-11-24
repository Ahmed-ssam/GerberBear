"""Gerber Bear installation check.

Confirms that KiCad, KiCad's Python (pcbnew), Java and the Freerouting autorouter
all work together on this computer, then generates real test boards end to end.
Writes reports/check_report.txt (human-readable) and reports/check_report.json.

It never prints or stores API keys: it only says whether one is saved and whether
a one-line test call to the provider succeeded.

Usage:  python check_install.py [--quick | --full] [--only T2] [--out DIR] [--no-ai]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

REPORT: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": [], "designs": []}
LINES: list[str] = []


def say(msg: str = ""):
    print(msg, flush=True)
    LINES.append(msg)


def check(name: str, ok: bool | None, detail: str = "", data=None):
    mark = {True: "PASS", False: "FAIL", None: "WARN"}[ok]
    say(f"[{mark}] {name}" + (f" - {detail}" if detail else ""))
    REPORT["checks"].append({"name": name, "status": mark, "detail": detail, "data": data})


def section(title: str):
    say("")
    say(f"== {title} " + "=" * max(0, 60 - len(title)))


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--quick", action="store_true", help="one small test board")
    g.add_argument("--full", action="store_true", help="also the dense 100+ part boards (slow)")
    ap.add_argument("--out", default=str(HERE.parent / "reports"))
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--only", help="run only test boards whose name contains this text, e.g. T2")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    section("System")
    REPORT["system"] = {"platform": platform.platform(), "python": sys.version.split()[0],
                        "executable": sys.executable, "cpus": os.cpu_count(),
                        "free_disk_gb": round(shutil.disk_usage(out).free / 1e9, 1)}
    for k, v in REPORT["system"].items():
        say(f"  {k}: {v}")
    check("Python 3.10+", sys.version_info >= (3, 10), sys.version.split()[0])
    check("Free disk >= 2 GB", REPORT["system"]["free_disk_gb"] >= 2, f"{REPORT['system']['free_disk_gb']} GB")

    section("Python packages")
    for mod in ("pydantic", "httpx", "keyring", "numpy", "trimesh", "PySide6", "PySide6.QtWebEngineWidgets"):
        try:
            m = __import__(mod, fromlist=["x"])
            check(f"import {mod}", True, getattr(m, "__version__", ""))
        except Exception as e:
            check(f"import {mod}", False, f"{type(e).__name__}: {e}")

    section("KiCad")
    from ai_pcb_designer.kicad.detection import detect
    caps = detect()
    REPORT["kicad"] = json.loads(caps.to_json())
    check("kicad-cli found", caps.installed, caps.cli_path or "not found")
    if not caps.installed:
        return finish(out, 2)
    check("KiCad version", (caps.major or 0) >= 8, caps.version or "")
    check("KiCad Python can import pcbnew", bool(caps.python_path),
          f"{caps.python_path} (pcbnew {caps.pcbnew_version})" if caps.python_path else "; ".join(caps.notes))
    check("Symbol library", bool(caps.symbol_dir), caps.symbol_dir or "missing")
    check("Footprint library", bool(caps.footprint_dir), caps.footprint_dir or "missing")
    check("3D model library", bool(caps.models3d_dir) or None,
          caps.models3d_dir or "not installed: 3D view shows bare footprints")
    check("CLI ERC", caps.cli_erc or None, "")
    check("CLI DRC", caps.cli_drc or None, "")
    check("CLI GLB export", caps.cli_glb or None, "")
    check("CLI IPC-2581 export", caps.cli_ipc2581 or None, "")
    say(f"  pcb export commands: {', '.join(caps.pcb_export_cmds)}")

    if caps.python_path:
        # Every pcbnew name the worker uses must exist in this KiCad's bindings.
        worker = HERE / "ai_pcb_designer" / "kicad" / "pcb" / "worker.py"
        names = sorted(set(re.findall(r"\bP\.([A-Za-z_][A-Za-z0-9_]*)", worker.read_text(encoding="utf-8"))))
        probe = ("import pcbnew,json,sys;n=json.loads(sys.argv[1]);"
                 "print(json.dumps([x for x in n if not hasattr(pcbnew,x)]))")
        from ai_pcb_designer.kicad.cli.runner import run
        r = run([caps.python_path, "-c", probe, json.dumps(names)], check=False, timeout=120)
        try:
            missing = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            missing = None
        alt = {"EDA_UNITS_MILLIMETRES"}           # has a fallback in the worker
        hard = [m for m in (missing or []) if m not in alt]
        check("pcbnew API used by the worker", missing is not None and not hard,
              f"{len(names)} names checked" + (f"; missing: {', '.join(hard)}" if hard else "") +
              ("" if missing is not None else f"; probe failed: {r.stderr[-400:]}"))

    section("Java + Freerouting")
    from ai_pcb_designer.core.java import find_java
    from ai_pcb_designer.core.config import Settings, freerouting_jar
    java, jv = find_java()
    check("Java 11+", bool(java), f"{java} (Java {jv})" if java else
          (f"found only Java {jv}" if jv else "not found - install Temurin 21 JRE"))
    jar = freerouting_jar(Settings.load())
    check("Freerouting jar", jar.exists(), str(jar))

    section("AI provider")
    if a.no_ai:
        say("  skipped (--no-ai)")
    else:
        ai_check()

    section("Test boards (real KiCad pipeline)")
    if not (caps.python_path and java and jar.exists()):
        say("  skipped: KiCad Python, Java or Freerouting missing (see FAIL above)")
        return finish(out, 1)
    designs = test_designs(a)
    for name, spec_or_circuit in designs:
        run_design(caps, jar, name, spec_or_circuit, out / "test_designs")
    return finish(out, 0)


def ai_check():
    from ai_pcb_designer.ai.providers.base import ProviderConfig
    from ai_pcb_designer.ai.providers.vendors import DEFAULT_MODELS, make_provider
    from ai_pcb_designer.core.config import Settings
    from ai_pcb_designer.security.secrets import backend_status, get_key
    st = Settings.load()
    ok_kr, msg = backend_status()
    check("OS keychain", ok_kr or None, msg)
    provider = st.ai.provider if st.ai.provider in ("gemini", "openai", "openai_compatible") else "gemini"
    key = get_key(provider)
    if not key:
        check(f"{provider} API key saved", None,
              "no key saved yet - open the app, Settings -> AI, paste the key, Save; then re-run this check")
        return
    model = st.ai.model if st.ai.provider == provider and st.ai.model else DEFAULT_MODELS[provider]
    check(f"{provider} API key saved", True, "present (not shown)")
    try:
        p = make_provider(ProviderConfig(provider, model, api_key=key, base_url=st.ai.base_url or None))
        t = time.monotonic()
        res = p.complete_json("Reply with JSON only.",
                              [_msg("user", 'Return {"ok": true, "board": "pcb"} exactly.')])
        check(f"{provider} test call ({model})", bool(res.get("ok")), f"{time.monotonic() - t:.1f}s, reply keys {sorted(res)}")
    except Exception as e:
        kind = getattr(e, "kind", "")
        if kind in ("network", "server", "rate_limit"):
            # the key and setup are fine; the service or the connection was slow this time
            check(f"{provider} test call ({model})", None,
                  f"{kind}: {str(e)[:200]} - setup is fine; check your connection and re-run later")
        else:
            check(f"{provider} test call ({model})", False, f"{type(e).__name__}: {str(e)[:300]}")


def _msg(role, text):
    from ai_pcb_designer.ai.providers.base import Message
    return Message(role, text)


def test_designs(a):
    from ai_pcb_designer.engineering.design.spec import DesignSpec
    d = [("T1_esp32_fan_2sensors", DesignSpec.model_validate({
        "project_name": "T1 fan controller", "controller": "esp32", "input_voltage": 12,
        "loads": {"count": 2, "kind": "dc_fan", "voltage": 12, "current_per_channel_a": 0.4},
        "sensors": [{"kind": "ds18b20"}, {"kind": "sht31"}]}))]
    if not a.quick:
        d.append(("T2_8ch_led_driver_2layer", DesignSpec.model_validate({
            "project_name": "T2 8ch LED driver", "controller": "esp32", "input_voltage": 24,
            "loads": {"count": 8, "kind": "led_strip", "voltage": 24, "current_per_channel_a": 0.35},
            "sensors": [{"kind": "sht31"}, {"kind": "ads1115"}], "layers": 2})))
        d.append(("T3_16ch_4layer", DesignSpec.model_validate({
            "project_name": "T3 16ch driver", "controller": "esp32", "input_voltage": 12,
            "loads": {"count": 16, "kind": "solenoid", "voltage": 12, "current_per_channel_a": 0.5},
            "sensors": [{"kind": "ds18b20"}, {"kind": "sht31"}, {"kind": "ads1115"}], "layers": 4})))
        d.append(("T4_usb_battery_node", DesignSpec.model_validate({
            "project_name": "T4 USB battery node", "controller": "esp32", "power_input": "usb_c",
            "battery": {"kind": "li_ion_1s", "capacity_mah": 1000}, "loads": {"count": 0},
            "sensors": [{"kind": "bme280"}], "interfaces": ["oled_i2c"]})))
        d.append(("T5_motors_relays_rs485", DesignSpec.model_validate({
            "project_name": "T5 motors relays RS485", "controller": "esp32", "input_voltage": 12,
            "usb_programming": True, "loads": {"count": 0},
            "motors": {"count": 2, "voltage": 12, "current_a": 1.0},
            "relays": {"count": 2, "contact_current_a": 3, "contact_voltage": 24},
            "interfaces": ["rs485"]})))
    if a.full:
        from ai_pcb_designer.engineering.circuit.ladder import LADDER
        for key in ("L3b", "L4"):
            if key in LADDER:
                d.append((f"ladder_{key}", LADDER[key]))
    if getattr(a, "only", None):
        d = [x for x in d if a.only.lower() in x[0].lower()]
    return d


def run_design(caps, jar, name, spec_or_circuit, root: Path):
    from ai_pcb_designer.engineering.design.compiler import compile_spec
    from ai_pcb_designer.engineering.design.spec import DesignSpec
    from ai_pcb_designer.engineering.pipeline import DesignPipeline
    from ai_pcb_designer.kicad.library import LibraryManager
    say(f"-- {name}")
    entry = {"name": name}
    REPORT["designs"].append(entry)
    t0 = time.monotonic()
    try:
        libs = LibraryManager(caps.symbol_dir, caps.footprint_dir, caps.models3d_dir)
        if isinstance(spec_or_circuit, DesignSpec):
            circuit = compile_spec(spec_or_circuit, libs).circuit
        else:
            circuit = spec_or_circuit() if callable(spec_or_circuit) else spec_or_circuit
        entry.update(components=len(circuit.components), layers=circuit.board.layers,
                     board_mm=[circuit.board.width_mm, circuit.board.height_mm])
        say(f"   {len(circuit.components)} parts, {circuit.board.layers} layers, "
            f"{circuit.board.width_mm:g} x {circuit.board.height_mm:g} mm")
        d = root / name
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

        def ev(e):
            if e["type"] == "step_end":
                say(f"   {e['name']:<22} {e['status']:<9} {e.get('seconds', 0):>7.1f}s"
                    + (f"  {e['error'][:200]}" if e.get("error") else ""))
        rec = DesignPipeline(caps, jar).run(circuit, d, on_event=ev)
        entry.update(status=rec.validation_status, summary=rec.summary,
                     steps=[{"name": s.name, "status": s.status, "seconds": s.seconds, "error": s.error,
                             "trace": (s.detail or {}).get("trace") if s.status != "ok" else None}
                            for s in rec.steps])
        s = rec.summary or {}
        drc = s.get("drc") or {}
        exp = next((st for st in rec.steps if st.name == "exports"), None)
        files = [f for r in ((exp.detail or {}).get("records", []) if exp else []) for f in r.get("files", [])]
        entry["exported_files"] = [str(Path(f).relative_to(d)) if str(f).startswith(str(d)) else f for f in files]
        failed = (exp.detail or {}).get("failed", []) if exp else ["exports not run"]
        op = s.get("order_package") or {}
        fn = s.get("functional") or {}
        ok = rec.validation_status in ("validated", "partially_validated") and not failed and not op.get("error")
        check(f"{name}", ok,
              f"status={rec.validation_status}, routed={s.get('routing_complete')}, "
              f"DRC errors={drc.get('errors')}, unconnected={drc.get('unconnected')}, "
              f"ERC={(s.get('erc') or {}).get('counts')}, exports failed={failed}, "
              f"design checks: {fn.get('errors')} errors/{fn.get('warnings')} warnings, "
              f"order package: {op.get('error') or op.get('detail')}"
              + (f" (LCSC matched {(op.get('sourcing') or {}).get('matched')})" if op.get('sourcing') else "") + ", "
              f"{time.monotonic() - t0:.0f}s")
    except Exception as e:
        entry.update(status="crashed", error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-3000:])
        check(f"{name}", False, f"{type(e).__name__}: {str(e)[:400]}")


def finish(out: Path, code: int) -> int:
    REPORT["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    fails = [c["name"] for c in REPORT["checks"] if c["status"] == "FAIL"]
    section("Result")
    say("ALL CHECKS PASSED" if not fails else f"{len(fails)} check(s) failed: {', '.join(fails)}")
    (out / "check_report.json").write_text(json.dumps(REPORT, indent=1, default=str), encoding="utf-8")
    (out / "check_report.txt").write_text("\n".join(LINES) + "\n", encoding="utf-8")
    say(f"Report written to {out / 'check_report.txt'}")
    return code if code else (1 if fails else 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        say(traceback.format_exc())
        finish(Path(HERE.parent / "reports"), 3)
        sys.exit(3)
