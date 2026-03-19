"""
VIT Signaling Protocol: Confirmation and Manual Review.

VIT uses structured output blocks and exit codes to signal the caller
(Claude Code) when human intervention is needed.

## Exit codes

- **0** — Success. All changes applied, no intervention needed.
- **1** — Error. VIT cannot proceed.
- **10 (EXIT_CONFIRM)** — Confirmation needed. VIT inferred a value from
  source evidence but the inference could be wrong. Emits [VIT:CONFIRM]
  blocks. Claude presents the assumption, gets confirmation, and re-runs
  VIT with the confirmed value as an explicit CLI argument.
- **11 (EXIT_MANUAL)** — Manual edits needed. VIT applied all safe
  automated changes but found code it cannot rewrite. Emits [VIT:MANUAL]
  blocks listing file:line locations. Claude presents these to the user
  for manual editing. No VIT re-run needed — the user edits source directly.

## When to use which

- **Has evidence, inferred a value** → EXIT_CONFIRM (10).
- **Applied safe changes, but some lines need human rewriting** → EXIT_MANUAL (11).
- **No evidence, arbitrary fallback** → hard error (exit 1).
- **Technical/performance default** → safe to hardcode (exit 0).
"""

import re
from dataclasses import dataclass, field
from typing import Optional


EXIT_CONFIRM = 10
EXIT_MANUAL = 11

_BLOCK_START = "[VIT:CONFIRM]"
_BLOCK_END = "[/VIT:CONFIRM]"
_MANUAL_START = "[VIT:MANUAL]"
_MANUAL_END = "[/VIT:MANUAL]"


@dataclass
class Assumption:
    """An assumption that needs human confirmation before VIT proceeds."""
    id: str                 # machine-readable identifier, e.g., "character_length"
    summary: str            # one-line human-readable description
    assumed_value: str      # the inferred or default value
    inferred_from: str      # evidence source (file:line, expression)
    context: str            # multi-line explanation
    cli_override: str       # CLI flag to confirm, e.g., "--length 1024"


@dataclass
class ManualEdit:
    """A source line that VIT cannot automatically rewrite."""
    file: str               # absolute path to source file
    line: int               # 1-indexed line number
    reason: str             # why VIT can't auto-rewrite (e.g., "complex intrinsic: EXP")
    source_text: str        # the source line that needs manual rewriting
    cli_override: str = ""  # CLI flag to provide the rewrite, e.g., "--rewrite 'file:line=<replacement>'"
    context: str = ""       # optional guidance for the human


@dataclass
class ManualReview:
    """A set of manual edits needed after VIT's automated changes."""
    fix_type: str           # which --fix produced this (e.g., "complex")
    summary: str            # one-line description
    edits: list[ManualEdit] = field(default_factory=list)
    changes_applied: int = 0  # how many references VIT did auto-rewrite


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


def print_manual_review(review: ManualReview) -> None:
    """Print a [VIT:MANUAL] block to stdout."""
    print(f"\n{_MANUAL_START}")
    print(f"fix_type: {review.fix_type}")
    print(f"summary: {review.summary}")
    print(f"changes_applied: {review.changes_applied}")
    print(f"edits_needed: {len(review.edits)}")
    for i, edit in enumerate(review.edits, 1):
        print(f"edit_{i}_file: {edit.file}")
        print(f"edit_{i}_line: {edit.line}")
        print(f"edit_{i}_reason: {edit.reason}")
        print(f"edit_{i}_source: {edit.source_text}")
        print(f"edit_{i}_cli_override: {edit.cli_override}")
        if edit.context:
            print(f"edit_{i}_context: {edit.context}")
    print(f"{_MANUAL_END}")


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


def parse_manual_reviews(output: str) -> list[ManualReview]:
    """
    Parse [VIT:MANUAL] blocks from VIT output.

    Used in tests to verify the manual review protocol output format.
    """
    results = []
    start_escaped = re.escape(_MANUAL_START)
    end_escaped = re.escape(_MANUAL_END)
    blocks = re.findall(
        rf'{start_escaped}\n(.*?)\n{end_escaped}',
        output,
        re.DOTALL,
    )

    for block_text in blocks:
        fields = _parse_block_fields(block_text)
        n_edits = int(fields.get("edits_needed", "0"))
        edits = []
        for i in range(1, n_edits + 1):
            edits.append(ManualEdit(
                file=fields.get(f"edit_{i}_file", ""),
                line=int(fields.get(f"edit_{i}_line", "0")),
                reason=fields.get(f"edit_{i}_reason", ""),
                source_text=fields.get(f"edit_{i}_source", ""),
                cli_override=fields.get(f"edit_{i}_cli_override", ""),
                context=fields.get(f"edit_{i}_context", ""),
            ))
        results.append(ManualReview(
            fix_type=fields.get("fix_type", ""),
            summary=fields.get("summary", ""),
            edits=edits,
            changes_applied=int(fields.get("changes_applied", "0")),
        ))

    return results


def _parse_block_fields(block_text: str) -> dict[str, str]:
    """Parse key: value pairs from a block, handling multi-line values."""
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
    return fields
