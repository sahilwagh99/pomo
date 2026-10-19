"""One GUI application for the whole test run (painting Pomo needs fonts, so not a bare QCoreApplication)."""
from PySide6.QtGui import QGuiApplication

_app = QGuiApplication.instance() or QGuiApplication([])
