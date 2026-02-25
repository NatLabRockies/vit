"""
VIT CLI — Verified Incremental Translator

Usage:
    vit init                        Initialize vit.yaml for a project
    vit extract <function_name>     Extract function with KGen
    vit interface <function> <cpp>  Generate Fortran↔C interface
    vit verify <function>           Build and run KGen kernel verification
    vit integrate <function>        Generate codebase integration plan
    vit run <function> <cpp_file>   Full pipeline (interface → verify → integrate)

The translation step is intentionally NOT part of this tool.
Use CodeScribe, any LLM, or hand-write the C++ — then feed it to VIT.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from .config import load_config, init_config, save_config, record_translation
from .fortran_parser import find_function_in_file, FortranSignature
from .interface_gen import (
    generate_cpp_wrapper,
    generate_fortran_interface_block,
    generate_fortran_wrapper,
)
from .extract import find_call_sites, insert_kgen_pragma, run_kgen_extract
from .kernel_verify import setup_kernel_cpp, run_kernel_verify
from .integrate import generate_integration_plan, apply_integration_plan


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

    # --- integrate ---
    p_integrate = subparsers.add_parser("integrate", help="Generate integration plan")
    p_integrate.add_argument("function_name", help="Function to integrate")
    p_integrate.add_argument("cpp_file", help="Path to C++ implementation")
    p_integrate.add_argument("--file", "-f", help="Fortran source file")
    p_integrate.add_argument("--apply", action="store_true", help="Apply changes")
    p_integrate.add_argument("--dry-run", action="store_true")

    # --- run (full pipeline) ---
    p_run = subparsers.add_parser("run", help="Full pipeline: interface → verify → integrate")
    p_run.add_argument("function_name", help="Function to translate")
    p_run.add_argument("cpp_file", help="Path to C++ implementation")
    p_run.add_argument("--file", "-f", help="Fortran source file")
    p_run.add_argument("--kernel-dir", help="KGen kernel/ directory")
    p_run.add_argument("--skip-verify", action="store_true")
    p_run.add_argument("--apply", action="store_true")

    # --- status ---
    p_status = subparsers.add_parser("status", help="Show translation status")

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
        elif args.command == "interface":
            return cmd_interface(args, config)
        elif args.command == "verify":
            return cmd_verify(args, config)
        elif args.command == "integrate":
            return cmd_integrate(args, config)
        elif args.command == "run":
            return cmd_run(args, config)
        elif args.command == "status":
            return cmd_status(args, config)
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

    print(f"Inserting KGen pragma at {file_path}:{line_num}")
    insert_kgen_pragma(file_path, line_num)

    print(f"Running KGen extraction...")
    result = run_kgen_extract(
        source_file=file_path,
        build_cmd=config.build_cmd,
        run_cmd=config.run_cmd,
        clean_cmd=config.clean_cmd,
        kgen_path=config.kgen_path,
        dry_run=args.dry_run,
    )

    if result["success"]:
        print(f"✓ Extraction successful")
        print(f"  Kernel: {result['kernel_dir']}")
        print(f"  State:  {result['state_dir']}")
    else:
        print(f"✗ Extraction failed")
        print(result["output"])
        return 1

    return 0


def cmd_interface(args, config):
    """Generate Fortran↔C interface files."""
    if args.file:
        sig = find_function_in_file(args.file, args.function_name)
    else:
        sig = _find_function_in_project(args.function_name, config)

    _print_signature(sig)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Generate interface block
    iface = generate_fortran_interface_block(sig, config.kind_aliases)
    iface_path = out_dir / f"{sig.name.lower()}_interface.f90"
    iface_path.write_text(iface)
    print(f"\n✓ Interface block:  {iface_path}")

    # Generate wrapper function
    wrapper = generate_fortran_wrapper(sig, config.kind_aliases)
    wrapper_path = out_dir / f"{sig.name.lower()}_wrapper.f90"
    wrapper_path.write_text(wrapper)
    print(f"✓ Wrapper function: {wrapper_path}")

    # Show what was generated
    print(f"\n--- Interface Block ---")
    print(iface)
    print(f"\n--- Wrapper Function ---")
    print(wrapper)

    return 0


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
        # Look in common locations
        for candidate in ["kernel", "state/../kernel", f"{args.function_name}/kernel"]:
            if os.path.isdir(candidate):
                kernel_dir = candidate
                break

    if not kernel_dir or not os.path.isdir(kernel_dir):
        print("ERROR: Could not find KGen kernel/ directory.")
        print("Run 'vit extract' first, or specify --kernel-dir")
        return 1

    print(f"Setting up C++ verification in {kernel_dir}...")
    setup_result = setup_kernel_cpp(
        kernel_dir=kernel_dir,
        sig=sig,
        cpp_impl_path=args.cpp_file,
        kind_aliases=config.kind_aliases,
    )

    if not setup_result["success"]:
        print(f"✗ Setup failed:")
        for err in setup_result["errors"]:
            print(f"  {err}")
        return 1

    print(f"  Modified {len(setup_result['files_modified'])} files")

    print(f"\nRunning verification...")
    verify_result = run_kernel_verify(kernel_dir, tolerance=config.tolerance)

    if verify_result.success:
        print(f"\n✓ VERIFICATION PASSED: {verify_result.passed_cases}/{verify_result.total_cases} cases")
        if verify_result.max_error > 0:
            print(f"  Max error: {verify_result.max_error:.2e} (tolerance: {config.tolerance:.2e})")

        record_translation(config, args.function_name, "verified", {
            "cases_passed": verify_result.passed_cases,
            "cases_total": verify_result.total_cases,
            "cpp_file": args.cpp_file,
        })
    else:
        print(f"\n✗ VERIFICATION FAILED: {verify_result.passed_cases}/{verify_result.total_cases} passed")
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

    cmake_file = config.abs_cmake_file if config.build_type == "cmake" else None

    plan = generate_integration_plan(
        sig=sig,
        cpp_impl_path=args.cpp_file,
        source_file=sig.source_file,
        cmake_file=cmake_file,
        target_name=config.target_name,
        kind_aliases=config.kind_aliases,
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
            record_translation(config, args.function_name, "integrated", {
                "cpp_file": args.cpp_file,
            })
        else:
            print("\n✗ Some patches could not be applied. Review manually.")

    return 0


def cmd_run(args, config):
    """Full pipeline: parse → interface → verify → integrate."""
    print(f"═══ VIT Full Pipeline: {args.function_name} ═══\n")

    # Step 1: Parse signature
    print("Step 1: Parsing function signature...")
    if args.file:
        sig = find_function_in_file(args.file, args.function_name)
    else:
        sig = _find_function_in_project(args.function_name, config)
    _print_signature(sig)

    # Step 2: Verify (if kernel dir exists and not skipped)
    if not args.skip_verify:
        kernel_dir = args.kernel_dir
        if not kernel_dir:
            for candidate in ["kernel", f"{args.function_name}/kernel"]:
                if os.path.isdir(candidate):
                    kernel_dir = candidate
                    break

        if kernel_dir and os.path.isdir(kernel_dir):
            print(f"\nStep 2: KGen kernel verification ({kernel_dir})...")
            setup_result = setup_kernel_cpp(
                kernel_dir=kernel_dir,
                sig=sig,
                cpp_impl_path=args.cpp_file,
                kind_aliases=config.kind_aliases,
            )

            if not setup_result["success"]:
                print(f"✗ Kernel setup failed: {setup_result['errors']}")
                return 1

            verify_result = run_kernel_verify(kernel_dir, tolerance=config.tolerance)

            if verify_result.success:
                print(f"✓ PASSED: {verify_result.passed_cases}/{verify_result.total_cases}")
            else:
                print(f"✗ FAILED: {verify_result.passed_cases}/{verify_result.total_cases}")
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


# --- Helpers ---

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


if __name__ == "__main__":
    sys.exit(main())
