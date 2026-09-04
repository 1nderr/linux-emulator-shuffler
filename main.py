"""Entry point for the Linux Emulator Shuffler."""

import logging
import os
import sys

from PySide6.QtWidgets import QApplication

from shuffler.ui import ShufflerWindow


def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("SHUFFLER_DEBUG") else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Linux Emulator Shuffler")
    # Lets the compositor match the window to the installed .desktop entry, so
    # the taskbar and alt-tab show the app's icon and name rather than a default.
    app.setDesktopFileName("linux-emulator-shuffler")

    window = ShufflerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
