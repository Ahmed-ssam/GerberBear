"""PCB operations executed inside KiCad's own Python (imports pcbnew).

Invoked as:  <kicad-python> worker.py <command> <job.json>
Prints one JSON object on the last stdout line. Stdlib + pcbnew only, so it
runs under whatever interpreter KiCad ships. Verified against KiCad 7.0.11.
"""
import json
import math
import os
import sys

import pcbnew as P

MM = P.FromMM


def _v(x, y):
    return P.VECTOR2I(MM(x), MM(y))


def _out(obj):
    print(json.dumps(obj))


def build(job):
    path = job["output"]
    if os.path.exists(path):
        os.remove(path)
    board = P.NewBoard(path)
    rules = job["rules"]
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(job["board"]["layers"])
    ds.SetBoardThickness(MM(job["board"]["thickness_mm"]))
    ds.m_MinClearance = MM(rules["clearance_mm"])
    ds.m_TrackMinWidth = MM(0.15)
    ds.m_CopperEdgeClearance = MM(rules["edge_clearance_mm"])
    ds.m_ViasMinSize = MM(0.6)
    ds.m_MinThroughDrill = MM(rules.get("min_drill_mm", 0.3))

    report = {"footprints": [], "assigned_pads": 0}
    ns = ds.m_NetSettings
    # KiCad 7/8: ns.m_DefaultNetClass / ns.m_NetClasses[name]
    # KiCad 9/10: ns.GetDefaultNetclass() / ns.SetNetclass(name, shared_ptr<NETCLASS>)
    dnc = ns.GetDefaultNetclass() if hasattr(ns, "GetDefaultNetclass") else ns.m_DefaultNetClass
    dnc.SetClearance(MM(rules["clearance_mm"]))
    dnc.SetTrackWidth(MM(rules["track_width_mm"]))
    dnc.SetViaDiameter(MM(rules["via_diameter_mm"]))
    dnc.SetViaDrill(MM(rules["via_drill_mm"]))

    for nc in job.get("netclasses", []):
        c = P.NETCLASS(nc["name"])
        c.SetClearance(MM(rules["clearance_mm"]))
        c.SetTrackWidth(MM(nc["track_width_mm"]))
        c.SetViaDiameter(MM(nc.get("via_diameter_mm", rules["via_diameter_mm"])))
        c.SetViaDrill(MM(nc.get("via_drill_mm", rules["via_drill_mm"])))
        try:
            if hasattr(ns, "SetNetclass"):
                ns.SetNetclass(nc["name"], c)
                for n in nc.get("nets", []):
                    ns.SetNetclassPatternAssignment(n, nc["name"])
            else:
                ns.m_NetClasses[nc["name"]] = c
        except Exception as e:      # the project-file write below is authoritative anyway
            report.setdefault("warnings", []).append(f"netclass {nc['name']} via API: {e}")

    nets = {}
    for n in job["nets"]:
        ni = P.NETINFO_ITEM(board, n["name"])
        board.Add(ni)
        nets[n["name"]] = ni

    W, H = job["board"]["width_mm"], job["board"]["height_mm"]
    outline = P.PCB_SHAPE(board)
    outline.SetShape(P.SHAPE_T_RECT)
    outline.SetLayer(P.Edge_Cuts)
    outline.SetStart(_v(0, 0))
    outline.SetEnd(_v(W, H))
    outline.SetWidth(MM(0.1))
    board.Add(outline)

    for f in job["footprints"]:
        fp = P.FootprintLoad(f["lib_path"], f["name"])
        if fp is None:
            raise RuntimeError(f"FootprintLoad failed for {f['lib']}:{f['name']}")
        fp.SetFPID(P.LIB_ID(f["lib"], f["name"]))
        fp.SetReference(f["ref"])
        fp.SetValue(f["value"])
        if f.get("hide_reference"):
            fp.Reference().SetVisible(False)
        if f.get("sym_path"):
            fp.SetPath(P.KIID_PATH(f["sym_path"]))
        fp.SetPosition(_v(f["x"], f["y"]))
        fp.SetOrientationDegrees(f.get("rotation", 0))
        board.Add(fp)
        for pad in fp.Pads():
            netname = f.get("pads", {}).get(pad.GetNumber())
            if netname:
                pad.SetNet(nets[netname])
                report["assigned_pads"] += 1
        report["footprints"].append(f["ref"])

    P.SaveBoard(path, board)
    # KiCad 7 stores net-class membership as patterns in the project file;
    # boards loaded afterwards (DSN export, DRC) resolve classes from it.
    pro = os.path.splitext(path)[0] + ".kicad_pro"
    with open(pro, encoding="utf-8") as fh:
        pdata = json.load(fh)
    # Net classes live in the project file since KiCad 7; write them explicitly so the
    # result does not depend on how this KiCad version's bindings expose NET_SETTINGS.
    nset = pdata.setdefault("net_settings", {})
    cls = nset.setdefault("classes", [])
    default = next((c for c in cls if c.get("name") == "Default"), None)
    if default is None:
        default = {"name": "Default"}
        cls.insert(0, default)
    default.update(clearance=rules["clearance_mm"], track_width=rules["track_width_mm"],
                   via_diameter=rules["via_diameter_mm"], via_drill=rules["via_drill_mm"])
    for nc in job.get("netclasses", []):
        entry = next((c for c in cls if c.get("name") == nc["name"]), None)
        if entry is None:
            entry = {k: v for k, v in default.items()}
            entry["name"] = nc["name"]
            cls.append(entry)
        entry.update(clearance=rules["clearance_mm"], track_width=nc["track_width_mm"],
                     via_diameter=nc.get("via_diameter_mm", rules["via_diameter_mm"]),
                     via_drill=nc.get("via_drill_mm", rules["via_drill_mm"]))
    pdata["net_settings"]["netclass_patterns"] = [
        {"netclass": nc["name"], "pattern": n}
        for nc in job.get("netclasses", []) for n in nc.get("nets", [])]
    with open(pro, "w", encoding="utf-8") as fh:
        json.dump(pdata, fh, indent=2)
    report["saved"] = path
    return report


def _via_width(v):
    """KiCad 9+ vias have padstacks: ask for the front-layer size explicitly (the no-layer
    form is deprecated and can raise a blocking assert); KiCad 7/8 only have GetWidth()."""
    try:
        return v.GetWidth(P.F_Cu)
    except TypeError:
        return v.GetWidth()


def _pad_size(p):
    try:
        return p.GetSize(P.F_Cu) if p.IsOnLayer(P.F_Cu) else p.GetSize(P.B_Cu)
    except TypeError:
        return p.GetSize()


def _load(job):
    return P.LoadBoard(job["output"])


def export_dsn(job):
    board = _load(job)
    ok = P.ExportSpecctraDSN(board, job["dsn"])
    return {"ok": bool(ok) and os.path.exists(job["dsn"]), "dsn": job["dsn"]}


