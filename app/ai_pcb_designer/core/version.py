"""Version and project links. The GitHub repository ("owner/name") is written into
resources/repo.txt by the installer build (from the git remote), so forks point at
their own Issues page without code changes."""
from __future__ import annotations

from pathlib import Path

__version__ = "0.9.0"
LICENSE = "GPL-3.0-or-later"


def repo() -> str | None:
    p = Path(__file__).resolve().parent.parent / "resources" / "repo.txt"
    try:
        r = p.read_text(encoding="utf-8").strip()
        return r if r.count("/") == 1 else None
    except OSError:
        return None


def links() -> dict[str, str | None]:
    r = repo()
    base = f"https://github.com/{r}" if r else None
    return {"home": base, "issues": f"{base}/issues" if base else None,
            "new_issue": f"{base}/issues/new/choose" if base else None,
            "releases": f"{base}/releases" if base else None,
            "api_latest": f"https://api.github.com/repos/{r}/releases/latest" if r else None}


def _parse(v: str) -> tuple:
    v = v.strip().lstrip("vV").split("-")[0]
    out = []
    for x in v.split("."):
        out.append(int(x) if x.isdigit() else 0)
    return tuple(out + [0] * (3 - len(out)))


def newer(latest: str, current: str = __version__) -> bool:
    try:
        return _parse(latest) > _parse(current)
    except Exception:
        return False


def latest_release(timeout: float = 6.0) -> dict | None:
    """{'version': '1.0.0', 'url': ...} if GitHub has a newer release, else None. Sends no
    data except the request itself; any failure is silent."""
    url = links()["api_latest"]
    if not url:
        return None
    try:
        import httpx
        r = httpx.get(url, timeout=timeout, headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        d = r.json()
        tag = str(d.get("tag_name") or "")
        if tag and newer(tag):
            return {"version": tag.lstrip("vV"), "url": d.get("html_url") or links()["releases"]}
    except Exception:
        return None
    return None
