from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from standalone_assistant.core.paths import ensure_runtime_dirs
from standalone_assistant.core.single_instance import acquire_single_instance
from standalone_assistant.core.storage import Storage
from standalone_assistant.ui.main_window import MainWindow


def main() -> int:
    instance_lock = acquire_single_instance("ESEO_Noor_Assistant")
    if instance_lock is None:
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName("ESEO Standalone Assistant")
    app.setOrganizationName("ESEO")
    ensure_runtime_dirs()
    app.noor_instance_lock = instance_lock  # type: ignore[attr-defined]
    app.aboutToQuit.connect(instance_lock.release)

    storage = Storage()
    storage.initialize()

    window = MainWindow(storage)
    window.resize(1320, 820)
    window.show()
    return app.exec()
