"""
Graph → Patch Compiler (GPC)
============================
Canonical Specification v1.0

The Graph→Patch Compiler is a pure, deterministic compiler that converts 
GAL intents into auditable, reversible code patches.

It is the ONLY component allowed to bridge:
    graph-level intent → textual code mutation

No agent, UI, or API may bypass it.

NON-NEGOTIABLE INVARIANTS (AXIOMS):
1. Purity - No file writes, no side effects, no global state
2. Determinism - Same input → same output (byte-for-byte)
3. Total reversibility - Every patch has a guaranteed inverse
4. Structural safety - Execution roots cannot change unless explicitly allowed
5. Graph-authoritative - Text is derived; graph is truth
"""

from .gpc import GraphPatchCompiler, CompilerConfig, CompilerInput, CompilerOutput
from .mutation_plan import MutationPlan, MutationOperation, OperationType
from .patch_artifact import PatchArtifact, TextualDiff
from .invariants import InvariantChecker, Invariant
from .formal_invariants import (
    FormalInvariantChecker, 
    FormalInvariant, 
    InvariantRegistry,
    Proof,
    ProofStatus,
    Predicate,
    Quantifier
)

__all__ = [
    'GraphPatchCompiler',
    'CompilerConfig',
    'CompilerInput',
    'CompilerOutput',
    'MutationPlan',
    'MutationOperation',
    'OperationType',
    'PatchArtifact',
    'TextualDiff',
    'InvariantChecker',
    'Invariant',
    'FormalInvariantChecker',
    'FormalInvariant',
    'InvariantRegistry',
    'Proof',
    'ProofStatus',
    'Predicate',
    'Quantifier'
]
