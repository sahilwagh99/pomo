"""One application for the whole test run (painting Pomo needs fonts, and some tests build windows)."""
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])
