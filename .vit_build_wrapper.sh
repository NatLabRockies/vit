#!/bin/bash
set -e

# VIT: State capture build wrapper
# Prepares the KGen-instrumented tree so the build links and capture
# actually produces state. See build_state_capture_wrapper's docstring
# for all nine steps and which are KGen's problem vs ours.

KGEN_UTILS_SRC="/tmp/kernel/kgen_utils.f90"
KGEN_UTILS_DEST="/tmp/kgen_utils.f90"
CMAKE_FILE="/tmp/CMakeLists.txt"

# Diagnostics are opt-in: export VIT_WRAPPER_LOG=/path/to/log to enable.
# Worth doing when capture reports no state data -- several failures here
# are silent by construction (a shared library links with unresolved
# symbols, so the build succeeds and only the capture comes back empty).
VIT_LOG="${VIT_WRAPPER_LOG:-/dev/null}"
echo "VIT_WRAPPER: invoked at $(date), cwd=$(pwd)" >> "$VIT_LOG"
echo "VIT_WRAPPER: KGEN_UTILS_SRC=$KGEN_UTILS_SRC exists=$([ -f $KGEN_UTILS_SRC ] && echo YES || echo NO)" >> "$VIT_LOG"

# Step 1: Copy kgen_utils.f90 into source tree (if it exists)
if [ -f "$KGEN_UTILS_SRC" ]; then
    cp "$KGEN_UTILS_SRC" "$KGEN_UTILS_DEST"
    # (kgen_utils.f90 copied as-is, no modifications needed)
    echo "VIT: Copied kgen_utils.f90 to source tree"
    echo "VIT_WRAPPER: copied kgen_utils.f90 to $KGEN_UTILS_DEST" >> "$VIT_LOG"

    # Patch CMakeLists.txt to include kgen_utils.f90 (only once)
    if [ -f "$CMAKE_FILE" ]; then
        # Backup only if no backup exists (wrapper may run multiple times)
        if [ ! -f "${CMAKE_FILE}.vit_backup" ]; then
            cp "$CMAKE_FILE" "${CMAKE_FILE}.vit_backup"
        fi
        # Add kgen_utils.f90 only if not already present
        if ! grep -q "kgen_utils.f90" "$CMAKE_FILE"; then
            sed -i '0,/^\( *src\/[A-Za-z_]*\.f90\)$/s//\1\n    src\/kgen_utils.f90/' "$CMAKE_FILE" 
            echo "VIT: Patched CMakeLists.txt to include kgen_utils.f90"
            echo "VIT: Reconfiguring cmake to pick up kgen_utils.f90"
            cd "/tmp/build" && cmake .. > /dev/null 2>&1
        fi
    fi
else
    echo "VIT_WRAPPER: kgen_utils.f90 not found at $KGEN_UTILS_SRC — skipping injection" >> "$VIT_LOG"
fi

# Step 2d: Touch source files to force cmake recompilation
# (Docker filesystem timestamps may not update on cp)
for f90 in "/tmp/src"/*.f90 "/tmp/src"/*.F90; do
    [ -f "$f90" ] && touch "$f90"
done

# Step 2e: Fix cross-module KGen instrumentation
python3 -c "
import sys; sys.path.insert(0, \"/workspace/vit\")
from vit.extract import fix_cross_module_instrumentation
fixes = fix_cross_module_instrumentation(
    \"/workspace/vit/state\",
    \"/tmp/CMakeLists.txt\",
    \"/tmp\"
)
for f in fixes: print(\"VIT: \" + f)
" 2>&1 | tee -a "$VIT_LOG"

# Step 3: Reconfigure cmake if CMakeLists was modified
if [ -f "/tmp/CMakeLists.txt.vit_backup" ]; then
    echo "VIT_WRAPPER: reconfiguring cmake" >> "$VIT_LOG"
    cd "/tmp/build" && cmake .. >> "$VIT_LOG" 2>&1
    echo "VIT_WRAPPER: cmake reconfigure exit code=$?" >> "$VIT_LOG"
fi

# Step 4: Run the actual build
# Temporarily disable set -e so the cleanup step always runs
set +e
echo build
BUILD_RC=$?
set -e

exit $BUILD_RC
