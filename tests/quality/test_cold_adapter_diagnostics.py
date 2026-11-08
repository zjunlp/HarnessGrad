"""First-use diagnostics through real stdlib-only registry subprocesses.

The child interpreter disables site-packages; fixtures contain no benchmark data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ColdAdapterDiagnostics(unittest.TestCase):
    def probe(self, home, name, operation, *, configured=True):
        env = {"PATH": os.defpath, "HOME": str(home), "HG_REVIEWED_ENVIRONMENT_ONLY": "1"}
        if configured:
            env["HG_DATA_ROOT"] = str(home / "datasets")
        source = f"""
import sys,json
sys.path.insert(0, {str(ROOT)!r})
from data.scaffolds import registry
try:
    getattr(registry, {operation!r})({name!r})
except Exception as exc:
    print(json.dumps({{'type':type(exc).__name__, 'error':str(exc)}}))
else:
    raise AssertionError('unprepared adapter unexpectedly succeeded')
"""
        child = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", source],
                               env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(child.returncode, 0, child.stderr)
        return json.loads(child.stdout)

    def test_missing_data_names_executable_preparation(self):
        with tempfile.TemporaryDirectory(prefix="hg-cold-diagnostics-") as tmp:
            home = Path(tmp)
            for name in ("ds1000", "livecodebench"):
                for operation in ("load", "provision_status"):
                    with self.subTest(name=name, operation=operation):
                        result = self.probe(home, name, operation)
                        self.assertEqual(result["type"], "FileNotFoundError")
                        self.assertIn("data_missing:", result["error"])
                        self.assertIn(f"tools/datasets.py --fetch --dataset {name}", result["error"])
                        if name == "livecodebench":
                            self.assertIn("data/datasets/livecodebench/prepare.py", result["error"])

    def test_absent_storage_configuration_stays_distinct(self):
        with tempfile.TemporaryDirectory(prefix="hg-cold-config-") as tmp:
            for name in ("ds1000", "livecodebench"):
                with self.subTest(name=name):
                    result = self.probe(Path(tmp), name, "load", configured=False)
                    self.assertEqual(result["type"], "DataRootNotConfigured")
                    self.assertIn("HG_DATA_ROOT", result["error"])
                    self.assertNotIn("data_missing", result["error"])

    def test_corrupt_data_is_not_reported_as_missing(self):
        with tempfile.TemporaryDirectory(prefix="hg-cold-corrupt-") as tmp:
            home = Path(tmp)
            for name, relative in (("ds1000", "_src/test.jsonl"), ("livecodebench", "pool.jsonl")):
                path = home / "datasets" / name / relative
                path.parent.mkdir(parents=True)
                path.write_text("not valid json\n", encoding="utf-8")
                with self.subTest(name=name):
                    result = self.probe(home, name, "load")
                    self.assertEqual(result["type"], "JSONDecodeError")
                    self.assertNotIn("data_missing", result["error"])


if __name__ == "__main__":
    unittest.main()
