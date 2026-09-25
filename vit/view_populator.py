"""
Generate Fortran BIND(C) populator subroutines for view structs.

A populator fills a C view struct from a real Fortran derived type:
- Scalars are copied with kind conversion
- ALLOCATABLE arrays are exposed as C_LOC pointers + SIZE dimensions
- ALLOCATED() checks handle optional arrays gracefully
- Nested C-compatible TYPE() fields are embedded and copied field-by-field
- ALLOCATABLE TYPE() fields with view-strategy children are populated as arrays of child view structs
- ALLOCATABLE TYPE() fields without view-strategy children remain opaque (C_NULL_PTR + SIZE)

The populator lives in a Fortran MODULE that also contains a BIND(C)
mirror type matching the C view struct layout exactly.
"""

import re
from typing import Optional

from .derived_type_parser import DerivedTypeInfo, DerivedTypeField, _extract_fixed_sizes
from .type_map import get_type_mapping, is_deferred_character

# How many bytes beyond the field's current LEN the C++ side may write into a
# deferred-length CHARACTER staging buffer.
#
# A number, and therefore a limit -- there is no way to give C++ an unbounded
# Fortran allocation through a raw pointer. What matters is that the limit is
# STATED to both sides rather than assumed: the populator publishes it as
# `n_<field>_cap`, and the reverse-copy re-checks it and REFUSES a longer
# assignment instead of truncating. A truncated error message is a plausible
# wrong answer, which is the one kind of wrong answer a bit-comparison cannot
# distinguish from a right one.
#
# 4096 is chosen against the shape these fields actually have: a diagnostic
# string that grows by a routine-name prefix per call frame. It is not derived
# from anything, and if a unit ever needs more, the reverse-copy says so.
VIT_DEFERRED_CHAR_HEADROOM = 4096


def _deferred_char_buffer(type_name_lower: str, field_name: str) -> str:
    """Name of the module-level staging buffer for one deferred-length field.

    Qualified by BOTH the type and the field: two view modules for two types
    that happen to share a field name are compiled into the same program.
    """
    return f"vit_defchar_{field_name.lower()}_{type_name_lower}"


def _lookup_type(registry: dict, type_name: str) -> Optional[DerivedTypeInfo]:
    """Case-insensitive lookup in a type registry."""
    upper = type_name.upper()
    for k, v in registry.items():
        if k.upper() == upper:
            return v
    return None


def _is_nested_embeddable(f: DerivedTypeField, registry: Optional[dict]) -> bool:
    """Check if a nested TYPE() field can be embedded in a BIND(C) mirror type."""
    if not f.fortran_type.upper().startswith("TYPE("):
        return False
    if not registry:
        return False
    nested_name = f.fortran_type[5:-1].strip()
    nested_info = _lookup_type(registry, nested_name)
    if nested_info is None:
        return False
    return nested_info.is_c_compatible_recursive(registry)


# Kinds that resolve through the header's own UNQUALIFIED `USE ISO_C_BINDING`
# and `USE ISO_FORTRAN_ENV`. Importing these from the source module would be
# wrong, and they usually are not there to import.
_INTRINSIC_KINDS = {
    "C_CHAR", "C_INT", "C_SHORT", "C_LONG", "C_LONG_LONG", "C_SIGNED_CHAR",
    "C_FLOAT", "C_DOUBLE", "C_LONG_DOUBLE", "C_BOOL", "C_SIZE_T",
    "C_PTR", "C_FUNPTR", "C_INT8_T", "C_INT16_T", "C_INT32_T", "C_INT64_T",
    "REAL32", "REAL64", "REAL128", "INT8", "INT16", "INT32", "INT64",
}


def _kind_symbol(fortran_type: str) -> Optional[str]:
    """The KIND parameter a declaration NAMES, or None if it names no symbol.

    `_direct_alloc_decls` writes a field's declared type verbatim into
    `<type>, POINTER :: vit_ap_<f>(:)`, so whatever kind that type carries has
    to be in scope in the GENERATED module -- and the header imports only the
    derived type. `REAL(8)` and `CHARACTER(LEN=:)` name nothing; `REAL(R8)` and
    `INTEGER(IntKi)` name a parameter of the source module.
    """
    m = re.match(r"^\s*[A-Za-z_]+\s*\(([^)]*)\)\s*$", fortran_type.strip())
    if not m:
        return None
    inner = m.group(1).strip()
    if inner.upper().startswith("KIND"):
        inner = inner.split("=", 1)[-1].strip()
    if not re.fullmatch(r"[A-Za-z_]\w*", inner):
        return None                       # a literal, `:`, or an expression
    if inner.upper() in _INTRINSIC_KINDS:
        return None
    return inner


