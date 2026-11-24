"""Read back connectivity from a schematic via kicad-cli and compare to the IR.

This is the ground truth for "did the schematic come out right": KiCad itself
computes the nets from the file we wrote.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import sexpr
from ..cli.runner import run
from ..sexpr import child, children


@dataclass
class Netlist:
    nets: dict[str, set[tuple[str, str]]]          # kicad net name -> {(ref, pin)}
    components: dict[str, dict[str, str]]          # ref -> {value, footprint, path}


@dataclass
class ConnectivityReport:
    ok: bool
    net_name_map: dict[str, str] = field(default_factory=dict)   # IR name -> KiCad name
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)


def export_netlist(cli: str, sch: Path, out: Path) -> Netlist:
    run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(sch)])
    root = sexpr.parse(out.read_text(encoding="utf-8"))
    comps = {}
    for comp in children(child(root, "components"), "comp"):
        ref = str(child(comp, "ref")[1])
        fp = child(comp, "footprint")
        sheetpath = child(comp, "sheetpath")
        tstamps = child(comp, "tstamps")
        comps[ref] = {"value": str(child(comp, "value")[1]),
                      "footprint": str(fp[1]) if fp else "",
                      "tstamp": str(tstamps[1]) if tstamps else ""}
    nets = {}
    for n in children(child(root, "nets"), "net"):
        name = str(child(n, "name")[1])
        nodes = {(str(child(nd, "ref")[1]), str(child(nd, "pin")[1])) for nd in children(n, "node")}
        nets[name] = nodes
    return Netlist(nets, comps)


def compare(ir_connections: dict[str, set[tuple[str, str]]], netlist: Netlist) -> ConnectivityReport:
    rep = ConnectivityReport(ok=True)
    by_nodes = {frozenset(v): k for k, v in netlist.nets.items()
                if not k.startswith("unconnected-")}
    for ir_name, nodes in ir_connections.items():
        if not nodes:
            continue
        kname = by_nodes.get(frozenset(nodes))
        if kname is None:
            rep.ok = False
            # find which KiCad nets contain these nodes for a useful message
            hits = {k for k, v in netlist.nets.items() if v & nodes}
            rep.missing.append(f"IR net {ir_name} {sorted(nodes)} not found; overlaps {sorted(hits)}")
        else:
            rep.net_name_map[ir_name] = kname
    used = set(rep.net_name_map.values())
    for k, v in netlist.nets.items():
        if k not in used and not k.startswith("unconnected-") and len(v) > 1:
            rep.ok = False
            rep.extra.append(f"KiCad net {k} {sorted(v)} has no IR counterpart")
    return rep
