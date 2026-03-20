"""
Mutation Plan - Core Abstraction
================================
A Mutation Plan is a pure graph-delta, not code.

Properties:
- Graph-native
- Order-independent
- Side-effect-free
- Fully reversible

No text, no AST yet - just graph operations.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import uuid
import json
import hashlib


class OperationType(Enum):
    """Graph-level operation types"""
    # Edge operations
    REMOVE_EDGE = "REMOVE_EDGE"
    ADD_EDGE = "ADD_EDGE"
    MODIFY_EDGE = "MODIFY_EDGE"
    
    # Node operations
    REMOVE_NODE = "REMOVE_NODE"
    ADD_NODE = "ADD_NODE"
    MODIFY_NODE = "MODIFY_NODE"
    
    # Metadata operations
    TAG_NODE = "TAG_NODE"
    UNTAG_NODE = "UNTAG_NODE"
    SET_OWNER = "SET_OWNER"
    
    # Structural operations
    ISOLATE_SUBGRAPH = "ISOLATE_SUBGRAPH"
    COLLAPSE_SUBGRAPH = "COLLAPSE_SUBGRAPH"
    INLINE_NODE = "INLINE_NODE"


class EdgeType(Enum):
    """Edge types in the graph"""
    IMPORTS = "imports"
    CALLS = "calls"
    OWNS = "owns"
    EXPOSES = "exposes"
    SPAWNS = "spawns"
    SCHEDULES = "schedules"
    DEPENDS_ON = "depends_on"


@dataclass
class EdgeRef:
    """Reference to an edge in the graph"""
    edge_type: str
    source_id: str
    target_id: str
    
    def to_dict(self) -> Dict:
        return {
            "type": self.edge_type,
            "from": self.source_id,
            "to": self.target_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'EdgeRef':
        return cls(
            edge_type=data.get("type", ""),
            source_id=data.get("from", ""),
            target_id=data.get("to", "")
        )


@dataclass
class NodeRef:
    """Reference to a node in the graph"""
    node_id: str
    node_type: str
    file_path: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "id": self.node_id,
            "type": self.node_type,
            "file_path": self.file_path
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'NodeRef':
        return cls(
            node_id=data.get("id", ""),
            node_type=data.get("type", ""),
            file_path=data.get("file_path")
        )


@dataclass
class MutationOperation:
    """
    A single graph mutation operation.
    
    Each operation maps to exactly one AST rewrite.
    One operation → one AST mutation.
    """
    op_id: str
    op_type: OperationType
    target: Dict  # NodeRef or EdgeRef as dict
    params: Dict = field(default_factory=dict)
    
    # For reversibility
    inverse_op_type: Optional[OperationType] = None
    inverse_target: Optional[Dict] = None
    inverse_params: Optional[Dict] = None
    
    def __post_init__(self):
        if not self.op_id:
            self.op_id = str(uuid.uuid4())[:8]
        
        # Auto-generate inverse operation
        if self.inverse_op_type is None:
            self._generate_inverse()
    
    def _generate_inverse(self):
        """Generate the inverse operation for reversibility"""
        inverse_map = {
            OperationType.REMOVE_EDGE: OperationType.ADD_EDGE,
            OperationType.ADD_EDGE: OperationType.REMOVE_EDGE,
            OperationType.REMOVE_NODE: OperationType.ADD_NODE,
            OperationType.ADD_NODE: OperationType.REMOVE_NODE,
            OperationType.TAG_NODE: OperationType.UNTAG_NODE,
            OperationType.UNTAG_NODE: OperationType.TAG_NODE,
            OperationType.ISOLATE_SUBGRAPH: OperationType.ADD_EDGE,  # Reconnect
        }
        
        self.inverse_op_type = inverse_map.get(self.op_type, self.op_type)
        self.inverse_target = self.target.copy() if self.target else None
        self.inverse_params = self.params.copy() if self.params else None
    
    def to_dict(self) -> Dict:
        return {
            "op_id": self.op_id,
            "op": self.op_type.value,
            "target": self.target,
            "params": self.params,
            "inverse": {
                "op": self.inverse_op_type.value if self.inverse_op_type else None,
                "target": self.inverse_target,
                "params": self.inverse_params
            }
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'MutationOperation':
        inverse = data.get("inverse", {})
        return cls(
            op_id=data.get("op_id", ""),
            op_type=OperationType(data.get("op")),
            target=data.get("target", {}),
            params=data.get("params", {}),
            inverse_op_type=OperationType(inverse.get("op")) if inverse.get("op") else None,
            inverse_target=inverse.get("target"),
            inverse_params=inverse.get("params")
        )


@dataclass
class MutationPlan:
    """
    A Mutation Plan is a pure graph-delta.
    
    This is the most important structure in the compiler.
    It represents intent at the graph level, before any code transformation.
    """
    plan_id: str
    gal_action_id: str
    gal_action_type: str
    operations: List[MutationOperation] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    
    # Computed properties
    affected_nodes: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.plan_id:
            self.plan_id = str(uuid.uuid4())
    
    def add_operation(self, op: MutationOperation):
        """Add an operation to the plan"""
        self.operations.append(op)
        self._update_affected()
    
    def _update_affected(self):
        """Update affected nodes and files lists"""
        nodes = set()
        files = set()
        
        for op in self.operations:
            target = op.target
            if "id" in target:
                nodes.add(target["id"])
            if "from" in target:
                nodes.add(target["from"])
            if "to" in target:
                nodes.add(target["to"])
            if "file_path" in target and target["file_path"]:
                files.add(target["file_path"])
        
        self.affected_nodes = list(nodes)
        self.affected_files = list(files)
    
    def get_inverse_plan(self) -> 'MutationPlan':
        """Generate the inverse plan for rollback"""
        inverse_ops = []
        
        # Reverse order for proper rollback
        for op in reversed(self.operations):
            inverse_op = MutationOperation(
                op_id=f"inv_{op.op_id}",
                op_type=op.inverse_op_type or op.op_type,
                target=op.inverse_target or op.target,
                params=op.inverse_params or op.params
            )
            inverse_ops.append(inverse_op)
        
        return MutationPlan(
            plan_id=f"inv_{self.plan_id}",
            gal_action_id=f"rollback_{self.gal_action_id}",
            gal_action_type=f"ROLLBACK_{self.gal_action_type}",
            operations=inverse_ops,
            metadata={"original_plan_id": self.plan_id, "is_rollback": True}
        )
    
    def compute_hash(self) -> str:
        """Compute deterministic hash of the plan"""
        data = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(data.encode()).hexdigest()
    
    def to_dict(self) -> Dict:
        return {
            "plan_id": self.plan_id,
            "gal_action_id": self.gal_action_id,
            "gal_action_type": self.gal_action_type,
            "operations": [op.to_dict() for op in self.operations],
            "metadata": self.metadata,
            "affected_nodes": self.affected_nodes,
            "affected_files": self.affected_files,
            "operation_count": len(self.operations)
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'MutationPlan':
        plan = cls(
            plan_id=data.get("plan_id", ""),
            gal_action_id=data.get("gal_action_id", ""),
            gal_action_type=data.get("gal_action_type", ""),
            metadata=data.get("metadata", {})
        )
        
        for op_data in data.get("operations", []):
            plan.operations.append(MutationOperation.from_dict(op_data))
        
        plan._update_affected()
        return plan


class MutationPlanBuilder:
    """Builder for creating mutation plans from GAL actions"""
    
    @staticmethod
    def from_gal_action(gal_action: Dict, graph_snapshot: Dict) -> MutationPlan:
        """
        Convert a GAL action into a mutation plan.
        
        This is a pure transformation - no side effects.
        """
        action_type = gal_action.get("action_type", "")
        action_id = gal_action.get("action_id", str(uuid.uuid4()))
        target_node = gal_action.get("target_node", "")
        
        plan = MutationPlan(
            plan_id=str(uuid.uuid4()),
            gal_action_id=action_id,
            gal_action_type=action_type
        )
        
        # Get node info from graph
        nodes = {n["id"]: n for n in graph_snapshot.get("nodes", [])}
        connections = graph_snapshot.get("connections", [])
        
        target_info = nodes.get(target_node, {})
        
        if action_type == "TAG_SUBGRAPH":
            # Tag operation - metadata only
            tag = gal_action.get("params", {}).get("tag", "unknown")
            plan.add_operation(MutationOperation(
                op_id="",
                op_type=OperationType.TAG_NODE,
                target={"id": target_node, "type": target_info.get("type"), "file_path": target_info.get("file_path")},
                params={"tag": tag}
            ))
            
        elif action_type == "ISOLATE_SUBGRAPH":
            # Remove all incoming edges to isolate
            incoming = [c for c in connections if c["target_id"] == target_node]
            for conn in incoming:
                plan.add_operation(MutationOperation(
                    op_id="",
                    op_type=OperationType.REMOVE_EDGE,
                    target={
                        "type": conn.get("type", "imports"),
                        "from": conn["source_id"],
                        "to": conn["target_id"]
                    }
                ))
                
        elif action_type == "PROPOSE_DELETE_SUBGRAPH":
            # Mark for deletion (actual deletion requires separate approval)
            plan.add_operation(MutationOperation(
                op_id="",
                op_type=OperationType.TAG_NODE,
                target={"id": target_node, "type": target_info.get("type"), "file_path": target_info.get("file_path")},
                params={"tag": "pending_deletion"}
            ))
            
            # Also remove all edges
            related = [c for c in connections if c["source_id"] == target_node or c["target_id"] == target_node]
            for conn in related:
                plan.add_operation(MutationOperation(
                    op_id="",
                    op_type=OperationType.REMOVE_EDGE,
                    target={
                        "type": conn.get("type", "imports"),
                        "from": conn["source_id"],
                        "to": conn["target_id"]
                    }
                ))
                
        elif action_type == "MARK_EXECUTION_ROOT":
            plan.add_operation(MutationOperation(
                op_id="",
                op_type=OperationType.MODIFY_NODE,
                target={"id": target_node, "type": target_info.get("type"), "file_path": target_info.get("file_path")},
                params={"execution_root": True}
            ))
        
        plan._update_affected()
        return plan
