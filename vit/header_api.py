"""What a translator may call, read out of the headers rather than described.

`TRANSLATION_TIPS.md` carried a hand-written list of the thirteen functions in
`vit_nwtc.h`, their argument orders, and which index conventions were 1-based.
That list is a copy, and a copy of a header drifts from the header — silently,
because nothing compares them. A translation written against the stale copy
fails at the compile step with a signature error, which is at least loud; a
translation that *avoids* a function because the list did not mention it fails
at nothing at all, and simply reimplements it inline.

So the inventory is read from the headers at prompt time. It cannot drift, it
grows when someone adds a shared function, and it shrinks when someone removes
one — with nobody maintaining a list.

Deliberately shallow: this reports declarations, not semantics. A one-line
comment directly above a declaration is carried along when there is one, because
that is where the 1-based/0-based conventions actually live in these headers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# A top-level declaration starts in column zero with a type-ish token. Members,
# locals and bodies are indented, which is what keeps this from reporting the
# insides of an inline function.
_DECL = re.compile(
    r"^(?:inline\s+|static\s+|constexpr\s+|const\s+|template\s*<[^>]*>\s*)*"
    r"(?:unsigned\s+|signed\s+|long\s+|short\s+)*"
    r"(?:void|double|float|int|bool|char|std::string|auto|[A-Za-z_]\w*_t)\s*[*&]?\s*"
    r"([A-Za-z_]\w*)\s*\(",
    re.MULTILINE)

_CONST = re.compile(
    r"^(?:inline\s+)?(?:static\s+)?constexpr\s+\w+\s+([A-Za-z_]\w*)\s*=\s*([^;]+);",
    re.MULTILINE)

_DEFINE = re.compile(r"^#define\s+([A-Za-z_]\w*)\s+([^\n\\]+)$", re.MULTILINE)


def _balanced(text: str, open_at: int) -> Optional[int]:
    """Index just past the `)` matching the `(` at `open_at`."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _leading_comment(text: str, start: int) -> str:
    """The `//` comment line immediately above a declaration, if any."""
    prev_end = text.rfind("\n", 0, start)
    if prev_end < 0:
        return ""
    prev_start = text.rfind("\n", 0, prev_end) + 1
    line = text[prev_start:prev_end].strip()
    if line.startswith("//"):
        return line.lstrip("/").strip()
    return ""


def declarations(source: str) -> list:
    """Every top-level function declaration, normalised to one line."""
    out = []
    for m in _DECL.finditer(source):
        open_at = source.index("(", m.start())
        end = _balanced(source, open_at)
        if end is None:
            continue
        decl = source[m.start():end]
        decl = re.sub(r"\s*\n\s*", " ", decl).strip()
        decl = re.sub(r"\s{2,}", " ", decl)
        out.append((decl, _leading_comment(source, m.start())))
    # A header may declare then define the same name (`inline` bodies do not,
    # but split declaration/definition pairs do). Keep the first spelling only.
    seen, unique = set(), []
    for decl, note in out:
        name = re.search(r"([A-Za-z_]\w*)\s*\(", decl)
        key = name.group(1) if name else decl
        if key in seen:
            continue
        seen.add(key)
        unique.append((decl, note))
    return unique


_GUARD = re.compile(r"^[A-Z0-9_]+_(?:H|HPP)$")


def constants(source: str) -> list:
    out = [(n, v.strip()) for n, v in _CONST.findall(source)]
    out += [(n, v.strip()) for n, v in _DEFINE.findall(source)
            if not v.strip().startswith("(")]      # skip function-like macros
    # An include guard is not something anyone can call or read.
    return [(n, v) for n, v in out if not _GUARD.match(n)]


def inventory(paths: list) -> str:
    """A prompt section listing what the shared headers actually provide.

    Returns "" when there are no headers, so a project without shared files
    gets no empty section.
    """
    blocks = []
    for p in paths:
        path = Path(p)
        if not path.is_file() or path.suffix not in (".h", ".hpp"):
            continue
        src = path.read_text()
        decls = declarations(src)
        consts = constants(src)
        if not decls and not consts:
            continue
        lines = [f"### `{path.name}`"]
        if consts:
            lines.append("Constants: " + ", ".join(f"`{n}`" for n, _ in consts))
        for decl, note in decls:
            lines.append(f"- `{decl};`" + (f" — {note}" if note else ""))
        blocks.append("\n".join(lines))

    if not blocks:
        return ""
    return ("## Shared functions available to call (read from the headers)\n\n"
            "These are compiled once and linked into every translation. Call them; "
            "do not reimplement them inline.\n\n" + "\n\n".join(blocks))