def generate_view_populator(
    type_info: DerivedTypeInfo,
    field_mappings: list,
    kind_aliases: Optional[dict] = None,
    source_module: Optional[str] = None,
    registry: Optional[dict] = None,
) -> str:
    """
    Generate a Fortran module containing a BIND(C) mirror type and
    populator subroutine for a view struct.

    The populator:
    1. Receives the real Fortran TYPE and the BIND(C) mirror view struct
    2. Copies scalars with kind conversion (INT, REAL)
    3. Uses C_LOC for ALLOCATABLE arrays, SIZE for dimensions
    4. Wraps ALLOCATABLE access in IF (ALLOCATED(...))
    5. Embeds nested C-compatible TYPE() fields and copies field-by-field
    6. Treats ALLOCATABLE TYPE() fields as opaque (C_NULL_PTR + SIZE)

    Args:
        type_info: Parsed derived type definition
        field_mappings: List of AllocatableFieldMapping from scan_allocatable_sizes()
        kind_aliases: Project-specific kind aliases
        source_module: Fortran module that defines the type (e.g., "ROSCO_Types").
                       Required — the populator needs USE <module>, ONLY: <type>.
        registry: Dict mapping type_name → DerivedTypeInfo for nested type lookup.
                  When provided, nested C-compatible TYPE() fields are embedded in
                  the mirror type and their fields are copied in the populator.

    Returns:
        Complete Fortran module source as a string
    """
    name = type_info.name
    name_lower = name.lower()
    view_type_name = f"{name_lower}_view_t"
    module_name = f"vit_{name_lower}_view"
    sub_name = f"vit_populate_{name_lower}"

    # Build lookup for ALLOCATABLE field mappings
    mapping_by_name = {m.field_name: m for m in field_mappings}

    # Classify fields
    alloc_names = {f.name for f in type_info.fields if f.is_allocatable}
    pointer_names = {f.name for f in type_info.fields if f.is_pointer}
    alloc_fields = [f for f in type_info.fields if f.is_allocatable]
    # Deferred-length CHARACTER fields are ALLOCATABLE too, but they are scalars
    # whose allocation carries a LENGTH rather than an extent, so every rank-
    # driven branch below is wrong for them and they are handled on their own.
    deferred_char_fields = [f for f in alloc_fields
                            if is_deferred_character(f.fortran_type, kind_aliases)]

    # Identify embeddable nested types (non-ALLOCATABLE TYPE() fields)
    embeddable_nested = {}  # field_name → DerivedTypeInfo
    for f in type_info.fields:
        if f.is_allocatable or f.is_pointer:
            continue
        if _is_nested_embeddable(f, registry):
            nested_name = f.fortran_type[5:-1].strip()
            embeddable_nested[f.name] = _lookup_type(registry, nested_name)

    # Identify ALLOCATABLE TYPE() fields whose inner type is a view type.
    # These get an allocate+populate loop instead of opaque C_NULL_PTR.
    view_type_alloc_fields = {}  # field_name → DerivedTypeInfo
    c_compat_type_alloc_fields = {}  # field_name → DerivedTypeInfo (C-compatible, direct C_LOC)
    for f in alloc_fields:
        if not f.fortran_type.upper().startswith("TYPE("):
            continue
        nested_name = f.fortran_type[5:-1].strip()
        nested_info = _lookup_type(registry, nested_name) if registry else None
        if nested_info and getattr(nested_info, 'view_strategy', None) == 'view':
            view_type_alloc_fields[f.name] = nested_info
        elif nested_info and nested_info.is_c_compatible_recursive(registry or {}):
            c_compat_type_alloc_fields[f.name] = nested_info

    # Identify non-ALLOCATABLE fixed-size TYPE() array fields whose inner type is a view type.
    view_type_fixed_fields = {}  # field_name → (DerivedTypeInfo, list[int])
    for f in type_info.fields:
        if f.is_allocatable or f.is_pointer:
            continue
        if not f.is_array or not f.array_dims:
            continue
        if not f.fortran_type.upper().startswith("TYPE("):
            continue
        if f.name in embeddable_nested:
            continue
        nested_name = f.fortran_type[5:-1].strip()
        nested_info = _lookup_type(registry, nested_name) if registry else None
        if nested_info and getattr(nested_info, 'view_strategy', None) == 'view':
            sizes = _extract_fixed_sizes(f.array_dims)
            view_type_fixed_fields[f.name] = (nested_info, sizes)

    _deferred_names = {f.name for f in deferred_char_fields}

    def _direct_alloc_ok(f) -> bool:
        if f.name in _deferred_names or f.is_pointer or not f.is_allocatable:
            return False
        if f.fortran_type.upper().startswith("TYPE("):
            return False
        m = mapping_by_name.get(f.name)
        rank = m.rank if m else (f.array_dims.count(",") + 1 if f.array_dims else 1)
        return rank == 1

    _direct_alloc = [f for f in type_info.fields if _direct_alloc_ok(f)]
    _direct_alloc_names = {f.name for f in _direct_alloc}

    # RANK 2, and the shape IS recoverable -- which is the reason the rank-1
    # bound was written and the reason it moves here. The comment further down
    # says "a higher-rank field's shape is not recoverable from [one extent]
    # without the per-dimension extents the struct spells differently per
    # field"; the struct spells them `n_<f>_rows` and `n_<f>_cols`, uniformly,
    # and the FORWARD populator already reads them back out of
    # `SIZE(src%<f>, 1)` and `SIZE(src%<f>, 2)`. Column-major on both sides, so
    # the buffer needs no transposition -- the C comment on the field says
    # `// column-major` for the same reason.
    def _direct_alloc2_ok(f) -> bool:
        if f.name in _deferred_names or f.is_pointer or not f.is_allocatable:
            return False
        if f.fortran_type.upper().startswith("TYPE("):
            return False
        m = mapping_by_name.get(f.name)
        rank = m.rank if m else (f.array_dims.count(",") + 1 if f.array_dims else 1)
        return rank == 2

    _direct_alloc2 = [f for f in type_info.fields if _direct_alloc2_ok(f)]
    _direct_alloc2_names = {f.name for f in _direct_alloc2}

    # The ADOPT path's field kinds: ALLOCATABLE arrays of intrinsic numeric or
    # LOGICAL type, rank 1..3 -- the ranks whose extents the view struct
    # spells. CHARACTER arrays are excluded: their element length is not in the
    # struct. Everything else that can appear in a view is still CHECKED by
    # vit_adopt_<t> (a replaced pointer on it is refused), just not adopted.
    def _field_rank(f) -> int:
        m = mapping_by_name.get(f.name)
        return m.rank if m else (f.array_dims.count(",") + 1 if f.array_dims else 1)

    _adoptable = [
        f for f in type_info.fields
        if f.is_allocatable and not f.is_pointer and f.name not in _deferred_names
        and not f.fortran_type.upper().startswith(("TYPE(", "CHARACTER"))
        and 1 <= _field_rank(f) <= 3
    ]

    lines = []
    lines.append(f"! Auto-generated by VIT (Verified Incremental Translator)")
    lines.append(f"! View populator for TYPE({name})")
    lines.append(f"! DO NOT EDIT — regenerate with: vit analyze-types --scan-allocatable --type {name}")
    lines.append(f"")
    lines.append(f"MODULE {module_name}")
    lines.append(f"    USE ISO_C_BINDING")
    if deferred_char_fields or _direct_alloc or _direct_alloc2:
        # The reverse-copy REFUSES an over-long assignment rather than
        # truncating it, and says so. Refusing in silence would be the same
        # defect one level down. `_direct_alloc`'s write-back refuses a changed
        # extent for the same reason, so it needs the same unit.
        lines.append(f"    USE ISO_FORTRAN_ENV, ONLY: ERROR_UNIT")
    if source_module:
        # THE KINDS THE POPULATOR'S OWN DECLARATIONS NAME, not just the type.
        # `_direct_alloc_decls` emits `REAL(R8), POINTER :: vit_ap_v(:)`, and
        # `ONLY: <type>` left R8 unresolvable -- gfortran reports
        # `Symbol 'r8' has no IMPLICIT type` and the pointer is then untyped
        # too, which reads like a missing declaration and is not one. Masked
        # for weeks because the only field to take this path in rosco-r2 was a
        # CHARACTER, rewritten below to CHARACTER(KIND=C_CHAR), whose kind
        # comes from ISO_C_BINDING.
        _kinds = []
        for f in _direct_alloc + _direct_alloc2 + _adoptable:
            if f.fortran_type.strip().upper().startswith("CHARACTER"):
                continue                  # rewritten to KIND=C_CHAR
            k = _kind_symbol(f.fortran_type)
            if k and k not in _kinds and k.lower() != name.lower():
                _kinds.append(k)
        _imports = ", ".join([name] + _kinds)
        lines.append(f"    USE {source_module}, ONLY: {_imports}")
    # USE child populator modules for ALLOCATABLE TYPE() view fields
    for field_name, child_info in view_type_alloc_fields.items():
        child_lower = child_info.name.lower()
        child_view_type = f"{child_lower}_view_t"
        child_populate_sub = f"vit_populate_{child_lower}"
        child_module = f"vit_{child_lower}_view"
        lines.append(f"    USE {child_module}, ONLY: {child_view_type}, {child_populate_sub}, &")
        lines.append(f"        vit_copy_scalars_to_{child_lower}, vit_adopt_{child_lower}")
    # USE child populator modules for fixed-size TYPE() view array fields
    for field_name, (child_info, sizes) in view_type_fixed_fields.items():
        child_lower = child_info.name.lower()
        child_module = f"vit_{child_lower}_view"
        child_view_type = f"{child_lower}_view_t"
        child_populate = f"vit_populate_{child_lower}"
        child_reverse = f"vit_copy_scalars_to_{child_lower}"
        lines.append(f"    USE {child_module}, ONLY: {child_view_type}, {child_populate}, {child_reverse}")
    lines.append(f"    IMPLICIT NONE")
    lines.append(f"    PRIVATE")
    stash_name = f"vit_original_{name_lower}"
    reverse_name = f"vit_copy_scalars_to_{name_lower}"
    direct_stash = f"vit_direct_{name_lower}"
    direct_in = f"vit_view_in_{name_lower}"
    direct_out = f"vit_view_out_{name_lower}"
    lines.append(f"    PUBLIC :: {view_type_name}, {sub_name}, {reverse_name}, {stash_name}")
    lines.append(f"    PUBLIC :: {direct_stash}, {direct_in}, {direct_out}")
    lines.append(f"    PUBLIC :: vit_snapshot_{name_lower}, vit_adopt_{name_lower}")
    lines.append(f"")
    # libc free(), for buffers a C++ translation allocated and this module
    # adopts. An interface to libc rather than a VIT-defined symbol, so linking
    # a view module never adds an undefined reference a build has to satisfy.
    lines.append(f"    INTERFACE")
    lines.append(f"        SUBROUTINE vit_c_free(p) BIND(C, NAME='free')")
    lines.append(f"            IMPORT :: C_PTR")
    lines.append(f"            TYPE(C_PTR), VALUE :: p")
    lines.append(f"        END SUBROUTINE vit_c_free")
    lines.append(f"    END INTERFACE")
    lines.append(f"")
    # The 32-byte header vit_adopt_alloc_lb (vit_types.h) writes in front of a
    # buffer: the lower bounds the C++ stated, and the base address to free.
    lines.append(f"    TYPE, BIND(C) :: vit_adopt_header_t")
    lines.append(f"        INTEGER(C_INT64_T) :: magic")
    lines.append(f"        TYPE(C_PTR) :: base")
    lines.append(f"        INTEGER(C_INT32_T) :: rank")
    lines.append(f"        INTEGER(C_INT32_T) :: lb(3)")
    lines.append(f"    END TYPE vit_adopt_header_t")
    lines.append(f"    INTEGER(C_INT64_T), PARAMETER :: VIT_ADOPT_MAGIC = INT(Z'5649544144505431', C_INT64_T)")
    lines.append(f"")
    lines.append(f"    ! Kernel stash: pointer to original Fortran type, set by wrapper before calling C++.")
    lines.append(f"    ! Callee bridges USE this to pass the original type to Fortran callees.")
    lines.append(f"    TYPE({name}), POINTER, SAVE :: {stash_name} => NULL()")
    lines.append(f"")
    lines.append(f"    ! DIRECT-CALLER ORIGINAL. The stash above is set by an integration or")
    lines.append(f"    ! kernel WRAPPER before it calls the C++. A C++ caller with NO wrapper --")
    lines.append(f"    ! the differential harness calls the translation directly -- leaves it")
    lines.append(f"    ! NULL, and a callee bridge that passes it dereferences a null pointer.")
    lines.append(f"    ! This is the object the bridge points it at in that case.")
    lines.append(f"    TYPE({name}), TARGET, SAVE :: {direct_stash}")
    # Module-level SAVE arrays for ALLOCATABLE TYPE() view fields
    for field_name, child_info in view_type_alloc_fields.items():
        child_lower = child_info.name.lower()
        child_view_type = f"{child_lower}_view_t"
        save_array_name = f"vit_{field_name.lower()}_views_{name_lower}"
        lines.append(f"    TYPE({child_view_type}), ALLOCATABLE, TARGET, SAVE :: {save_array_name}(:)")
        # Pre-call copy of the child views, taken by vit_snapshot_<t>: what the
        # adopt path compares against to tell a child the C++ touched from one
        # it did not.
        lines.append(f"    TYPE({child_view_type}), ALLOCATABLE, SAVE :: "
                     f"vit_{field_name.lower()}_snaps_{name_lower}(:)")
    # Module-level SAVE staging buffers for deferred-length CHARACTER fields.
    #
    # `CHARACTER(:), ALLOCATABLE` has no C type, because its LENGTH is part of
    # its allocation: assigning a longer string to it reallocates it. C++ cannot
    # reallocate Fortran's storage, so the field crosses as a buffer Fortran
    # owns, plus the length the C++ wants, and the REVERSE-COPY does the
    # reallocation. This is that buffer.
    #
    # It cannot be `C_LOC(src%<f>(1:1))` directly onto the real field: that would
    # give the C++ exactly LEN() bytes and no room to write a longer answer, so
    # the field would be readable and not writable, and the branch that grows an
    # error message could not be translated at all.
    #
    # SAVE, and one per field rather than one per call, because the C view struct
    # holds a raw pointer that must stay valid for the whole of the C++ call.
    if deferred_char_fields:
        lines.append(f"")
        lines.append(f"    ! Staging buffers for deferred-length CHARACTER fields. The view's")
        lines.append(f"    ! pointer aims here, not at the Fortran field, so the C++ has room to")
        lines.append(f"    ! write a LONGER string than it was given; {reverse_name} performs the")
        lines.append(f"    ! reallocating assignment back onto the real type.")
        lines.append(f"    !")
        lines.append(f"    ! HEADROOM is what the C++ may add. It is finite and therefore a limit,")
        lines.append(f"    ! so it is REPORTED to the C++ as n_<field>_cap and CHECKED again in the")
        lines.append(f"    ! reverse copy -- a request that does not fit is refused, never")
        lines.append(f"    ! truncated. Truncating would produce a shorter-but-plausible string")
        lines.append(f"    ! that no value comparison could tell from a correct one.")
        lines.append(f"    INTEGER, PARAMETER :: VIT_DEFERRED_CHAR_HEADROOM = "
                     f"{VIT_DEFERRED_CHAR_HEADROOM}")
    for f in deferred_char_fields:
        buf = _deferred_char_buffer(name_lower, f.name)
        lines.append(f"    CHARACTER(LEN=:), ALLOCATABLE, TARGET, SAVE :: {buf}")
    lines.append(f"")

    # --- Nested BIND(C) types (emitted before the main mirror type) ---
    for field_name, nested_info in embeddable_nested.items():
        nested_view_name = f"{nested_info.name.lower()}_view_t"
        lines.append(f"    ! Nested BIND(C) mirror for TYPE({nested_info.name})")
        lines.append(f"    TYPE, BIND(C) :: {nested_view_name}")
        for nf in nested_info.fields:
            if nf.fortran_type.upper().startswith("TYPE("):
                continue  # Skip deeply nested types
            if nf.is_allocatable or nf.is_pointer:
                continue
            iso_decl = _field_to_iso_c_decl(nf, kind_aliases)
            if iso_decl:
                lines.append(f"        {iso_decl}")
        lines.append(f"    END TYPE {nested_view_name}")
        lines.append(f"")

    # --- BIND(C) mirror type ---
    lines.append(f"    ! Mirror of the C view struct — layout MUST match {view_type_name} in C header")
    lines.append(f"    TYPE, BIND(C) :: {view_type_name}")

    # Scalar/fixed fields
    for f in type_info.fields:
        if f.is_allocatable or f.is_pointer:
            continue
        if f.fortran_type.upper().startswith("TYPE("):
            if f.name in embeddable_nested:
                nested_info = embeddable_nested[f.name]
                nested_view_name = f"{nested_info.name.lower()}_view_t"
                if f.is_array and f.array_dims:
                    fortran_size = _extract_fixed_sizes(f.array_dims)[0]
                    lines.append(f"        TYPE({nested_view_name}) :: {f.name}({fortran_size})")
                else:
                    lines.append(f"        TYPE({nested_view_name}) :: {f.name}")
            elif f.name in view_type_fixed_fields:
                child_info, sizes = view_type_fixed_fields[f.name]
                child_view_name = f"{child_info.name.lower()}_view_t"
                lines.append(f"        TYPE({child_view_name}) :: {f.name}({sizes[0]})")
            else:
                lines.append(f"        ! TYPE({f.fortran_type[5:-1].strip()}) {f.name} — nested type, not embeddable")
            continue
        iso_decl = _field_to_iso_c_decl(f, kind_aliases)
        if iso_decl:
            lines.append(f"        {iso_decl}")

    # ALL ALLOCATABLE fields as TYPE(C_PTR) + INTEGER(C_INT32_T) size
    if alloc_fields:
        lines.append(f"        ! ALLOCATABLE fields (pointer + size)")
    for f in alloc_fields:
        m = mapping_by_name.get(f.name)
        rank = m.rank if m else (f.array_dims.count(",") + 1 if f.array_dims else 1)
        lines.append(f"        TYPE(C_PTR) :: {f.name} = C_NULL_PTR")
        if f in deferred_char_fields:
            # Deferred-length CHARACTER: a length and a capacity, not an extent.
            lines.append(f"        INTEGER(C_INT32_T) :: n_{f.name} = 0")
            lines.append(f"        INTEGER(C_INT32_T) :: n_{f.name}_cap = 0")
            continue
        if rank == 1:
            lines.append(f"        INTEGER(C_INT32_T) :: n_{f.name} = 0")
        elif rank == 2:
            lines.append(f"        INTEGER(C_INT32_T) :: n_{f.name}_rows = 0")
            lines.append(f"        INTEGER(C_INT32_T) :: n_{f.name}_cols = 0")
        elif rank >= 3:
            lines.append(f"        INTEGER(C_INT32_T) :: n_{f.name}_dim1 = 0")
            lines.append(f"        INTEGER(C_INT32_T) :: n_{f.name}_dim2 = 0")
            lines.append(f"        INTEGER(C_INT32_T) :: n_{f.name}_dim3 = 0")

    lines.append(f"    END TYPE {view_type_name}")
    lines.append(f"")
    lines.append(f"CONTAINS")
    lines.append(f"")

    # --- Populator subroutine ---
    lines.append(f"    SUBROUTINE {sub_name}(src, view)")
    lines.append(f"        TYPE({name}), INTENT(IN), TARGET :: src")
    lines.append(f"        TYPE({view_type_name}), INTENT(OUT) :: view")
    if view_type_alloc_fields:
        lines.append(f"        INTEGER :: vit_i")
    if view_type_fixed_fields:
        lines.append(f"        INTEGER :: vit_k")
    lines.append(f"")

    # Copy scalars
    lines.append(f"        ! --- Scalars ---")
    for f in type_info.fields:
        if f.is_allocatable or f.is_pointer:
            continue
        if f.fortran_type.upper().startswith("TYPE("):
            if f.name in embeddable_nested:
                nested_info = embeddable_nested[f.name]
                lines.append(f"        ! --- Nested TYPE({nested_info.name}) {f.name} ---")
                for nf in nested_info.fields:
                    if nf.fortran_type.upper().startswith("TYPE("):
                        continue
                    if nf.is_allocatable or nf.is_pointer:
                        continue
                    assign = _nested_scalar_assignment(f.name, nf, kind_aliases)
                    if assign:
                        lines.append(f"        {assign}")
            continue
        assign = _scalar_assignment(f, kind_aliases)
        if assign:
            lines.append(f"        {assign}")

    # Fixed-size TYPE() view array fields
    if view_type_fixed_fields:
        lines.append(f"")
        lines.append(f"        ! --- Fixed-size TYPE() view arrays ---")
        for field_name, (child_info, sizes) in view_type_fixed_fields.items():
            child_populate = f"vit_populate_{child_info.name.lower()}"
            lines.append(f"        DO vit_k = 1, {sizes[0]}")
            lines.append(f"            CALL {child_populate}(src%{field_name}(vit_k), view%{field_name}(vit_k))")
            lines.append(f"        END DO")

    lines.append(f"")

    # ALL ALLOCATABLE arrays
    if alloc_fields:
        lines.append(f"        ! --- ALLOCATABLE arrays ---")
    for f in alloc_fields:
        m = mapping_by_name.get(f.name)
        rank = m.rank if m else (f.array_dims.count(",") + 1 if f.array_dims else 1)

        # ALLOCATABLE TYPE() handling
        is_type_alloc = f.fortran_type.upper().startswith("TYPE(")
        is_view_type_alloc = f.name in view_type_alloc_fields

        # Deferred-length CHARACTER: copy into the staging buffer, aim the view
        # at THAT, and publish both the current length and the writable
        # capacity. The buffer is reallocated only when it is too small, so a
        # repeated call does not churn the heap and the pointer the C++ holds
        # from one call is not silently reused across a resize.
        if f in deferred_char_fields:
            buf = _deferred_char_buffer(name_lower, f.name)
            lines.append(f"        IF (ALLOCATED(src%{f.name})) THEN")
            lines.append(f"            IF (ALLOCATED({buf})) THEN")
            lines.append(f"                IF (LEN({buf}) < LEN(src%{f.name}) + VIT_DEFERRED_CHAR_HEADROOM) &")
            lines.append(f"                    DEALLOCATE({buf})")
            lines.append(f"            END IF")
            lines.append(f"            IF (.NOT. ALLOCATED({buf})) &")
            lines.append(f"                ALLOCATE(CHARACTER(LEN=LEN(src%{f.name}) + VIT_DEFERRED_CHAR_HEADROOM) :: {buf})")
            # SUBSTRING on the left, not the whole variable. `buf = src%<f>` is
            # a whole-variable assignment to a deferred-length ALLOCATABLE, so
            # Fortran 2003 AUTOMATIC REALLOCATION applies and buf comes back out
            # at LEN(src%<f>) -- the headroom allocated one line above is
            # silently discarded, and the capacity published to the C++ is a
            # lie in the dangerous direction. Measured, not reasoned about: the
            # generator's round-trip test reported cap=5 where 4101 was
            # allocated, and the grow case wrote 18 bytes into 4.
            #
            # Assignment to a SUBSTRING is not a reallocating assignment. It
            # blank-pads to the substring's length, which is what is wanted.
            lines.append(f"            {buf}(1:LEN({buf})) = src%{f.name}   ! blank-pads; substring so it does not reallocate")
            lines.append(f"            view%{f.name} = C_LOC({buf}(1:1))")
            lines.append(f"            view%n_{f.name} = INT(LEN(src%{f.name}), C_INT32_T)")
            lines.append(f"            view%n_{f.name}_cap = INT(LEN({buf}), C_INT32_T)")
            lines.append(f"        ELSE")
            # NOT ALLOCATED is a distinct input from length 0, and it must not
            # render as one (P6).
            #
            # It was rendered as a NULL POINTER with cap 0 until 2026-08-12,
            # which carried the distinction correctly and left the C++ with NO
            # BUFFER AT ALL -- so a reference whose own statement is
            # `ErrVar%ErrMsg = <expr>` (a REALLOCATING assignment, which
            # ALLOCATES an unallocated field) had no representation on the C
            # side at all. Measured at rosco-r2 unit #17 `Read_OL_Input`, whose
            # only reachable path is exactly that assignment: the translation
            # could not write the field, and the KGen comparator -- guarded on
            # `IF (ALLOCATED(var%errmsg))`, the KERNEL's own value -- dropped
            # the comparison and reported 200 of 200 IDENTICAL.
            #
            # The staging buffer is therefore supplied EITHER WAY, and the
            # not-allocated signal moves onto the LENGTH, using the convention
            # this codebase already uses for an ALLOCATABLE array extent:
            # n < 0 unallocated, n == 0 allocated-empty, n > 0 allocated.
            # `vit_copy_scalars_to_*` already guards `view%n_<f> >= 0`, so a
            # field the C++ does not touch stays unallocated exactly as before.
            lines.append(f"            IF (.NOT. ALLOCATED({buf})) &")
            lines.append(f"                ALLOCATE(CHARACTER(LEN=VIT_DEFERRED_CHAR_HEADROOM) :: {buf})")
            lines.append(f"            {buf}(1:LEN({buf})) = ''   ! blank-fill; substring so it does not reallocate")
            lines.append(f"            view%{f.name} = C_LOC({buf}(1:1))")
            lines.append(f"            view%n_{f.name} = -1_C_INT32_T   ! NOT ALLOCATED, distinct from length 0")
            lines.append(f"            view%n_{f.name}_cap = INT(LEN({buf}), C_INT32_T)")
            lines.append(f"        END IF")
            continue

        lines.append(f"        IF (ALLOCATED(src%{f.name})) THEN")
        if is_view_type_alloc:
            # Populate an array of child view structs
            child_info = view_type_alloc_fields[f.name]
            child_lower = child_info.name.lower()
            child_populate_sub = f"vit_populate_{child_lower}"
            save_array_name = f"vit_{f.name.lower()}_views_{name_lower}"
            lines.append(f"            IF (ALLOCATED({save_array_name})) DEALLOCATE({save_array_name})")
            lines.append(f"            ALLOCATE({save_array_name}(SIZE(src%{f.name})))")
            lines.append(f"            DO vit_i = 1, SIZE(src%{f.name})")
            lines.append(f"                CALL {child_populate_sub}(src%{f.name}(vit_i), {save_array_name}(vit_i))")
            lines.append(f"            END DO")
            lines.append(f"            view%{f.name} = C_LOC({save_array_name}(1))")
            if rank == 1:
                lines.append(f"            view%n_{f.name} = INT(SIZE(src%{f.name}), C_INT32_T)")
            elif rank == 2:
                lines.append(f"            view%n_{f.name}_rows = INT(SIZE(src%{f.name}, 1), C_INT32_T)")
                lines.append(f"            view%n_{f.name}_cols = INT(SIZE(src%{f.name}, 2), C_INT32_T)")
        elif is_type_alloc and f.name in c_compat_type_alloc_fields:
            if rank == 1:
                lines.append(f"            view%{f.name} = C_LOC(src%{f.name}(LBOUND(src%{f.name},1)))")
                lines.append(f"            view%n_{f.name} = INT(SIZE(src%{f.name}), C_INT32_T)")
            elif rank == 2:
                lines.append(f"            view%{f.name} = C_LOC(src%{f.name}(LBOUND(src%{f.name},1),LBOUND(src%{f.name},2)))")
                lines.append(f"            view%n_{f.name}_rows = INT(SIZE(src%{f.name}, 1), C_INT32_T)")
                lines.append(f"            view%n_{f.name}_cols = INT(SIZE(src%{f.name}, 2), C_INT32_T)")
        elif is_type_alloc:
            lines.append(f"            view%{f.name} = C_NULL_PTR  ! TYPE() — opaque, not interoperable")
            if rank == 1:
                lines.append(f"            view%n_{f.name} = INT(SIZE(src%{f.name}), C_INT32_T)")
            elif rank == 2:
                lines.append(f"            view%n_{f.name}_rows = INT(SIZE(src%{f.name}, 1), C_INT32_T)")
                lines.append(f"            view%n_{f.name}_cols = INT(SIZE(src%{f.name}, 2), C_INT32_T)")
        elif rank == 1:
            lines.append(f"            view%{f.name} = C_LOC(src%{f.name}(LBOUND(src%{f.name},1)))")
            lines.append(f"            view%n_{f.name} = INT(SIZE(src%{f.name}), C_INT32_T)")
        elif rank == 2:
            lines.append(f"            view%{f.name} = C_LOC(src%{f.name}(LBOUND(src%{f.name},1),LBOUND(src%{f.name},2)))")
            lines.append(f"            view%n_{f.name}_rows = INT(SIZE(src%{f.name}, 1), C_INT32_T)")
            lines.append(f"            view%n_{f.name}_cols = INT(SIZE(src%{f.name}, 2), C_INT32_T)")
        elif rank >= 3:
            lines.append(f"            view%{f.name} = C_LOC(src%{f.name}(LBOUND(src%{f.name},1),LBOUND(src%{f.name},2),LBOUND(src%{f.name},3)))")
            lines.append(f"            view%n_{f.name}_dim1 = INT(SIZE(src%{f.name}, 1), C_INT32_T)")
            lines.append(f"            view%n_{f.name}_dim2 = INT(SIZE(src%{f.name}, 2), C_INT32_T)")
            lines.append(f"            view%n_{f.name}_dim3 = INT(SIZE(src%{f.name}, 3), C_INT32_T)")
        lines.append(f"        ELSE")
        lines.append(f"            view%{f.name} = C_NULL_PTR")
        if is_type_alloc:
            if rank == 1:
                lines.append(f"            view%n_{f.name} = 0")
            elif rank == 2:
                lines.append(f"            view%n_{f.name}_rows = 0")
                lines.append(f"            view%n_{f.name}_cols = 0")
            elif rank >= 3:
                lines.append(f"            view%n_{f.name}_dim1 = 0")
                lines.append(f"            view%n_{f.name}_dim2 = 0")
                lines.append(f"            view%n_{f.name}_dim3 = 0")
        elif rank == 1:
            lines.append(f"            view%n_{f.name} = 0")
        elif rank == 2:
            lines.append(f"            view%n_{f.name}_rows = 0")
            lines.append(f"            view%n_{f.name}_cols = 0")
        elif rank >= 3:
            lines.append(f"            view%n_{f.name}_dim1 = 0")
            lines.append(f"            view%n_{f.name}_dim2 = 0")
            lines.append(f"            view%n_{f.name}_dim3 = 0")
        lines.append(f"        END IF")

    lines.append(f"")
    lines.append(f"    END SUBROUTINE {sub_name}")
    lines.append(f"")

    # --- Reverse-copy subroutine (view → Fortran TYPE, scalars only) ---
    lines.append(f"    SUBROUTINE {reverse_name}(view, dest)")
    lines.append(f"        TYPE({view_type_name}), INTENT(IN) :: view")
    lines.append(f"        TYPE({name}), INTENT(INOUT) :: dest")

    # Check if CHARACTER fields exist (need a loop variable)
    has_chars = any(
        not f.is_allocatable and not f.is_pointer
        and not f.fortran_type.upper().startswith("TYPE(")
        and get_type_mapping(f.fortran_type, kind_aliases).character_length
        for f in type_info.fields
    )
    if has_chars:
        lines.append(f"        INTEGER :: i")
    if view_type_fixed_fields:
        lines.append(f"        INTEGER :: vit_k")

    lines.append(f"")
    lines.append(f"        ! --- Scalars (reverse of populator) ---")

    for f in type_info.fields:
        if f.is_allocatable or f.is_pointer:
            continue
        if f.fortran_type.upper().startswith("TYPE("):
            if f.name in embeddable_nested:
                nested_info = embeddable_nested[f.name]
                lines.append(f"        ! --- Nested TYPE({nested_info.name}) {f.name} ---")
                for nf in nested_info.fields:
                    if nf.fortran_type.upper().startswith("TYPE("):
                        continue
                    if nf.is_allocatable or nf.is_pointer:
                        continue
                    reverse = _nested_reverse_assignment(f.name, nf, kind_aliases)
                    if reverse:
                        for line in reverse:
                            lines.append(f"        {line}")
            elif f.name in view_type_fixed_fields:
                child_info, sizes = view_type_fixed_fields[f.name]
                child_reverse = f"vit_copy_scalars_to_{child_info.name.lower()}"
                lines.append(f"        DO vit_k = 1, {sizes[0]}")
                lines.append(f"            CALL {child_reverse}(view%{f.name}(vit_k), dest%{f.name}(vit_k))")
                lines.append(f"        END DO")
            continue
        reverse = _reverse_scalar_assignment(f, kind_aliases)
        if reverse:
            for line in reverse:
                lines.append(f"        {line}")

    # --- Deferred-length CHARACTER fields: the REALLOCATING half ---
    #
    # This is the only place a view-struct field changes the ALLOCATION of the
    # Fortran object rather than its contents, and it is the reason the field is
    # writable at all. The C++ wrote bytes into the staging buffer and set
    # `n_<f>` to the length it wants; the assignment below reallocates
    # `dest%<f>` to exactly that length.
    #
    # Three guards, and each one is a distinct wrong answer being refused rather
    # than a defensive habit:
    #
    #   * buffer not allocated  -> the populator never ran, or ran on an
    #                              unallocated field. There is nothing to copy
    #                              back and copying zero bytes would ALLOCATE a
    #                              field the caller left unallocated.
    #   * n_<f> < 0             -> not a length. Left alone.
    #   * n_<f> > LEN(buffer)   -> the C++ asked for more than the capacity it
    #                              was handed. REFUSED, not truncated: the
    #                              truncated string is the plausible wrong
    #                              answer no bit comparison can catch. It is
    #                              reported on stderr because a silent refusal
    #                              is the same defect one level down.
    if deferred_char_fields:
        lines.append(f"")
        lines.append(f"        ! --- Deferred-length CHARACTER fields (reallocating assignment) ---")
    for f in deferred_char_fields:
        buf = _deferred_char_buffer(name_lower, f.name)
        lines.append(f"        IF (ALLOCATED({buf}) .AND. view%n_{f.name} >= 0) THEN")
        lines.append(f"            IF (view%n_{f.name} <= LEN({buf})) THEN")
        lines.append(f"                dest%{f.name} = {buf}(1:view%n_{f.name})")
        lines.append(f"            ELSE")
        lines.append(f"                WRITE(ERROR_UNIT,'(A,I0,A,I0,A)') &")
        lines.append(f"                    'VIT: {name}%{f.name} assignment of ', view%n_{f.name}, &")
        lines.append(f"                    ' bytes exceeds the ', LEN({buf}), &")
        lines.append(f"                    '-byte staging buffer; field left unchanged'")
        lines.append(f"            END IF")
        lines.append(f"        END IF")

    lines.append(f"")
    lines.append(f"    END SUBROUTINE {reverse_name}")
    lines.append(f"")

    # --- The DIRECT-CALLER pair -------------------------------------------
    #
    # Everything above moves a deferred-length CHARACTER field through the
    # module staging buffer, because in the wrapper path the wrapper's own
    # `vit_populate_<t>` fills that buffer first and hands the C++ a pointer
    # INTO it. A callee bridge reached from a DIRECT C++ caller has no such
    # call in front of it: the staging buffer is unallocated, so the reverse
    # copy silently leaves the field alone, and the populator on the way out
    # REPOINTS `view%<f>` away from the buffer the caller owns.
    #
    # These two do the same conversions against the CALLER'S OWN buffer, and
    # leave its pointer and capacity untouched. They are additive -- nothing
    # above changes -- so no already-generated wrapper or kernel bridge moves.
    #
    # Measured on rosco-r2 unit #45 `interp2d`, the campaign's first
    # differential harness whose callee takes a view type: the bridge
    # segfaulted on case 0 dereferencing the null stash, and with the stash
    # alone repaired 123 of 1117 cases disagreed on `ErrMsg` and `n_ErrMsg`.
    #
    # A FIELD KIND NEITHER OF THESE HANDLES IS AN `ERROR STOP`, not a silent
    # skip. They are unreachable except from a direct caller, so an unhandled
    # kind costs nothing until something actually needs it -- and then it says
    # so instead of comparing a field nobody filled.
    # TWO KINDS CAME OFF THAT LIST AT rosco-r2 UNIT #60, AND BOTH BECAUSE A
    # DIRECT CALLER FINALLY NEEDED THEM.
    #
    # `VariableSpeedControl` hands `LocalVar` whole to `PIDController`, which
    # reads `LocalVar%FP` and `LocalVar%iStatus` and lets `LPFilter` write `FP`
    # back. The generated bridge takes the direct path, and
    # `vit_view_in_localvariables` was an `ERROR STOP` naming six fields --
    # `ACC_INFILE, WE, FP, piP, resP, rlP` -- so the differential harness could
    # not run at all.
    #
    #   1. EMBEDDED NESTED TYPE() fields. Their conversion is not new and not a
    #      judgement: `vit_copy_scalars_to_<t>` has emitted exactly these
    #      assignments since the reverse copier existed, and every integrated
    #      unit's wrapper already runs them. The direct pair simply did not.
    #   2. RANK-1 ALLOCATABLE arrays of INTRINSIC type. `direct_in` allocates to
    #      the extent the caller states and copies the caller's buffer in;
    #      `direct_out` writes back INTO that buffer and leaves its pointer and
    #      extent exactly as supplied -- which is the whole point of the direct
    #      pair (the populator would REPOINT the field at Fortran's own
    #      storage).
    #
    # Everything else still stops: ALLOCATABLE TYPE(), POINTER, and rank >= 2.
    # Rank is the bound because the extent a view struct carries for a rank-1
    # array is one number, and a higher-rank field's shape is not recoverable
    # from it without the per-dimension extents the struct spells differently
    # per field. An unmeasured kind says so rather than comparing a field
    # nobody filled.
    unhandled = [
        f.name for f in type_info.fields
        if f.name not in _deferred_names
        and f.name not in _direct_alloc_names
        and f.name not in _direct_alloc2_names
        and f.name not in embeddable_nested
        and (f.is_allocatable or f.is_pointer
             or f.fortran_type.upper().startswith("TYPE("))
    ]

    def _direct_alloc_ptr(f) -> str:
        return f"vit_ap_{f.name.lower()}"

    def _direct_alloc_decls() -> list:
        out = []
        for f in _direct_alloc:
            base = f.fortran_type.strip()
            if base.upper().startswith("CHARACTER"):
                base = "CHARACTER(KIND=C_CHAR)"
            out.append(f"        {base}, POINTER :: {_direct_alloc_ptr(f)}(:)")
        for f in _direct_alloc2:
            base = f.fortran_type.strip()
            if base.upper().startswith("CHARACTER"):
                base = "CHARACTER(KIND=C_CHAR)"
            out.append(f"        {base}, POINTER :: {_direct_alloc_ptr(f)}(:,:)")
        return out

    lines.append(f"    SUBROUTINE {direct_in}(view, dest)")
    lines.append(f"        ! view -> Fortran TYPE, reading the CALLER'S buffers.")
    lines.append(f"        TYPE({view_type_name}), INTENT(IN) :: view")
    lines.append(f"        TYPE({name}), INTENT(INOUT) :: dest")
    if deferred_char_fields:
        lines.append(f"        CHARACTER(KIND=C_CHAR), POINTER :: vit_buf(:)")
        lines.append(f"        INTEGER :: vit_k")
    if has_chars:
        lines.append(f"        INTEGER :: i")
    lines.extend(_direct_alloc_decls())
    lines.append(f"")
    if unhandled:
        lines.extend(_unmeasured_field_stop(direct_in, unhandled))
    for f in type_info.fields:
        if f.is_allocatable or f.is_pointer:
            continue
        if f.fortran_type.upper().startswith("TYPE("):
            if f.name in embeddable_nested:
                nested_info = embeddable_nested[f.name]
                lines.append(f"        ! --- Nested TYPE({nested_info.name}) {f.name} ---")
                for nf in nested_info.fields:
                    if nf.fortran_type.upper().startswith("TYPE("):
                        continue
                    if nf.is_allocatable or nf.is_pointer:
                        continue
                    reverse = _nested_reverse_assignment(f.name, nf, kind_aliases)
                    if reverse:
                        for line in reverse:
                            lines.append(f"        {line}")
            continue
        reverse = _reverse_scalar_assignment(f, kind_aliases)
        if reverse:
            for line in reverse:
                lines.append(f"        {line}")
    for f in _direct_alloc:
        p = _direct_alloc_ptr(f)
        lines.append(f"        IF (ALLOCATED(dest%{f.name})) DEALLOCATE(dest%{f.name})")
        lines.append(f"        IF (C_ASSOCIATED(view%{f.name}) .AND. view%n_{f.name} > 0) THEN")
        lines.append(f"            CALL C_F_POINTER(view%{f.name}, {p}, [INT(view%n_{f.name})])")
        lines.append(f"            ALLOCATE(dest%{f.name}(view%n_{f.name}))")
        lines.append(f"            dest%{f.name} = {p}")
        lines.append(f"        ELSE IF (view%n_{f.name} == 0) THEN")
        lines.append(f"            ALLOCATE(dest%{f.name}(0))   ! allocated-empty, distinct from unallocated")
        lines.append(f"        END IF")
    # RANK 2. The rank-1 block one loop up with the extent read from the two
    # names the struct spells for a two-dimensional field, and no transposition:
    # the caller's buffer is column-major (`C_LOC(src%f(LBOUND1, LBOUND2))` in
    # the forward populator) and so is the Fortran array being filled.
    for f in _direct_alloc2:
        p2 = _direct_alloc_ptr(f)
        lines.append(f"        IF (ALLOCATED(dest%{f.name})) DEALLOCATE(dest%{f.name})")
        lines.append(f"        IF (C_ASSOCIATED(view%{f.name}) .AND. view%n_{f.name}_rows > 0 &")
        lines.append(f"                .AND. view%n_{f.name}_cols > 0) THEN")
        lines.append(f"            CALL C_F_POINTER(view%{f.name}, {p2}, &")
        lines.append(f"                [INT(view%n_{f.name}_rows), INT(view%n_{f.name}_cols)])")
        lines.append(f"            ALLOCATE(dest%{f.name}(view%n_{f.name}_rows, view%n_{f.name}_cols))")
        lines.append(f"            dest%{f.name} = {p2}")
        lines.append(f"        ELSE IF (view%n_{f.name}_rows == 0 .AND. view%n_{f.name}_cols == 0) THEN")
        lines.append(f"            ALLOCATE(dest%{f.name}(0, 0))   ! allocated-empty, distinct from unallocated")
        lines.append(f"        END IF")
    for f in deferred_char_fields:
        lines.append(f"        IF (ALLOCATED(dest%{f.name})) DEALLOCATE(dest%{f.name})")
        lines.append(f"        IF (C_ASSOCIATED(view%{f.name}) .AND. view%n_{f.name} >= 0) THEN")
        lines.append(f"            CALL C_F_POINTER(view%{f.name}, vit_buf, [INT(view%n_{f.name}_cap)])")
        lines.append(f"            ALLOCATE(CHARACTER(LEN=view%n_{f.name}) :: dest%{f.name})")
        lines.append(f"            DO vit_k = 1, view%n_{f.name}")
        lines.append(f"                dest%{f.name}(vit_k:vit_k) = vit_buf(vit_k)")
        lines.append(f"            END DO")
        lines.append(f"        END IF")
    lines.append(f"")
    lines.append(f"    END SUBROUTINE {direct_in}")
    lines.append(f"")

    lines.append(f"    SUBROUTINE {direct_out}(src, view)")
    lines.append(f"        ! Fortran TYPE -> view, writing INTO the caller's buffer and")
    lines.append(f"        ! leaving its pointer and capacity exactly as it supplied them.")
    lines.append(f"        TYPE({name}), INTENT(IN) :: src")
    lines.append(f"        TYPE({view_type_name}), INTENT(INOUT) :: view")
    if deferred_char_fields:
        lines.append(f"        CHARACTER(KIND=C_CHAR), POINTER :: vit_buf(:)")
        lines.append(f"        INTEGER :: vit_k")
    lines.extend(_direct_alloc_decls())
    lines.append(f"")
    if unhandled:
        lines.extend(_unmeasured_field_stop(direct_out, unhandled))
    for f in type_info.fields:
        if f.is_allocatable or f.is_pointer:
            continue
        if f.fortran_type.upper().startswith("TYPE("):
            if f.name in embeddable_nested:
                nested_info = embeddable_nested[f.name]
                lines.append(f"        ! --- Nested TYPE({nested_info.name}) {f.name} ---")
                for nf in nested_info.fields:
                    if nf.fortran_type.upper().startswith("TYPE("):
                        continue
                    if nf.is_allocatable or nf.is_pointer:
                        continue
                    assign = _nested_scalar_assignment(f.name, nf, kind_aliases)
                    if assign:
                        lines.append(f"        {assign}")
            continue
        assignment = _scalar_assignment(f, kind_aliases)
        if assignment:
            lines.append(f"        {assignment}")
    for f in _direct_alloc:
        p = _direct_alloc_ptr(f)
        # WRITE BACK INTO THE CALLER'S BUFFER, never repoint it and never
        # resize it. A rank-1 ALLOCATABLE whose extent changed inside the callee
        # has nowhere to go -- C++ cannot reallocate Fortran's storage and the
        # buffer is the CALLER's -- so it is reported rather than truncated.
        lines.append(f"        IF (C_ASSOCIATED(view%{f.name}) .AND. view%n_{f.name} > 0) THEN")
        lines.append(f"            IF (ALLOCATED(src%{f.name})) THEN")
        lines.append(f"                IF (SIZE(src%{f.name}) == view%n_{f.name}) THEN")
        lines.append(f"                    CALL C_F_POINTER(view%{f.name}, {p}, [INT(view%n_{f.name})])")
        lines.append(f"                    {p} = src%{f.name}")
        lines.append(f"                ELSE")
        lines.append(f"                    WRITE(ERROR_UNIT,'(A,I0,A,I0,A)') &")
        lines.append(f"                        'VIT: {name}%{f.name} came back at ', SIZE(src%{f.name}), &")
        lines.append(f"                        ' element(s) against the ', view%n_{f.name}, &")
        lines.append(f"                        ' the caller supplied; left unchanged'")
        lines.append(f"                END IF")
        lines.append(f"            END IF")
        lines.append(f"        END IF")
    # RANK 2, write-back. The same refusal as rank-1 -- a field that came back at
    # a different shape has nowhere to go -- checked on BOTH extents, because a
    # square array whose two dimensions were swapped has the same total size.
    for f in _direct_alloc2:
        p2 = _direct_alloc_ptr(f)
        lines.append(f"        IF (C_ASSOCIATED(view%{f.name}) .AND. view%n_{f.name}_rows > 0 &")
        lines.append(f"                .AND. view%n_{f.name}_cols > 0) THEN")
        lines.append(f"            IF (ALLOCATED(src%{f.name})) THEN")
        lines.append(f"                IF (SIZE(src%{f.name}, 1) == view%n_{f.name}_rows .AND. &")
        lines.append(f"                    SIZE(src%{f.name}, 2) == view%n_{f.name}_cols) THEN")
        lines.append(f"                    CALL C_F_POINTER(view%{f.name}, {p2}, &")
        lines.append(f"                        [INT(view%n_{f.name}_rows), INT(view%n_{f.name}_cols)])")
        lines.append(f"                    {p2} = src%{f.name}")
        lines.append(f"                ELSE")
        lines.append(f"                    WRITE(ERROR_UNIT,\'(A,I0,A,I0,A,I0,A,I0,A)\') &")
        lines.append(f"                        \'VIT: {name}%{f.name} came back at \', SIZE(src%{f.name}, 1), &")
        lines.append(f"                        \' x \', SIZE(src%{f.name}, 2), &")
        lines.append(f"                        \' against the \', view%n_{f.name}_rows, &")
        lines.append(f"                        \' x \', view%n_{f.name}_cols, &")
        lines.append(f"                        \' the caller supplied; left unchanged\'")
        lines.append(f"                END IF")
        lines.append(f"            END IF")
        lines.append(f"        END IF")
    for f in deferred_char_fields:
        lines.append(f"        IF (C_ASSOCIATED(view%{f.name})) THEN")
        lines.append(f"            IF (ALLOCATED(src%{f.name})) THEN")
        lines.append(f"                IF (LEN(src%{f.name}) <= view%n_{f.name}_cap) THEN")
        lines.append(f"                    CALL C_F_POINTER(view%{f.name}, vit_buf, [INT(view%n_{f.name}_cap)])")
        lines.append(f"                    DO vit_k = 1, LEN(src%{f.name})")
        lines.append(f"                        vit_buf(vit_k) = src%{f.name}(vit_k:vit_k)")
        lines.append(f"                    END DO")
        lines.append(f"                    view%n_{f.name} = INT(LEN(src%{f.name}), C_INT32_T)")
        lines.append(f"                ELSE")
        lines.append(f"                    WRITE(ERROR_UNIT,'(A,I0,A,I0,A)') &")
        lines.append(f"                        'VIT: {name}%{f.name} of ', LEN(src%{f.name}), &")
        lines.append(f"                        ' bytes exceeds the ', view%n_{f.name}_cap, &")
        lines.append(f"                        '-byte buffer the caller supplied; left unchanged'")
        lines.append(f"                END IF")
        lines.append(f"            ELSE")
        lines.append(f"                view%n_{f.name} = -1_C_INT32_T   ! NOT ALLOCATED")
        lines.append(f"            END IF")
        lines.append(f"        END IF")
    lines.append(f"")
    lines.append(f"    END SUBROUTINE {direct_out}")
    lines.append(f"")

    lines.extend(_adopt_routines(
        type_info, name, name_lower, view_type_name, alloc_fields, _adoptable,
        view_type_alloc_fields, _deferred_names, _field_rank))

    lines.append(f"END MODULE {module_name}")
    lines.append(f"")

    return "\n".join(lines)