def import_ses(job):
    """Import a Specctra session. KiCad >= 8 has ImportSpecctraSES(board, file);
    KiCad 7 only offers the GUI-bound overload, so we apply wires/vias ourselves."""
    board = _load(job)
    for t in list(board.GetTracks()):          # re-route replaces previous (unlocked) copper
        if not t.IsLocked():
            _GRAVEYARD.append(t)
            board.Remove(t)
    try:
        ok = P.ImportSpecctraSES(board, job["ses"])
        method = "native"
    except TypeError:
        ok, method = _import_ses_manual(board, job["ses"]), "manual (KiCad 7 fallback)"
    if not ok:
        return {"ok": False, "method": method}
    P.SaveBoard(job["output"], board)
    # Cleanup runs as a separate process ("cleanup-tracks"): KiCad 7's SWIG layer can return
    # untyped objects after bulk Remove/Add in one interpreter session.
    return {"ok": True, "method": method, **_LAST_IMPORT}


def clear_routes(job):
    """Remove all unlocked copper (keeps locked plane fanouts) before a fresh routing attempt."""
    board = _load(job)
    n = 0
    for t in list(board.GetTracks()):
        if not t.IsLocked():
            _GRAVEYARD.append(t)
            board.Remove(t)
            n += 1
    P.SaveBoard(job["output"], board)
    return {"removed": n}


def cleanup_tracks(job):
    board = _load(job)
    res = _cleanup_tracks(board, set(job.get("plane_nets", [])), job.get("plane_layers", []))
    P.SaveBoard(job["output"], board)
    return res


_GRAVEYARD = []   # removed items stay referenced: freeing SWIG wrappers of removed items corrupts memory


def _cleanup_tracks(board, plane_nets=frozenset(), plane_layers=()):
    """Remove duplicate segments, dangling track stubs and vias joining fewer than two layers.

    Geometry is copied into plain Python once; the fix-point analysis never touches SWIG
    objects, and all removals happen in one pass at the end (KiCad 7 SWIG is fragile
    when items are removed while wrappers are still being used)."""
    items = list(board.GetTracks())
    locked = {i for i, it in enumerate(items) if it.IsLocked()}
    T, V = [], []
    for idx, it in enumerate(items):
        if it.GetClass() == "PCB_VIA":
            p = it.GetPosition()
            V.append((idx, p.x, p.y, it.GetNetCode()))
        else:
            a, b = it.GetStart(), it.GetEnd()
            T.append((idx, it.GetLayer(), it.GetNetCode(), a.x, a.y, b.x, b.y, it.GetWidth()))
    pads = []
    for fp in board.GetFootprints():
        for pd in fp.Pads():
            pads.append(pd)
    pads_by_net = {}
    for pd in pads:
        pads_by_net.setdefault(pd.GetNetCode(), []).append(pd)
    nplane = {board.FindNet(n).GetNetCode() for n in plane_nets if board.FindNet(n)}
    plane_ids = [getattr(P, l.replace(".", "_")) for l in plane_layers]
    cu_layers = [P.F_Cu, P.B_Cu] + [getattr(P, f"In{k}_Cu") for k in range(1, 31) if hasattr(P, f"In{k}_Cu")]

    dead = set()
    seen = set()
    for idx, layer, net, ax, ay, bx, by, w in T:          # duplicates / zero length
        key = (layer, net, min((ax, ay), (bx, by)), max((ax, ay), (bx, by)))
        if (key in seen or (ax, ay) == (bx, by)) and idx not in locked:
            dead.add(idx)
        seen.add(key)
    dupes = len(dead)

    pad_hit = {}                                            # (x, y, layer, net) -> bool, via SWIG once
    def on_pad(x, y, layer, net):
        k = (x, y, layer, net)
        if k not in pad_hit:
            v = P.VECTOR2I(x, y)
            pad_hit[k] = any(pd.IsOnLayer(layer) and pd.HitTest(v) for pd in pads_by_net.get(net, []))
        return pad_hit[k]

    def on_seg(x, y, t):
        _, _, _, ax, ay, bx, by, w = t
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        u = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
        px, py = ax + u * dx, ay + u * dy
        return (x - px) ** 2 + (y - py) ** 2 <= (w / 2 + 1) ** 2

    removed_t = removed_v = 0
    while True:
        alive_t = [t for t in T if t[0] not in dead]
        alive_v = [v for v in V if v[0] not in dead]
        ends = {}
        by_nl = {}
        for t in alive_t:
            by_nl.setdefault((t[2], t[1]), []).append(t)
            for pt in ((t[3], t[4]), (t[5], t[6])):
                ends[(t[1],) + pt] = ends.get((t[1],) + pt, 0) + 1
        vias_at = {(v[1], v[2], v[3]) for v in alive_v}
        new_dead = []
        for t in alive_t:
            idx, layer, net = t[0], t[1], t[2]
            for (x, y) in ((t[3], t[4]), (t[5], t[6])):
                if ends.get((layer, x, y), 0) > 1 or (x, y, net) in vias_at or on_pad(x, y, layer, net):
                    continue
                if any(o[0] != idx and on_seg(x, y, o) for o in by_nl.get((net, layer), [])):
                    continue
                new_dead.append(idx)
                break
        for idx, x, y, net in alive_v:
            layers = {l for (n, l), ts in by_nl.items() if n == net and any(on_seg(x, y, o) for o in ts)}
            layers |= {l for l in cu_layers if on_pad(x, y, l, net)}
            if net in nplane:
                layers |= set(plane_ids)
            if len(layers) < 2:
                new_dead.append(idx)
        new_dead = [i for i in new_dead if i not in locked]
        if not new_dead:
            break
        for idx in new_dead:
            dead.add(idx)
            if items[idx].GetClass() == "PCB_VIA":
                removed_v += 1
            else:
                removed_t += 1
    for idx in sorted(dead):
        _GRAVEYARD.append(items[idx])
        board.Remove(items[idx])
    return {"duplicates_removed": dupes, "dangling_tracks_removed": removed_t, "dangling_vias_removed": removed_v}


_LAST_IMPORT = {}


def _import_ses_manual(board, ses_path):
    import re
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    from ai_pcb_designer.kicad import sexpr
    from ai_pcb_designer.kicad.sexpr import child, children

    root = sexpr.parse(open(ses_path, encoding="utf-8").read())
    routes = child(root, "routes")
    res = child(routes, "resolution")
    unit, per = str(res[1]), float(res[2])
    to_mm = {"um": 0.001, "mm": 1.0, "mil": 0.0254, "inch": 25.4}[unit] / per

    def pt(x, y):  # Specctra Y is up
        return P.VECTOR2I(MM(float(x) * to_mm), MM(-float(y) * to_mm))

    layers = {board.GetLayerName(l): l for l in range(P.PCB_LAYER_ID_COUNT)}
    nwires = nvias = 0
    for net in children(child(routes, "network_out"), "net"):
        ni = board.FindNet(str(net[1]))
        if ni is None:
            raise RuntimeError(f"SES references unknown net {net[1]}")
        for w in children(net, "wire"):
            path = child(w, "path")
            layer = layers[str(path[1])]
            width = MM(float(path[2]) * to_mm)
            coords = path[3:]
            for i in range(0, len(coords) - 2, 2):
                t = P.PCB_TRACK(board)
                t.SetStart(pt(coords[i], coords[i + 1]))
                t.SetEnd(pt(coords[i + 2], coords[i + 3]))
                t.SetWidth(width)
                t.SetLayer(layer)
                t.SetNet(ni)
                board.Add(t)
                nwires += 1
        for v in children(net, "via"):
            m = re.search(r"_(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)_(um|mm|mil)", str(v[1]))
            scale = {"um": 0.001, "mm": 1.0, "mil": 0.0254}[m.group(3)] if m else 0.001
            via = P.PCB_VIA(board)
            via.SetPosition(pt(v[2], v[3]))
            via.SetWidth(MM(float(m.group(1)) * scale) if m else MM(0.8))
            via.SetDrill(MM(float(m.group(2)) * scale) if m else MM(0.4))
            via.SetLayerPair(P.F_Cu, P.B_Cu)
            via.SetViaType(P.VIATYPE_THROUGH)
            via.SetNet(ni)
            board.Add(via)
            nvias += 1
    _LAST_IMPORT.update(segments=nwires, vias=nvias)
    return True


