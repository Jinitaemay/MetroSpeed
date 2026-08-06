#!/usr/bin/env python3
"""Read-only helper for inspecting HarmonyOS ext4 system images.

The script deliberately has no write operation against the image itself.  The
only command that writes is ``extract``, and it writes one explicitly selected
file to an explicitly selected host path.

Dependency:
    python -m pip install dissect.extfs
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

try:
    from dissect.extfs.exceptions import Error as ExtFSError
    from dissect.extfs.extfs import ExtFS
except ImportError as exc:  # pragma: no cover - depends on analyst environment
    raise SystemExit(
        "Missing dependency 'dissect.extfs'. Install it with:\n"
        "  python -m pip install dissect.extfs"
    ) from exc


def normalize_image_path(value: str) -> str:
    """Return an absolute POSIX path without allowing an empty path."""
    path = PurePosixPath("/" + value.lstrip("/"))
    return path.as_posix()


def display_name(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return str(value)


def is_directory(node: object) -> bool:
    return stat.S_ISDIR(int(getattr(node, "filetype")))


def walk(node: object, current_path: PurePosixPath) -> Iterator[tuple[str, object]]:
    """Yield descendants in stable path order."""
    children = [
        child
        for child in node.iterdir()
        if display_name(child.filename) not in {".", ".."}
    ]
    children.sort(key=lambda item: display_name(item.filename))
    for child in children:
        child_path = current_path / display_name(child.filename)
        yield child_path.as_posix(), child
        if is_directory(child):
            yield from walk(child, child_path)


def sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest().upper()


def find_stream(
    stream: BinaryIO,
    needle: bytes,
    ignore_case: bool,
) -> int | None:
    """Return the first byte offset of ``needle`` without loading the file."""
    overlap = max(0, len(needle) - 1)
    previous = b""
    consumed = 0
    comparable_needle = needle.lower() if ignore_case else needle
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        data = previous + block
        comparable_data = data.lower() if ignore_case else data
        position = comparable_data.find(comparable_needle)
        if position >= 0:
            return consumed - len(previous) + position
        previous = data[-overlap:] if overlap else b""
        consumed += len(block)
    return None


def command_list(fs: ExtFS, args: argparse.Namespace) -> int:
    image_path = normalize_image_path(args.path)
    root = fs.get(image_path)
    if not is_directory(root):
        print(f"{root.size:12d}  FILE  {image_path}")
        return 0

    if args.recursive:
        entries = walk(root, PurePosixPath(image_path))
    else:
        entries = (
            (
                (PurePosixPath(image_path) / display_name(child.filename)).as_posix(),
                child,
            )
            for child in root.iterdir()
            if display_name(child.filename) not in {".", ".."}
        )

    match = args.match.casefold() if args.match else None
    rows = []
    for path, node in entries:
        if match is not None and match not in path.casefold():
            continue
        kind = "DIR " if is_directory(node) else "FILE"
        rows.append((path, int(node.size), kind))

    for path, size, kind in sorted(rows):
        print(f"{size:12d}  {kind}  {path}")
    return 0


def command_cat(fs: ExtFS, args: argparse.Namespace) -> int:
    image_path = normalize_image_path(args.path)
    node = fs.get(image_path)
    if is_directory(node):
        raise IsADirectoryError(image_path)
    data = node.open().read()
    sys.stdout.write(data.decode(args.encoding, errors=args.errors))
    return 0


def command_extract(fs: ExtFS, args: argparse.Namespace) -> int:
    image_path = normalize_image_path(args.path)
    node = fs.get(image_path)
    if is_directory(node):
        raise IsADirectoryError(image_path)

    output = Path(args.output).expanduser().resolve()
    if output.exists() and not args.force:
        raise FileExistsError(f"{output} already exists; pass --force to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as destination:
            fd = -1
            with node.open() as source:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        with temp_path.open("rb") as extracted:
            digest = sha256_stream(extracted)
        os.replace(temp_path, output)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    print(f"{output}\nsize={output.stat().st_size}\nsha256={digest}")
    return 0


def command_search_content(fs: ExtFS, args: argparse.Namespace) -> int:
    image_path = normalize_image_path(args.path)
    root = fs.get(image_path)
    needle = args.text.encode(args.encoding)
    if not needle:
        raise ValueError("search text must not be empty")

    if is_directory(root):
        entries: Iterator[tuple[str, object]] = walk(
            root, PurePosixPath(image_path)
        )
    else:
        entries = iter(((image_path, root),))

    name_match = args.name_match.casefold() if args.name_match else None
    hits = 0
    for path, node in entries:
        if is_directory(node):
            continue
        if name_match is not None and name_match not in path.casefold():
            continue
        with node.open() as stream:
            offset = find_stream(stream, needle, args.ignore_case)
        if offset is None:
            continue
        print(f"{offset:12d}  {int(node.size):12d}  {path}")
        hits += 1
        if args.max_results is not None and hits >= args.max_results:
            break
    return 0 if hits else 1


def command_search_archives(fs: ExtFS, args: argparse.Namespace) -> int:
    """Search decompressed entries inside HAP/HSP/HAR/ZIP files in an image."""
    image_path = normalize_image_path(args.path)
    root = fs.get(image_path)
    needle = args.text.encode(args.encoding)
    if not needle:
        raise ValueError("search text must not be empty")

    if is_directory(root):
        entries: Iterator[tuple[str, object]] = walk(
            root, PurePosixPath(image_path)
        )
    else:
        entries = iter(((image_path, root),))

    archive_match = args.archive_match.casefold() if args.archive_match else None
    entry_match = args.entry_match.casefold() if args.entry_match else None
    archive_suffixes = (".hap", ".hsp", ".har", ".zip")
    scanned_archives = 0
    scanned_entries = 0
    unreadable_archives = 0
    unreadable_entries = 0
    hits = 0
    stop = False
    stopped_early = False

    for archive_path, node in entries:
        if is_directory(node):
            continue
        if not archive_path.casefold().endswith(archive_suffixes):
            continue
        if archive_match is not None and archive_match not in archive_path.casefold():
            continue
        scanned_archives += 1
        try:
            with node.open() as archive_stream:
                with zipfile.ZipFile(archive_stream) as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        if (
                            entry_match is not None
                            and entry_match not in info.filename.casefold()
                        ):
                            continue
                        scanned_entries += 1
                        try:
                            with archive.open(info) as entry_stream:
                                offset = find_stream(
                                    entry_stream,
                                    needle,
                                    args.ignore_case,
                                )
                        except (
                            EOFError,
                            NotImplementedError,
                            OSError,
                            RuntimeError,
                            zipfile.BadZipFile,
                            zipfile.LargeZipFile,
                        ) as exc:
                            unreadable_entries += 1
                            print(
                                "warning: cannot inspect "
                                f"{archive_path}!{info.filename}: {exc}",
                                file=sys.stderr,
                            )
                            continue
                        if offset is None:
                            continue
                        print(
                            f"{offset:12d}  {int(node.size):12d}  "
                            f"{int(info.file_size):12d}  "
                            f"{archive_path}!{info.filename}"
                        )
                        hits += 1
                        if args.max_results is not None and hits >= args.max_results:
                            stop = True
                            stopped_early = True
                            break
        except (
            EOFError,
            NotImplementedError,
            OSError,
            RuntimeError,
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
        ) as exc:
            unreadable_archives += 1
            print(
                f"warning: cannot inspect {archive_path}: {exc}",
                file=sys.stderr,
            )
        if stop:
            break

    unreadable = unreadable_archives + unreadable_entries
    complete = unreadable == 0 and not stopped_early
    print(
        "archive_search_summary "
        f"archives={scanned_archives} entries={scanned_entries} "
        f"unreadable={unreadable} "
        f"unreadable_archives={unreadable_archives} "
        f"unreadable_entries={unreadable_entries} hits={hits} "
        f"stopped_early={int(stopped_early)} complete={int(complete)}",
        file=sys.stderr,
    )
    if unreadable:
        return 2
    return 0 if hits else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect an ext4 HarmonyOS image without mounting it."
    )
    parser.add_argument("image", help="Path to system.img, vendor.img, or another ext4 image")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List a directory or find paths")
    list_parser.add_argument("path", nargs="?", default="/")
    list_parser.add_argument("-r", "--recursive", action="store_true")
    list_parser.add_argument(
        "--match",
        help="Case-insensitive substring filter; use with -r as a simple image-wide search",
    )
    list_parser.set_defaults(handler=command_list)

    cat_parser = subparsers.add_parser("cat", help="Print one text file")
    cat_parser.add_argument("path")
    cat_parser.add_argument("--encoding", default="utf-8")
    cat_parser.add_argument(
        "--errors",
        choices=("strict", "replace", "ignore"),
        default="replace",
    )
    cat_parser.set_defaults(handler=command_cat)

    extract_parser = subparsers.add_parser("extract", help="Extract one file")
    extract_parser.add_argument("path")
    extract_parser.add_argument("output")
    extract_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the host output file if it already exists",
    )
    extract_parser.set_defaults(handler=command_extract)

    search_parser = subparsers.add_parser(
        "search-content",
        help="Find files containing a literal byte string",
    )
    search_parser.add_argument("text")
    search_parser.add_argument("path", nargs="?", default="/")
    search_parser.add_argument("--encoding", default="utf-8")
    search_parser.add_argument(
        "-i",
        "--ignore-case",
        action="store_true",
        help="Use byte-wise ASCII-compatible case folding",
    )
    search_parser.add_argument(
        "--name-match",
        help="Only scan image paths containing this case-insensitive substring",
    )
    search_parser.add_argument(
        "--max-results",
        type=int,
        help="Stop after this many matching files",
    )
    search_parser.set_defaults(handler=command_search_content)

    archive_search_parser = subparsers.add_parser(
        "search-archives",
        help="Search decompressed entries inside HAP/HSP/HAR/ZIP files",
    )
    archive_search_parser.add_argument("text")
    archive_search_parser.add_argument("path", nargs="?", default="/")
    archive_search_parser.add_argument("--encoding", default="utf-8")
    archive_search_parser.add_argument(
        "-i",
        "--ignore-case",
        action="store_true",
        help="Use byte-wise ASCII-compatible case folding",
    )
    archive_search_parser.add_argument(
        "--archive-match",
        help="Only scan archive paths containing this case-insensitive substring",
    )
    archive_search_parser.add_argument(
        "--entry-match",
        help="Only scan archive entries containing this case-insensitive substring",
    )
    archive_search_parser.add_argument(
        "--max-results",
        type=int,
        help="Stop after this many matching entries",
    )
    archive_search_parser.set_defaults(handler=command_search_archives)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "encoding"):
        try:
            codecs.lookup(args.encoding)
        except LookupError:
            parser.error(f"unknown text encoding: {args.encoding}")
    image = Path(args.image).expanduser().resolve()
    if not image.is_file():
        parser.error(f"image does not exist or is not a file: {image}")
    if getattr(args, "max_results", None) is not None and args.max_results <= 0:
        parser.error("--max-results must be positive")

    try:
        with image.open("rb") as image_stream:
            filesystem = ExtFS(image_stream)
            return int(args.handler(filesystem, args))
    except EOFError:
        print(
            "error: image is truncated or too small to contain an ExtFS filesystem",
            file=sys.stderr,
        )
        return 2
    except (
        ExtFSError,
        FileExistsError,
        IsADirectoryError,
        KeyError,
        OSError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