def _extent_members(field_name: str, rank: int) -> list:
    """The view-struct members that hold a rank-`rank` field's extents."""
    if rank == 1:
        return [f"n_{field_name}"]
    if rank == 2:
        return [f"n_{field_name}_rows", f"n_{field_name}_cols"]
    return [f"n_{field_name}_dim{k}" for k in range(1, rank + 1)]


def _stop_lines(indent: str, msg: str) -> list:
    """`ERROR STOP 'msg'` split across continuation lines (free-form 132-column limit)."""
    msg = msg.replace("'", "''")
    chunks = [msg[i:i + 80] for i in range(0, len(msg), 80)] or [""]
    if len(chunks) == 1:
        return [f"{indent}ERROR STOP '{chunks[0]}'"]
    out = [f"{indent}ERROR STOP '{chunks[0]}' // &"]
    for i, c in enumerate(chunks[1:], 1):
        tail = " // &" if i < len(chunks) - 1 else ""
        out.append(f"{indent}    '{c}'{tail}")
    return out


def _if_stop(indent: str, cond: str, msg: str) -> list:
    """`IF (cond) THEN / ERROR STOP / END IF`, the condition on its own line."""
    return ([f"{indent}IF ({cond}) THEN"] + _stop_lines(indent + "    ", msg)
            + [f"{indent}END IF"])


