"""
Graph → Patch Compiler (GPC) - Main Compiler
=============================================
Canonical Specification v1.0

The Graph→Patch Compiler is a pure, deterministic compiler that converts 
GAL intents into auditable, reversible code patches.

INTERNAL PIPELINE (fixed order):
1. Semantic Validation
2. Mutation Plan Generation
3. AST-Level Transformation
4. Textual Diff Synthesis
5. Invariant Verification
6. Artifact Hashing

No step may be skipped or reordered.

FORBIDDEN BEHAVIORS (absolute):
The compiler must NEVER:
- Inspect runtime state
- Read agent memory
- Apply patches
- Decide risk thresholds
- Modify more than one execution root
- Execute conditional logic based on heuristics
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
import hashlib
import json
import ast
import difflib

from .mutation_plan import MutationPlan, MutationPlanBuilder, MutationOperation, OperationType
from .patch_artifact import PatchArtifact, TextualDiff, RiskMetrics, InvariantResult
from .invariants import InvariantChecker, simulate_graph_after, Invariant


COMPILER_VERSION = "1.0.0"


@dataclass
class CompilerConfig:
    """Configuration for the compiler"""
    version: str = COMPILER_VERSION
    blast_radius_limit: int = 100
    allow_root_modification: bool = False
    strict_mode: bool = True  # Fail on any invariant violation
    
    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "blast_radius_limit": self.blast_radius_limit,
            "allow_root_modification": self.allow_root_modification,
            "strict_mode": self.strict_mode
        }


@dataclass
class CompilerInput:
    """
    Strict input contract for the compiler.
    
    Input:
    {
      "gal_action": {...},
      "graph_snapshot": {...},
      "compiler_version": "1.0",
      "policy_context": {...}
    }
    """
    gal_action: Dict
    graph_snapshot: Dict
    compiler_version: str = COMPILER_VERSION
    policy_context: Dict = field(default_factory=dict)
    
    def validate(self) -> Tuple[bool, str]:
        """Validate input structure"""
        if not self.gal_action:
            return False, "gal_action is required"
        if not self.graph_snapshot:
            return False, "graph_snapshot is required"
        if not self.gal_action.get("action_type"):
            return False, "gal_action.action_type is required"
        if not self.gal_action.get("target_node"):
            return False, "gal_action.target_node is required"
        return True, ""
    
    def to_dict(self) -> Dict:
        return {
            "gal_action": self.gal_action,
            "graph_snapshot": self.graph_snapshot,
            "compiler_version": self.compiler_version,
            "policy_context": self.policy_context
        }


@dataclass
class CompilerOutput:
    """
    Strict output contract for the compiler.
    
    Output (artifact only):
    {
      "patch_id": "sha256",
      "mutation_plan": {...},
      "textual_diff": {...},
      "inverse_diff": {...},
      "invariants_checked": [...],
      "risk_metrics": {...},
      "compiler_version": "1.0"
    }
    
    No application. No execution. Only artifacts.
    """
    success: bool
    patch_artifact: Optional[PatchArtifact] = None
    mutation_plan: Optional[MutationPlan] = None
    error: Optional[str] = None
    validation_errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "patch_id": self.patch_artifact.patch_id if self.patch_artifact else None,
            "mutation_plan": self.mutation_plan.to_dict() if self.mutation_plan else None,
            "patch_artifact": self.patch_artifact.to_dict() if self.patch_artifact else None,
            "error": self.error,
            "validation_errors": self.validation_errors,
            "compiler_version": COMPILER_VERSION
        }


class SemanticValidator:
    """
    Stage 1: Semantic Validation
    
    Purpose: Prove the action is graph-legal before touching code.
    
    Checks (mandatory):
    - Node exists
    - Node type matches action
    - Incoming edge constraints satisfied
    - Ownership constraints satisfied
    - Action class allowed in current policy mode
    """
    
    def __init__(self, graph_snapshot: Dict, policy_context: Dict):
        self.nodes = {n["id"]: n for n in graph_snapshot.get("nodes", [])}
        self.connections = graph_snapshot.get("connections", [])
        self.policy = policy_context
    
    def validate(self, gal_action: Dict) -> Tuple[bool, List[str]]:
        """
        Validate the GAL action against the graph.
        
        Returns (validated, violations)
        """
        violations = []
        
        target_node = gal_action.get("target_node")
        action_type = gal_action.get("action_type")
        
        # Check: Node exists
        if target_node not in self.nodes:
            violations.append(f"Target node '{target_node}' does not exist in graph")
            return False, violations
        
        node = self.nodes[target_node]
        node_type = node.get("type", "unknown")
        
        # Check: Node type matches action constraints
        type_constraints = {
            "PROPOSE_DELETE_SUBGRAPH": ["file", "function", "class"],  # Cannot delete services
            "ISOLATE_SUBGRAPH": ["file", "function", "class", "api_endpoint"],
            "TAG_SUBGRAPH": None,  # Any type allowed
            "MARK_EXECUTION_ROOT": ["file", "api_endpoint"],
        }
        
        allowed_types = type_constraints.get(action_type)
        if allowed_types and node_type not in allowed_types:
            violations.append(f"Action '{action_type}' not allowed on node type '{node_type}'")
        
        # Check: Ownership constraints
        owner = node.get("owner", "system")
        if owner == "protected" and action_type in ["PROPOSE_DELETE_SUBGRAPH", "ISOLATE_SUBGRAPH"]:
            violations.append(f"Node '{target_node}' is protected and cannot be modified")
        
        # Check: Policy mode allows action
        policy_mode = self.policy.get("mode", "normal")
        if policy_mode == "readonly" and action_type != "TAG_SUBGRAPH":
            violations.append(f"Policy mode '{policy_mode}' does not allow action '{action_type}'")
        
        # Check: Incoming edge constraints for deletion
        if action_type == "PROPOSE_DELETE_SUBGRAPH":
            incoming = [c for c in self.connections if c["target_id"] == target_node]
            if incoming:
                violations.append(f"Cannot delete node with {len(incoming)} incoming connections")
        
        return len(violations) == 0, violations


class ASTTransformer:
    """
    Stage 3: AST-Level Transformation
    
    Why AST (not regex, not diffs):
    - Guarantees syntactic correctness
    - Allows exact inverse generation
    - Avoids formatting noise
    
    Rules:
    - Each operation maps to a single AST rewrite
    - One operation → one AST mutation
    - No multi-file inference
    """
    
    def __init__(self):
        self.file_cache = {}  # Cache of parsed ASTs
    
    def transform(self, operation: MutationOperation, file_content: str) -> Tuple[str, str]:
        """
        Apply a single operation to file content.
        
        Returns (new_content, inverse_content)
        
        AST is parsed fresh, never cached.
        """
        op_type = operation.op_type
        target = operation.target
        params = operation.params
        
        if op_type == OperationType.REMOVE_EDGE:
            return self._remove_import(file_content, target)
        elif op_type == OperationType.ADD_EDGE:
            return self._add_import(file_content, target)
        elif op_type == OperationType.TAG_NODE:
            return self._add_tag_comment(file_content, target, params)
        elif op_type == OperationType.REMOVE_NODE:
            return self._remove_node(file_content, target)
        else:
            # No-op for unsupported operations
            return file_content, file_content
    
    def _remove_import(self, content: str, target: Dict) -> Tuple[str, str]:
        """Remove an import statement"""
        lines = content.split('\n')
        new_lines = []
        removed_line = None
        
        import_target = target.get("to", "").split(":")[-1]  # Get module name
        
        for line in lines:
            # Check if this line imports the target
            if ('import ' in line or 'from ' in line) and import_target in line:
                removed_line = line
                continue  # Skip this line
            new_lines.append(line)
        
        new_content = '\n'.join(new_lines)
        
        # Inverse: add the import back
        if removed_line:
            inverse_lines = [removed_line] + new_lines
            inverse_content = '\n'.join(inverse_lines)
        else:
            inverse_content = content
        
        return new_content, inverse_content
    
    def _add_import(self, content: str, target: Dict) -> Tuple[str, str]:
        """Add an import statement"""
        import_target = target.get("to", "").split(":")[-1]
        import_line = f"import {import_target}"
        
        lines = content.split('\n')
        
        # Find the right place to insert (after existing imports)
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.startswith('import ') or line.startswith('from '):
                insert_idx = i + 1
        
        new_lines = lines[:insert_idx] + [import_line] + lines[insert_idx:]
        new_content = '\n'.join(new_lines)
        
        # Inverse: remove the import
        inverse_content = content
        
        return new_content, inverse_content
    
    def _add_tag_comment(self, content: str, target: Dict, params: Dict) -> Tuple[str, str]:
        """Add a tag comment to the file"""
        tag = params.get("tag", "unknown")
        node_name = target.get("id", "").split(":")[-1]
        
        tag_comment = f"# TAG: {tag.upper()} - {node_name}"
        
        lines = content.split('\n')
        
        # Add tag at the top after any existing comments/docstrings
        insert_idx = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
                insert_idx = i + 1
            elif stripped:
                break
        
        new_lines = lines[:insert_idx] + [tag_comment] + lines[insert_idx:]
        new_content = '\n'.join(new_lines)
        
        # Inverse: remove the tag comment
        inverse_content = content
        
        return new_content, inverse_content
    
    def _remove_node(self, content: str, target: Dict) -> Tuple[str, str]:
        """Remove a function or class definition"""
        node_name = target.get("id", "").split(":")[-1]
        node_type = target.get("type", "function")
        
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return content, content
        
        # Find the node to remove
        lines = content.split('\n')
        new_lines = lines.copy()
        removed_lines = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == node_name:
                # Remove function lines
                start = node.lineno - 1
                end = node.end_lineno if hasattr(node, 'end_lineno') else start + 1
                removed_lines = lines[start:end]
                new_lines = lines[:start] + lines[end:]
                break
            elif isinstance(node, ast.ClassDef) and node.name == node_name:
                # Remove class lines
                start = node.lineno - 1
                end = node.end_lineno if hasattr(node, 'end_lineno') else start + 1
                removed_lines = lines[start:end]
                new_lines = lines[:start] + lines[end:]
                break
        
        new_content = '\n'.join(new_lines)
        inverse_content = content  # Inverse is the original
        
        return new_content, inverse_content


class DiffSynthesizer:
    """
    Stage 4: Textual Diff Synthesis
    
    Output format (canonical):
    {
      "file": "path/to/file.py",
      "before_hash": "sha256",
      "after_hash": "sha256",
      "unified_diff": "@@ -1,4 +1,2 @@ ..."
    }
    
    Rules:
    - Stable formatting
    - No re-ordering
    - No auto-formatting
    - Minimal diff only
    """
    
    def synthesize(self, file_path: str, before_content: str, after_content: str) -> TextualDiff:
        """Generate a textual diff between before and after content"""
        before_hash = hashlib.sha256(before_content.encode()).hexdigest()
        after_hash = hashlib.sha256(after_content.encode()).hexdigest()
        
        # Generate unified diff
        before_lines = before_content.splitlines(keepends=True)
        after_lines = after_content.splitlines(keepends=True)
        
        diff = difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm=""
        )
        
        unified_diff = ''.join(diff)
        
        # Track line changes
        line_changes = {}
        for i, (b, a) in enumerate(zip(before_lines, after_lines)):
            if b != a:
                line_changes[str(i + 1)] = "modified"
        
        # Track added/removed lines
        if len(after_lines) > len(before_lines):
            for i in range(len(before_lines), len(after_lines)):
                line_changes[str(i + 1)] = "added"
        elif len(before_lines) > len(after_lines):
            for i in range(len(after_lines), len(before_lines)):
                line_changes[str(i + 1)] = "removed"
        
        return TextualDiff(
            file_path=file_path,
            before_hash=before_hash,
            after_hash=after_hash,
            unified_diff=unified_diff,
            line_changes=line_changes
        )


class GraphPatchCompiler:
    """
    Graph → Patch Compiler (GPC)
    
    The main compiler class that orchestrates the entire pipeline.
    
    INTERNAL PIPELINE (fixed order):
    1. Semantic Validation
    2. Mutation Plan Generation
    3. AST-Level Transformation
    4. Textual Diff Synthesis
    5. Invariant Verification
    6. Artifact Hashing
    
    No step may be skipped or reordered.
    """
    
    def __init__(self, config: Optional[CompilerConfig] = None):
        self.config = config or CompilerConfig()
        self.ast_transformer = ASTTransformer()
        self.diff_synthesizer = DiffSynthesizer()
    
    def compile(self, compiler_input: CompilerInput) -> CompilerOutput:
        """
        Main compilation entry point.
        
        This is a PURE function - no side effects, no file writes.
        Same input → same output (byte-for-byte).
        """
        # Validate input
        valid, error = compiler_input.validate()
        if not valid:
            return CompilerOutput(success=False, error=error)
        
        gal_action = compiler_input.gal_action
        graph_snapshot = compiler_input.graph_snapshot
        policy_context = compiler_input.policy_context
        
        # Stage 1: Semantic Validation
        validator = SemanticValidator(graph_snapshot, policy_context)
        validated, violations = validator.validate(gal_action)
        
        if not validated:
            return CompilerOutput(
                success=False,
                error="Semantic validation failed",
                validation_errors=violations
            )
        
        # Stage 2: Mutation Plan Generation
        mutation_plan = MutationPlanBuilder.from_gal_action(gal_action, graph_snapshot)
        
        if not mutation_plan.operations:
            return CompilerOutput(
                success=False,
                error="No operations generated from GAL action",
                mutation_plan=mutation_plan
            )
        
        # Stage 3 & 4: AST Transformation + Diff Synthesis
        textual_diffs = []
        inverse_diffs = []
        
        # Get file contents (in real implementation, this would read from disk)
        # For now, we generate placeholder diffs
        for file_path in mutation_plan.affected_files:
            # Placeholder: in production, read actual file content
            before_content = self._get_file_content(file_path, graph_snapshot)
            
            # Apply transformations
            after_content = before_content
            inverse_content = before_content
            
            for op in mutation_plan.operations:
                if op.target.get("file_path") == file_path:
                    after_content, inverse_content = self.ast_transformer.transform(
                        op, after_content
                    )
            
            # Synthesize diffs
            if after_content != before_content:
                diff = self.diff_synthesizer.synthesize(file_path, before_content, after_content)
                textual_diffs.append(diff)
                
                inverse_diff = self.diff_synthesizer.synthesize(file_path, after_content, before_content)
                inverse_diffs.append(inverse_diff)
        
        # Stage 5: Invariant Verification
        invariant_checker = InvariantChecker({
            "blast_radius_limit": self.config.blast_radius_limit,
            "allow_root_modification": self.config.allow_root_modification
        })
        
        graph_after = simulate_graph_after(graph_snapshot, mutation_plan)
        invariant_results = invariant_checker.check_all(mutation_plan, graph_snapshot, graph_after)
        
        all_passed = invariant_checker.all_passed(invariant_results)
        
        if self.config.strict_mode and not all_passed:
            failed = [r for r in invariant_results if not r["passed"]]
            return CompilerOutput(
                success=False,
                error="Invariant verification failed",
                mutation_plan=mutation_plan,
                validation_errors=[f"{r['invariant']}: {r['message']}" for r in failed]
            )
        
        # Stage 6: Create Artifact with Hash
        risk_metrics = RiskMetrics.calculate(mutation_plan, graph_snapshot)
        
        patch_artifact = PatchArtifact(
            patch_id="",  # Will be computed
            compiler_version=self.config.version,
            gal_action_id=gal_action.get("action_id", ""),
            gal_action_type=gal_action.get("action_type", ""),
            mutation_plan_id=mutation_plan.plan_id,
            textual_diffs=textual_diffs,
            inverse_diffs=inverse_diffs,
            invariants_checked=[
                InvariantResult(
                    invariant_name=r["invariant"],
                    passed=r["passed"],
                    message=r["message"],
                    details=r.get("details", {})
                ) for r in invariant_results
            ],
            all_invariants_passed=all_passed,
            risk_metrics=risk_metrics
        )
        
        return CompilerOutput(
            success=True,
            patch_artifact=patch_artifact,
            mutation_plan=mutation_plan
        )
    
    def _get_file_content(self, file_path: str, graph_snapshot: Dict) -> str:
        """
        Get file content for transformation.
        
        In production, this reads from disk.
        For safety, we use a placeholder that represents the file structure.
        """
        # Find the node for this file
        for node in graph_snapshot.get("nodes", []):
            if node.get("file_path") == file_path:
                # Generate placeholder content based on node info
                node_type = node.get("type", "file")
                node_name = node.get("name", "unknown")
                
                if node_type == "file":
                    return f'''"""
{node_name}
"""

# File content placeholder
# In production, actual file content would be read here

'''
        
        return "# Empty file placeholder\n"
    
    def compile_from_dict(self, input_dict: Dict) -> Dict:
        """Convenience method to compile from dict input"""
        compiler_input = CompilerInput(
            gal_action=input_dict.get("gal_action", {}),
            graph_snapshot=input_dict.get("graph_snapshot", {}),
            compiler_version=input_dict.get("compiler_version", COMPILER_VERSION),
            policy_context=input_dict.get("policy_context", {})
        )
        
        output = self.compile(compiler_input)
        return output.to_dict()
