from __future__ import annotations

from pathlib import Path
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import rosbagel
from typer.testing import CliRunner

from rosbagel.cli.main import (
    DEPENDENCY_PACKAGES,
    PACKAGE_NAMES,
    app,
    uninstall_packages,
)


class CliMetadataTests(unittest.TestCase):
    def test_uninstall_scope_keeps_dependencies_by_default(self) -> None:
        packages = set(uninstall_packages(remove_dependencies=False))

        self.assertEqual(packages, set(PACKAGE_NAMES))
        self.assertNotIn("rosbags", packages)

    def test_uninstall_scope_can_include_all_dependencies(self) -> None:
        packages = set(uninstall_packages(remove_dependencies=True))

        self.assertTrue(set(PACKAGE_NAMES).issubset(packages))
        self.assertTrue(set(DEPENDENCY_PACKAGES).issubset(packages))

    def test_interactive_uninstall_asks_whether_to_remove_dependencies(self) -> None:
        runner = CliRunner()
        with patch(
            "rosbagel.cli.main.subprocess.run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            result = runner.invoke(app, ["uninstall"], input="y\nn\n")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Also remove all ROSBagel dependencies?", result.output)
        command = run.call_args.args[0]
        self.assertTrue(set(PACKAGE_NAMES).issubset(command))
        self.assertFalse(set(DEPENDENCY_PACKAGES).intersection(command))

    def test_interactive_uninstall_can_remove_dependencies(self) -> None:
        runner = CliRunner()
        with patch(
            "rosbagel.cli.main.subprocess.run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            result = runner.invoke(app, ["uninstall"], input="y\ny\n")

        self.assertEqual(result.exit_code, 0, result.output)
        command = run.call_args.args[0]
        self.assertTrue(set(DEPENDENCY_PACKAGES).issubset(command))

    def test_windows_batch_helpers_are_present(self) -> None:
        root = Path(__file__).resolve().parents[1]

        self.assertTrue((root / "install.bat").exists())
        self.assertTrue((root / "uninstall.bat").exists())
        self.assertTrue((root / "bagel.bat").exists())
        self.assertFalse((root / "ros2unbag.bat").exists())

    def test_package_version_matches_project_metadata(self) -> None:
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(rosbagel.__version__, metadata["project"]["version"])
        for filename in ("README.md", "README.zh-CN.md"):
            self.assertIn(f"/tree/v{rosbagel.__version__})", (root / filename).read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["name"], "ROSBagel")
        self.assertEqual(metadata["project"]["authors"], [{"name": "Owen ZI-WEN ZHOU"}])
        self.assertEqual(metadata["project"]["maintainers"], [{"name": "Owen ZI-WEN ZHOU"}])
        self.assertEqual(metadata["project"]["license"], {"text": "AGPL-3.0-or-later"})
        self.assertIn("Copyright (C) 2026 Owen ZI-WEN ZHOU", (root / "LICENSE").read_text())
        self.assertEqual(
            metadata["project"]["scripts"],
            {"bagel": "rosbagel.cli.main:app"},
        )


if __name__ == "__main__":
    unittest.main()