def _adopt_routines(type_info, name, name_lower, view_type_name, alloc_fields,
                    adoptable, child_fields, deferred_names, field_rank) -> list:
    """`vit_snapshot_<t>` and `vit_adopt_<t>`: components a C++ translation ALLOCATES.

    THE PROBLEM. A view carries each ALLOCATABLE component as (pointer,
    extents) into Fortran's own storage. A translation of a routine that
    ALLOCATES a component -- an INTENT(OUT) argument arrives with every
    component deallocated, and an initializer's whole job is to allocate them --
    has nothing to write into, and C++ cannot allocate a Fortran allocatable.
    Until this path existed such a routine got a hand-written two-pass wrapper
    (ROSCO ReadControlParameterFileSub; AeroDyn ReadAFfile and AFI_Init).

    THE CONVENTION. The C++ allocates with `vit_adopt_alloc_lb` (vit_types.h),
    stating the lower bounds exactly as the ALLOCATE it translates does, fills
    in column-major order, and sets the component's pointer and extents in the
    view. `vit_adopt_<t>` then ADOPTS it: ALLOCATEs the Fortran component with
    those extents and lower bounds, copies, and frees the buffer. One call, so
    no side effect runs twice. The lower bounds ride in a header in front of the
    buffer because the view struct has no members for them, and adding members
    would change its C layout under every header already generated.

    WHICH COMPONENTS. Decided PER FIELD from a snapshot of the view taken
    before the call, not by the caller naming arguments: a component whose
    pointer and extents are unchanged was written in place (or not at all) and
    is left alone; one whose pointer changed was replaced and is adopted; one
    set to NULL is deallocated. This is what makes the path safe to apply to
    every component. `vit_view_in_<t>` deallocates every component first, and
    for one the C++ never touched the view still points at that storage --
    so it read freed memory unless the caller named exactly the right arguments.

    REFUSED, NEVER GUESSED (ERROR STOP naming the field):
      * a pointer replaced on a component kind this path does not adopt;
      * the same pointer with different extents (C++ cannot resize Fortran
        storage in place);
      * a replaced pointer equal to another component's pre-call pointer --
        Fortran-owned storage that the adopt would then free;
      * a buffer without the vit_adopt_alloc header, or whose header states a
        different rank.

    ALLOCATABLE TYPE() arrays of view types are adopted one level deep: the
    children's own components through `vit_adopt_<child>`, their scalars through
    `vit_copy_scalars_to_<child>`. A child's own ALLOCATABLE TYPE() arrays have
    no snapshot, and reaching one with elements stops rather than guessing.
    """
    adoptable_names = {f.name for f in adoptable}
    child_rank1 = {n: c for n, c in child_fields.items()
                   if field_rank(next(f for f in type_info.fields if f.name == n)) == 1}
    checked_only = [f for f in alloc_fields
                    if not f.is_pointer and f.name not in adoptable_names
                    and f.name not in child_rank1]
    all_ptr_fields = [f for f in alloc_fields if not f.is_pointer]

    def same_lines(indent, fname):
        # C_ASSOCIATED(a, b) is .FALSE. when a is NULL, so "both NULL" is spelled out.
        return [f"{indent}vit_same = C_ASSOCIATED(view%{fname}, snap%{fname})",
                f"{indent}IF (.NOT. C_ASSOCIATED(view%{fname}) .AND. &",
                f"{indent}    .NOT. C_ASSOCIATED(snap%{fname})) vit_same = .TRUE."]

    def foreign_check(fname, indent):
        out = []
        for g in all_ptr_fields:
            if g.name == fname:
                continue
            out.extend(_if_stop(indent, f"C_ASSOCIATED(view%{fname}, snap%{g.name})",
                                f"VIT adopt: {name}%{fname} was set to the pre-call storage of "
                                f"{name}%{g.name}; only a malloc buffer can be adopted"))
        return out

    L = []
    # ---- snapshot -------------------------------------------------------
    L.append(f"    SUBROUTINE vit_snapshot_{name_lower}(view, snap)")
    L.append(f"        ! Pre-call copy of a view, for vit_adopt_{name_lower}.")
    L.append(f"        TYPE({view_type_name}), INTENT(IN) :: view")
    L.append(f"        TYPE({view_type_name}), INTENT(OUT) :: snap")
    for fname, child in child_rank1.items():
        L.append(f"        TYPE({child.name.lower()}_view_t), POINTER :: vit_cv_{fname.lower()}(:)")
    L.append(f"")
    L.append(f"        snap = view")
    for fname, child in child_rank1.items():
        snaps = f"vit_{fname.lower()}_snaps_{name_lower}"
        cv = f"vit_cv_{fname.lower()}"
        L.append(f"        IF (ALLOCATED({snaps})) DEALLOCATE({snaps})")
        L.append(f"        IF (C_ASSOCIATED(view%{fname}) .AND. view%n_{fname} > 0) THEN")
        L.append(f"            CALL C_F_POINTER(view%{fname}, {cv}, [INT(view%n_{fname})])")
        L.append(f"            ALLOCATE({snaps}(view%n_{fname}))")
        L.append(f"            {snaps} = {cv}")
        L.append(f"        END IF")
    L.append(f"    END SUBROUTINE vit_snapshot_{name_lower}")
    L.append(f"")

    # ---- adopt ----------------------------------------------------------
    L.append(f"    SUBROUTINE vit_adopt_{name_lower}(view, snap, dest)")
    L.append(f"        ! Adopt the ALLOCATABLE components a C++ translation allocated.")
    L.append(f"        ! See view_populator._adopt_routines for the convention.")
    L.append(f"        TYPE({view_type_name}), INTENT(IN) :: view")
    L.append(f"        TYPE({view_type_name}), INTENT(IN) :: snap")
    L.append(f"        TYPE({name}), INTENT(INOUT) :: dest")
    L.append(f"        LOGICAL :: vit_same")
    L.append(f"        TYPE(vit_adopt_header_t), POINTER :: vit_h")
    if child_rank1:
        L.append(f"        INTEGER :: vit_i, vit_lb")
    for f in adoptable:
        rank = field_rank(f)
        base = f.fortran_type.strip()
        L.append(f"        {base}, POINTER :: vit_ad_{f.name.lower()}({','.join([':'] * rank)})")
    for fname, child in child_rank1.items():
        cv = f"{child.name.lower()}_view_t"
        L.append(f"        TYPE({cv}), POINTER :: vit_cv_{fname.lower()}(:)")
        L.append(f"        TYPE({cv}) :: vit_zero_{fname.lower()}   ! every component pointer NULL")
    L.append(f"")

    for f in adoptable:
        rank = field_rank(f)
        ext = _extent_members(f.name, rank)
        p = f"vit_ad_{f.name.lower()}"
        L.append(f"        ! --- {f.name} ---")
        L.extend(same_lines("        ", f.name))
        L.append(f"        IF (.NOT. vit_same) THEN")
        L.append(f"            IF (ALLOCATED(dest%{f.name})) DEALLOCATE(dest%{f.name})")
        L.append(f"            IF (C_ASSOCIATED(view%{f.name})) THEN")
        L.extend(foreign_check(f.name, "                "))
        shape = ", ".join(f"INT(view%{e})" for e in ext)
        L.append(f"                vit_h => vit_adopt_header(view%{f.name}, {rank}, '{name}%{f.name}')")
        L.append(f"                CALL C_F_POINTER(view%{f.name}, {p}, &")
        L.append(f"                    [{shape}])")
        bounds = [f"vit_h%lb({k}):vit_h%lb({k})+view%{e}-1" for k, e in enumerate(ext, 1)]
        L.append(f"                ALLOCATE(dest%{f.name}( &")
        for i, bnd in enumerate(bounds):
            L.append(f"                    {bnd}{', &' if i < len(bounds) - 1 else '))'}")
        L.append(f"                dest%{f.name} = {p}")
        L.append(f"                CALL vit_c_free(vit_h%base)")
        L.append(f"            END IF")
        L.append(f"        ELSE IF (C_ASSOCIATED(view%{f.name})) THEN")
        for e in ext:
            L.extend(_if_stop("            ", f"view%{e} /= snap%{e}",
                              f"VIT adopt: {name}%{f.name} changed extent without a new buffer; "
                              f"C++ cannot resize Fortran storage in place"))
        L.append(f"        END IF")

    for fname, child in child_rank1.items():
        f = next(x for x in type_info.fields if x.name == fname)
        c = child.name.lower()
        cv = f"vit_cv_{fname.lower()}"
        snaps = f"vit_{fname.lower()}_snaps_{name_lower}"
        L.append(f"        ! --- {fname}: ALLOCATABLE TYPE({child.name}) ---")
        L.extend(same_lines("        ", fname))
        L.append(f"        IF (vit_same) THEN")
        L.append(f"            IF (C_ASSOCIATED(view%{fname})) THEN")
        L.extend(_if_stop("                ", f"view%n_{fname} /= snap%n_{fname}",
                          f"VIT adopt: {name}%{fname} changed extent without a new buffer"))
        L.append(f"                IF (view%n_{fname} > 0) THEN")
        L.extend(_if_stop("                    ", f".NOT. ALLOCATED({snaps})",
                          f"VIT adopt: {name}%{fname} has no snapshot; "
                          f"vit_snapshot_{name_lower} must run before the C++ call"))
        L.append(f"                    CALL C_F_POINTER(view%{fname}, {cv}, [INT(view%n_{fname})])")
        L.append(f"                    vit_lb = LBOUND(dest%{fname}, 1)")
        L.append(f"                    DO vit_i = 1, view%n_{fname}")
        L.append(f"                        CALL vit_copy_scalars_to_{c}({cv}(vit_i), &")
        L.append(f"                            dest%{fname}(vit_lb + vit_i - 1))")
        L.append(f"                        CALL vit_adopt_{c}({cv}(vit_i), {snaps}(vit_i), &")
        L.append(f"                            dest%{fname}(vit_lb + vit_i - 1))")
        L.append(f"                    END DO")
        L.append(f"                END IF")
        L.append(f"            END IF")
        L.append(f"        ELSE")
        L.append(f"            IF (ALLOCATED(dest%{fname})) DEALLOCATE(dest%{fname})")
        L.append(f"            IF (C_ASSOCIATED(view%{fname})) THEN")
        L.extend(foreign_check(fname, "                "))
        L.append(f"                vit_h => vit_adopt_header(view%{fname}, 1, '{name}%{fname}')")
        L.append(f"                vit_lb = vit_h%lb(1)")
        L.append(f"                CALL C_F_POINTER(view%{fname}, {cv}, [INT(view%n_{fname})])")
        L.append(f"                ALLOCATE(dest%{fname}(vit_lb:vit_lb+view%n_{fname}-1))")
        L.append(f"                DO vit_i = 1, view%n_{fname}")
        L.append(f"                    CALL vit_copy_scalars_to_{c}({cv}(vit_i), &")
        L.append(f"                        dest%{fname}(vit_lb + vit_i - 1))")
        L.append(f"                    CALL vit_adopt_{c}({cv}(vit_i), vit_zero_{fname.lower()}, &")
        L.append(f"                        dest%{fname}(vit_lb + vit_i - 1))")
        L.append(f"                END DO")
        L.append(f"                CALL vit_c_free(vit_h%base)")
        L.append(f"            END IF")
        L.append(f"        END IF")

    for f in checked_only:
        why = ("deferred-length CHARACTER is written through its staging buffer, never replaced"
               if f.name in deferred_names else "this component kind is not adopted")
        L.extend(same_lines("        ", f.name))
        L.extend(_if_stop("        ", ".NOT. vit_same",
                          f"VIT adopt: {name}%{f.name} was replaced by the C++; {why}"))

    L.append(f"    END SUBROUTINE vit_adopt_{name_lower}")
    L.append(f"")
    L.append(f"    FUNCTION vit_adopt_header(p, rank, what) RESULT(h)")
    L.append(f"        ! The header vit_adopt_alloc_lb put 32 bytes in front of buffer p.")
    L.append(f"        TYPE(C_PTR), INTENT(IN) :: p")
    L.append(f"        INTEGER, INTENT(IN) :: rank")
    L.append(f"        CHARACTER(*), INTENT(IN) :: what")
    L.append(f"        TYPE(vit_adopt_header_t), POINTER :: h")
    L.append(f"        INTEGER(C_INTPTR_T) :: a")
    L.append(f"        a = TRANSFER(p, a) - 32_C_INTPTR_T")
    L.append(f"        CALL C_F_POINTER(TRANSFER(a, p), h)")
    L.append(f"        IF (h%magic /= VIT_ADOPT_MAGIC) ERROR STOP 'VIT adopt: ' // what // &")
    L.append(f"            ' was not allocated with vit_adopt_alloc; only such a buffer can be adopted'")
    L.append(f"        IF (h%rank /= 0 .AND. h%rank /= rank) ERROR STOP 'VIT adopt: ' // what // &")
    L.append(f"            ' was allocated with vit_adopt_alloc_lb for a different rank'")
    L.append(f"    END FUNCTION vit_adopt_header")
    L.append(f"")
    return L


