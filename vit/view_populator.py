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

    lines = []
    lines.append(f"! Auto-generated by VIT (Verified Incremental Translator)")
    lines.append(f"! View populator for TYPE({name})")
    lines.append(f"! DO NOT EDIT — regenerate with: vit analyze-types --scan-allocatable --type {name}")
    lines.append(f"")
    lines.append(f"MODULE {module_name}")
    lines.append(f"    USE ISO_C_BINDING")
    if deferred_char_fields:
        # The reverse-copy REFUSES an over-long assignment rather than
        # truncating it, and says so. Refusing in silence would be the same
        # defect one level down.
        lines.append(f"    USE ISO_FORTRAN_ENV, ONLY: ERROR_UNIT")
    if source_module:
        lines.append(f"    USE {source_module}, ONLY: {name}")
    # USE child populator modules for ALLOCATABLE TYPE() view fields
    for field_name, child_info in view_type_alloc_fields.items():
        child_lower = child_info.name.lower()
        child_view_type = f"{child_lower}_view_t"
        child_populate_sub = f"vit_populate_{child_lower}"
        child_module = f"vit_{child_lower}_view"
        lines.append(f"    USE {child_module}, ONLY: {child_view_type}, {child_populate_sub}")
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
    lines.append(f"    PUBLIC :: {view_type_name}, {sub_name}, {reverse_name}, {stash_name}")
    lines.append(f"")
    lines.append(f"    ! Kernel stash: pointer to original Fortran type, set by wrapper before calling C++.")
    lines.append(f"    ! Callee bridges USE this to pass the original type to Fortran callees.")
    lines.append(f"    TYPE({name}), POINTER, SAVE :: {stash_name} => NULL()")
    # Module-level SAVE arrays for ALLOCATABLE TYPE() view fields
    for field_name, child_info in view_type_alloc_fields.items():
        child_lower = child_info.name.lower()
        child_view_type = f"{child_lower}_view_t"
        save_array_name = f"vit_{field_name.lower()}_views_{name_lower}"
        lines.append(f"    TYPE({child_view_type}), ALLOCATABLE, TARGET, SAVE :: {save_array_name}(:)")
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
    lines.append(f"END MODULE {module_name}")
    lines.append(f"")

    return "\n".join(lines)


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
