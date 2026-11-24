"""Block-based heuristic placement.

1. Local layout per functional block: the anchor part sits at the origin; an
   edge connector is rotated so its outward side faces local "up" and the rest
   of the block is kept behind it; remaining parts are packed greedily toward
   the parts they connect to.
2. Edge blocks are rotated so the connector faces its board edge and spread
   evenly along that edge.
3. Other blocks are placed greedily toward the centroid of the blocks they
   share signal nets with (high-fanout nets such as GND are ignored).
Limitations: block bounding boxes are rectangles (no interlocking), no
thermal model, no optimisation pass after the greedy step.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ...kicad import sexpr
from ...kicad.sexpr import child, children
from ..circuit.model import Circuit

SIDES = ["top", "left", "bottom", "right"]           # CCW order (KiCad +angle is CCW on screen)
DIRS = {"top": (0, -1), "left": (-1, 0), "bottom": (0, 1), "right": (1, 0)}
# Outward side of footprints where the courtyard heuristic is wrong or ambiguous.
# Verified by rendering the KiCad 3D models: Phoenix horizontal terminal blocks have their
# wire entry on local +y (the courtyard extends slightly further toward -y, which fooled
# the heuristic and pointed every wire entry into the board).
FACING_OVERRIDES: dict[str, str] = {}
FACING_PATTERNS: list[tuple[str, str, str]] = [("TerminalBlock_Phoenix:", "_Horizontal", "bottom")]
HIGH_FANOUT = 8
USB_FANOUT_DEPTH = 3.0      # mm behind the USB-C pad row kept for the locked D+/D- routing
EDGE_MARGIN = 1.0           # the placer keeps courtyards this far from the board edge (rules: 0.5 + 0.5)
DEBUG: dict = {}


@dataclass
class Box:
    minx: float
    miny: float
    maxx: float
    maxy: float

    @property
    def w(self): return self.maxx - self.minx

    @property
    def h(self): return self.maxy - self.miny

    def moved(self, x, y):
        return Box(self.minx + x, self.miny + y, self.maxx + x, self.maxy + y)

    def rotated(self, ang):
        pts = [rot(x, y, ang) for x, y in ((self.minx, self.miny), (self.maxx, self.miny),
                                           (self.minx, self.maxy), (self.maxx, self.maxy))]
        return Box(min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))

    def overlaps(self, o: "Box", gap: float) -> bool:
        return not (self.maxx + gap <= o.minx or o.maxx + gap <= self.minx or
                    self.maxy + gap <= o.miny or o.maxy + gap <= self.miny)

    def union(self, o: "Box") -> "Box":
        return Box(min(self.minx, o.minx), min(self.miny, o.miny), max(self.maxx, o.maxx), max(self.maxy, o.maxy))


@dataclass
class FootprintGeom:
    box: Box                 # courtyard relative to the footprint origin
    facing: str              # local side that faces outward for edge parts
    pads: int = 0
    pad_pos: dict | None = None   # pad number -> (x, y) relative to the footprint origin
    pad_box: Box | None = None    # extent of the pads (the real body, unlike T-shaped courtyards)


@dataclass
class Placement:
    ref: str
    x: float
    y: float
    rotation: float = 0.0


class PlacementError(RuntimeError):
    pass


def rot(x: float, y: float, ang: float) -> tuple[float, float]:
    """Rotate a point as KiCad rotates footprints (+angle = CCW on a Y-down screen)."""
    a = int(ang) % 360
    return {0: (x, y), 90: (y, -x), 180: (-x, -y), 270: (-y, x)}[a]


def footprint_geom(kicad_mod: Path, fpid: str = "") -> FootprintGeom:
    root = sexpr.parse(kicad_mod.read_text(encoding="utf-8"))
    xs, ys = [], []
    for g in root:
        if not isinstance(g, list) or sexpr.tag(g) not in ("fp_line", "fp_rect", "fp_circle", "fp_poly", "fp_arc"):
            continue
        layer = child(g, "layer")
        if not layer or str(layer[1]) != "F.CrtYd":
            continue
        for k in ("start", "end", "center", "mid"):
            pt = child(g, k)
            if pt:
                xs.append(float(pt[1])); ys.append(float(pt[2]))
        pts = child(g, "pts")
        for xy in children(pts, "xy") if pts else []:
            xs.append(float(xy[1])); ys.append(float(xy[2]))
    pads = [(float(child(p, "at")[1]), float(child(p, "at")[2])) for p in children(root, "pad")]
    if not xs:
        for p in children(root, "pad"):
            at, size = child(p, "at"), child(p, "size")
            x, y, w, h = float(at[1]), float(at[2]), float(size[1]), float(size[2])
            xs += [x - w / 2, x + w / 2]; ys += [y - h / 2, y + h / 2]
    box = Box(min(xs), min(ys), max(xs), max(ys))
    cx = sum(p[0] for p in pads) / len(pads) if pads else 0.0
    cy = sum(p[1] for p in pads) / len(pads) if pads else 0.0
    ext = {"left": cx - box.minx, "right": box.maxx - cx, "top": cy - box.miny, "bottom": box.maxy - cy}
    facing = FACING_OVERRIDES.get(fpid) or next(
        (f for pre, suf, f in FACING_PATTERNS if fpid.startswith(pre) and fpid.endswith(suf)), None) \
        or max(ext, key=ext.get)
    pad_pos, px, py = {}, [], []
    for p in children(root, "pad"):
        at, size = child(p, "at"), child(p, "size")
        num = str(p[1]) if len(p) > 1 else ""
        if at and num and num not in pad_pos:
            pad_pos[num] = (float(at[1]), float(at[2]))
        if at and size:
            x, y, w, h = float(at[1]), float(at[2]), float(size[1]), float(size[2])
            if len(at) > 3 and int(float(at[3])) % 180 == 90:
                w, h = h, w
            px += [x - w / 2, x + w / 2]; py += [y - h / 2, y + h / 2]
    pbox = Box(min(px), min(py), max(px), max(py)) if px else box
    if "USB_C_Receptacle" in fpid:
        # keep the area behind the signal pads free: the D+/D- joins are routed there
        # (see the worker's usbc-fanout), 3.3 mm deep plus clearance
        keep = USB_FANOUT_DEPTH
        if cy < 0:
            box = Box(box.minx, min(box.miny, pbox.miny - keep), box.maxx, box.maxy)
        else:
            box = Box(box.minx, box.miny, box.maxx, max(box.maxy, pbox.maxy + keep))
        # the receptacle's front face must be flush with the board edge or the plug will not
        # seat: shorten the box on the facing side so that, once the placer keeps it
        # EDGE_MARGIN from the edge, the body front (F.Fab) lands on the edge
        fab = _layer_extent(root, "F.Fab")
        if fab and facing in ("top", "bottom", "left", "right"):
            if facing == "bottom":
                box = Box(box.minx, box.miny, box.maxx, fab[3] - EDGE_MARGIN)
            elif facing == "top":
                box = Box(box.minx, fab[1] + EDGE_MARGIN, box.maxx, box.maxy)
            elif facing == "right":
                box = Box(box.minx, box.miny, fab[2] - EDGE_MARGIN, box.maxy)
            else:
                box = Box(fab[0] + EDGE_MARGIN, box.miny, box.maxx, box.maxy)
    return FootprintGeom(box, facing, len(pads), pad_pos, pbox)


def _layer_extent(root, layer: str):
    xs, ys = [], []
    for g in root:
        if not isinstance(g, list) or sexpr.tag(g) not in ("fp_line", "fp_rect", "fp_poly", "fp_arc"):
            continue
        ly = child(g, "layer")
        if not ly or str(ly[1]) != layer:
            continue
        for k in ("start", "end", "mid"):
            pt = child(g, k)
            if pt:
                xs.append(float(pt[1])); ys.append(float(pt[2]))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def courtyard(kicad_mod: Path) -> Box:  # backwards compatible helper
    return footprint_geom(kicad_mod).box


def place(circuit: Circuit, geoms: dict[str, FootprintGeom], *, keep: dict | None = None, **kw) -> list[Placement]:
    """`keep`: {block: {ref: (x, y, rotation)}} - blocks fixed where a previous revision put
    them (incremental edits); everything else is placed around them. If the changed blocks
    no longer fit around the kept ones, the whole board is placed afresh."""
    if keep:
        try:
            return _place(circuit, geoms, keep=keep, **kw)
        except PlacementError:
            DEBUG["keep_dropped"] = True
    out = _place(circuit, geoms, keep=None, **kw)
    if keep:
        DEBUG["keep_dropped"] = True
    return out


def _place(circuit: Circuit, geoms: dict[str, FootprintGeom], *, grid: float = 0.5,
           spacing: float = 1.0, block_gap: float | None = None, fanout_margin: float = 1.0,
           fanout_pads: int = 8, keep: dict | None = None) -> list[Placement]:
    # ICs with many pads get extra room around them for escape routing
    geoms = {r: (FootprintGeom(Box(g.box.minx - fanout_margin, g.box.miny - fanout_margin,
                                   g.box.maxx + fanout_margin, g.box.maxy + fanout_margin), g.facing, g.pads,
                                 g.pad_pos, g.pad_box)
                 if g.pads >= fanout_pads and not circuit_part_is_edge(circuit, r) else g)
             for r, g in geoms.items()}
    W, H = circuit.board.width_mm, circuit.board.height_mm
    edge = circuit.rules.edge_clearance_mm + 0.5
    block_gap = spacing + 0.5 if block_gap is None else block_gap
    comps = {c.ref: c for c in circuit.components}
    fanout: dict[str, int] = {}
    for c in circuit.components:
        for n in set(c.pin_nets.values()):
            fanout[n] = fanout.get(n, 0) + 1
    signal_nets = {c.ref: {n for n in c.pin_nets.values() if fanout[n] <= HIGH_FANOUT} for c in circuit.components}
    # decoupling capacitor -> (chip, chip pad number on the supply net it serves)
    import re as _re
    # Also the parts of a switching regulator's current loops: input capacitors at IN,
    # the inductor at SW, the bootstrap capacitor at BST - placed right at their pins.
    decap: dict[str, tuple[str, str]] = {}
    critical = _re.compile(r"decoupl|\bHF\b|bypass|buck input|buck output|bootstrap|buck inductor|"
                           r"LDO input|LDO output|charger|VM bulk|VM decoupl", _re.I)
    for c in circuit.components:
        if c.ref[0] not in "CL" or not critical.search(c.description):
            continue
        nets = [n for n in c.pin_nets.values() if n != "GND"]
        if not nets:
            continue
        chips = [u for u in circuit.components if u.ref.startswith("U") and set(nets) & set(u.pin_nets.values())]
        if c.ref.startswith("L"):
            nets = [n for n in nets if circuit.net(n).kind.value != "power"] or nets   # SW node first
        elif _re.search(r"output", c.description, _re.I):
            ind = [u for u in circuit.components if u.ref.startswith("L") and u.block == c.block
                   and set(nets) & set(u.pin_nets.values())]
            chips = ind or chips                            # output caps sit at the inductor's output
        same = [u for u in chips if u.block == c.block]
        if not same and not _re.search(r"decoupl|\bHF\b|bypass", c.description, _re.I):
            continue
        same = same or chips
        if same:
            u = same[0]
            cand = [k for k, v in u.pin_nets.items() if v == nets[0]] or \
                [k for k, v in u.pin_nets.items() if v in nets]
            pp = geoms[u.ref].pad_pos or {}
            gnd = [pp[k] for k, v in u.pin_nets.items() if v == "GND" and k in pp]
            if gnd and "GND" in c.pin_nets.values():      # the pin whose ground return is shortest
                cand.sort(key=lambda k: min(((pp[k][0] - g[0]) ** 2 + (pp[k][1] - g[1]) ** 2) for g in gnd)
                          if k in pp else 1e9)
            decap[c.ref] = (u.ref, cand[0])

    # ------------------------------------------------------------ 1. local
    blocks: dict[str, list[str]] = {}
    for c in circuit.components:
        blocks.setdefault(c.block or c.ref, []).append(c.ref)

    variants: dict[str, list[tuple[dict, Box]]] = {}
    block_edge: dict[str, str | None] = {}

    def local_layout(refs, lateral_pref: bool):
        anchor = next((r for r in refs if comps[r].anchor), None) or max(
            refs, key=lambda r: geoms[r].box.w * geoms[r].box.h)
        edge_ref = next((r for r in refs if comps[r].edge), None)
        pos: dict[str, tuple[float, float, float]] = {}
        boxes: dict[str, Box] = {}

        def lbox(r, x, y, a):
            return geoms[r].box.rotated(a).moved(x, y)

        first = edge_ref or anchor
        a0 = (4 - SIDES.index(geoms[first].facing)) % 4 * 90 if edge_ref else 0
        pos[first] = (0.0, 0.0, a0)
        boxes[first] = lbox(first, 0, 0, a0)
        behind = boxes[first].miny + boxes[first].h / 2 if edge_ref else None
        rest = [r for r in refs if r != first]
        while rest:
            # a decoupling cap goes right after its chip, before anything can crowd the pin
            rest.sort(key=lambda r: (not (r in decap and decap[r][0] in pos),
                                     _loop_rank(comps[r]) if (r in decap and decap[r][0] in pos) else 9,
                                     -sum(len(signal_nets[r] & set(comps[p].pin_nets.values())) for p in pos), r))
            r = rest.pop(0)
            target = None
            if r in decap and decap[r][0] in pos:
                u, pin = decap[r]
                ux, uy, ua = pos[u]
                pp = (geoms[u].pad_pos or {}).get(pin)
                if pp is not None:
                    px, py = rot(pp[0], pp[1], ua)
                    target = (ux + px, uy + py)
            if target is not None:
                tx, ty = target
            else:
                nbrs = [pos[p] for p in pos if set(comps[r].pin_nets.values()) & set(comps[p].pin_nets.values())] \
                    or list(pos.values())
                tx = sum(p[0] for p in nbrs) / len(nbrs)
                ty = sum(p[1] for p in nbrs) / len(nbrs)

            is_decap = target is not None
            chip = decap[r][0] if is_decap else None
            if chip is not None:
                cx_, cy_, ca_ = pos[chip]
                pb = geoms[chip].pad_box or geoms[chip].box
                chip_pads = pb.rotated(ca_).moved(cx_, cy_)
            else:
                chip_pads = None

            def ok(x, y, r=r, lateral=lateral_pref, is_decap=is_decap, chip=chip, chip_pads=chip_pads, ang=0):
                b = lbox(r, x, y, ang)
                if behind is not None:
                    # parts stay behind the edge part's centre line; a decoupling cap may sit
                    # beside it at its supply pin (below the top of its pin rows, so never in
                    # an antenna keep-out), but never in front of it
                    if b.miny < (chip_pads.miny if (is_decap and chip == first) else
                                 boxes[first].miny if is_decap else behind):
                        return False
                    # narrow variant: keep the block no wider than its connector (stack in depth)
                    if lateral and not is_decap and (b.minx < boxes[first].minx - 0.25
                                                     or b.maxx > boxes[first].maxx + 0.25):
                        return False
                if chip_pads is not None:
                    # against its own chip only the pads matter (courtyards can be T-shaped)
                    return (not b.overlaps(chip_pads, spacing) and
                            all(not b.overlaps(o, spacing) for k, o in boxes.items() if k != chip))
                return all(not b.overlaps(o, spacing) for o in boxes.values())

            if is_decap:
                # try all four orientations; keep the one that puts the part's pad on the
                # shared net closest to the chip pin (short supply / switching loops)
                u, pin = decap[r]
                net = comps[u].pin_nets.get(pin)
                k = next((k for k, v in comps[r].pin_nets.items() if v == net), None)
                off = (geoms[r].pad_pos or {}).get(k) if k else None
                best = None
                for ang in ((0, 90, 180, 270) if off else ()):
                    ox, oy = rot(off[0], off[1], ang)
                    try:
                        x, y = _ring_search(lambda x, y, ang=ang: ok(x, y, ang=ang), _g(tx - ox, grid),
                                            _g(ty - oy, grid), grid, 80 if edge_ref else 200)
                    except PlacementError:
                        continue
                    d_ = (x + ox - tx) ** 2 + (y + oy - ty) ** 2
                    if best is None or d_ < best[0] - 1e-9:
                        best = (d_, x, y, ang)
                if best is not None:
                    _, x, y, ang = best
                    pos[r] = (x, y, float(ang))
                    boxes[r] = lbox(r, x, y, ang)
                    continue
            try:
                x, y = _ring_search(ok, tx, ty, grid, 80 if edge_ref else 200)
            except PlacementError:
                x, y = _ring_search(lambda x, y, r=r: ok(x, y, r, lateral=False), tx, ty, grid, 200)
            pos[r] = (x, y, 0.0)
            boxes[r] = lbox(r, x, y, 0)
        bb = None
        for b in boxes.values():
            bb = b if bb is None else bb.union(b)
        return pos, bb, (comps[edge_ref].edge if edge_ref else None)

    for bname, refs in blocks.items():
        opts = []
        for lateral in ((True, False) if any(comps[r].edge for r in refs) else (False,)):
            pos, bb, e = local_layout(refs, lateral)
            R = SIDES.index(e) * 90 if e else 0          # rotate so local "up" faces the board edge
            v = ({r: (*rot(x, y, R), (a + R) % 360) for r, (x, y, a) in pos.items()}, bb.rotated(R))
            if not any(abs(o[1].w - v[1].w) < 1e-6 and abs(o[1].h - v[1].h) < 1e-6 for o in opts):
                opts.append(v)
            block_edge[bname] = e
        variants[bname] = opts
    local = {b: v[0][0] for b, v in variants.items()}
    block_box = {b: v[0][1] for b, v in variants.items()}

    DEBUG.clear()
    DEBUG.update(block_box=block_box, block_edge=dict(block_edge), local=local,
                 variants={b: [(round(v[1].w, 1), round(v[1].h, 1)) for v in vs] for b, vs in variants.items()})
    # ------------------------------------------------------------ 2. board
    occupied: list[Box] = []
    for mh in circuit.board.mounting_holes:
        r = 3.5
        occupied.append(Box(mh.x_mm - r, mh.y_mm - r, mh.x_mm + r, mh.y_mm + r))
    origin: dict[str, tuple[float, float]] = {}
    kept: dict[str, dict] = {}
    for bname, parts in (keep or {}).items():
        if bname not in blocks or set(parts) != set(blocks[bname]):
            continue
        box = None
        for r, (x, y, a) in parts.items():
            bb_ = geoms[r].box.rotated(a).moved(x, y)
            box = bb_ if box is None else box.union(bb_)
        if box.minx < edge - 0.01 or box.miny < edge - 0.01 or box.maxx > W - edge + 0.01 or box.maxy > H - edge + 0.01:
            continue
        if any(box.overlaps(o, 0) for o in occupied):
            continue
        kept[bname] = parts
        occupied.append(box)
        origin[bname] = (0.0, 0.0)
        block_box[bname] = box
    DEBUG["kept_blocks"] = sorted(kept)

    def fits(bname, x, y):
        b = block_box[bname].moved(x, y)
        if b.minx < edge or b.miny < edge or b.maxx > W - edge or b.maxy > H - edge:
            return False
        return all(not b.overlaps(o, block_gap) for o in occupied)

    def commit(bname, x, y):
        origin[bname] = (x, y)
        occupied.append(block_box[bname].moved(x, y))

    for side in SIDES:
        group = [b for b in blocks if block_edge[b] == side and b not in kept]
        if not group:
            continue
        horiz = side in ("top", "bottom")
        span = W if horiz else H
        total = sum((block_box[b].w if horiz else block_box[b].h) for b in group) + block_gap * (len(group) - 1)
        cursor = max(edge, (span - total) / 2)
        for bname in group:
            pos = None
            for vlocal, vbox in variants[bname]:          # narrow first, then wide
                block_box[bname], local[bname] = vbox, vlocal
                bb = vbox
                # snap flush coordinate *inward* so grid rounding never violates the edge clearance
                if horiz:
                    tx = _g(cursor - bb.minx, grid)
                    ty = _ceil(edge - bb.miny, grid) if side == "top" else _floor(H - edge - bb.maxy, grid)
                else:
                    ty = _g(cursor - bb.miny, grid)
                    tx = _ceil(edge - bb.minx, grid) if side == "left" else _floor(W - edge - bb.maxx, grid)
                # 1) slide along the edge (stay flush), nearest first
                for k in range(int(span / grid) + 1):
                    for sgn in ((1,) if k == 0 else (1, -1)):
                        x, y = (tx + sgn * k * grid, ty) if horiz else (tx, ty + sgn * k * grid)
                        if fits(bname, x, y):
                            pos = (round(x, 3), round(y, 3))
                            break
                    if pos:
                        break
                if pos:
                    break
            if pos is None:
                block_box[bname], local[bname] = variants[bname][0][1], variants[bname][0][0]
                bb = block_box[bname]
            # 2) otherwise anywhere near the target, recorded as an edge miss
            if pos is None:
                pos = _ring_search(lambda x, y, b=bname: fits(b, x, y), tx, ty, grid, int(max(W, H) / grid),
                                   fail=f"edge block {bname} ({side}, {bb.w:.1f}x{bb.h:.1f} mm) on {W}x{H} mm board")
                DEBUG.setdefault("edge_misses", []).append(bname)
            commit(bname, *pos)
            cursor = (pos[0] + bb.maxx if horiz else pos[1] + bb.maxy) + block_gap

    block_nets = {b: set().union(*(signal_nets[r] for r in refs)) for b, refs in blocks.items()}
    remaining = [b for b in blocks if b not in origin]
    while remaining:
        remaining.sort(key=lambda b: (-sum(len(block_nets[b] & block_nets[p]) for p in origin),
                                      -block_box[b].w * block_box[b].h, b))
        bname = remaining.pop(0)
        nb = [p for p in origin if block_nets[bname] & block_nets[p]]
        if nb:
            tx = sum(origin[p][0] + block_box[p].minx + block_box[p].w / 2 for p in nb) / len(nb)
            ty = sum(origin[p][1] + block_box[p].miny + block_box[p].h / 2 for p in nb) / len(nb)
        else:
            tx, ty = W / 2, H / 2
        bb = block_box[bname]
        tx, ty = tx - (bb.minx + bb.w / 2), ty - (bb.miny + bb.h / 2)
        x, y = _ring_search(lambda x, y, b=bname: fits(b, x, y), _g(tx, grid), _g(ty, grid), grid,
                            int(max(W, H) / grid), fail=f"block {bname}")
        commit(bname, x, y)

    out = []
    for bname, refs in blocks.items():
        if bname in kept:
            out += [Placement(r, *map(float, kept[bname][r])) for r in refs]
            continue
        ox, oy = origin[bname]
        for r in refs:
            x, y, a = local[bname][r]
            out.append(Placement(r, round(ox + x, 3), round(oy + y, 3), a))
    return out


def _loop_rank(c) -> int:
    """Placement order among parts that belong at a chip pin: high-frequency decoupling and
    the regulator's input capacitors first (the hot loop), then inductor, bootstrap, outputs."""
    d = c.description.lower()
    if "hf" in d.split() or "decoupl" in d or "bypass" in d:
        return 0
    if "input" in d:
        return 1
    if "inductor" in d:
        return 2
    if "bootstrap" in d:
        return 3
    return 4


def circuit_part_is_edge(circuit: Circuit, ref: str) -> bool:
    return any(c.ref == ref and c.edge for c in circuit.components)


def _g(v, grid):
    return round(v / grid) * grid


def _ceil(v, grid):
    return math.ceil(round(v / grid, 6)) * grid


def _floor(v, grid):
    return math.floor(round(v / grid, 6)) * grid


def _ring_search(ok, tx, ty, grid, max_rings, fail="part"):
    for ring in range(max_rings + 1):
        best = None
        for i in range(-ring, ring + 1):
            for j in ((-ring, ring) if abs(i) != ring else range(-ring, ring + 1)):
                x, y = round(tx + i * grid, 3), round(ty + j * grid, 3)
                if ok(x, y):
                    d = math.hypot(x - tx, y - ty)
                    if best is None or d < best[0]:
                        best = (d, x, y)
        if best:
            return best[1], best[2]
    raise PlacementError(f"no room for {fail}")