def _unmeasured_field_stop(sub_name: str, fields: list) -> list:
    """`ERROR STOP` for a field kind the direct-caller pair does not convert.

    THE FIELD NAMES GO IN A COMMENT AND NOT IN THE LITERAL. Free-form Fortran
    truncates a source line at 132 characters, and gfortran is invoked here with
    `-Werror=line-truncation`, so a message carrying the field list DOES NOT
    COMPILE for any type with more than two or three of them. Measured on VIT's
    own conformance fixture, which is the only reason it was caught at all:
    `AllocType`, `CharFieldType` and `NestedType` each flipped from
    `integrates = yes` to `integrates = no` on `Cannot open module file
    vit_<t>_view.mod` -- a missing .mod, three cells away from the line that
    caused it. Unit #5's 132-column bridge-generator finding, one generator over.

    So the literal names only the SUBROUTINE, which is bounded by the type name,
    and the fields are listed in wrapped comments above it where a long list
    costs nothing.
    """
    out = ["        ! The direct-caller conversion is not measured on these field(s):"]
    line = "        !  "
    for f in fields:
        if len(line) + len(f) + 2 > 100:
            out.append(line)
            line = "        !  "
        line += f + ", "
    out.append(line.rstrip(", "))
    out.append(f"        ERROR STOP 'VIT: {sub_name}: an unmeasured field kind; "
               f"see the comment above'")
    return out


