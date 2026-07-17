from __future__ import annotations

import os
import unittest

from comfy_setup.package_sources import (
    PYPI_SIMPLE,
    PackageSourceError,
    clean_package_environment,
    validate_github_repository,
    validate_package_index,
    validate_requirement_line,
)


class PackageSourcePolicyTests(unittest.TestCase):
    def test_official_sources_are_allowed(self) -> None:
        self.assertEqual(validate_package_index(PYPI_SIMPLE), PYPI_SIMPLE)
        self.assertEqual(
            validate_package_index("https://download.pytorch.org/whl/cu130"),
            "https://download.pytorch.org/whl/cu130",
        )
        self.assertEqual(
            validate_github_repository("https://github.com/Comfy-Org/ComfyUI.git"),
            "https://github.com/Comfy-Org/ComfyUI.git",
        )

    def test_private_or_non_github_sources_are_rejected(self) -> None:
        bad = [
            "https://packages.example.internal/simple",
            "https://mirror." + "open" + "ai.org/simple",
            "https://example.invalid/simple",
            "http://github.com/example/project.git",
        ]
        for url in bad:
            with self.subTest(url=url):
                with self.assertRaises(PackageSourceError):
                    if url.endswith(".git"):
                        validate_github_repository(url)
                    else:
                        validate_package_index(url)

    def test_requirement_files_cannot_override_indexes(self) -> None:
        with self.assertRaises(PackageSourceError):
            validate_requirement_line("--index-url https://example.invalid/simple")
        with self.assertRaises(PackageSourceError):
            validate_requirement_line("thing @ https://example.invalid/thing.whl")
        validate_requirement_line("thing @ git+https://github.com/example/thing.git")

    def test_environment_discards_inherited_indexes(self) -> None:
        previous = dict(os.environ)
        try:
            os.environ["PIP_INDEX_URL"] = "https://private.invalid/simple"
            os.environ["UV_INDEX"] = "https://private.invalid/simple"
            env = clean_package_environment()
            self.assertEqual(env["PIP_INDEX_URL"], PYPI_SIMPLE)
            self.assertEqual(env["UV_DEFAULT_INDEX"], PYPI_SIMPLE)
            self.assertNotIn("UV_INDEX", env)
        finally:
            os.environ.clear()
            os.environ.update(previous)


if __name__ == "__main__":
    unittest.main()