SMALL_PASSIVE = ("_0201_", "_0402_", "_0603_", "_0805_", "LED_0603", "LED_0805", "D_SOD-123", "D_SOD-323")


def _relieve_small_pads(board, pour_nets) -> list:
    """Small two-terminal parts: a pad joined solidly to a pour heats far more slowly than
    its partner during reflow and can lift the part ("tombstoning"). Give those pads
    thermal-relief spokes; large pads, ICs and connectors stay solid."""
    out = []
    for fp in board.GetFootprints():
        fpid = str(fp.GetFPID().GetLibItemName())
        pads = list(fp.Pads())
        if len(pads) != 2 or not any(k.strip("_") in fpid for k in SMALL_PASSIVE):
            continue
        for pad in pads:
            if pad.GetAttribute() == P.PAD_ATTRIB_SMD and pad.GetNetname() in pour_nets:
                if hasattr(pad, "SetLocalZoneConnection"):
                    pad.SetLocalZoneConnection(P.ZONE_CONNECTION_THERMAL)
                else:
                    pad.SetZoneConnection(P.ZONE_CONNECTION_THERMAL)
                out.append(f"{fp.GetReference()}.{pad.GetNumber()}")
    return out


def add_zones(job):
    board = _load(job)
    W, H = job["board"]["width_mm"], job["board"]["height_mm"]
    inset = job["rules"]["edge_clearance_mm"]
    added = []
    existing = {(zz.GetLayer(), zz.GetNetname()) for zz in board.Zones()}
    for z in job.get("zones", []):
        net = board.FindNet(z["net"])
        if net is None:
            raise RuntimeError(f"zone net {z['net']} not on board")
        layer = getattr(P, z["layer"].replace(".", "_"))
        if (layer, z["net"]) in existing:
            continue
        if z.get("plane"):
            board.SetLayerType(layer, P.LT_POWER)   # Specctra export treats it as a plane layer
        zone = P.ZONE(board)
        zone.SetLayer(layer)
        zone.SetNetCode(net.GetNetCode())
        zone.SetLocalClearance(MM(job["rules"]["clearance_mm"] + 0.1))
        zone.SetMinThickness(MM(0.25))
        zone.SetPadConnection(P.ZONE_CONNECTION_THT_THERMAL)   # SMD solid, THT relieved
        zone.SetThermalReliefSpokeWidth(MM(0.3))
        zone.SetThermalReliefGap(MM(0.3))
        zone.SetIsFilled(False)
        poly = zone.Outline()
        poly.NewOutline()
        for x, y in ((inset, inset), (W - inset, inset), (W - inset, H - inset), (inset, H - inset)):
            poly.Append(MM(x), MM(y))
        board.Add(zone)
        added.append(f"{z['net']}@{z['layer']}")
    relieved = _relieve_small_pads(board, {z["net"] for z in job.get("zones", []) if not z.get("plane")})
    filler = P.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    P.SaveBoard(job["output"], board)
    return {"zones": added, "thermal_relief_pads": relieved}


def drc(job):
    board = _load(job)
    # refill so DRC sees current copper
    if board.Zones().size() if hasattr(board.Zones(), "size") else len(board.Zones()):
        P.ZONE_FILLER(board).Fill(board.Zones())
    units = getattr(P, "EDA_UNITS_MILLIMETRES", None)      # KiCad 7/8
    if units is None:
        units = getattr(P, "EDA_UNITS_MM")                    # KiCad 9/10 renamed it
    ok = P.WriteDRCReport(board, job["drc_report"], units, True)
    return {"ok": bool(ok), "report": job["drc_report"]}


def stats(job):
    board = _load(job)
    tracks = vias = 0
    length = 0.0
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            vias += 1
        else:
            tracks += 1
            length += P.ToMM(t.GetLength())
    conn = board.GetConnectivity()
    conn.RecalculateRatsnest()
    fps = []
    for fp in board.GetFootprints():
        pos = fp.GetPosition()
        bb = fp.GetBoundingBox(False, False)
        pads = []
        for p in fp.Pads():
            pp, sz = p.GetPosition(), p.GetSize()
            rot = int(round(p.GetOrientationDegrees())) % 180
            w, h = (sz.y, sz.x) if rot == 90 else (sz.x, sz.y)
            pads.append({"num": p.GetNumber(), "net": p.GetNetname(), "x": round(P.ToMM(pp.x), 4),
                         "y": round(P.ToMM(pp.y), 4), "w": round(P.ToMM(w), 4), "h": round(P.ToMM(h), 4),
                         "through": p.GetAttribute() in (P.PAD_ATTRIB_PTH, P.PAD_ATTRIB_NPTH),
                         "bottom": p.IsOnLayer(P.B_Cu) and not p.IsOnLayer(P.F_Cu)})
        fps.append({"ref": fp.GetReference(), "value": fp.GetValue(),
                    "fpid": fp.GetFPIDAsString(), "x": P.ToMM(pos.x), "y": P.ToMM(pos.y),
                    "rotation": fp.GetOrientationDegrees(),
                    "layer": fp.GetLayerName(), "path": fp.GetPath().AsString(),
                    "bbox": [round(P.ToMM(v), 3) for v in (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom())],
                    "pads": {p["num"]: p["net"] for p in pads}, "pad_geom": pads})
    eb = board.GetBoardEdgesBoundingBox()
    board_info = {"bbox": [round(P.ToMM(v), 3) for v in (eb.GetLeft(), eb.GetTop(), eb.GetRight(), eb.GetBottom())],
                  "copper_layers": board.GetCopperLayerCount(),
                  "thickness_mm": round(P.ToMM(board.GetDesignSettings().GetBoardThickness()), 3)}
    return {"tracks": tracks, "vias": vias, "track_length_mm": round(length, 2),
            "unrouted_connections": conn.GetUnconnectedCount(False),
            "nets": [str(n) for n in board.GetNetsByName().keys() if str(n)],
            "zones": len(list(board.Zones())), "footprints": fps, "board": board_info}