def _field_to_iso_c_decl(f: DerivedTypeField, kind_aliases: Optional[dict]) -> Optional[str]:
    """Generate an ISO_C_BINDING declaration for a scalar/fixed field in the mirror type."""
    mapping = get_type_mapping(f.fortran_type, kind_aliases)

    if mapping.character_length:
        # BIND(C) types require CHARACTER(LEN=1); use array form for strings
        return f"CHARACTER(KIND=C_CHAR) :: {f.name}({mapping.character_length})"

    if f.is_array and f.array_dims:
        # Fixed-size array — use ISO C type with explicit dimensions
        dims = f.array_dims
        return f"{mapping.iso_c_type} :: {f.name}({dims})"

    # Scalar — use type-appropriate default
    # LOGICAL fields use LOGICAL(C_BOOL) in view structs regardless of the
    # global type_map (which maps LOGICAL→int32_t for non-view struct compat).
    # The populator handles the conversion explicitly.
    if f.fortran_type.upper().startswith("LOGICAL"):
        return f"LOGICAL(C_BOOL) :: {f.name} = .FALSE."
    if mapping.c_type.startswith("vit_complex_"):
        return f"{mapping.iso_c_type} :: {f.name} = (0, 0)"
    return f"{mapping.iso_c_type} :: {f.name} = 0"


