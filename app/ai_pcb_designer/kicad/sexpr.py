"""Minimal, loss-less S-expression parser/serializer for KiCad files.

Atoms are kept as `Sym` (bare tokens) or `str` (quoted strings) so a round trip
preserves quoting, which KiCad's parser cares about.
"""
from __future__ import annotations

import re
from typing import Union


class Sym(str):
    """A bare (unquoted) token such as `symbol`, `yes`, `1.27`."""


Node = Union[list, Sym, str]

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text: str) -> list:
    stack: list[list] = [[]]
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"S-expression parse error at {pos}: {text[pos:pos+40]!r}")
        pos = m.end()
        lp, rp, q, atom = m.groups()
        if lp:
            stack.append([])
        elif rp:
            node = stack.pop()
            stack[-1].append(node)
        elif q is not None:
            stack[-1].append(q.replace('\\"', '"').replace("\\\\", "\\"))
        elif atom is not None:
            stack[-1].append(Sym(atom))
    if len(stack) != 1:
        raise ValueError("Unbalanced S-expression")
    return stack[0][0] if len(stack[0]) == 1 else stack[0]


def _atom(a) -> str:
    if isinstance(a, Sym):
        return str(a)
    if isinstance(a, bool):
        return "yes" if a else "no"
    if isinstance(a, (int, float)):
        return _num(a)
    s = str(a).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def _num(v: float) -> str:
    if isinstance(v, int):
        return str(v)
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def dumps(node: Node, indent: int = 0) -> str:
    if not isinstance(node, list):
        return _atom(node)
    pad = "  " * indent
    simple = all(not isinstance(c, list) for c in node)
    if simple:
        return "(" + " ".join(_atom(c) for c in node) + ")"
    parts = []
    head = []
    for c in node:
        if isinstance(c, list):
            break
        head.append(_atom(c))
    out = "(" + " ".join(head)
    for c in node[len(head):]:
        parts.append("\n" + pad + "  " + dumps(c, indent + 1))
    return out + "".join(parts) + "\n" + pad + ")"


# ---- query helpers ---------------------------------------------------------
def tag(node) -> str | None:
    return str(node[0]) if isinstance(node, list) and node else None


def children(node: list, name: str) -> list[list]:
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def child(node: list, name: str) -> list | None:
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None
