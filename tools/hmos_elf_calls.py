#!/usr/bin/env python3
"""Recover direct function calls from a stripped HarmonyOS ELF.

Many HarmonyOS system libraries retain a compressed ``.gnu_debugdata`` section.
After that section is decompressed into a mini-debug ELF, this helper combines
its local function symbols with the executable bytes in the original library.
It supports AArch64 and x86-64 and never modifies either input.

Dependencies:
    python -m pip install pyelftools capstone

Optional demangling (works when the host package can find its C++ ABI library):
    python -m pip install cxxfilt

Example:
    python tools/hmos_elf_calls.py calls library.so library.debug \
        Higeo3dvdrProcess --depth 2
"""

from __future__ import annotations

import argparse
import bisect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from capstone import (
        CS_ARCH_ARM64,
        CS_ARCH_X86,
        CS_MODE_ARM,
        CS_MODE_LITTLE_ENDIAN,
        CS_MODE_64,
        Cs,
    )
    from capstone.arm64 import ARM64_INS_BL, ARM64_OP_IMM
    from capstone.x86 import X86_INS_CALL, X86_OP_IMM
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
except ImportError as exc:  # pragma: no cover - analyst environment dependency
    raise SystemExit(
        "Missing dependency. Install it with:\n"
        "  python -m pip install pyelftools capstone"
    ) from exc

try:
    import cxxfilt
except ImportError:  # pragma: no cover - demangling is optional
    cxxfilt = None


@dataclass(frozen=True)
class Function:
    address: int
    size: int
    mangled: str
    name: str

    @property
    def end(self) -> int:
        return self.address + self.size


def demangle(name: str) -> str:
    global cxxfilt
    if cxxfilt is None:
        return name
    try:
        return str(cxxfilt.demangle(name))
    except Exception:
        # The Python package depends on a host ``libc`` and can be importable
        # yet unusable on Windows.  Mangled names remain searchable, so fail
        # soft and disable further attempts for this process.
        cxxfilt = None
        return name


def read_functions(path: Path) -> list[Function]:
    functions: dict[tuple[int, str], Function] = {}
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        for section_name in (".symtab", ".dynsym"):
            section = elf.get_section_by_name(section_name)
            # Mini-debug ELFs often retain a NOBITS placeholder named
            # ``.dynsym``.  pyelftools exposes that as a generic Section.
            if section is None or not hasattr(section, "iter_symbols"):
                continue
            for symbol in section.iter_symbols():
                info = symbol["st_info"]
                address = int(symbol["st_value"])
                if (
                    info["type"] != "STT_FUNC"
                    or symbol["st_shndx"] == "SHN_UNDEF"
                    or address == 0
                    or not symbol.name
                ):
                    continue
                item = Function(
                    address=address,
                    size=int(symbol["st_size"]),
                    mangled=symbol.name,
                    name=demangle(symbol.name),
                )
                functions[(item.address, item.mangled)] = item
    return sorted(functions.values(), key=lambda item: (item.address, item.name))


def merge_functions(*groups: Iterable[Function]) -> list[Function]:
    merged: dict[tuple[int, str], Function] = {}
    for group in groups:
        for item in group:
            key = (item.address, item.mangled)
            previous = merged.get(key)
            if previous is None or item.size > previous.size:
                merged[key] = item
    return sorted(merged.values(), key=lambda item: (item.address, item.name))


def choose_symbol(items: list[Function]) -> Function:
    """Prefer a sized, demangled, non-compiler-generated alias."""
    return sorted(
        items,
        key=lambda item: (
            item.size == 0,
            item.name.startswith((".", "$")),
            len(item.name),
            item.name,
        ),
    )[0]


