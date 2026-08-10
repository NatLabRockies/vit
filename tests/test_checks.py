"""The check registry's own red test.

A registry of checks with no known-bad is a fails-green surface, which is
precisely the disease being treated: every check would report clean, and a
clean report would mean nothing. So the contract is enforced here rather than
promised in a doc — **every** entry is run against a known-bad it must flag and
a known-good it must not, and the parametrisation is over `REGISTRY` itself, so
a check added without examples fails the suite instead of passing silently.

The second property tested is that the prompt is *derived*. If the guidance
section could be edited independently of the checks, it would drift from what
is enforced, and drift is exactly what deleting `TRANSLATION_TIPS.md` is meant
to end.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vit import checks                                              # noqa: E402
from vit.checks import (PUSHED_TO_MUTATION, REGISTRY, RETIRED,      # noqa: E402
                        Context, prompt_section, run,
                        strip_cpp_comments, strip_fortran_comments)


IDS = [c.id for c in REGISTRY]


# ------------------------------------------------------- the contract itself

@pytest.mark.parametrize("check", REGISTRY, ids=IDS)
def test_every_check_flags_its_known_bad(check):
    """The red test. A check that cannot fire is not a check."""
    found = check.detect(check.known_bad)
    assert found, f"{check.id} did not flag its own known-bad"
    assert all(f.check_id == check.id for f in found)


@pytest.mark.parametrize("check", REGISTRY, ids=IDS)
def test_every_check_is_silent_on_its_known_good(check):
    """A noisy check gets disabled, and then you have neither check nor tip."""
    found = check.detect(check.known_good)
    assert not found, f"{check.id} false-positived on its known-good: {found}"


@pytest.mark.parametrize("check", REGISTRY, ids=IDS)
def test_a_check_does_not_fire_on_an_empty_translation(check):
    """Nothing to look at must mean nothing to say -- otherwise the first run
    on an unrelated function buries the real findings in noise."""
    assert check.detect(Context()) == []


def test_check_ids_are_unique():
    assert len(IDS) == len(set(IDS))


# ----------------------------------------------------- no cross-contamination

def test_each_known_bad_is_flagged_only_by_its_own_check():
    """Two checks firing on one defect double-reports it, and worse, means the
    second check's known-good is not really testing the second check."""
    for check in REGISTRY:
        firing = {f.check_id for f in run(check.known_bad.fortran,
                                          check.known_bad.cpp)}
        assert firing == {check.id}, f"{check.id}'s known-bad also fired {firing}"


# ------------------------------------------------------- the prompt is derived

def test_the_prompt_section_is_exactly_the_active_checks():
    section = prompt_section()
    for check in REGISTRY:
        assert check.prompt_line in section, f"{check.id} missing from prompt"
    assert section.count("\n- ") == len(REGISTRY), "prompt has lines no check owns"


def test_a_retired_check_contributes_no_prompt_line():
    """The property this whole design exists for: when the tool makes a defect
    impossible, its guidance disappears WITHOUT anyone editing the prompt."""
    section = prompt_section()
    for retired_id in RETIRED:
        assert retired_id not in section
    assert RETIRED, "retirements are the mechanism; keep at least one recorded"


def test_a_check_is_silent_on_accepted_code_without_being_dead():
    """*Zero findings* and *cannot produce a finding* look identical in a
    report, and only one is good news.

    Every check must stay silent on a plausible ACCEPTED translation -- one that
    exercises its shape correctly -- while still firing on its known-bad. A
    detector that is silent on everything would pass the false-positive sweep
    perfectly and be worth nothing.
    """
    for check in REGISTRY:
        assert check.detect(check.known_good) == [], f"{check.id} noisy on known-good"
        assert check.detect(check.known_bad), f"{check.id} is DEAD -- it cannot fire"
        # ...and the two must actually differ, or the pair proves nothing.
        assert check.known_bad.cpp != check.known_good.cpp or \
               check.known_bad.fortran != check.known_good.fortran, \
               f"{check.id}'s known-bad and known-good are identical"


# ------------------------------------------- every instance, not just the first

