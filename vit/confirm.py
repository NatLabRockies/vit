"""
VIT Assumption Confirmation Protocol.

When VIT infers a value from source evidence that could be wrong, it signals
the caller (Claude Code) via exit code 10 and a structured [VIT:CONFIRM] block.
Claude presents the assumption to the user, gets confirmation, and re-runs VIT
with the confirmed value as an explicit CLI argument.

## When to use this protocol

- **Has evidence, inferred a value** → EXIT_CONFIRM (10). VIT found source
  evidence and derived a value, but the inference could be wrong. Human must
  validate. Example: inferred CHARACTER buffer size from ALLOCATE call.

- **No evidence, arbitrary fallback** → hard error (exit 1). VIT doesn't have
  enough information to proceed. Guessing would silently produce wrong code.
  Example: unknown function return type.

- **Technical/performance default** → safe to hardcode. Wrong value = slower
  or noisier, not semantically incorrect. Example: max_iterations=5.

The test: if a wrong default would produce code that compiles but behaves
incorrectly, it needs either EXIT_CONFIRM (if inference exists) or a hard
error (if no evidence).
"""

import re
from dataclasses import dataclass
from typing import Optional


EXIT_CONFIRM = 10

_BLOCK_START = "[VIT:CONFIRM]"
_BLOCK_END = "[/VIT:CONFIRM]"


@dataclass
class Assumption:
    """An assumption that needs human confirmation before VIT proceeds."""
    id: str                 # machine-readable identifier, e.g., "character_length"
    summary: str            # one-line human-readable description
    assumed_value: str      # the inferred or default value
    inferred_from: str      # evidence source (file:line, expression)
    context: str            # multi-line explanation
    cli_override: str       # CLI flag to confirm, e.g., "--length 1024"


def print_assumption(assumption: Assumption) -> None:
    """Print a [VIT:CONFIRM] block to stdout."""
    print(f"\n{_BLOCK_START}")
    print(f"id: {assumption.id}")
    print(f"summary: {assumption.summary}")
    print(f"assumed_value: {assumption.assumed_value}")
    print(f"inferred_from: {assumption.inferred_from}")
    # Handle multi-line context with indentation
    context_lines = assumption.context.strip().split("\n")
    if len(context_lines) == 1:
        print(f"context: {context_lines[0]}")
    else:
        print(f"context: |")
        for line in context_lines:
            print(f"  {line}")
    print(f"cli_override: {assumption.cli_override}")
    print(f"{_BLOCK_END}")


def parse_assumptions(output: str) -> list[Assumption]:
    """
    Parse [VIT:CONFIRM] blocks from VIT output.

    Used in tests to verify the protocol output format.
    """
    results = []
    start_escaped = re.escape(_BLOCK_START)
    end_escaped = re.escape(_BLOCK_END)
    blocks = re.findall(
        rf'{start_escaped}\n(.*?)\n{end_escaped}',
        output,
        re.DOTALL,
    )

    for block_text in blocks:
        fields: dict[str, str] = {}
        lines = block_text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            m = re.match(r'^(\w+):\s*(.*)', line)
            if m:
                key = m.group(1)
                value = m.group(2)
                if value == "|":
                    # Multi-line value: collect indented lines
                    multi_lines = []
                    i += 1
                    while i < len(lines):
                        if lines[i].startswith("  "):
                            multi_lines.append(lines[i][2:])
                        else:
                            break
                        i += 1
                    fields[key] = "\n".join(multi_lines)
                    continue
                else:
                    fields[key] = value
            i += 1

        if "id" in fields:
            results.append(Assumption(
                id=fields.get("id", ""),
                summary=fields.get("summary", ""),
                assumed_value=fields.get("assumed_value", ""),
                inferred_from=fields.get("inferred_from", ""),
                context=fields.get("context", ""),
                cli_override=fields.get("cli_override", ""),
            ))

    return results