class SymbolIndex:
    def __init__(self, functions: list[Function]) -> None:
        self.functions = functions
        self.by_address: dict[int, list[Function]] = {}
        for item in functions:
            self.by_address.setdefault(item.address, []).append(item)
        self.addresses = sorted(self.by_address)

    def exact(self, address: int) -> Function | None:
        aliases = self.by_address.get(address)
        return choose_symbol(aliases) if aliases else None

    def containing(self, address: int) -> Function | None:
        position = bisect.bisect_right(self.addresses, address) - 1
        if position < 0:
            return None
        candidate = self.exact(self.addresses[position])
        if candidate is None:
            return None
        if candidate.size and address < candidate.end:
            return candidate
        return None

    def search(self, pattern: str) -> list[Function]:
        folded = pattern.casefold()
        matches = [
            item
            for item in self.functions
            if folded in item.name.casefold() or folded in item.mangled.casefold()
        ]
        unique: dict[tuple[int, str], Function] = {}
        for item in matches:
            unique[(item.address, item.name)] = item
        return sorted(unique.values(), key=lambda item: (item.address, item.name))

    def select_one(self, query: str) -> Function:
        exact = [
            item
            for item in self.functions
            if query in {item.name, item.mangled}
        ]
        if exact:
            return choose_symbol(exact)
        matches = self.search(query)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(f"no function matches {query!r}")
        preview = "\n".join(
            f"  0x{item.address:x}  {item.name}" for item in matches[:20]
        )
        suffix = "\n  ..." if len(matches) > 20 else ""
        raise ValueError(
            f"{query!r} is ambiguous ({len(matches)} matches):\n{preview}{suffix}"
        )


@dataclass(frozen=True)
class Executable:
    code: bytes
    address: int
    machine: str


@dataclass(frozen=True)
class ElfIdentity:
    machine: str
    elfclass: int
    little_endian: bool
    text_address: int
    text_size: int
    build_id: str | None


def normalize_build_id(value: object) -> str:
    if isinstance(value, bytes):
        return value.hex()
    return str(value).strip().lower().removeprefix("0x")


def read_identity(path: Path) -> ElfIdentity:
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        text = elf.get_section_by_name(".text")
        if text is None:
            raise ValueError(f"{path} has no .text section")

        build_id = None
        for section in elf.iter_sections():
            if not hasattr(section, "iter_notes"):
                continue
            for note in section.iter_notes():
                note_name = note["n_name"]
                if isinstance(note_name, bytes):
                    note_name = note_name.decode("ascii", errors="replace")
                if (
                    str(note_name).rstrip("\0") == "GNU"
                    and note["n_type"] in ("NT_GNU_BUILD_ID", 3)
                ):
                    build_id = normalize_build_id(note["n_desc"])
                    break
            if build_id is not None:
                break

        return ElfIdentity(
            machine=str(elf["e_machine"]),
            elfclass=int(elf.elfclass),
            little_endian=bool(elf.little_endian),
            text_address=int(text["sh_addr"]),
            text_size=int(text["sh_size"]),
            build_id=build_id,
        )


def validate_pair(
    elf_path: Path,
    elf_identity: ElfIdentity,
    debug_path: Path,
    debug_identity: ElfIdentity,
) -> None:
    if (
        elf_identity.machine,
        elf_identity.elfclass,
        elf_identity.little_endian,
    ) != (
        debug_identity.machine,
        debug_identity.elfclass,
        debug_identity.little_endian,
    ):
        raise ValueError(
            "ELF/debug architecture mismatch: "
            f"{elf_path} is {elf_identity.machine}/{elf_identity.elfclass}-bit/"
            f"{'LE' if elf_identity.little_endian else 'BE'}, while "
            f"{debug_path} is {debug_identity.machine}/{debug_identity.elfclass}-bit/"
            f"{'LE' if debug_identity.little_endian else 'BE'}"
        )

    elf_text = (elf_identity.text_address, elf_identity.text_size)
    debug_text = (debug_identity.text_address, debug_identity.text_size)
    if elf_text != debug_text:
        raise ValueError(
            "ELF/debug .text layout mismatch: "
            f"{elf_path} has address=0x{elf_text[0]:x}, size={elf_text[1]}, while "
            f"{debug_path} has address=0x{debug_text[0]:x}, size={debug_text[1]}"
        )

    if elf_identity.build_id and debug_identity.build_id:
        if elf_identity.build_id != debug_identity.build_id:
            raise ValueError(
                "ELF/debug GNU Build ID mismatch: "
                f"{elf_path} has {elf_identity.build_id}, while "
                f"{debug_path} has {debug_identity.build_id}"
            )
        return

    print(
        "warning: one or both ELF files lack a GNU Build ID; "
        "architecture and .text layout match, but the pair cannot be "
        "cryptographically authenticated",
        file=sys.stderr,
    )


def read_executable(path: Path) -> Executable:
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        text = elf.get_section_by_name(".text")
        if text is None:
            raise ValueError(f"{path} has no .text section")
        return Executable(
            code=text.data(),
            address=int(text["sh_addr"]),
            machine=str(elf["e_machine"]),
        )


