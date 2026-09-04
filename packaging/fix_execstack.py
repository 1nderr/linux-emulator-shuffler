"""Clear the executable-stack marker from bundled ELF objects.

Some Python builds (notably mise/pyenv ones compiled without -z noexecstack)
mark libpython's PT_GNU_STACK segment as RWE. Hardened kernels refuse to load
such a library, so a PyInstaller bundle made with that interpreter dies at
startup with "cannot enable executable stack as shared object requires".

CPython does not need an executable stack -- distribution builds ship it
non-executable -- so clearing the flag on the bundled copy is safe. This does
the same thing as `execstack -c`, without needing that tool installed.
"""

from __future__ import annotations

import os
import struct
import sys

ELF_MAGIC = b"\x7fELF"
ELFCLASS64 = 2
ELFDATA2LSB = 1
PT_GNU_STACK = 0x6474E551
PF_X = 0x1


def clear_execstack(path: str) -> bool:
    """Clear PF_X on the file's PT_GNU_STACK segment. True if it changed."""
    with open(path, "r+b") as elf:
        header = elf.read(64)
        if len(header) < 64 or header[:4] != ELF_MAGIC:
            return False
        if header[4] != ELFCLASS64 or header[5] != ELFDATA2LSB:
            return False  # Only 64-bit little-endian is handled.

        e_phoff = struct.unpack_from("<Q", header, 0x20)[0]
        e_phentsize = struct.unpack_from("<H", header, 0x36)[0]
        e_phnum = struct.unpack_from("<H", header, 0x38)[0]
        if not e_phoff or e_phentsize < 8:
            return False

        for index in range(e_phnum):
            entry = e_phoff + index * e_phentsize
            elf.seek(entry)
            chunk = elf.read(8)
            if len(chunk) < 8:
                break

            p_type, p_flags = struct.unpack("<II", chunk)
            if p_type != PT_GNU_STACK or not p_flags & PF_X:
                continue

            elf.seek(entry + 4)
            elf.write(struct.pack("<I", p_flags & ~PF_X))
            return True

    return False


def main(roots: list[str]) -> int:
    fixed = []
    for root in roots:
        targets = [root] if os.path.isfile(root) else []
        for directory, _, names in os.walk(root):
            targets.extend(os.path.join(directory, name) for name in names)

        for target in targets:
            if os.path.islink(target) or not os.access(target, os.W_OK):
                continue
            try:
                if clear_execstack(target):
                    fixed.append(target)
            except OSError:
                continue

    for path in fixed:
        print(f"  cleared executable stack: {os.path.relpath(path)}")
    print(f"{len(fixed)} file(s) needed the executable-stack flag cleared")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["dist"]))
