"""Desktop application entry point:  python -m ai_pcb_designer.app.main"""
from __future__ import annotations

import logging
import os
import sys


def main(argv=None) -> int:
    from ..core.branding import Branding
    from ..core.config import Settings
    from ..core.logging_setup import setup_logging

    logdir = setup_logging()
    log = logging.getLogger("app")
    settings = Settings.load()
    from ..ui import motion
    motion.set_enabled(not getattr(settings.ui, "reduce_motion", False))
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if settings.ui.software_3d or os.environ.get("AIPCB_SOFTWARE_GL") == "1":
        # Chromium renders WebGL with SwiftShader (CPU); GPU compositing stays off so Qt can present
        # the page (with ANGLE/SwiftShader compositing, Qt shows a blank view on GPU-less X servers).
        flags += " --disable-gpu-compositing --enable-unsafe-swiftshader --ignore-gpu-blocklist"
    if os.environ.get("AIPCB_NO_SANDBOX") == "1" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        flags += " --no-sandbox"           # Chromium refuses to sandbox as root (CI containers)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags.strip()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    from ..ui.main_window import MainWindow
    from ..ui.theme import stylesheet

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    if sys.platform == "win32":
        # own taskbar identity, so Windows shows the Gerber Bear icon instead of python.exe's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GerberBear.Designer")
        except Exception:
            pass
    app = QApplication(argv or sys.argv)
    branding = Branding.load()
    app.setApplicationName(branding.name)
    app.setOrganizationName("AIPCBDesigner")
    from ..core.config import package_root
    for f in sorted((package_root() / "resources" / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))           # bundled Inter + Roboto Mono (OFL)
    from ..ui.icons import app_icon
    app.setWindowIcon(app_icon())
    fams = set(QFontDatabase.families())
    ui_font = branding.fonts["ui"] if branding.fonts["ui"] in fams else app.font().family()
    branding.fonts["ui"] = ui_font
    if branding.fonts["mono"] not in fams:
        branding.fonts["mono"] = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"
    app.setFont(QFont(ui_font, 10))
    from PySide6.QtGui import QColor, QPalette
    c = branding.colors
    pal = QPalette()
    for role, col in ((QPalette.ColorRole.Window, c["bg"]), (QPalette.ColorRole.Base, c["bg2"]),
                      (QPalette.ColorRole.AlternateBase, c["surface"]), (QPalette.ColorRole.Text, c["text"]),
                      (QPalette.ColorRole.WindowText, c["text"]), (QPalette.ColorRole.Button, c["surface2"]),
                      (QPalette.ColorRole.ButtonText, c["text"]), (QPalette.ColorRole.Highlight, "#0E7490"),
                      (QPalette.ColorRole.HighlightedText, "#FFFFFF"), (QPalette.ColorRole.ToolTipBase, c["surface2"]),
                      (QPalette.ColorRole.ToolTipText, c["text"]), (QPalette.ColorRole.PlaceholderText, c["muted"]),
                      (QPalette.ColorRole.Link, c["accent"])):
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#55627A"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#55627A"))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(branding))
    log.info("starting %s, logs in %s", branding.name, logdir)
    w = MainWindow(settings, branding)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
