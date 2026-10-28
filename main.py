import os
import sys


def _prepare_frozen_qt() -> None:
    """Prefer the bundled Qt runtime when running the packaged desktop app."""
    if not getattr(sys, "frozen", False):
        return
    bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    qt_directory = os.path.join(bundle_root, "PySide6")
    for directory in (qt_directory, bundle_root):
        if not os.path.isdir(directory):
            continue
        try:
            os.add_dll_directory(directory)
        except (AttributeError, OSError):
            pass
        os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")


_prepare_frozen_qt()

from dorklink.gui import main


if __name__ == "__main__":
    main()