def make_disassembler(machine: str) -> tuple[Cs, int, int]:
    if machine == "EM_AARCH64":
        engine = Cs(CS_ARCH_ARM64, CS_MODE_ARM | CS_MODE_LITTLE_ENDIAN)
        return engine, ARM64_INS_BL, ARM64_OP_IMM
    if machine == "EM_X86_64":
        engine = Cs(CS_ARCH_X86, CS_MODE_64)
        return engine, X86_INS_CALL, X86_OP_IMM
    raise ValueError(f"unsupported ELF machine: {machine}")


def direct_calls(
    executable: Executable,
    function: Function,
) -> list[tuple[int, int]]:
    if function.size <= 0:
        raise ValueError(f"function has no size: {function.name}")
    offset = function.address - executable.address
    if offset < 0 or offset + function.size > len(executable.code):
        raise ValueError(
            f"function 0x{function.address:x} is outside the original .text section"
        )
    engine, call_instruction, immediate_operand = make_disassembler(
        executable.machine
    )
    engine.detail = True
    calls: list[tuple[int, int]] = []
    code = executable.code[offset : offset + function.size]
    for instruction in engine.disasm(code, function.address):
        if instruction.id != call_instruction or not instruction.operands:
            continue
        operand = instruction.operands[0]
        if operand.type == immediate_operand:
            calls.append((int(instruction.address), int(operand.imm)))
    return calls


def command_list(index: SymbolIndex, args: argparse.Namespace) -> int:
    matches = index.search(args.pattern)
    for item in matches:
        print(f"0x{item.address:08x}  {item.size:7d}  {item.name}")
    return 0 if matches else 1


def format_target(index: SymbolIndex, address: int) -> str:
    exact = index.exact(address)
    if exact is not None:
        return exact.name
    containing = index.containing(address)
    if containing is not None:
        return f"{containing.name}+0x{address - containing.address:x}"
    return f"0x{address:x}"


def command_calls(
    executable: Executable,
    index: SymbolIndex,
    args: argparse.Namespace,
) -> int:
    root = index.select_one(args.function)
    queue: list[tuple[Function, int]] = [(root, 0)]
    visited: set[int] = set()
    while queue:
        caller, level = queue.pop(0)
        if caller.address in visited:
            continue
        visited.add(caller.address)
        print(
            f"\n0x{caller.address:08x}  {caller.name} "
            f"(size={caller.size}, depth={level})"
        )
        calls = direct_calls(executable, caller)
        if not calls:
            print("  (no direct calls)")
        for call_site, target in calls:
            print(f"  0x{call_site:08x} -> 0x{target:08x}  {format_target(index, target)}")
            callee = index.exact(target)
            if (
                callee is not None
                and callee.size > 0
                and level < args.depth
                and callee.address not in visited
            ):
                queue.append((callee, level + 1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Combine a stripped HarmonyOS ELF with its decompressed "
            ".gnu_debugdata ELF to recover direct calls."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Find function symbols")
    list_parser.add_argument("elf", type=Path)
    list_parser.add_argument("debug_elf", type=Path)
    list_parser.add_argument("pattern")

    calls_parser = subparsers.add_parser(
        "calls", help="Print direct calls from one function"
    )
    calls_parser.add_argument("elf", type=Path)
    calls_parser.add_argument("debug_elf", type=Path)
    calls_parser.add_argument("function", help="Exact name or unique substring")
    calls_parser.add_argument(
        "--depth",
        type=int,
        default=0,
        help="Recursively inspect known direct callees (default: 0)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    for attribute in ("elf", "debug_elf"):
        path = getattr(args, attribute)
        if not path.is_file():
            parser.error(f"{attribute} does not exist or is not a file: {path}")
    if getattr(args, "depth", 0) < 0:
        parser.error("--depth must be non-negative")

    try:
        original_identity = read_identity(args.elf)
        debug_identity = read_identity(args.debug_elf)
        validate_pair(
            args.elf,
            original_identity,
            args.debug_elf,
            debug_identity,
        )
        original_functions = read_functions(args.elf)
        debug_functions = read_functions(args.debug_elf)
        index = SymbolIndex(merge_functions(original_functions, debug_functions))
        if args.command == "list":
            return command_list(index, args)
        executable = read_executable(args.elf)
        return command_calls(executable, index, args)
    except (ELFError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
