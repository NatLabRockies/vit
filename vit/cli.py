"""
VIT CLI — Verified Incremental Translator

Usage:
    vit init                        Initialize vit.yaml for a project
    vit extract <function_name>     Extract function with KGen
    vit translate <function>        Generate C++ translation scaffold + prompt
    vit interface <function> <cpp>  Generate Fortran↔C interface
    vit verify <function>           Build and run KGen kernel verification
    vit integrate <function>        Generate codebase integration plan
    vit sim-compare <base> <int>    Compare simulation arrays (non-KGen verification)
    vit run <function> <cpp_file>   Full pipeline (interface → verify → integrate)
    vit analyze-types               Analyze derived types for C compatibility
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from .config import load_config, init_config, record_translation
from .derived_type_parser import build_derived_type_registry
from .fortran_parser import find_function_in_file, FortranSignature
from .interface_gen import (
    generate_fortran_interface_block,
    generate_fortran_wrapper,
)
from .extract import find_call_sites, insert_kgen_pragma, remove_kgen_pragma, run_kgen_extract
from .kernel_verify import setup_kernel_cpp, run_kernel_verify
from .integrate import generate_integration_plan, apply_integration_plan
from .sim_compare import compare_arrays, format_result
from .translate import generate_translation_scaffold, generate_translation_prompt
from .analyze_types import (
    analyze_all_types, format_analysis_report, fix_character_allocatable,
    infer_character_length, fix_logical_field, fix_complex_field,
    fix_bind_c, scan_allocatable_sizes, format_allocatable_scan_report,
)
from .confirm import (
    Assumption, ManualReview, EXIT_CONFIRM, EXIT_MANUAL,
    print_assumption, print_manual_review,
)


def main():
    parser = argparse.ArgumentParser(
        prog="vit",
        description="Verified Incremental Translator — "
                    "Extract, verify, and integrate Fortran→C++ translations",
    )
    parser.add_argument("--config", "-c", help="Path to vit.yaml")
    parser.add_argument("--verbose", "-v", action="store_true")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- init ---
    p_init = subparsers.add_parser("init", help="Initialize vit.yaml")
    p_init.add_argument("--name", default="my_project", help="Project name")
    p_init.add_argument("--source-dir", default="src")
    p_init.add_argument("--build-cmd", default="cmake --build build")
    p_init.add_argument("--run-cmd", default="python run_simulation.py")

    # --- find ---
    p_find = subparsers.add_parser("find", help="Find call sites for a function")
    p_find.add_argument("function_name", help="Function name to find")
    p_find.add_argument("--source-dir", help="Override source directory")

    # --- parse ---
    p_parse = subparsers.add_parser("parse", help="Parse a function signature")
    p_parse.add_argument("function_name", help="Function name to parse")
    p_parse.add_argument("--file", "-f", help="Specific file to search")

    # --- extract ---
    p_extract = subparsers.add_parser("extract", help="Extract function with KGen")
    p_extract.add_argument("function_name", help="Function to extract")
    p_extract.add_argument("--file", "-f", help="Source file containing call site")
    p_extract.add_argument("--line", "-l", type=int, help="Line number of call site")
    p_extract.add_argument("--dry-run", action="store_true")
    p_extract.add_argument("--run-args", help="Extra arguments to append to the run command (e.g., '--scenario 3')")
    p_extract.add_argument("--assumed-size", action="append", metavar="NAME=SIZE",
                           help="Override assumed-size array size (e.g., --assumed-size avrSWAP=3000)")

    # --- translate ---
    p_translate = subparsers.add_parser("translate", help="Generate C++ translation scaffold")
    p_translate.add_argument("function_name", help="Function name to translate")
    p_translate.add_argument("--file", "-f", required=True,
                             help="Fortran source file with function definition")
    p_translate.add_argument("--output", "-o", help="Output directory override")
    p_translate.add_argument("--module", help="Module name for directory organization")

    # --- interface ---
    p_iface = subparsers.add_parser("interface", help="Generate Fortran↔C interface")
    p_iface.add_argument("function_name", help="Function name")
    p_iface.add_argument("--file", "-f", help="Fortran source file with function definition")
    p_iface.add_argument("--output-dir", "-o", default=".", help="Output directory")

    # --- verify ---
    p_verify = subparsers.add_parser("verify", help="Run KGen kernel verification")
    p_verify.add_argument("function_name", help="Function to verify")
    p_verify.add_argument("cpp_file", help="Path to C++ implementation")
    p_verify.add_argument("--kernel-dir", help="Path to KGen kernel/ directory")
    p_verify.add_argument("--file", "-f", help="Fortran source with function definition")
    p_verify.add_argument("--no-field-log", action="store_true",
                          help="Suppress per-field value logging")

    # --- integrate ---
    p_integrate = subparsers.add_parser("integrate", help="Generate integration plan")
    p_integrate.add_argument("function_name", help="Function to integrate")
    p_integrate.add_argument("cpp_file", help="Path to C++ implementation")
    p_integrate.add_argument("--file", "-f", help="Fortran source file")
    p_integrate.add_argument("--apply", action="store_true", help="Apply changes")
    p_integrate.add_argument("--dry-run", action="store_true")
    p_integrate.add_argument("--reverse-copy", action="store_true",
                             help="Add reverse-copy calls after C++ call (for functions that modify view-type scalars)")
    p_integrate.add_argument("--auto-allocate", action="store_true",
                             help="Auto-extract ALLOCATE statements from Fortran source into wrapper")

    # --- sim-compare ---
    p_simcmp = subparsers.add_parser("sim-compare",
                                      help="Compare simulation output arrays (non-KGen verification)")
    p_simcmp.add_argument("baseline_dir", help="Directory with baseline .npz files")
    p_simcmp.add_argument("integrated_dir", help="Directory with integrated .npz files")
    p_simcmp.add_argument("--tolerance", type=float, default=0.0,
                          help="Max allowed absolute difference (0.0 = exact match)")

    # --- run (full pipeline) ---
    p_run = subparsers.add_parser("run", help="Full pipeline: interface → verify → integrate")
    p_run.add_argument("function_name", help="Function to translate")
    p_run.add_argument("cpp_file", help="Path to C++ implementation")
    p_run.add_argument("--file", "-f", help="Fortran source file")
    p_run.add_argument("--kernel-dir", help="KGen kernel/ directory")
    p_run.add_argument("--skip-verify", action="store_true")
    p_run.add_argument("--no-field-log", action="store_true",
                       help="Suppress per-field value logging")
    p_run.add_argument("--apply", action="store_true")

    # --- status ---
    p_status = subparsers.add_parser("status", help="Show translation status")

    # --- analyze-types ---
    p_analyze = subparsers.add_parser("analyze-types",
                                       help="Analyze derived types for C compatibility")
    p_analyze.add_argument("--source-dir", help="Override source directory")
    p_analyze.add_argument("--verbose", "-V", action="store_true",
                           help="Show all types including compatible ones")
    p_analyze.add_argument("--fix",
                           choices=["character", "logical", "complex",
                                    "bind_c"],
                           help="Apply automated fix")
    p_analyze.add_argument("--type", dest="type_name",
                           help="Type name to fix (required with --fix)")
    p_analyze.add_argument("--field", dest="field_name",
                           help="Field name to fix (required with --fix)")
    p_analyze.add_argument("--length", type=int, default=None,
                           help="Fixed length for CHARACTER fix (inferred if omitted)")
    p_analyze.add_argument("--rewrite", action="append", default=[],
                           help="Manual rewrite: 'file:line=replacement' (repeatable)")
    p_analyze.add_argument("--apply", action="store_true",
                           help="Apply changes (default is dry-run)")
    p_analyze.add_argument("--scan-allocatable", action="store_true",
                           help="Scan for ALLOCATABLE field size sources")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    config = load_config(args.config)

    try:
        if args.command == "init":
            return cmd_init(args, config)
        elif args.command == "find":
            return cmd_find(args, config)
        elif args.command == "parse":
            return cmd_parse(args, config)
        elif args.command == "extract":
            return cmd_extract(args, config)
        elif args.command == "translate":
            return cmd_translate(args, config)
        elif args.command == "interface":
            return cmd_interface(args, config)
        elif args.command == "verify":
            return cmd_verify(args, config)
        elif args.command == "integrate":
            return cmd_integrate(args, config)
        elif args.command == "sim-compare":
            return cmd_sim_compare(args, config)
        elif args.command == "run":
            return cmd_run(args, config)
        elif args.command == "status":
            return cmd_status(args, config)
        elif args.command == "analyze-types":
            return cmd_analyze_types(args, config)
        else:
            parser.print_help()
            return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def cmd_init(args, config):
    """Initialize vit.yaml."""
    path = init_config(
        project_dir=".",
        project_name=args.name,
        source_dir=args.source_dir,
        build_cmd=args.build_cmd,
        run_cmd=args.run_cmd,
    )
    print(f"Created {path}")
    print("Edit this file to configure your project settings.")
    return 0


def cmd_find(args, config):
    """Find call sites for a function."""
    source_dir = args.source_dir or config.abs_source_dir
    sites = find_call_sites(source_dir, args.function_name)

    if not sites:
        print(f"No call sites found for '{args.function_name}' in {source_dir}")
        return 1

    print(f"Found {len(sites)} call site(s) for '{args.function_name}':")
    for filepath, line_num, line_text in sites:
        print(f"  {filepath}:{line_num}")
        print(f"    {line_text.strip()}")
    return 0


def cmd_parse(args, config):
    """Parse a function signature."""
    if args.file:
        sig = find_function_in_file(args.file, args.function_name)
    else:
        sig = _find_function_in_project(args.function_name, config)

    _print_signature(sig)
    return 0


def cmd_extract(args, config):
    """Extract function with KGen."""
    if not args.file or not args.line:
        # Auto-find call site
        source_dir = config.abs_source_dir
        sites = find_call_sites(source_dir, args.function_name)
        if not sites:
            print(f"No call sites found. Use --file and --line to specify manually.")
            return 1

        if len(sites) > 1:
            print(f"Multiple call sites found. Please specify with --file and --line:")
            for fp, ln, lt in sites:
                print(f"  {fp}:{ln}  {lt.strip()}")
            return 1

        file_path, line_num, _ = sites[0]
    else:
        file_path = args.file
        line_num = args.line

    # Verify the file exists
    if not os.path.isfile(file_path):
        print(f"ERROR: File not found: {file_path}")
        return 1

    # Clean any leftover pragmas from ALL source files before backing up.
    # KGen reads all source files in the project (not just the target file),
    # so stale pragmas in other files would contaminate the extraction —
    # KGen would find the stale pragma and use its function name for state
    # capture paths, overwriting the intended extraction target.
    source_dir = config.abs_source_dir
    if source_dir:
        for f90 in sorted(Path(source_dir).glob("*.f90")) + sorted(Path(source_dir).glob("*.F90")):
            remove_kgen_pragma(str(f90))
    else:
        remove_kgen_pragma(file_path)

    # Back up the (now clean) source file
    backup_path = file_path + ".vit_backup"
    shutil.copy2(file_path, backup_path)

    try:

        # Re-read line number after pragma removal (may have shifted)
        # Search for the function call near the original line
        lines = Path(file_path).read_text().split("\n")
        name_upper = args.function_name.upper()
        actual_line = None
        # Search within ±5 lines of the original
        for offset in range(0, 6):
            for candidate in [line_num - 1 + offset, line_num - 1 - offset]:
                if 0 <= candidate < len(lines):
                    stripped = lines[candidate].strip().upper()
                    if "!" in stripped:
                        stripped = stripped[:stripped.index("!")]
                    if re.search(rf'\b{name_upper}\s*\(', stripped):
                        actual_line = candidate + 1  # back to 1-indexed
                        break
            if actual_line:
                break

        if actual_line is None:
            actual_line = line_num  # Fall back to original

        print(f"Inserting KGen pragma at {file_path}:{actual_line}")
        insert_kgen_pragma(file_path, actual_line, function_name=args.function_name)

        # Apply --run-args to customize run command for this extraction
        run_cmd = config.run_cmd
        if hasattr(args, 'run_args') and args.run_args:
            run_cmd = f"{run_cmd} {args.run_args}"

        # Build effective assumed_size_arrays: config + CLI overrides + auto-detect
        assumed_size = dict(config.assumed_size_arrays or {})

        # CLI overrides (user explicitly confirmed — skip validation for these)
        cli_overridden = set()
        if hasattr(args, 'assumed_size') and args.assumed_size:
            for override in args.assumed_size:
                if "=" in override:
                    name, val = override.split("=", 1)
                    assumed_size[name.strip()] = int(val.strip())
                    cli_overridden.add(name.strip())

        # Auto-detect assumed-size (*) arrays from the enclosing subroutine
        if config.abs_source_dir:
            from .extract import detect_assumed_size_from_source
            detected = detect_assumed_size_from_source(file_path, actual_line)
            for varname in detected:
                if varname not in assumed_size:
                    assumed_size[varname] = 0  # Mark as "needs inference"

        # Validate/infer sizes for arrays not explicitly overridden on CLI
        arrays_to_validate = {k: v for k, v in assumed_size.items()
                              if k not in cli_overridden}
        if arrays_to_validate and config.abs_source_dir:
            from .extract import infer_assumed_size_arrays
            size_assumptions = infer_assumed_size_arrays(
                config.abs_source_dir, arrays_to_validate
            )
            if size_assumptions:
                # Separate hard errors (no evidence) from confirmations (has proposal)
                errors = [a for a in size_assumptions if not a.assumed_value]
                confirms = [a for a in size_assumptions if a.assumed_value]
                if errors:
                    for err in errors:
                        print(f"ERROR: {err.summary}", file=sys.stderr)
                        print(f"  {err.context}", file=sys.stderr)
                    return 1
                for assumption in confirms:
                    print_assumption(assumption)
                return EXIT_CONFIRM

        print(f"Running KGen extraction...")
        result = run_kgen_extract(
            source_file=file_path,
            build_cmd=config.build_cmd,
            run_cmd=run_cmd,
            clean_cmd=config.clean_cmd,
            install_cmd=config.install_cmd,
            kgen_path=config.kgen_path,
            source_dir=config.abs_source_dir,
            build_dir=config.abs_build_dir,
            cmake_file=config.abs_cmake_file if config.build_type == "cmake" else "",
            package_name=config.project_name,
            invocation=config.invocation,
            assumed_size_arrays=assumed_size or None,
            dll_persistence=config.dll_persistence or None,
            dry_run=args.dry_run,
        )

        # Display warnings (e.g., editable pip install conflicts)
        for w in result.get("warnings", []):
            print(f"\n⚠ {w}")

        if result["cleaned"]:
            print(f"  Cleaned stale: {', '.join(os.path.basename(f) for f in result['cleaned'])}")
        if result["include_ini"]:
            print(f"  Generated include.ini with module search paths")

        if result["success"]:
            # Move KGen's output into kernel/{function_name}/ for per-function isolation.
            # KGen deposits files directly into kernel/ (which may already contain
            # subdirectories from previous extractions like kernel/saturate/).
            # We move only the loose files into a new per-function subdirectory.
            if result["kernel_dir"] and Path(result["kernel_dir"]).is_dir():
                kernel_root = Path(result["kernel_dir"])
                per_func_dir = kernel_root / args.function_name
                if kernel_root.resolve() != per_func_dir.resolve():
                    if per_func_dir.exists():
                        shutil.rmtree(per_func_dir)
                    per_func_dir.mkdir(parents=True, exist_ok=True)
                    # Move only files (not subdirectories, which are other functions)
                    for item in list(kernel_root.iterdir()):
                        if item.is_file():
                            shutil.move(str(item), str(per_func_dir / item.name))
                    result["kernel_dir"] = str(per_func_dir)

            # Clean up state/ (intermediate artifacts, not needed after extraction)
            state_dir = Path(result.get("state_dir", "") or "state")
            if state_dir.is_dir():
                shutil.rmtree(state_dir)
                result["state_dir"] = None

            cases = result.get("state_cases", 0)
            print(f"✓ Extraction successful")
            print(f"  Kernel: {result['kernel_dir']}")
            if result["state_dir"]:
                print(f"  State:  {result['state_dir']}")
            if cases > 0:
                print(f"  Cases:  {cases}")
            else:
                print(f"  WARNING: No state data captured.")
                print(f"  The simulation may not have run, or the instrumented library")
                print(f"  wasn't installed where the simulation loads it from.")
                print(f"  Set install_cmd in vit.yaml, e.g.:")
                print(f"    install_cmd: cp <build_dir>/libdiscon.so <install_dir>/libdiscon.so")
        else:
            print(f"✗ Extraction failed")
            print(result["output"])

    finally:
        # Always restore the original source file
        # (KGen's state/Makefile also does this, but be safe)
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, file_path)
            os.remove(backup_path)

    return 0 if result["success"] else 1


def cmd_translate(args, config):
    """Generate C++ translation scaffold and translation prompt."""
    from . import __version__

    # Parse function signature
    sig = find_function_in_file(args.file, args.function_name)

    # Determine module name
    module_name = args.module or sig.module_name
    if not module_name:
        # Fall back to filename stem
        module_name = Path(args.file).stem

    derived_types = _build_registry(sig, config)

    # Determine output directory
    if args.output:
        out_dir = Path(args.output)
    else:
        out_dir = Path("translations") / module_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # Generate/update consolidated types header
    struct_header_names = []
    if derived_types:
        from .derived_type_parser import generate_consolidated_types_header
        types_path = Path("translations") / "vit_types.h"
        existing = types_path.read_text() if types_path.exists() else ""
        new_content = generate_consolidated_types_header(
            derived_types, config.kind_aliases, existing)
        if new_content != existing:
            types_path.parent.mkdir(parents=True, exist_ok=True)
            types_path.write_text(new_content)
        struct_header_names = ["vit_types.h"]

    # Generate scaffold
    scaffold = generate_translation_scaffold(
        sig=sig,
        kind_aliases=config.kind_aliases,
        source_file=args.file,
        vit_version=__version__,
        struct_headers=struct_header_names,
        derived_types=derived_types,
    )

    # Write scaffold
    out_path = out_dir / f"{sig.name.lower()}.cpp"
    out_path.write_text(scaffold)

    # Extract Fortran function source for the prompt
    source_text = Path(args.file).read_text()
    from .translate import _extract_function_text
    fortran_text = _extract_function_text(source_text, args.function_name)

    # Load TRANSLATION_TIPS.md if it exists
    tips_text = None
    tips_path = Path("TRANSLATION_TIPS.md")
    if tips_path.is_file():
        tips_text = tips_path.read_text()

    # Generate prompt
    prompt = generate_translation_prompt(
        sig=sig,
        kind_aliases=config.kind_aliases,
        source_file=args.file,
        fortran_source_text=fortran_text,
        scaffold_text=scaffold,
        tips_text=tips_text,
        derived_types=derived_types,
    )

    # Print summary
    _print_signature(sig)
    print(f"\n✓ Scaffold written: {out_path}")
    print(f"  Module: {module_name}")

    # Print prompt
    print(f"\n{'─' * 60}")
    print(f"Translation prompt:")
    print(f"{'─' * 60}\n")
    print(prompt)

    return 0


def cmd_interface(args, config):
    """Generate Fortran↔C interface files."""
    if args.file:
        sig = find_function_in_file(args.file, args.function_name)
    else:
        sig = _find_function_in_project(args.function_name, config)

    _print_signature(sig)

    derived_types = _build_registry(sig, config)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Generate interface block
    iface = generate_fortran_interface_block(sig, config.kind_aliases, derived_types)
    iface_path = out_dir / f"{sig.name.lower()}_interface.f90"
    iface_path.write_text(iface)
    print(f"\n✓ Interface block:  {iface_path}")

    # Generate wrapper function
    wrapper = generate_fortran_wrapper(sig, config.kind_aliases, derived_types)
    wrapper_path = out_dir / f"{sig.name.lower()}_wrapper.f90"
    wrapper_path.write_text(wrapper)
    print(f"✓ Wrapper function: {wrapper_path}")

    # Show what was generated
    print(f"\n--- Interface Block ---")
    print(iface)
    print(f"\n--- Wrapper Function ---")
    print(wrapper)

    return 0


def _format_verify_summary(verify_result) -> str:
    """Format verification result as a human-readable summary line."""
    valid = verify_result.total_cases - verify_result.skipped_cases
    skip_note = ""
    if verify_result.skipped_cases > 0:
        skip_note = f" ({verify_result.skipped_cases} skipped: NaN reference data)"

    if verify_result.success:
        return f"✓ VERIFICATION PASSED: {verify_result.passed_cases}/{valid} passed{skip_note}"
    elif verify_result.total_cases == 0:
        return "✗ VERIFICATION FAILED: no cases found"
    else:
        return f"✗ VERIFICATION FAILED: {verify_result.passed_cases}/{valid} passed{skip_note}"


def cmd_verify(args, config):
    """Run KGen kernel verification with C++ implementation."""
    # Parse the function signature
    if args.file:
        sig = find_function_in_file(args.file, args.function_name)
    else:
        sig = _find_function_in_project(args.function_name, config)

    # Find kernel directory
    kernel_dir = args.kernel_dir
    if not kernel_dir:
        # Look in common locations (per-function first, then legacy flat)
        for candidate in [f"kernel/{args.function_name}", "kernel",
                          "state/../kernel", f"{args.function_name}/kernel"]:
            if os.path.isdir(candidate):
                kernel_dir = candidate
                break

    if not kernel_dir or not os.path.isdir(kernel_dir):
        print("ERROR: Could not find KGen kernel/ directory.")
        print("Run 'vit extract' first, or specify --kernel-dir")
        return 1

    derived_types = _build_registry(sig, config)
    field_log = not getattr(args, 'no_field_log', False)

    print(f"Setting up C++ verification in {kernel_dir}...")
    setup_result = setup_kernel_cpp(
        kernel_dir=kernel_dir,
        sig=sig,
        cpp_impl_path=args.cpp_file,
        kind_aliases=config.kind_aliases,
        field_log=field_log,
        derived_types=derived_types,
        fortran_flags=config.fortran_flags,
        translations=config.translations,
        source_dir=config.abs_source_dir,
    )

    if not setup_result["success"]:
        print(f"✗ Setup failed:")
        for err in setup_result["errors"]:
            print(f"  {err}")
        return 1

    print(f"  Modified {len(setup_result['files_modified'])} files")

    print(f"\nRunning verification...")
    verify_result = run_kernel_verify(kernel_dir, tolerance=config.tolerance,
                                      field_log=field_log, sig=sig)

    print(f"\n{_format_verify_summary(verify_result)}")

    if field_log and verify_result.field_data:
        csv_path = os.path.join(kernel_dir, "verify_fields.csv")
        print(f"  Field log: {csv_path} ({len(verify_result.field_data)} entries)")

    if verify_result.success:
        if verify_result.max_error > 0:
            print(f"  Max error: {verify_result.max_error:.2e} (tolerance: {config.tolerance:.2e})")

        valid = verify_result.total_cases - verify_result.skipped_cases
        record_translation(config, args.function_name, "verified", {
            "cases_passed": verify_result.passed_cases,
            "cases_total": valid,
            "cases_skipped": verify_result.skipped_cases,
            "cpp_file": args.cpp_file,
            "kernel_dir": kernel_dir,
        })
    else:
        for detail in verify_result.details:
            if detail["status"] == "FAILED":
                print(f"  FAILED: {detail['line']}")

    return 0 if verify_result.success else 1


def cmd_integrate(args, config):
    """Generate integration plan."""
    if args.file:
        sig = find_function_in_file(args.file, args.function_name)
    else:
        sig = _find_function_in_project(args.function_name, config)

    derived_types = _build_registry(sig, config)
    cmake_file = config.abs_cmake_file if config.build_type == "cmake" else None
    reverse_copy = getattr(args, 'reverse_copy', False)
    auto_allocate = getattr(args, 'auto_allocate', False)

    plan = generate_integration_plan(
        sig=sig,
        cpp_impl_path=args.cpp_file,
        source_file=sig.source_file,
        cmake_file=cmake_file,
        target_name=config.target_name,
        kind_aliases=config.kind_aliases,
        derived_types=derived_types,
        reverse_copy=reverse_copy,
        auto_allocate=auto_allocate,
        allocatable_helpers=config.allocatable_helpers,
    )

    print(plan.to_markdown())

    if args.apply:
        print("\nApplying integration plan...")
        result = apply_integration_plan(plan, dry_run=args.dry_run)
        for action in result["applied"]:
            print(f"  {action}")
        for error in result["errors"]:
            print(f"  ERROR: {error}")

        if result["success"]:
            print("\n✓ Integration applied successfully")
            extras = {"cpp_file": args.cpp_file}
            if reverse_copy:
                extras["verification"] = "simulation"
            record_translation(config, args.function_name, "integrated", extras)
        else:
            print("\n✗ Some patches could not be applied. Review manually.")

    return 0


def cmd_sim_compare(args, config):
    """Compare simulation output arrays between baseline and integrated builds."""
    result = compare_arrays(
        baseline_dir=args.baseline_dir,
        integrated_dir=args.integrated_dir,
        tolerance=args.tolerance,
    )
    print(format_result(result))
    return 0 if result.passed else 1


def cmd_run(args, config):
    """Full pipeline: parse → verify → integrate."""
    print(f"═══ VIT Full Pipeline: {args.function_name} ═══\n")

    # Step 1: Parse signature
    print("Step 1: Parsing function signature...")
    if args.file:
        sig = find_function_in_file(args.file, args.function_name)
    else:
        sig = _find_function_in_project(args.function_name, config)
    _print_signature(sig)

    derived_types = _build_registry(sig, config)

    # Step 2: Verify (if kernel dir exists and not skipped)
    if not args.skip_verify:
        kernel_dir = args.kernel_dir
        if not kernel_dir:
            for candidate in [f"kernel/{args.function_name}", "kernel",
                              f"{args.function_name}/kernel"]:
                if os.path.isdir(candidate):
                    kernel_dir = candidate
                    break

        if kernel_dir and os.path.isdir(kernel_dir):
            field_log = not getattr(args, 'no_field_log', False)

            print(f"\nStep 2: KGen kernel verification ({kernel_dir})...")
            setup_result = setup_kernel_cpp(
                kernel_dir=kernel_dir,
                sig=sig,
                cpp_impl_path=args.cpp_file,
                kind_aliases=config.kind_aliases,
                field_log=field_log,
                derived_types=derived_types,
                fortran_flags=config.fortran_flags,
            )

            if not setup_result["success"]:
                print(f"✗ Kernel setup failed: {setup_result['errors']}")
                return 1

            verify_result = run_kernel_verify(kernel_dir, tolerance=config.tolerance,
                                              field_log=field_log, sig=sig)
            print(_format_verify_summary(verify_result))

            if field_log and verify_result.field_data:
                csv_path = os.path.join(kernel_dir, "verify_fields.csv")
                print(f"  Field log: {csv_path} ({len(verify_result.field_data)} entries)")

            if not verify_result.success:
                print("Aborting pipeline — verification must pass before integration.")
                return 1
        else:
            print("\nStep 2: Skipped (no kernel/ directory found)")
            print("  Run 'vit extract' first for verified integration")

    # Step 3: Generate integration plan
    print(f"\nStep 3: Generating integration plan...")
    cmake_file = config.abs_cmake_file if config.build_type == "cmake" else None

    plan = generate_integration_plan(
        sig=sig,
        cpp_impl_path=args.cpp_file,
        source_file=sig.source_file,
        cmake_file=cmake_file,
        target_name=config.target_name,
        kind_aliases=config.kind_aliases,
        derived_types=derived_types,
    )

    print(plan.to_markdown())

    if args.apply:
        print("\nStep 4: Applying integration...")
        result = apply_integration_plan(plan)
        for action in result["applied"]:
            print(f"  {action}")

        if result["success"]:
            print("\n═══ ✓ Pipeline complete ═══")
            record_translation(config, args.function_name, "integrated", {
                "cpp_file": args.cpp_file,
                "verified": not args.skip_verify,
            })
        else:
            print("\n═══ ✗ Integration had errors — review manually ═══")
            return 1
    else:
        print("\nRun with --apply to apply these changes.")

    return 0


def cmd_status(args, config):
    """Show translation status."""
    if not config.translations:
        print("No translations recorded yet.")
        print("Use 'vit run <function> <cpp_file>' to translate and verify functions.")
        return 0

    print(f"Project: {config.project_name}")
    print(f"Translations: {len(config.translations)}")
    print()

    for name, info in config.translations.items():
        status = info.get("status", "unknown")
        if status == "verified":
            cases = info.get("cases_passed", "?")
            total = info.get("cases_total", "?")
            print(f"  ✓ {name}: verified ({cases}/{total} cases)")
        elif status == "integrated":
            verified = info.get("verified", False)
            v_str = " (verified)" if verified else " (unverified)"
            print(f"  ✓ {name}: integrated{v_str}")
        else:
            print(f"  ? {name}: {status}")

    return 0


def cmd_analyze_types(args, config):
    """Analyze derived types for C compatibility."""
    source_dir = getattr(args, 'source_dir', None) or config.abs_source_dir

    if not os.path.isdir(source_dir):
        print(f"ERROR: Source directory not found: {source_dir}")
        return 1

    if args.scan_allocatable:
        return _cmd_scan_allocatable(args, config, source_dir)

    if args.fix:
        # Fix mode — bind_c doesn't need --field
        if args.fix == "bind_c":
            if not args.type_name:
                print("ERROR: --type is required with --fix bind_c")
                return 1
        else:
            if not args.type_name or not args.field_name:
                print("ERROR: --type and --field are required with --fix")
                return 1

        dry_run = not args.apply
        if dry_run:
            print(f"Dry run — changes will NOT be written (use --apply to write)")
        if args.fix == "bind_c":
            print(f"Fixing {args.fix}: {args.type_name}")
        else:
            print(f"Fixing {args.fix}: {args.type_name}.{args.field_name}")

        if args.fix == "character":
            # If --length not provided, infer and require confirmation
            fixed_length = args.length
            if fixed_length is None:
                inference = infer_character_length(
                    source_dir, args.type_name, args.field_name
                )
                if inference is not None:
                    inferred_length, evidence = inference
                    if inferred_length is not None:
                        assumed = inferred_length
                        source_info = evidence
                    else:
                        assumed = 1024
                        source_info = f"{evidence} (expression not evaluable; using default 1024)"
                else:
                    assumed = 1024
                    source_info = "no ALLOCATE found; using default 1024"

                assumption = Assumption(
                    id="character_length",
                    summary=f"CHARACTER buffer size for {args.type_name}.{args.field_name}",
                    assumed_value=str(assumed),
                    inferred_from=source_info,
                    context=(
                        f"The field {args.type_name}.{args.field_name} is "
                        f"CHARACTER(:), ALLOCATABLE.\n"
                        f"VIT will convert it to CHARACTER({assumed}).\n"
                        f"Confirm this length is correct for your project."
                    ),
                    cli_override=f"--length {assumed}",
                )
                print_assumption(assumption)
                return EXIT_CONFIRM

            result = fix_character_allocatable(
                source_dir=source_dir,
                type_name=args.type_name,
                field_name=args.field_name,
                fixed_length=fixed_length,
                dry_run=dry_run,
            )

        elif args.fix == "logical":
            result = fix_logical_field(
                source_dir=source_dir,
                type_name=args.type_name,
                field_name=args.field_name,
                dry_run=dry_run,
            )

        elif args.fix == "complex":
            # Parse --rewrite args: "file:line=replacement"
            rewrites = {}
            for r in getattr(args, 'rewrite', []):
                if '=' not in r:
                    print(f"ERROR: --rewrite must be 'file:line=replacement', got: {r}")
                    return 1
                loc, replacement = r.split('=', 1)
                rewrites[loc] = replacement

            result = fix_complex_field(
                source_dir=source_dir,
                type_name=args.type_name,
                field_name=args.field_name,
                dry_run=dry_run,
                rewrites=rewrites if rewrites else None,
            )

        elif args.fix == "bind_c":
            result = fix_bind_c(
                source_dir=source_dir,
                type_name=args.type_name,
                dry_run=dry_run,
            )

        # Manual edits needed — nothing was written, exit 11
        if result.manual_edits:
            review = ManualReview(
                fix_type=args.fix,
                summary=(
                    f"{len(result.manual_edits)} reference(s) need "
                    f"manual rewriting"
                ),
                edits=result.manual_edits,
                changes_applied=0,
            )
            print_manual_review(review)
            return EXIT_MANUAL

        for change in result.changes:
            print(f"  {change}")
        for error in result.errors:
            print(f"  ERROR: {error}")

        if result.success:
            if dry_run:
                print(f"\n{len(result.changes)} change(s) would be applied. Run with --apply to write.")
            else:
                print(f"\n✓ Fixed {len(result.files_modified)} file(s)")

                # Build-verify-fix: use the compiler to catch downstream consequences
                if config.build_cmd:
                    from .build_fixer import build_and_fix
                    print(f"\nVerifying build...")
                    build_result = build_and_fix(
                        build_cmd=config.build_cmd,
                        source_dir=source_dir,
                        compiler=config.fortran_compiler,
                    )
                    for fix in build_result.fixes_applied:
                        print(f"  Auto-fixed: {fix.description}")
                        print(f"    {fix.file}:{fix.line}")
                    if build_result.success:
                        total = len(build_result.fixes_applied)
                        if total > 0:
                            print(f"\n✓ Build succeeded after {total} auto-fix(es)")
                        else:
                            print(f"✓ Build succeeded")
                    else:
                        print(f"\n✗ Build failed with unfixable errors:")
                        for err in build_result.remaining_errors:
                            print(f"  {err.file}:{err.line}: {err.message}")
                        return 1
        else:
            print(f"\n✗ Fix failed")
        return 0 if result.success else 1
    else:
        # Analysis mode
        analyses = analyze_all_types(source_dir, config.kind_aliases)
        verbose = getattr(args, 'verbose', False)
        report = format_analysis_report(analyses, verbose=verbose)
        print(report)
        return 0


# --- Helpers ---

def _build_registry(sig: FortranSignature, config) -> dict:
    """Build a derived type registry for the function's TYPE(...) args.

    Applies type strategies from config (e.g., view pattern for ALLOCATABLE types).
    Pre-computes ALLOCATABLE field mappings for view types so downstream code
    (integrate, verify, translate) can use type_info.field_mappings directly.
    """
    source_dir = config.abs_source_dir
    if source_dir and os.path.isdir(source_dir):
        from .derived_type_parser import apply_type_strategies
        from .analyze_types import scan_allocatable_sizes
        registry = build_derived_type_registry(sig, source_dir)
        apply_type_strategies(registry, config.type_strategies)
        # Pre-compute field mappings for view types
        for name, info in registry.items():
            if getattr(info, 'view_strategy', None) == 'view':
                info.field_mappings = scan_allocatable_sizes(
                    source_dir, name, info,
                    allocatable_helpers=config.allocatable_helpers,
                )
        return registry
    return {}


def _find_function_in_project(function_name: str, config) -> FortranSignature:
    """Search the project source tree for a function definition."""
    source_dir = Path(config.abs_source_dir)

    for f90_file in source_dir.rglob("*.f90"):
        try:
            sig = find_function_in_file(str(f90_file), function_name)
            return sig
        except ValueError:
            continue

    # Also try .F90
    for f90_file in source_dir.rglob("*.F90"):
        try:
            sig = find_function_in_file(str(f90_file), function_name)
            return sig
        except ValueError:
            continue

    raise ValueError(
        f"Could not find function '{function_name}' in {config.abs_source_dir}. "
        f"Use --file to specify the source file directly."
    )


def _print_signature(sig: FortranSignature):
    """Pretty-print a parsed function signature."""
    kind_str = "FUNCTION" if sig.is_function else "SUBROUTINE"
    print(f"\n{kind_str} {sig.name}")
    if sig.source_file:
        print(f"  File: {sig.source_file}:{sig.line_number or '?'}")
    if sig.module_name:
        print(f"  Module: {sig.module_name}")

    print(f"  Arguments:")
    for arg in sig.args:
        intent_str = f" INTENT({arg.intent.value})" if arg.intent.value else ""
        array_str = f"({arg.array_dims})" if arg.is_array else ""
        print(f"    {arg.name}: {arg.fortran_type}{intent_str}{array_str}")

    if sig.is_function:
        result_str = sig.result_name or sig.name
        type_str = sig.result_type or "(type from context)"
        print(f"  Returns: {result_str} :: {type_str}")

    print(f"  C name: {sig.c_func_name}")


def _cmd_scan_allocatable(args, config, source_dir):
    """Handle --scan-allocatable: scan for ALLOCATABLE field size sources."""
    from .derived_type_parser import scan_all_derived_types

    if not args.type_name:
        print("ERROR: --type is required with --scan-allocatable")
        return 1

    # Find the type definition
    all_types = scan_all_derived_types(source_dir)
    type_info = None
    for ti in all_types:
        if ti.name.upper() == args.type_name.upper():
            type_info = ti
            break

    if type_info is None:
        print(f"ERROR: Type '{args.type_name}' not found in {source_dir}")
        return 1

    # Get all ALLOCATABLE field names for unmapped tracking
    alloc_field_names = {
        f.name for f in type_info.fields if f.is_allocatable
    }

    # Scan
    mappings = scan_allocatable_sizes(source_dir, args.type_name, type_info,
                                      allocatable_helpers=config.allocatable_helpers)

    # Determine unmapped
    mapped_names = {m.field_name for m in mappings}
    unmapped = sorted(alloc_field_names - mapped_names)

    # Report
    report = format_allocatable_scan_report(args.type_name, mappings, unmapped)
    print(report)

    # Summary
    confirm_count = sum(1 for m in mappings if m.needs_confirm)
    if confirm_count:
        print(f"  {confirm_count} field(s) need confirmation (Type C)")

    return 0


if __name__ == "__main__":
    sys.exit(main())