def test_every_offending_line_is_reported_with_its_line_number():
    """Measured the hard way, on the fix that motivated it.

    The first version returned at the first match, so `vit check` reported ONE
    finding for a function with six. Three real ROSCO translations were fixed
    from that report and three more instances of the identical defect remained,
    found only by reading. A count that understates the work reads as "one line
    to fix" -- silence about the rest, in the tool built to end silence.
    """
    fortran = "a = k*x**2 + c*y**2\n"
    cpp = ("double f(double k, double x, double c, double y) {\n"
           "    double a = k * x * x;\n"
           "    double b = c * y * y;\n"
           "    return a + b;\n}\n")
    found = run(fortran=fortran, cpp=cpp)
    assert len(found) == 2, f"expected both, got {[str(f) for f in found]}"
    assert sorted(f.line for f in found) == [2, 3]


def test_a_benign_match_does_not_swallow_a_real_one_behind_it():
    """`re.finditer` is non-overlapping, so exempting `2.0 * DT * DT` consumed
    the text and hid the `CornerFreq` pair on the same line. Measured on
    `SecLPFilter`, where it silently cost two of six lines."""
    fortran = "x = 2.0*DT**2.0*CornerFreq**2.0\n"
    cpp = "double x = 2.0 * DT * DT * CornerFreq * CornerFreq;\n"
    found = run(fortran=fortran, cpp=cpp)
    assert len(found) == 1
    assert "CornerFreq" in found[0].evidence
    assert "2.0 * DT * DT" not in found[0].evidence


def test_a_parenthesised_leading_factor_still_regroups():
    """`(x0 - 3.0*x1) * x0 * x0` regroups exactly like `a * x0 * x0`. Requiring
    an identifier missed it -- found by running the check over the very code it
    had just been used to fix."""
    fortran = "a0 = (x0-3*x1)*x0**2/d3\n"
    cpp = "double a0 = (x0 - 3.0 * x1) * x0 * x0 / d3;\n"
    assert len(run(fortran=fortran, cpp=cpp)) == 1


def test_a_line_number_points_at_the_original_file_not_the_stripped_text():
    """Comments are blanked rather than deleted so offsets survive; a line
    number computed on shortened text would drift further the more prose a
    translation carries."""
    fortran = "a = k*x**2\n"
    cpp = ("// a comment mentioning k * x * x that must not be flagged\n"
           "/* a block\n   comment */\n"
           "double f(double k, double x) { return k * x * x; }\n")
    found = run(fortran=fortran, cpp=cpp)
    assert len(found) == 1
    assert found[0].line == 4, f"reported line {found[0].line}, expected 4"


def test_gaps_are_recorded_rather_than_silently_absent():
    """A shape with no precise detector is rung 3 (measured by mutation), not
    rung 4 (told). Recording it is what keeps it from being forgotten."""
    assert PUSHED_TO_MUTATION
    for shape, reason in PUSHED_TO_MUTATION.items():
        assert shape not in IDS, f"{shape} is both shipped and pushed to mutation"
        assert len(reason) > 40, f"{shape} needs a reason, not a label"


# ------------------------------------------------------------ source handling

def test_a_defect_named_in_a_comment_is_not_a_defect():
    """Comment stripping is load-bearing: a translation that WARNS about
    `std::toupper` in a comment must not be flagged for using it."""
    cpp = "// never call std::toupper(c) here\nchar f(char c) { return c; }\n"
    assert run(cpp=cpp) == []


def test_a_fortran_comment_does_not_trigger_a_cross_source_check():
    assert run(fortran="! NINT(x) would be wrong here\n",
               cpp="int i = (int)std::rint(x);\n") == []


def test_strip_keeps_string_contents():
    assert "a!b" in strip_fortran_comments("x = 'a!b'  ! real comment")
    assert "real comment" not in strip_fortran_comments("x = 'a!b'  ! real comment")
    assert "//not a comment" in strip_cpp_comments('s = "//not a comment"; // gone')
    assert "gone" not in strip_cpp_comments('s = "//not a comment"; // gone')


