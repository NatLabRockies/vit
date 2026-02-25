"""
VIT — Verified Incremental Translator

A pipeline orchestrator for trustworthy Fortran→C++ incremental translation.

VIT handles everything EXCEPT the translation itself:
- Extract functions with KGen
- Generate deterministic Fortran↔C interfaces (no LLM needed)
- Wire C++ into KGen kernels for verification
- Run bit-identical verification against captured runtime state
- Generate integration patches for the real codebase

The translation step is intentionally external — use CodeScribe, any LLM,
or hand-write the C++. VIT verifies it's correct.
"""

__version__ = "0.1.0"
