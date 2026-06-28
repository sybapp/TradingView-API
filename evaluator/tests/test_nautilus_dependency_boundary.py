import importlib.util
import unittest
from pathlib import Path


class NautilusDependencyBoundaryTest(unittest.TestCase):
    def test_nautilus_trader_import_does_not_resolve_from_reference_submodule(self):
        spec = importlib.util.find_spec("nautilus_trader")
        if spec is None or spec.origin is None:
            self.skipTest("nautilus_trader is not installed")

        module_path = Path(spec.origin).resolve()
        repo_root = Path(__file__).resolve().parents[2]
        reference_checkout = repo_root / "third_party" / "nautilus_trader"

        self.assertNotEqual(module_path, reference_checkout)
        self.assertNotIn(reference_checkout, module_path.parents)


if __name__ == "__main__":
    unittest.main()