# ------------------------------------------------------- a couple of real ones

def test_nint_check_needs_the_fortran_to_actually_use_nint():
    """Cross-source precision: `rint` in a translation whose reference has no
    `NINT` is somebody's deliberate half-to-even, not a defect."""
    assert run(fortran="y = x * 2\n", cpp="double y = std::rint(x);\n") == []


def test_save_static_ignores_parameters_and_dummies():
    """A PARAMETER is a named constant and an INTENT line is a dummy; neither
    is an implicitly-SAVEd local, and flagging them would make the check noisy
    enough to be turned off."""
    f = ("    REAL(DbKi), PARAMETER :: Tol = 1.0D-6\n"
         "    REAL(DbKi), INTENT(IN) :: x\n")
    assert run(fortran=f, cpp="double g(double x) { return x; }\n") == []


def test_findings_render_with_the_check_id():
    found = run(cpp='#include "vit_translated.h"\n')
    assert found and "forbidden-include" in str(found[0])


# ------------------------------------------- a skipped check is not a clean one

def test_cross_source_checks_are_reported_skipped_without_a_reference():
    """The failure mode this project is named after. Without `-f` the
    cross-source checks CANNOT run, and a report that omitted them would read
    as `clean` while four of seven checks never executed."""
    rep = checks.report(cpp="double f(void) { return 1.0; }\n")
    skipped = {cid for cid, _ in rep.skipped}
    assert skipped == {c.id for c in REGISTRY if c.needs_fortran}
    assert all(why for _, why in rep.skipped), "a skip must say why"
    assert rep.clean, "no findings here -- but see the next assertion"
    assert len(rep.ran) < len(REGISTRY), "clean must not imply everything ran"


def test_supplying_the_reference_runs_everything():
    rep = checks.report(fortran="SUBROUTINE s\nEND SUBROUTINE\n",
                        cpp="double f(void) { return 1.0; }\n")
    assert rep.skipped == []
    assert len(rep.ran) == len(REGISTRY)


def test_vits_own_integration_preamble_is_not_judged_as_translator_error():
    """`vit integrate` prepends `#include "vit_translated.h"` itself. Checking
    the whole file flags VIT's output as a human's mistake -- measured as 5 of 5
    forbidden-include hits across AeroDyn's 40 translations, every one on a
    verified, integrated function."""
    integrated = ('// After verification: Foo kernel PASSED\n'
                  '#include "vit_translated.h"\n\n'
                  '// --- User-provided C++ implementation ---\n'
                  '#include "vit_types.h"\n'
                  'void Foo(void) {}\n')
    assert run(cpp=integrated) == []
    # ...but a translator writing it themselves is still caught.
    assert run(cpp='#include "vit_translated.h"\nvoid Foo(void) {}\n')


def test_an_untouched_scaffold_is_recognised_and_not_checked():
    """Measured on the real tree: an unimplemented scaffold was the only source
    of false signal across 62 files. Findings about absent code are what get a
    checker disabled."""
    scaffold = ("// VIT Translation Scaffold\n"
                "void Read_OL_Input(char* f) {\n    // TODO: implement Read_OL_Input\n}\n")
    assert checks.is_unimplemented_scaffold(scaffold)
    assert not checks.is_unimplemented_scaffold(
        '#include "vit_types.h"\ndouble f(void) { return 1.0; }\n')


def test_the_translate_prompt_carries_the_generated_section():
    """`vit translate`'s guidance must come from the registry, not from prose
    sitting beside it -- otherwise the two drift and only one is enforced."""
    from vit.fortran_parser import parse_signature_from_source
    from vit.translate import generate_translation_prompt

    src = ("FUNCTION Sq(x)\n    REAL(8), INTENT(IN) :: x\n"
           "    REAL(8) :: Sq\nEND FUNCTION Sq\n")
    prompt = generate_translation_prompt(
        sig=parse_signature_from_source(src, "Sq"), fortran_source_text=src)
    for check in REGISTRY:
        assert check.prompt_line in prompt, f"{check.id} absent from translate prompt"
