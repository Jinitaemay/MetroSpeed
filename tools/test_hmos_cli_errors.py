#!/usr/bin/env python3
import importlib
import importlib.util
import io
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
ELF_SCRIPT = REPO_ROOT / "tools" / "hmos_elf_calls.py"
IMAGE_SCRIPT = REPO_ROOT / "tools" / "hmos_image_inspect.py"


def dependency_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError):
        return False


def import_image_inspector():
    module_name = (
        f"{__package__}.hmos_image_inspect"
        if __package__
        else "hmos_image_inspect"
    )
    return importlib.import_module(module_name)


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

    @unittest.skipUnless(
        dependency_available("dissect.extfs"),
        "optional ExtFS analyst dependency is not installed",
    )
    def test_force_extract_preserves_existing_target_when_copy_fails(self) -> None:
        image_inspector = import_image_inspector()

        class FailingStream(io.BytesIO):
            def __init__(self, payload: bytes) -> None:
                super().__init__(payload)
                self.read_count = 0

            def read(self, size: int = -1) -> bytes:
                if self.read_count > 0:
                    raise OSError("injected image read failure")
                self.read_count += 1
                return super().read(4 if size < 0 else min(size, 4))

        class FakeNode:
            filetype = stat.S_IFREG

            def open(self) -> FailingStream:
                return FailingStream(b"replacement payload")

        class FakeFilesystem:
            def get(self, _path: str) -> FakeNode:
                return FakeNode()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "existing.bin"
            original = b"original payload"
            output.write_bytes(original)
            args = SimpleNamespace(
                path="/source.bin",
                output=str(output),
                force=True,
            )

            with self.assertRaisesRegex(OSError, "injected image read failure"):
                image_inspector.command_extract(FakeFilesystem(), args)

            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(list(output.parent.glob(f".{output.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
