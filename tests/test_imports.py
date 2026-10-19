import importlib
import pkgutil
import unittest

import pomodoro


class ImportTests(unittest.TestCase):
    def test_every_module_imports(self):
        """Catches syntax and import errors in modules no other test touches (windows, dialogs)."""
        for module in pkgutil.iter_modules(pomodoro.__path__):
            with self.subTest(module=module.name):
                importlib.import_module(f"pomodoro.{module.name}")


if __name__ == "__main__":
    unittest.main()
