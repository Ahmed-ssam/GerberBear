"""Exports driven by kicad-cli, plus STEP -> GLB for the embedded 3D viewer.

Every export reports what was actually written; a step that fails is recorded
as failed with KiCad's error text rather than skipped silently.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..cli.runner import run


@dataclass
class ExportRecord:
    name: str
    ok: bool
    files: list[str] = field(default_factory=list)
    detail: str = ""


GERBER_EXT = {"F.Cu": "gtl", "B.Cu": "gbl", "F.Paste": "gtp", "B.Paste": "gbp",
              "F.Silkscreen": "gto", "B.Silkscreen": "gbo", "F.Mask": "gts", "B.Mask": "gbs",
              "Edge.Cuts": "gm1"}
FAB_LAYERS = ",".join(GERBER_EXT)

SVG_LAYER_SETS = {
    "top": "F.Cu,F.Paste,F.Silkscreen,F.Mask,Edge.Cuts",
    "bottom": "B.Cu,B.Silkscreen,B.Mask,Edge.Cuts",
    "copper_top": "F.Cu,Edge.Cuts",
    "copper_bottom": "B.Cu,Edge.Cuts",
    "assembly": "F.Fab,F.Courtyard,F.Silkscreen,Edge.Cuts",
}


def _rec(name, fn, outs):
    try:
        res = fn()
        files = []
        for o in map(Path, outs):
            if o.is_dir():
                files += [str(p) for p in sorted(o.iterdir()) if p.is_file()]
            elif o.exists():
                files.append(str(o))
        return ExportRecord(name, bool(files), files, (res.stdout + res.stderr).strip()[-400:])
    except Exception as e:
        return ExportRecord(name, False, [], str(e)[-600:])


class Exporter:
    def __init__(self, cli: str, env: dict | None = None):
        self.cli = cli
        self.env = env or {}

    def pcb_svgs(self, pcb: Path, out: Path) -> list[ExportRecord]:
        out.mkdir(parents=True, exist_ok=True)
        recs = []
        for name, layers in SVG_LAYER_SETS.items():
            f = out / f"pcb_{name}.svg"
            args = [self.cli, "pcb", "export", "svg", "--layers", layers, "--exclude-drawing-sheet",
                    "--page-size-mode", "2", "-o", str(f), str(pcb)]
            if name.startswith("bottom") or name == "copper_bottom":
                args.insert(-3, "--mirror")
            recs.append(_rec(f"svg:{name}", lambda a=args: run(a, env=self.env), [f]))
        return recs

    def layer_svgs(self, pcb: Path, out: Path, copper_layers: int = 2) -> ExportRecord:
        """One monochrome SVG per layer, all on the same board-area page, for the
        interactive 2D viewer (which colours and toggles them)."""
        out.mkdir(parents=True, exist_ok=True)
        layers = ["B.Cu"] + [f"In{k}.Cu" for k in range(copper_layers - 2, 0, -1)] + ["F.Cu", "B.Mask", "F.Mask",
                  "B.Silkscreen", "F.Silkscreen", "F.Fab", "Edge.Cuts"]
        files, errors = [], []
        for layer in layers:
            f = out / f"layer_{layer.replace('.', '_')}.svg"
            try:
                run([self.cli, "pcb", "export", "svg", "--layers", f"{layer},Edge.Cuts", "--black-and-white",
                     "--exclude-drawing-sheet", "--page-size-mode", "2", "-o", str(f), str(pcb)], env=self.env)
                files.append(str(f))
            except Exception as e:
                errors.append(f"{layer}: {e}")
        manifest = out / "layers.json"
        manifest.write_text(json.dumps([{"layer": l, "file": Path(f).name}
                                        for l, f in zip([x for x in layers], files)] if not errors else
                                       [{"layer": Path(f).stem[6:].replace("_", "."), "file": Path(f).name}
                                        for f in files]))
        return ExportRecord("svg:layers", not errors and len(files) == len(layers), files + [str(manifest)],
                            "; ".join(errors)[-400:])

    def schematic(self, sch: Path, out: Path) -> list[ExportRecord]:
        out.mkdir(parents=True, exist_ok=True)
        pdf = out / f"{sch.stem}.pdf"
        return [
            _rec("sch:pdf", lambda: run([self.cli, "sch", "export", "pdf", "-o", str(pdf), str(sch)], env=self.env), [pdf]),
            _rec("sch:svg", lambda: run([self.cli, "sch", "export", "svg", "-o", str(out), str(sch)], env=self.env),
                 [out / f"{sch.stem}.svg"]),
        ]

    def fabrication(self, pcb: Path, out: Path, copper_layers: int = 2) -> list[ExportRecord]:
        """Gerbers for every copper layer the board has (inner planes included), drill, placement."""
        g = out / "gerbers"
        g.mkdir(parents=True, exist_ok=True)
        pos = out / f"{pcb.stem}-pos.csv"
        layers = ["F.Cu"] + [f"In{k}.Cu" for k in range(1, copper_layers - 1)] + ["B.Cu"] + \
                 [l for l in GERBER_EXT if not l.endswith(".Cu")]

        def by_layer():
            return [p for l in layers for p in sorted(g.glob(f"{pcb.stem}-{l.replace('.', '_')}.*"))]

        status = {"ran": False}

        def gerbers():
            res = run([self.cli, "pcb", "export", "gerbers", "--layers", ",".join(layers),
                       "--subtract-soldermask", "-o", str(g) + "/", str(pcb)], env=self.env)
            missing = [l for l in layers if not list(g.glob(f"{pcb.stem}-{l.replace('.', '_')}.*"))]
            if missing:
                raise RuntimeError(f"Gerber layers not produced: {missing}")
            status["ran"] = True
            return res

        recs = [_rec("gerbers", gerbers, []),
                _rec("drill", lambda: run([self.cli, "pcb", "export", "drill", "--format", "excellon",
                                           "--excellon-separate-th", "--excellon-units", "mm",
                                           "--generate-map", "--map-format", "gerberx2",
                                           "-o", str(g) + "/", str(pcb)], env=self.env),
                     [g / f"{pcb.stem}-PTH.drl"]),
                _rec("pos", lambda: run([self.cli, "pcb", "export", "pos", "--format", "csv", "--units", "mm",
                                         "--side", "both", "-o", str(pos), str(pcb)], env=self.env), [pos])]
        if status["ran"]:
            recs[0].files = [str(p) for p in by_layer()]
            recs[0].ok = len(recs[0].files) == len(layers)
            recs[0].detail = f"{len(recs[0].files)} layers: " + ", ".join(layers)
        return recs

    def step(self, pcb: Path, out: Path) -> ExportRecord:
        out.mkdir(parents=True, exist_ok=True)
        f = out / f"{pcb.stem}.step"
        return _rec("step", lambda: run([self.cli, "pcb", "export", "step", "--subst-models", "--force",
                                         "-o", str(f), str(pcb)], timeout=900, env=self.env), [f])


    def glb(self, pcb: Path, out: Path) -> ExportRecord:
        """Native GLB (KiCad >= 9): coloured board + 3D models, no OCP needed."""
        out.mkdir(parents=True, exist_ok=True)
        f = out / f"{pcb.stem}.glb"
        base = [self.cli, "pcb", "export", "glb", "--subst-models", "--force",
                "--include-tracks", "--include-pads", "--include-zones"]

        def go():
            # silkscreen and solder mask make the 3D view look like the real board (KiCad 9+);
            # older CLIs reject the options, so fall back to the plain export
            try:
                return run(base + ["--include-silkscreen", "--include-soldermask", "-o", str(f), str(pcb)],
                           timeout=900, env=self.env)
            except Exception:
                return run(base + ["-o", str(f), str(pcb)], timeout=900, env=self.env)
        return _rec("glb", go, [f])

    def ipc2581(self, pcb: Path, out: Path) -> ExportRecord:
        out.mkdir(parents=True, exist_ok=True)
        f = out / f"{pcb.stem}.ipc2581.xml"
        return _rec("ipc2581", lambda: run([self.cli, "pcb", "export", "ipc2581", "-o", str(f), str(pcb)],
                                           timeout=600, env=self.env), [f])

    def drc(self, pcb: Path, report: Path, refill_zones: bool = True) -> Path:
        """kicad-cli pcb drc, JSON report in mm. Exit code is not used to judge the board:
        the report is parsed. Fails only if no report was written."""
        report.unlink(missing_ok=True)
        argv = [self.cli, "pcb", "drc", "--format", "json", "--units", "mm", "--severity-all",
                "--all-track-errors", "-o", str(report)]
        if refill_zones:
            argv.insert(3, "--refill-zones")
        res = run(argv + [str(pcb)], check=False, timeout=900, env=self.env)
        if not report.exists() and refill_zones and "refill" in (res.stderr + res.stdout).lower():
            argv.remove("--refill-zones")          # older CLI without the option
            res = run(argv + [str(pcb)], check=False, timeout=900, env=self.env)
        if not report.exists():
            raise RuntimeError(f"kicad-cli drc wrote no report (rc={res.returncode}): {res.stderr[-600:]}")
        return report

    def erc(self, sch: Path, out: Path) -> dict:
        """kicad-cli sch erc (KiCad >= 8). Returns counts by severity and the report path."""
        out.mkdir(parents=True, exist_ok=True)
        f = out / f"{sch.stem}-erc.json"
        res = run([self.cli, "sch", "erc", "--format", "json", "--severity-all", "-o", str(f), str(sch)],
                  check=False, timeout=600, env=self.env)
        counts = {"error": 0, "warning": 0}
        types: dict[str, int] = {}
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            for sheet in data.get("sheets", []):
                for v in sheet.get("violations", []):
                    sev = v.get("severity", "error")
                    counts[sev] = counts.get(sev, 0) + 1
                    types[v.get("type", "?")] = types.get(v.get("type", "?"), 0) + 1
        return {"ran": f.exists(), "rc": res.returncode, "report": str(f), "counts": counts, "types": types}


# ---------------------------------------------------------------- STEP -> GLB
def step_to_glb(step: Path, glb: Path, linear_deflection: float = 0.05) -> ExportRecord:
    """Tessellate a STEP (keeping XCAF colours) and write a GLB for Three.js."""
    try:
        import numpy as np
        import trimesh
        from OCP.BRep import BRep_Tool
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.Quantity import Quantity_Color
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TCollection import TCollection_ExtendedString
        try:
            from OCP.TDF import TDF_LabelSequence
        except ImportError:  # OCP >= 7.8 exposes NCollection sequences here
            from OCP.collections import Sequence_TDF_Label as TDF_LabelSequence
        from OCP.TDocStd import TDocStd_Document
        from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopLoc import TopLoc_Location
        from OCP.TopoDS import TopoDS
        from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool
    except ImportError as e:
        return ExportRecord("glb", False, [], f"OCP/trimesh unavailable: {e}")

    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    if reader.ReadFile(str(step)) != IFSelect_RetDone or not reader.Transfer(doc):
        return ExportRecord("glb", False, [], "STEP read failed")
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    from OCP.XCAFDoc import XCAFDoc_ColorTool

    def color_of(label, shape, inherited):
        c = Quantity_Color()
        for ct in (XCAFDoc_ColorType.XCAFDoc_ColorSurf, XCAFDoc_ColorType.XCAFDoc_ColorGen):
            if (shape is not None and color_tool.GetColor(shape, ct, c)) or \
               (label is not None and XCAFDoc_ColorTool.GetColor_s(label, ct, c)):
                return (c.Red(), c.Green(), c.Blue())
        return inherited

    scene = trimesh.Scene()
    counter = [0]

    def mesh_shape(shape, loc, rgb, name):
        """Tessellate `shape` in its own frame (so XCAF face colours resolve), then place by `loc`."""
        BRepMesh_IncrementalMesh(shape, linear_deflection, False, 0.5, True)
        verts, faces, cols = [], [], []
        off = 0
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            face = (TopoDS.Face_s if hasattr(TopoDS, "Face_s") else TopoDS.Face)(exp.Current())
            floc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, floc)
            if tri is not None:
                trsf = loc.Multiplied(floc).Transformation()
                fc = color_of(None, face, rgb)
                for i in range(1, tri.NbNodes() + 1):
                    p = tri.Node(i).Transformed(trsf)
                    verts.append((p.X(), p.Y(), p.Z()))
                    cols.append((*fc, 1.0))
                rev = face.Orientation() == TopAbs_REVERSED
                for i in range(1, tri.NbTriangles() + 1):
                    a, b, c = tri.Triangle(i).Get()
                    faces.append((a - 1 + off, c - 1 + off, b - 1 + off) if rev else
                                 (a - 1 + off, b - 1 + off, c - 1 + off))
                off += tri.NbNodes()
            exp.Next()
        if faces:
            m = trimesh.Trimesh(np.array(verts), np.array(faces), process=False,
                                vertex_colors=(np.array(cols) * 255).astype(np.uint8))
            counter[0] += 1
            scene.add_geometry(m, node_name=f"{name}_{counter[0]}")

    def walk(label, loc, rgb):
        rgb = color_of(label, shape_tool.GetShape_s(label), rgb)
        if shape_tool.IsAssembly_s(label):
            comps = TDF_LabelSequence()
            shape_tool.GetComponents_s(label, comps)
            for i in range(1, comps.Length() + 1):
                c = comps.Value(i)
                ref = c
                from OCP.TDF import TDF_Label
                target = TDF_Label()
                if shape_tool.GetReferredShape_s(c, target):
                    ref = target
                cloc = loc.Multiplied(shape_tool.GetLocation_s(c))
                walk(ref, cloc, color_of(c, shape_tool.GetShape_s(c), rgb))
        else:
            mesh_shape(shape_tool.GetShape_s(label), loc, rgb, "part")

    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    for i in range(1, roots.Length() + 1):
        walk(roots.Value(i), TopLoc_Location(), (0.6, 0.6, 0.6))
    if not scene.geometry:
        return ExportRecord("glb", False, [], "no triangles produced")
    # KiCad STEP is Z-up in mm; glTF is Y-up in metres-agnostic units -> rotate
    scene.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    glb.parent.mkdir(parents=True, exist_ok=True)
    glb.write_bytes(scene.export(file_type="glb"))
    return ExportRecord("glb", True, [str(glb)], f"{len(scene.geometry)} meshes")