def plane_fanout(job):
    """Give every SMD pad on a plane net a short locked stub + through via to the plane.

    Candidate via positions: outward from the footprint first, then the other axis
    directions, at increasing distance. A position is accepted only if the via (plus
    clearance) and the stub clear every other-net pad, every other via, keepout areas
    and the board edge. Pads that cannot be fanned out are reported."""
    board = _load(job)
    r = job["rules"]
    via_d, drill, clr = MM(r["via_diameter_mm"]), MM(r["via_drill_mm"]), MM(r["clearance_mm"])
    edge = MM(r["edge_clearance_mm"])
    plane = {n: board.FindNet(n) for n in job["plane_nets"] if board.FindNet(n)}
    widths = job.get("widths", {})
    W, H = MM(job["board"]["width_mm"]), MM(job["board"]["height_mm"])
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    rects = []
    for p in pads:
        bb = p.GetBoundingBox()
        rects.append((bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom(), p.GetNetCode(), p))
    keepouts = [z for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    for fp in board.GetFootprints():
        keepouts += [z for z in fp.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    vias = [(v.GetPosition().x, v.GetPosition().y) for v in board.GetTracks() if v.GetClass() == "PCB_VIA"]
    h2h = board.GetDesignSettings().m_HoleToHoleMin + MM(0.05)
    holes = [(v[0], v[1], drill / 2) for v in vias]
    for p in pads:
        if p.GetAttribute() in (P.PAD_ATTRIB_PTH, P.PAD_ATTRIB_NPTH):
            ds_ = p.GetDrillSize()
            holes.append((p.GetPosition().x, p.GetPosition().y, max(ds_.x, ds_.y) / 2))

    def rect_hit(x1, y1, x2, y2, net, own):
        for (a, b, c, d, n, p) in rects:
            if p is own or (n == net and n != 0):
                continue
            if x1 < c + clr and x2 > a - clr and y1 < d + clr and y2 > b - clr:
                return True
        return False

    def add_track(a, b, w, layer, net):
        t = P.PCB_TRACK(board)
        t.SetStart(a); t.SetEnd(b); t.SetWidth(w); t.SetLayer(layer); t.SetNetCode(net); t.SetLocked(True)
        board.Add(t)

    placed, bridged, skipped, failed, via_in_pad = 0, 0, 0, [], []
    for fp in board.GetFootprints():
        fc = fp.GetPosition()
        fpads = list(fp.Pads())
        multi = len(fpads) >= 8
        # pads already tied to the plane through their own through-hole vias (thermal arrays)
        via_numbers = {(p.GetNumber(), p.GetNetCode()) for p in fpads if p.GetAttribute() == P.PAD_ATTRIB_PTH}
        smd = [p for p in fpads if p.GetNetname() in plane and p.GetAttribute() == P.PAD_ATTRIB_SMD]
        # bridge adjacent same-net pads in a row (e.g. IC address pins tied to GND): one via per group
        anchor_of = {}
        for p in smd:
            for q in smd:
                if q is p or q.GetNetCode() != p.GetNetCode() or id(q) in anchor_of:
                    continue
                a, b = p.GetPosition(), q.GetPosition()
                if (a.x == b.x or a.y == b.y) and abs(a.x - b.x) + abs(a.y - b.y) <= MM(1.3) and id(p) not in anchor_of:
                    # the bridge must not pass over any other-net pad (e.g. pin 2 between pins 1 and 3)
                    bw2 = min(MM(0.25), min(q.GetSize().x, q.GetSize().y)) // 2
                    x1, x2 = sorted((a.x, b.x)); y1, y2 = sorted((a.y, b.y))
                    blocked = any(not (x1 - bw2 >= rc or x2 + bw2 <= ra or y1 - bw2 >= rd or y2 + bw2 <= rb)
                                  for (ra, rb, rc, rd, n, pp) in rects
                                  if pp is not p and pp is not q and (n != p.GetNetCode() or n == 0))
                    if not blocked:
                        anchor_of[id(q)] = p
        for pad in smd:
            net = pad.GetNetCode()
            if (pad.GetNumber(), net) in via_numbers:
                skipped += 1
                continue
            layer = P.F_Cu if pad.IsOnLayer(P.F_Cu) else P.B_Cu
            if id(pad) in anchor_of:
                anc = anchor_of[id(pad)]
                bw = min(MM(0.25), min(pad.GetSize().x, pad.GetSize().y))
                add_track(pad.GetPosition(), anc.GetPosition(), bw, layer, net)
                bridged += 1
                continue
            size = pad.GetSize()
            if min(size.x, size.y) >= max(MM(1.0), via_d + MM(0.1)) and multi:
                # exposed/thermal pad: via-in-pad at its centre (tent or plug these vias in fab)
                cpos = pad.GetPosition()
                if not any((cpos.x - a) ** 2 + (cpos.y - b) ** 2 < (drill / 2 + hr + h2h) ** 2 for a, b, hr in holes):
                    v = P.PCB_VIA(board)
                    v.SetPosition(cpos); v.SetWidth(via_d); v.SetDrill(drill)
                    v.SetLayerPair(P.F_Cu, P.B_Cu); v.SetViaType(P.VIATYPE_THROUGH)
                    v.SetNetCode(net); v.SetLocked(True)
                    board.Add(v)
                    vias.append((cpos.x, cpos.y)); holes.append((cpos.x, cpos.y, drill / 2))
                    via_in_pad.append(f"{fp.GetReference()}.{pad.GetNumber()}")
                    continue
            w = MM(widths.get(pad.GetNetname(), r["track_width_mm"]))
            w = min(w, min(pad.GetSize().x, pad.GetSize().y)) if multi else w
            c = pad.GetPosition()
            bb = pad.GetBoundingBox()
            hx, hy = (bb.GetRight() - bb.GetLeft()) / 2, (bb.GetBottom() - bb.GetTop()) / 2
            ox, oy = c.x - fc.x, c.y - fc.y
            primary = (1 if ox >= 0 else -1, 0) if abs(ox) >= abs(oy) else (0, 1 if oy >= 0 else -1)
            if multi:   # fan IC pads inward (under the body) to keep outward escape lanes free
                primary = (-primary[0], -primary[1])
            dirs = [primary, (-primary[1], primary[0]), (primary[1], -primary[0]), (-primary[0], -primary[1])]
            layer = P.F_Cu if pad.IsOnLayer(P.F_Cu) else P.B_Cu
            done = False
            for dx, dy in dirs:
                half = hx if dx else hy
                for k in range(8):
                    dist = half + via_d / 2 + clr + MM(0.1) + k * MM(0.25)
                    vx, vy = int(c.x + dx * dist), int(c.y + dy * dist)
                    rv = via_d / 2
                    if vx - rv < edge or vy - rv < edge or vx + rv > W - edge or vy + rv > H - edge:
                        continue
                    if rect_hit(vx - rv, vy - rv, vx + rv, vy + rv, net, pad):
                        continue
                    # stub rectangle from pad edge to via
                    sx1, sx2 = sorted((c.x, vx)); sy1, sy2 = sorted((c.y, vy))
                    if rect_hit(sx1 - w // 2, sy1 - w // 2, sx2 + w // 2, sy2 + w // 2, net, pad):
                        continue
                    if any((vx - a) ** 2 + (vy - b) ** 2 < (via_d + clr) ** 2 for a, b in vias):
                        continue
                    if any((vx - a) ** 2 + (vy - b) ** 2 < (drill / 2 + hr + h2h) ** 2 for a, b, hr in holes):
                        continue
                    if any(z.Outline().Contains(P.VECTOR2I(vx, vy)) for z in keepouts):
                        continue
                    add_track(c, P.VECTOR2I(vx, vy), w, layer, net)
                    v = P.PCB_VIA(board)
                    v.SetPosition(P.VECTOR2I(vx, vy)); v.SetWidth(via_d); v.SetDrill(drill)
                    v.SetLayerPair(P.F_Cu, P.B_Cu); v.SetViaType(P.VIATYPE_THROUGH)
                    v.SetNetCode(net); v.SetLocked(True)
                    board.Add(v)
                    vias.append((vx, vy))
                    holes.append((vx, vy, drill / 2))
                    placed += 1
                    done = True
                    break
                if done:
                    break
            if not done:
                failed.append(f"{fp.GetReference()}.{pad.GetNumber()}")
    P.ZONE_FILLER(board).Fill(board.Zones())
    P.SaveBoard(job["output"], board)
    return {"fanouts": placed, "bridged": bridged, "skipped_have_vias": skipped, "via_in_pad": via_in_pad,
            "failed": failed}


def repair(job):
    """Apply controlled, targeted repair operations in place, then refill zones."""
    board = _load(job)
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    done = []
    for op in job["ops"]:
        kind = op["op"]
        if kind in ("shrink_reference", "hide_reference"):
            for ref in op["refs"]:
                fp = fps.get(ref)
                if fp is None:
                    continue
                t = fp.Reference()
                if kind == "shrink_reference":
                    sz = MM(op.get("size_mm", 0.8))
                    t.SetTextSize(P.VECTOR2I(sz, sz))
                    t.SetTextThickness(int(sz * 0.15))
                else:
                    t.SetVisible(False)
                done.append(f"{kind}:{ref}")
        elif kind == "solid_zone_connection":
            for ref, num in op["pads"]:
                fp = fps.get(ref)
                for pad in (fp.Pads() if fp else []):
                    if pad.GetNumber() == num:
                        if hasattr(pad, "SetLocalZoneConnection"):   # KiCad >= 8 name
                            pad.SetLocalZoneConnection(P.ZONE_CONNECTION_FULL)
                        else:
                            pad.SetZoneConnection(P.ZONE_CONNECTION_FULL)
                done.append(f"{kind}:{ref}.{num}")
        elif kind == "remove_dangling":
            pt = _v(op["x"], op["y"])
            conn = board.GetConnectivity()
            conn.RecalculateRatsnest()
            before = conn.GetUnconnectedCount(False)
            target = None
            for t in board.GetTracks():
                if t.GetNetname() != op["net"]:
                    continue
                is_via = t.GetClass() == "PCB_VIA"
                if op["kind"] == "via" and is_via and t.HitTest(pt):
                    target = t
                elif op["kind"] == "track" and not is_via and t.GetLayerName() == op["layer"] \
                        and abs(P.ToMM(t.GetLength()) - op["length"]) < 0.01 and t.HitTest(pt, MM(0.01)):
                    target = t
                if target is not None:
                    break
            if target is None:
                done.append(f"{kind}:not-found@{op['x']},{op['y']}")
                continue
            board.Remove(target)
            _GRAVEYARD.append(target)
            conn = board.GetConnectivity()
            conn.RecalculateRatsnest()
            if conn.GetUnconnectedCount(False) > before:      # it was carrying a connection: undo
                board.Add(target)
                done.append(f"{kind}:reverted@{op['x']},{op['y']}")
            else:
                done.append(f"{kind}:{op['kind']}@{op['x']},{op['y']}")
        else:
            raise ValueError(f"unknown repair op {kind}")
    P.ZONE_FILLER(board).Fill(board.Zones())
    P.SaveBoard(job["output"], board)
    return {"applied": done}


def neck_stubs(job):
    """Neck-down at fine-pitch pins. The autorouter cannot narrow a trace at a pin, so a net
    whose trace is wider than a pad with close neighbours (e.g. a 0.5 mm ground or 0.3 mm
    3V3 trace into a 0.3 mm pad at 0.5 mm pitch) could never connect to it.

    On ICs the pad gets a short locked stub, narrower than the pad, running INWARD under the
    body (free space that no other pin needs), ending in a via; the full-width trace then
    reaches that via on the opposite layer, where the area under the part is open. If no via
    fits, or the part is not an IC, the stub runs outward instead. Pads whose full-width trace
    already clears every neighbour are left alone."""
    board = _load(job)
    r = job["rules"]
    clr, edge = MM(r["clearance_mm"]), MM(r["edge_clearance_mm"])
    via_d, drill = MM(r["via_diameter_mm"]), MM(r["via_drill_mm"])
    h2h = board.GetDesignSettings().m_HoleToHoleMin + MM(0.05)
    W_, H_ = MM(job["board"]["width_mm"]), MM(job["board"]["height_mm"])
    widths = {n: MM(w) for n, w in job.get("net_widths", {}).items()}
    skip = set(job.get("plane_nets", []))
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    rects = []
    for p in pads:
        bb = p.GetBoundingBox()
        rects.append((bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom(), p.GetNetCode(), p))
    vias = [(v.GetPosition().x, v.GetPosition().y) for v in board.GetTracks() if v.GetClass() == "PCB_VIA"]
    holes = [(x, y, drill / 2) for x, y in vias]
    for p in pads:
        if p.GetAttribute() in (P.PAD_ATTRIB_PTH, P.PAD_ATTRIB_NPTH):
            ds_ = p.GetDrillSize()
            holes.append((p.GetPosition().x, p.GetPosition().y, max(ds_.x, ds_.y) / 2))

    def hit(x1, y1, x2, y2, net, own):
        for (a, b, c, d, n, p) in rects:
            if p is own or (n == net and n != 0):
                continue
            if x1 < c + clr and x2 > a - clr and y1 < d + clr and y2 > b - clr:
                return True
        return False

    def inside(x, y, rad):
        return x - rad >= edge and y - rad >= edge and x + rad <= W_ - edge and y + rad <= H_ - edge

    def add_track(a, b, w, layer, net):
        t = P.PCB_TRACK(board)
        t.SetStart(a); t.SetEnd(b); t.SetWidth(w); t.SetLayer(layer); t.SetNetCode(net); t.SetLocked(True)
        board.Add(t)
        rects.append((min(a.x, b.x) - w // 2, min(a.y, b.y) - w // 2, max(a.x, b.x) + w // 2,
                      max(a.y, b.y) + w // 2, net, None))

    inward, outward, failed = [], [], []
    for fp in board.GetFootprints():
        fc = fp.GetPosition()
        is_ic = len(list(fp.Pads())) >= 8
        for pad in fp.Pads():
            name = pad.GetNetname()
            if pad.GetAttribute() != P.PAD_ATTRIB_SMD or name not in widths or name in skip:
                continue
            W, net = widths[name], pad.GetNetCode()
            bb = pad.GetBoundingBox()
            c = pad.GetPosition()
            wx, wy = bb.GetRight() - bb.GetLeft(), bb.GetBottom() - bb.GetTop()
            horiz = wx >= wy
            minor = min(wx, wy)
            if minor >= W + MM(0.05):
                continue
            # would a full-width trace laid along the pad violate clearance to a neighbour?
            m = W // 2 + (MM(0.05) if is_ic else 0)   # near-exact clearance fits at fine-pitch IC pins fail in the router
            if horiz:
                probe = (bb.GetLeft(), c.y - m, bb.GetRight(), c.y + m)
            else:
                probe = (c.x - m, bb.GetTop(), c.x + m, bb.GetBottom())
            if not hit(*probe, net, pad):
                continue
            half = (wx if horiz else wy) // 2
            out = (1 if c.x >= fc.x else -1, 0) if horiz else (0, 1 if c.y >= fc.y else -1)
            sw = max(MM(0.15), minor - MM(0.05))
            layer = P.F_Cu if pad.IsOnLayer(P.F_Cu) else P.B_Cu
            done = False
            if is_ic:
                # 1) a same-net pad just inside (e.g. a GND pin next to the exposed GND pad):
                #    join it directly, no via and nothing outside the part is blocked
                d = (-out[0], -out[1])
                tx, ty = int(c.x + d[0] * half), int(c.y + d[1] * half)
                for other in fp.Pads():
                    if other is pad or other.GetNetCode() != net:
                        continue
                    ob = other.GetBoundingBox()
                    for k in range(1, 13):
                        dist = half + k * MM(0.1)
                        ex, ey = int(c.x + d[0] * dist), int(c.y + d[1] * dist)
                        if ob.GetLeft() < ex < ob.GetRight() and ob.GetTop() < ey < ob.GetBottom():
                            x1, x2 = sorted((tx, ex)); y1, y2 = sorted((ty, ey))
                            if not hit(x1 - sw // 2, y1 - sw // 2, x2 + sw // 2, y2 + sw // 2, net, pad):
                                add_track(c, P.VECTOR2I(ex, ey), sw, layer, net)
                                inward.append(f"{fp.GetReference()}.{pad.GetNumber()}->{other.GetNumber()}")
                                done = True
                            break
                    if done:
                        break
            if is_ic and not done:                        # 2) inward stub + via under the body
                for k in range(4):
                    dist = half + clr + via_d // 2 + k * MM(0.15)
                    vx, vy = int(c.x + d[0] * dist), int(c.y + d[1] * dist)
                    tx, ty = int(c.x + d[0] * half), int(c.y + d[1] * half)
                    x1, x2 = sorted((tx, vx)); y1, y2 = sorted((ty, vy))
                    rv = via_d // 2
                    if (not inside(vx, vy, rv) or hit(vx - rv, vy - rv, vx + rv, vy + rv, net, pad)
                            or hit(x1 - sw // 2, y1 - sw // 2, x2 + sw // 2, y2 + sw // 2, net, pad)
                            or any((vx - a) ** 2 + (vy - b) ** 2 < (via_d + clr) ** 2 for a, b in vias)
                            or any((vx - a) ** 2 + (vy - b) ** 2 < (drill / 2 + hr + h2h) ** 2 for a, b, hr in holes)):
                        continue
                    add_track(c, P.VECTOR2I(vx, vy), sw, layer, net)
                    v = P.PCB_VIA(board)
                    v.SetPosition(P.VECTOR2I(vx, vy)); v.SetWidth(via_d); v.SetDrill(drill)
                    v.SetLayerPair(P.F_Cu, P.B_Cu); v.SetViaType(P.VIATYPE_THROUGH)
                    v.SetNetCode(net); v.SetLocked(True)
                    board.Add(v)
                    vias.append((vx, vy)); holes.append((vx, vy, drill / 2))
                    rects.append((vx - rv, vy - rv, vx + rv, vy + rv, net, None))
                    inward.append(f"{fp.GetReference()}.{pad.GetNumber()}")
                    done = True
                    break
            if not done:                                  # outward neck-down stub
                ext = W // 2 + clr + MM(0.05)
                ex, ey = int(c.x + out[0] * (half + ext)), int(c.y + out[1] * (half + ext))
                tx, ty = int(c.x + out[0] * half), int(c.y + out[1] * half)
                x1, x2 = sorted((tx, ex)); y1, y2 = sorted((ty, ey))
                if (hit(x1 - sw // 2, y1 - sw // 2, x2 + sw // 2, y2 + sw // 2, net, pad)
                        or hit(ex - W // 2, ey - W // 2, ex + W // 2, ey + W // 2, net, pad)
                        or not inside(ex, ey, W // 2)):
                    failed.append(f"{fp.GetReference()}.{pad.GetNumber()}")
                    continue
                add_track(c, P.VECTOR2I(ex, ey), sw, layer, net)
                outward.append(f"{fp.GetReference()}.{pad.GetNumber()}")
    P.SaveBoard(job["output"], board)
    return {"inward_via": inward, "outward": outward, "failed": failed}


def width_audit(job):
    """Routed (unlocked) copper narrower than the net's required width.

    Narrow pieces are grouped into connected chains; a chain of at most 3 mm that touches a
    same-net pad smaller than the required width is a neck-down into that pin (normal
    practice) and is reported separately. Anything else is an under-width current path.
    Locked copper is the deliberate neck-down / fan-out stubs."""
    board = _load(job)
    req = job.get("net_widths", {})
    pads_by_net = {}
    for fp in board.GetFootprints():
        for pd in fp.Pads():
            pads_by_net.setdefault(pd.GetNetCode(), []).append(pd)
    thin, stubs, locked_segs = {}, {}, {}
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            continue
        n = t.GetNetname()
        if n not in req:
            continue
        w = P.ToMM(t.GetWidth())
        if t.IsLocked():
            stubs[n] = min(stubs.get(n, 99), w)
            a, b = t.GetStart(), t.GetEnd()
            locked_segs.setdefault(n, []).append(((a.x, a.y), (b.x, b.y), t.GetWidth()))
        elif w < req[n] - 0.005:
            a, b = t.GetStart(), t.GetEnd()
            thin.setdefault(n, []).append(((a.x, a.y), (b.x, b.y), w, P.ToMM(t.GetLength()), t))
    narrow, necks = {}, {}
    tol = MM(0.01)
    for n, segs in thin.items():
        parent = list(range(len(segs)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                if any(abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol
                       for p in segs[i][:2] for q in segs[j][:2]):
                    parent[find(i)] = find(j)
        chains = {}
        for i in range(len(segs)):
            chains.setdefault(find(i), []).append(segs[i])
        for chain in chains.values():
            length = sum(c[3] for c in chain)
            w = min(c[2] for c in chain)
            net_pads = pads_by_net.get(chain[0][4].GetNetCode(), [])
            def touches(pd, seg):                      # a track end overlapping the pad counts
                acc = int(seg.GetWidth() // 2)
                try:
                    return pd.HitTest(seg.GetStart(), acc) or pd.HitTest(seg.GetEnd(), acc)
                except TypeError:
                    return pd.HitTest(seg.GetStart()) or pd.HitTest(seg.GetEnd())
            at_small_pad = any(P.ToMM(min(_pad_size(pd).x, _pad_size(pd).y)) < req[n] and touches(pd, c[4])
                               for c in chain for pd in net_pads)
            def on_stub(pt):
                for (s0, s1, sw) in locked_segs.get(n, []):
                    dx, dy = s1[0] - s0[0], s1[1] - s0[1]
                    L2 = dx * dx + dy * dy
                    u = 0 if L2 == 0 else max(0, min(1, ((pt[0] - s0[0]) * dx + (pt[1] - s0[1]) * dy) / L2))
                    if math.hypot(pt[0] - s0[0] - u * dx, pt[1] - s0[1] - u * dy) <= sw / 2 + tol:
                        return True
                return False
            at_stub = any(on_stub(p) for c in chain for p in c[:2])
            # a short neck-down into a small pin, or a slightly narrower run (>= 90% of a width
            # that already carries a 1.5x current margin) leading to one, is accepted
            if (at_small_pad or at_stub) and (length <= 3.0 or (w >= 0.9 * req[n] - 0.005 and length <= 10.0)):
                necks[n] = min(necks.get(n, 99), round(w, 3))
            else:
                narrow[n] = min(narrow.get(n, 99), round(w, 3))
    return {"below_required": {n: {"required": req[n], "narrowest": w} for n, w in narrow.items()},
            "pin_neck_downs": necks,
            "neck_down_stub_min": {n: round(w, 3) for n, w in stubs.items()}}


def _set_angle(t, deg):
    if hasattr(t, "SetTextAngleDegrees"):
        t.SetTextAngleDegrees(deg)
    else:
        t.SetTextAngle(P.EDA_ANGLE(deg, P.DEGREES_T))


def place_labels(job):
    """Put every reference label where a person can read it after manufacture.

    For each part, candidate spots around its outline (above, below, left, right, corners,
    then shifted positions; horizontal first, then vertical) are tried in order of
    preference. A spot is accepted when the label's box keeps clear of every pad (the fab
    trims silkscreen off exposed copper), via, silkscreen outline, already-placed label and
    the board edge. Labels shrink (1.0 -> 0.8 -> 0.7 mm) only if nothing fits; a label is
    hidden only as a last resort, and every shrink/hide is reported."""
    board = _load(job)
    W, H = MM(job["board"]["width_mm"]), MM(job["board"]["height_mm"])
    edge = MM(0.4)
    pad_clr, silk_clr, gap = MM(0.2), MM(0.15), MM(0.25)
    obstacles = []                    # (x1, y1, x2, y2, clearance, owner_ref)
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        for pd in fp.Pads():
            b = pd.GetBoundingBox()
            obstacles.append((b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom(), pad_clr, None))
        for g in fp.GraphicalItems():
            if g.GetLayer() in (P.F_SilkS, P.B_SilkS) and g.GetClass() not in ("PCB_FIELD", "FP_TEXT", "PCB_TEXT"):
                b = g.GetBoundingBox()
                obstacles.append((b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom(), silk_clr, ref))
    for v in board.GetTracks():
        if v.GetClass() == "PCB_VIA":
            b = v.GetBoundingBox()
            obstacles.append((b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom(), MM(0.1), None))

    def free(x1, y1, x2, y2):
        if x1 < edge or y1 < edge or x2 > W - edge or y2 > H - edge:
            return False
        for (a, b, c, d, clr, _) in obstacles:
            if x1 < c + clr and x2 > a - clr and y1 < d + clr and y2 > b - clr:
                return False
        return True

    placed, shrunk, hidden = 0, [], []
    fps = sorted(board.GetFootprints(), key=lambda f: -f.GetBoundingBox(False, False).GetWidth()
                 * f.GetBoundingBox(False, False).GetHeight())
    for fp in sorted(fps, key=lambda f: f.GetBoundingBox(False, False).GetWidth()
                     * f.GetBoundingBox(False, False).GetHeight()):
        t = fp.Reference()
        if not t.IsVisible() and fp.GetReference().startswith("H"):
            continue                                   # mounting holes stay unlabelled
        body = fp.GetBoundingBox(False, False)
        bl, bt, br, bb = body.GetLeft(), body.GetTop(), body.GetRight(), body.GetBottom()
        cx, cy = (bl + br) // 2, (bt + bb) // 2
        done = False
        for size in (1.0, 0.8, 0.7):
            t.SetVisible(True)
            t.SetTextSize(P.VECTOR2I(MM(size), MM(size)))
            t.SetTextThickness(MM(0.15 if size >= 1.0 else 0.12))
            for deg in (0, 90):
                _set_angle(t, deg)
                t.SetPosition(P.VECTOR2I(cx, cy))
                tb = t.GetBoundingBox()
                tw, th = tb.GetWidth(), tb.GetHeight()
                hw, hh = tw // 2, th // 2
                spots = [(cx, bt - gap - hh), (cx, bb + gap + hh), (bl - gap - hw, cy), (br + gap + hw, cy),
                         (bl - gap - hw, bt - gap - hh), (br + gap + hw, bt - gap - hh),
                         (bl - gap - hw, bb + gap + hh), (br + gap + hw, bb + gap + hh)]
                for k in (1, 2, 3):                    # slide along the long edges
                    d = k * MM(0.8)
                    spots += [(cx - d, bt - gap - hh), (cx + d, bt - gap - hh),
                              (cx - d, bb + gap + hh), (cx + d, bb + gap + hh),
                              (bl - gap - hw, cy - d), (bl - gap - hw, cy + d),
                              (br + gap + hw, cy - d), (br + gap + hw, cy + d)]
                for k in (1, 2):                       # a little further out
                    o = k * MM(0.6)
                    spots += [(cx, bt - gap - hh - o), (cx, bb + gap + hh + o),
                              (bl - gap - hw - o, cy), (br + gap + hw + o, cy)]
                for (x, y) in spots:
                    x1, y1, x2, y2 = x - hw, y - hh, x + hw, y + hh
                    if free(x1, y1, x2, y2):
                        t.SetPosition(P.VECTOR2I(int(x), int(y)))
                        obstacles.append((x1, y1, x2, y2, silk_clr, fp.GetReference()))
                        done = True
                        break
                if done:
                    break
            if done:
                if size < 1.0:
                    shrunk.append(fp.GetReference())
                placed += 1
                break
        if not done:
            t.SetVisible(False)
            hidden.append(fp.GetReference())
    P.SaveBoard(job["output"], board)
    return {"placed": placed, "shrunk": shrunk, "hidden": hidden}


def fab_facts(job):
    """What a board house asks for, measured from the copper that is actually on the board."""
    board = _load(job)
    mm = lambda v: round(P.ToMM(v), 3)
    tw, vd, vdr, ring = [], [], [], []
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            d, dr = _via_width(t), t.GetDrillValue()
            vd.append(d); vdr.append(dr); ring.append((d - dr) / 2)
        else:
            tw.append(t.GetWidth())
    pth, npth = [], []
    parts = {"top": {"smd": 0, "tht": 0}, "bottom": {"smd": 0, "tht": 0}}
    tht_refs, pth_by_ref = [], {}
    for fp in board.GetFootprints():
        side = "bottom" if fp.GetLayer() == P.B_Cu else "top"
        tht = False
        has_copper = False
        for pad in fp.Pads():
            a = pad.GetAttribute()
            if a == P.PAD_ATTRIB_PTH:
                tht = has_copper = True
                dr = min(pad.GetDrillSize().x, pad.GetDrillSize().y)
                pth.append(dr)
                pth_by_ref[fp.GetReference()] = min(dr, pth_by_ref.get(fp.GetReference(), dr))
                sz = _pad_size(pad)
                ring.append((min(sz.x, sz.y) - dr) / 2)
            elif a == P.PAD_ATTRIB_NPTH:
                npth.append(min(pad.GetDrillSize().x, pad.GetDrillSize().y))
            elif a == P.PAD_ATTRIB_SMD:
                has_copper = True
        if not has_copper:                      # mounting holes, fiducials without copper, logos
            continue
        parts[side]["tht" if tht else "smd"] += 1
        if tht:
            tht_refs.append(fp.GetReference())
    eb = board.GetBoardEdgesBoundingBox()
    # the outline's bounding box includes the Edge.Cuts line width: report the outline itself
    bw, bh = job.get("board", {}).get("width_mm"), job.get("board", {}).get("height_mm")
    return {"width_mm": bw or mm(eb.GetWidth()), "height_mm": bh or mm(eb.GetHeight()),
            "copper_layers": board.GetCopperLayerCount(),
            "thickness_mm": mm(board.GetDesignSettings().GetBoardThickness()),
            "min_track_mm": mm(min(tw)) if tw else None,
            "min_via_diameter_mm": mm(min(vd)) if vd else None,
            "min_via_drill_mm": mm(min(vdr)) if vdr else None,
            "min_pth_drill_mm": mm(min(pth)) if pth else None,
            "min_npth_drill_mm": mm(min(npth)) if npth else None,
            "min_annular_ring_mm": mm(min(ring)) if ring else None,
            "min_clearance_mm": job.get("rules", {}).get("clearance_mm"),
            "vias": len(vd), "parts": parts, "tht_refs": tht_refs,
            "pth_drill_by_ref": {k: mm(v) for k, v in pth_by_ref.items()}}


def usbc_fanout(job):
    """USB-C receptacles duplicate D+ (A6, B6) and D- (A7, B7) so the plug works either way
    round; along the pad row the pairs interleave (B7 D-, A6 D+, A7 D-, B6 D+ at 0.5 mm
    pitch), so they cannot be joined on one layer and no autorouter finds the two-layer
    solution reliably. This routes both joins completely, as locked copper behind the pads:

      D- : B7 and A7 run inward on the top layer and meet below the row;
      D+ : A6 drops through a via inside that loop, B6 bends outward to a second via, and
           the two vias are joined on the bottom layer.

    0.15 mm tracks, 0.6/0.3 mm vias, >= 0.2 mm clearance everywhere (worked out for the
    standard 0.5 mm USB-C 16P pitch; other pitches are left to the router). The rest of
    each net (ESD chip, USB-UART) is routed normally from this copper."""
    board = _load(job)
    vd, vdr, tw = MM(0.6), MM(0.3), MM(0.15)
    done, skipped = [], []
    for fp in board.GetFootprints():
        if "USB_C_Receptacle" not in fp.GetFPIDAsString():
            continue
        pads = {p.GetNumber(): p for p in fp.Pads()}
        if not all(k in pads and pads[k].GetNetCode() for k in ("A6", "A7", "B6", "B7")):
            continue
        b6, b7 = pads["B6"].GetPosition(), pads["B7"].GetPosition()
        ux, uy = b6.x - b7.x, b6.y - b7.y
        ln = (ux * ux + uy * uy) ** 0.5
        if abs(ln - MM(1.5)) > MM(0.02):              # not the standard 0.5 mm pitch
            skipped.append(fp.GetReference())
            continue
        ux, uy = ux / ln, uy / ln                       # lateral unit, B7 -> B6
        c = fp.GetPosition()
        mx, my = (b6.x + b7.x) / 2, (b6.y + b7.y) / 2
        dx, dy = -uy, ux                                # inward: away from the receptacle body
        if (mx - c.x) * dx + (my - c.y) * dy < 0:
            dx, dy = -dx, -dy

        def pt(lat, depth):                             # mm, relative to the pad-row centre
            return P.VECTOR2I(int(mx + ux * MM(lat) + dx * MM(depth)), int(my + uy * MM(lat) + dy * MM(depth)))

        def track(pts, net, layer=P.F_Cu):
            for a, b in zip(pts, pts[1:]):
                t = P.PCB_TRACK(board)
                t.SetStart(pt(*a)); t.SetEnd(pt(*b)); t.SetWidth(tw); t.SetLayer(layer)
                t.SetNetCode(net); t.SetLocked(True)
                board.Add(t)

        def via(at, net):
            v = P.PCB_VIA(board)
            v.SetPosition(pt(*at)); v.SetWidth(vd); v.SetDrill(vdr)
            v.SetLayerPair(P.F_Cu, P.B_Cu); v.SetViaType(P.VIATYPE_THROUGH)
            v.SetNetCode(net); v.SetLocked(True)
            board.Add(v)
        dm, dp = pads["B7"].GetNetCode(), pads["A6"].GetNetCode()
        if pads["A7"].GetNetCode() != dm or pads["B6"].GetNetCode() != dp:
            skipped.append(fp.GetReference())
            continue
        # D-: B7 straight in, A7 in and out to 0.5, joined at depth 3.0 on the top layer
        track([(-0.75, 0), (-0.75, 3.0), (0.5, 3.0), (0.5, 1.25), (0.25, 1.0), (0.25, 0)], dm)
        # D+: A6 to a via inside that loop, B6 out to a via beside it, joined underneath
        va, vb = (-0.125, 2.3), (1.1, 1.9)
        track([(-0.25, 0), (-0.25, 1.8), va], dp)
        track([(0.75, 0), (0.75, 0.9), (1.1, 1.25), vb], dp)
        via(va, dp)
        via(vb, dp)
        track([va, vb], dp, P.B_Cu)
        done.append(fp.GetReference())
    P.SaveBoard(job["output"], board)
    return {"routed": done, "left_to_router": skipped}

COMMANDS = {"usbc-fanout": usbc_fanout, "fab-facts": fab_facts, "place-labels": place_labels, "width-audit": width_audit, "neck-stubs": neck_stubs, "clear-routes": clear_routes, "plane-fanout": plane_fanout, "repair": repair, "cleanup-tracks": cleanup_tracks, "build": build, "export-dsn": export_dsn, "import-ses": import_ses,
            "add-zones": add_zones, "drc": drc, "stats": stats}

if __name__ == "__main__":
    cmd, job_path = sys.argv[1], sys.argv[2]
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    try:
        _out({"ok": True, "result": COMMANDS[cmd](job)})
        code = 0
    except Exception as e:  # report, never swallow
        import traceback
        _out({"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
        code = 1
    sys.stdout.flush()
    os._exit(code)   # skip interpreter teardown (SWIG destructors on removed items)