def _scalar_assignment(f: DerivedTypeField, kind_aliases: Optional[dict]) -> Optional[str]:
    """Generate a scalar copy assignment for the populator: view%field = convert(src%field)."""
    mapping = get_type_mapping(f.fortran_type, kind_aliases)

    if mapping.character_length:
        # CHARACTER(N) → CHARACTER(1) array: use TRANSFER for byte-level copy
        n = mapping.character_length
        return f"view%{f.name} = TRANSFER(src%{f.name}, view%{f.name})"

    if f.is_array and f.array_dims:
        # Fixed-size array — direct copy
        return f"view%{f.name} = src%{f.name}"

    # Scalar with kind conversion
    # LOGICAL fields: always convert via LOGICAL(src, C_BOOL) regardless of
    # type_map (view structs use C_BOOL for LOGICALs, non-view use int32_t)
    if f.fortran_type.upper().startswith("LOGICAL"):
        return f"view%{f.name} = LOGICAL(src%{f.name}, C_BOOL)"

    if mapping.c_type.startswith("vit_complex_"):
        return f"view%{f.name} = src%{f.name}"
    iso_type = mapping.iso_c_type  # e.g., "REAL(C_DOUBLE)", "INTEGER(C_INT)"
    if iso_type.startswith("REAL("):
        c_kind = mapping.c_kind  # e.g., "C_DOUBLE"
        return f"view%{f.name} = REAL(src%{f.name}, {c_kind})"
    elif iso_type.startswith("INTEGER("):
        c_kind = mapping.c_kind
        return f"view%{f.name} = INT(src%{f.name}, {c_kind})"

    # Fallback: direct copy
    return f"view%{f.name} = src%{f.name}"


