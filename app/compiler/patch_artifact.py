"""
Patch Artifact - Compiler Output
================================
The final output of the Graph→Patch Compiler.

Contains:
- Textual diff (deterministic)
- Inverse diff (for rollback)
- Invariants checked
- Risk metrics
- Immutable hash for auditability
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
import hashlib
import json


@dataclass
class TextualDiff:
    """
    A single file diff.
    
    Format is canonical:
    - Stable formatting
    - No re-ordering
    - No auto-formatting
    - Minimal diff only
    """
    file_path: str
    before_hash: str
    after_hash: str
    unified_diff: str
    line_changes: Dict = field(default_factory=dict)  # {line_num: change_type}
    
    def to_dict(self) -> Dict:
        return {
            "file": self.file_path,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "unified_diff": self.unified_diff,
            "line_changes": self.line_changes
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'TextualDiff':
        return cls(
            file_path=data.get("file", ""),
            before_hash=data.get("before_hash", ""),
            after_hash=data.get("after_hash", ""),
            unified_diff=data.get("unified_diff", ""),
            line_changes=data.get("line_changes", {})
        )
    
    def compute_hash(self) -> str:
        """Compute hash of this diff"""
        data = f"{self.file_path}:{self.before_hash}:{self.after_hash}:{self.unified_diff}"
        return hashlib.sha256(data.encode()).hexdigest()


@dataclass
class InvariantResult:
    """Result of an invariant check"""
    invariant_name: str
    passed: bool
    message: str
    details: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "invariant": self.invariant_name,
            "passed": self.passed,
            "message": self.message,
            "details": self.details
        }


@dataclass
class RiskMetrics:
    """Risk assessment for the patch"""
    blast_radius: int
    affected_files: int
    affected_functions: int
    execution_roots_touched: int
    cross_service: bool
    risk_score: float  # 0-10
    
    def to_dict(self) -> Dict:
        return {
            "blast_radius": self.blast_radius,
            "affected_files": self.affected_files,
            "affected_functions": self.affected_functions,
            "execution_roots_touched": self.execution_roots_touched,
            "cross_service": self.cross_service,
            "risk_score": self.risk_score
        }
    
    @classmethod
    def calculate(cls, mutation_plan, graph_snapshot: Dict) -> 'RiskMetrics':
        """Calculate risk metrics from mutation plan"""
        affected_files = len(mutation_plan.affected_files)
        affected_nodes = len(mutation_plan.affected_nodes)
        
        # Check for execution roots
        nodes = {n["id"]: n for n in graph_snapshot.get("nodes", [])}
        roots_touched = sum(
            1 for nid in mutation_plan.affected_nodes
            if nodes.get(nid, {}).get("type") in ["service", "api_endpoint"]
        )
        
        # Check for cross-service
        services = set()
        for nid in mutation_plan.affected_nodes:
            node = nodes.get(nid, {})
            if node.get("service"):
                services.add(node["service"])
        cross_service = len(services) > 1
        
        # Calculate risk score
        risk_score = (
            min(affected_files * 0.5, 3) +
            min(affected_nodes * 0.2, 3) +
            (roots_touched * 2) +
            (2 if cross_service else 0)
        )
        
        return cls(
            blast_radius=affected_nodes,
            affected_files=affected_files,
            affected_functions=affected_nodes,
            execution_roots_touched=roots_touched,
            cross_service=cross_service,
            risk_score=min(10, risk_score)
        )


@dataclass
class PatchArtifact:
    """
    The complete, immutable output of the Graph→Patch Compiler.
    
    This is the ONLY artifact that can be used for execution.
    No application. No execution. Only artifacts.
    """
    patch_id: str  # SHA256 hash
    compiler_version: str
    
    # Source references
    gal_action_id: str
    gal_action_type: str
    mutation_plan_id: str
    
    # The actual diffs
    textual_diffs: List[TextualDiff] = field(default_factory=list)
    inverse_diffs: List[TextualDiff] = field(default_factory=list)
    
    # Verification
    invariants_checked: List[InvariantResult] = field(default_factory=list)
    all_invariants_passed: bool = False
    
    # Risk assessment
    risk_metrics: Optional[RiskMetrics] = None
    
    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.patch_id:
            self.patch_id = self._compute_patch_id()
    
    def _compute_patch_id(self) -> str:
        """
        Compute deterministic patch ID.
        
        patch_id = SHA256(
            compiler_version +
            gal_action +
            mutation_plan +
            textual_diff
        )
        """
        components = [
            self.compiler_version,
            self.gal_action_id,
            self.gal_action_type,
            self.mutation_plan_id,
        ]
        
        for diff in self.textual_diffs:
            components.append(diff.compute_hash())
        
        combined = ":".join(components)
        return hashlib.sha256(combined.encode()).hexdigest()
    
    def is_valid(self) -> bool:
        """Check if this artifact is valid for execution"""
        return (
            self.all_invariants_passed and
            len(self.textual_diffs) > 0 and
            self.patch_id == self._compute_patch_id()  # Integrity check
        )
    
    def get_rollback_artifact(self) -> 'PatchArtifact':
        """Generate the rollback artifact"""
        return PatchArtifact(
            patch_id="",  # Will be computed
            compiler_version=self.compiler_version,
            gal_action_id=f"rollback_{self.gal_action_id}",
            gal_action_type=f"ROLLBACK_{self.gal_action_type}",
            mutation_plan_id=f"rollback_{self.mutation_plan_id}",
            textual_diffs=self.inverse_diffs,
            inverse_diffs=self.textual_diffs,
            invariants_checked=[],  # Rollback doesn't need invariant check
            all_invariants_passed=True,
            risk_metrics=self.risk_metrics,
            metadata={
                "is_rollback": True,
                "original_patch_id": self.patch_id
            }
        )
    
    def to_dict(self) -> Dict:
        return {
            "patch_id": self.patch_id,
            "compiler_version": self.compiler_version,
            "gal_action_id": self.gal_action_id,
            "gal_action_type": self.gal_action_type,
            "mutation_plan_id": self.mutation_plan_id,
            "textual_diffs": [d.to_dict() for d in self.textual_diffs],
            "inverse_diffs": [d.to_dict() for d in self.inverse_diffs],
            "invariants_checked": [i.to_dict() for i in self.invariants_checked],
            "all_invariants_passed": self.all_invariants_passed,
            "risk_metrics": self.risk_metrics.to_dict() if self.risk_metrics else None,
            "created_at": self.created_at,
            "metadata": self.metadata,
            "is_valid": self.is_valid()
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'PatchArtifact':
        artifact = cls(
            patch_id=data.get("patch_id", ""),
            compiler_version=data.get("compiler_version", ""),
            gal_action_id=data.get("gal_action_id", ""),
            gal_action_type=data.get("gal_action_type", ""),
            mutation_plan_id=data.get("mutation_plan_id", ""),
            all_invariants_passed=data.get("all_invariants_passed", False),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {})
        )
        
        for diff_data in data.get("textual_diffs", []):
            artifact.textual_diffs.append(TextualDiff.from_dict(diff_data))
        
        for diff_data in data.get("inverse_diffs", []):
            artifact.inverse_diffs.append(TextualDiff.from_dict(diff_data))
        
        for inv_data in data.get("invariants_checked", []):
            artifact.invariants_checked.append(InvariantResult(
                invariant_name=inv_data.get("invariant", ""),
                passed=inv_data.get("passed", False),
                message=inv_data.get("message", ""),
                details=inv_data.get("details", {})
            ))
        
        return artifact
