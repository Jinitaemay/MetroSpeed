#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
ELF_SCRIPT = REPO_ROOT / "tools" / "hmos_elf_calls.py"
IMAGE_SCRIPT = REPO_ROOT / "tools" / "hmos_image_inspect.py"


def dependency_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError):
        return False


class HmosCliErrorTests(unittest.TestCase):
    @unittest.skipUnless(
        dependency_available("capstone") and dependency_available("elftools"),
        "optional ELF analyst dependencies are not installed",
    )
    def test_invalid_elf_is_reported_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_elf = Path(temp_dir) / "invalid.elf"
            invalid_elf.write_bytes(b"not an ELF file")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ELF_SCRIPT),
                    "list",
                    str(invalid_elf),
                    str(invalid_elf),
                    "target",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    @unittest.skipUnless(
        dependency_available("dissect.extfs"),
        "optional ExtFS analyst dependency is not installed",
    )
    def test_invalid_extfs_image_is_reported_without_traceback(self) -> None:
        for name, payload in (
            ("truncated.img", b"not an ExtFS image"),
            ("wrong-magic.img", bytes(4096)),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                invalid_image = Path(temp_dir) / name
                invalid_image.write_bytes(payload)
                result = subprocess.run(
                    [
                        sys.executable,
                        str(IMAGE_SCRIPT),
                        str(invalid_image),
                        "list",
                        "/",
                    ],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )

            self.assertEqual(result.returncode, 2)
            self.assertIn("error:", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