def _nested_scalar_assignment(
    parent_field: str, nf: DerivedTypeField, kind_aliases: Optional[dict]
) -> Optional[str]:
    """Generate nested field copy: view%parent%field = convert(src%parent%field)."""
    mapping = get_type_mapping(nf.fortran_type, kind_aliases)

    if mapping.character_length:
        return f"view%{parent_field}%{nf.name} = TRANSFER(src%{parent_field}%{nf.name}, view%{parent_field}%{nf.name})"

    if nf.is_array and nf.array_dims:
        return f"view%{parent_field}%{nf.name} = src%{parent_field}%{nf.name}"

    # LOGICAL: always use LOGICAL(C_BOOL) conversion in view populators
    if nf.fortran_type.upper().startswith("LOGICAL"):
        return f"view%{parent_field}%{nf.name} = LOGICAL(src%{parent_field}%{nf.name}, C_BOOL)"

    iso_type = mapping.iso_c_type
    if iso_type.startswith("REAL("):
        c_kind = mapping.c_kind
        return f"view%{parent_field}%{nf.name} = REAL(src%{parent_field}%{nf.name}, {c_kind})"
    elif iso_type.startswith("INTEGER("):
        c_kind = mapping.c_kind
        return f"view%{parent_field}%{nf.name} = INT(src%{parent_field}%{nf.name}, {c_kind})"

    return f"view%{parent_field}%{nf.name} = src%{parent_field}%{nf.name}"


def _reverse_scalar_assignment(f: DerivedTypeField, kind_aliases: Optional[dict]) -> Optional[list[str]]:
    """Generate reverse scalar copy: dest%field = convert(view%field).

    Returns a list of lines (CHARACTER fields need DO loops).
    """
    mapping = get_type_mapping(f.fortran_type, kind_aliases)

    if mapping.character_length:
        # CHARACTER → copy char-by-char from C_CHAR array
        n = mapping.character_length
        return [
            f"DO i = 1, {n}",
            f"    dest%{f.name}(i:i) = view%{f.name}(i)",
            f"END DO",
        ]

    if f.is_array and f.array_dims:
        # Fixed-size array — direct copy
        return [f"dest%{f.name} = view%{f.name}"]

    # Scalar with kind conversion (reverse direction)
    # LOGICAL: view has C_BOOL, dest has default LOGICAL — direct assign works
    if f.fortran_type.upper().startswith("LOGICAL"):
        return [f"dest%{f.name} = view%{f.name}"]

    # Use ISO C kinds (C_DOUBLE, C_INT) — the populator module only USEs ISO_C_BINDING,
    # not project-specific kind modules (ReKi, IntKi, etc.)
    iso_type = mapping.iso_c_type
    if iso_type.startswith("REAL("):
        c_kind = mapping.c_kind  # e.g., "C_DOUBLE"
        return [f"dest%{f.name} = REAL(view%{f.name}, {c_kind})"]
    elif iso_type.startswith("INTEGER("):
        c_kind = mapping.c_kind  # e.g., "C_INT"
        return [f"dest%{f.name} = INT(view%{f.name}, {c_kind})"]

    # Fallback: direct copy
    return [f"dest%{f.name} = view%{f.name}"]


def _nested_reverse_assignment(
    parent_field: str, nf: DerivedTypeField, kind_aliases: Optional[dict]
) -> Optional[list[str]]:
    """Generate nested reverse copy: dest%parent%field = convert(view%parent%field)."""
    mapping = get_type_mapping(nf.fortran_type, kind_aliases)

    if mapping.character_length:
        n = mapping.character_length
        return [
            f"DO i = 1, {n}",
            f"    dest%{parent_field}%{nf.name}(i:i) = view%{parent_field}%{nf.name}(i)",
            f"END DO",
        ]

    if nf.is_array and nf.array_dims:
        return [f"dest%{parent_field}%{nf.name} = view%{parent_field}%{nf.name}"]

    # LOGICAL: view has C_BOOL, dest has default LOGICAL — direct assign works
    if nf.fortran_type.upper().startswith("LOGICAL"):
        return [f"dest%{parent_field}%{nf.name} = view%{parent_field}%{nf.name}"]

    iso_type = mapping.iso_c_type
    if iso_type.startswith("REAL("):
        c_kind = mapping.c_kind
        return [f"dest%{parent_field}%{nf.name} = REAL(view%{parent_field}%{nf.name}, {c_kind})"]
    elif iso_type.startswith("INTEGER("):
        c_kind = mapping.c_kind
        return [f"dest%{parent_field}%{nf.name} = INT(view%{parent_field}%{nf.name}, {c_kind})"]

    return [f"dest%{parent_field}%{nf.name} = view%{parent_field}%{nf.name}"]


def _extract_kind(fortran_type: str, kind_aliases: Optional[dict]) -> str:
    """Extract the Fortran kind from a type declaration, resolving aliases.

    E.g., 'REAL(DbKi)' → 'DbKi' (keep alias name for readability).
    'REAL(8)' → '8'.
    'INTEGER' → 'KIND(0)' (default kind).
    """
    import re
    m = re.match(r'(?:REAL|INTEGER|LOGICAL)\s*\(\s*(\w+)\s*\)', fortran_type, re.I)
    if m:
        return m.group(1)
    # No explicit kind — use default
    if fortran_type.upper().startswith("REAL"):
        return "KIND(0.0D0)"
    elif fortran_type.upper().startswith("INTEGER"):
        return "KIND(0)"
    return "KIND(0)"
