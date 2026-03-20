"""
Graph Action Language (GAL) - Constrained Mutation Protocol
============================================================
GAL is not a programming language.
It is a constrained mutation protocol over a code execution graph.
The agent never edits files directly - it declares intent over the graph,
which is then compiled into code diffs.

Core Axioms:
1. Graph is truth - if it's not in the graph, it does not exist
2. All actions are reversible
3. All actions are scoped
4. All actions are simulated before execution
5. No free-form text operations
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Any
from datetime import datetime
import uuid
import hashlib
import json


class NodeType(Enum):
    SERVICE = "service"
    MODULE = "module"
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    ENDPOINT = "api_endpoint"
    TASK = "task"
    AGENT = "agent"


class EdgeType(Enum):
    IMPORTS = "imports"
    CALLS = "calls"
    OWNS = "owns"
    EXPOSES = "exposes"
    SPAWNS = "spawns"
    SCHEDULES = "schedules"
    DEPENDS_ON = "depends_on"


class ActionCategory(Enum):
    INSPECTION = "inspection"      # Read-only, unlimited
    CLASSIFICATION = "classification"  # Semantic labeling, no code change
    RESTRUCTURING = "restructuring"    # Low-risk topology edits
    DELETION = "deletion"          # Highest risk, gated
    EXECUTION = "execution"        # Compiler layer


@dataclass
class NodeMetadata:
    """Required metadata for agent operations"""
    reachable: bool = False
    reachability_score: float = 0.0
    execution_root: bool = False
    mutation_risk: int = 0  # 0-10
    blast_radius: int = 0
    runtime_frequency: Optional[float] = None
    owner: str = "system"  # system|agent|human
    last_verified: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class ActionContract:
    """Every action must emit this contract"""
    action_id: str
    action_type: str
    category: ActionCategory
    target_node: str
    before_metrics: Dict[str, Any]
    after_metrics_simulated: Dict[str, Any]
    risk_score: float
    utility_score: float
    rollback_token: str
    timestamp: datetime = field(default_factory=datetime.now)
    status: str = "proposed"  # proposed|approved|executed|rolled_back|rejected
    
    def to_dict(self) -> Dict:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "category": self.category.value,
            "target_node": self.target_node,
            "before_metrics": self.before_metrics,
            "after_metrics_simulated": self.after_metrics_simulated,
            "risk_score": self.risk_score,
            "utility_score": self.utility_score,
            "rollback_token": self.rollback_token,
            "timestamp": self.timestamp.isoformat(),
            "status": self.status
        }


class GALEngine:
    """Graph Action Language Engine - executes GAL operations"""
    
    def __init__(self, graph_data: Dict):
        self.nodes = {n['id']: n for n in graph_data.get('nodes', [])}
        self.connections = graph_data.get('connections', [])
        self.services = graph_data.get('services', {})
        self.metadata: Dict[str, NodeMetadata] = {}
        self.action_history: List[ActionContract] = []
        self.pending_proposals: List[ActionContract] = []
        
        # Initialize metadata for all nodes
        self._initialize_metadata()
    
    def _initialize_metadata(self):
        """Initialize metadata for all nodes"""
        # Build connection maps
        incoming = {}
        outgoing = {}
        for conn in self.connections:
            src, tgt = conn['source_id'], conn['target_id']
            outgoing.setdefault(src, []).append(tgt)
            incoming.setdefault(tgt, []).append(src)
        
        # Calculate metadata for each node
        for node_id, node in self.nodes.items():
            in_count = len(incoming.get(node_id, []))
            out_count = len(outgoing.get(node_id, []))
            
            # Determine if reachable (has incoming connections or is a root)
            is_root = node.get('type') == 'service' or node.get('type') == 'api_endpoint'
            reachable = is_root or in_count > 0
            
            # Calculate blast radius (how many nodes affected if this changes)
            blast_radius = self._calculate_blast_radius(node_id, outgoing, set())
            
            # Calculate mutation risk
            mutation_risk = min(10, blast_radius // 5 + (5 if is_root else 0))
            
            self.metadata[node_id] = NodeMetadata(
                reachable=reachable,
                reachability_score=1.0 if reachable else 0.0,
                execution_root=is_root,
                mutation_risk=mutation_risk,
                blast_radius=blast_radius,
                owner="system",
                last_verified=datetime.now()
            )
    
    def _calculate_blast_radius(self, node_id: str, outgoing: Dict, visited: Set) -> int:
        """Calculate how many nodes are affected if this node changes"""
        if node_id in visited:
            return 0
        visited.add(node_id)
        
        count = 1
        for target in outgoing.get(node_id, []):
            count += self._calculate_blast_radius(target, outgoing, visited)
        return count
    
    def _generate_rollback_token(self, action_data: Dict) -> str:
        """Generate a unique rollback token for an action"""
        data = json.dumps(action_data, sort_keys=True, default=str)
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    def _calculate_utility(self, reachability_gain: float, risk_score: float, 
                          blast_radius: int, cross_service: bool = False) -> float:
        """
        Calculate utility score for an action
        utility = (Δ reachability * 10) - (risk_score * 2) - (blast_radius * 0.5) - (cross_service_penalty)
        """
        utility = (reachability_gain * 10) - (risk_score * 2) - (blast_radius * 0.5)
        if cross_service:
            utility -= 5  # Cross-service penalty
        return utility

    # ==================== INSPECTION ACTIONS (read-only) ====================
    
    def scan_unreachable(self) -> List[Dict]:
        """Find all unreachable nodes"""
        unreachable = []
        for node_id, meta in self.metadata.items():
            if not meta.reachable:
                node = self.nodes.get(node_id, {})
                unreachable.append({
                    "id": node_id,
                    "name": node.get('name', 'unknown'),
                    "type": node.get('type', 'unknown'),
                    "file_path": node.get('file_path', ''),
                    "blast_radius": meta.blast_radius,
                    "mutation_risk": meta.mutation_risk
                })
        return unreachable
    
    def trace_root(self, node_id: str) -> Dict:
        """Trace back to execution roots from a node"""
        if node_id not in self.nodes:
            return {"error": "Node not found"}
        
        # Build reverse connection map
        incoming = {}
        for conn in self.connections:
            incoming.setdefault(conn['target_id'], []).append(conn['source_id'])
        
        # BFS to find roots
        visited = set()
        queue = [node_id]
        path = []
        roots = []
        
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            
            meta = self.metadata.get(current)
            if meta and meta.execution_root:
                roots.append(current)
            
            path.append(current)
            for parent in incoming.get(current, []):
                queue.append(parent)
        
        return {
            "node": node_id,
            "roots_found": roots,
            "path_length": len(path),
            "is_reachable": len(roots) > 0
        }
    
    def calculate_blast_radius(self, node_id: str) -> Dict:
        """Calculate the blast radius for a specific node"""
        if node_id not in self.nodes:
            return {"error": "Node not found"}
        
        outgoing = {}
        for conn in self.connections:
            outgoing.setdefault(conn['source_id'], []).append(conn['target_id'])
        
        affected = set()
        self._collect_affected(node_id, outgoing, affected)
        
        return {
            "node": node_id,
            "blast_radius": len(affected),
            "affected_nodes": list(affected)[:50]  # Limit output
        }
    
    def _collect_affected(self, node_id: str, outgoing: Dict, affected: Set):
        if node_id in affected:
            return
        affected.add(node_id)
        for target in outgoing.get(node_id, []):
            self._collect_affected(target, outgoing, affected)
    
    def find_orphan_endpoints(self) -> List[Dict]:
        """Find API endpoints with no callers"""
        incoming = {}
        for conn in self.connections:
            incoming.setdefault(conn['target_id'], []).append(conn['source_id'])
        
        orphans = []
        for node_id, node in self.nodes.items():
            if node.get('type') == 'api_endpoint':
                callers = incoming.get(node_id, [])
                if len(callers) == 0:
                    orphans.append({
                        "id": node_id,
                        "name": node.get('name'),
                        "file_path": node.get('file_path'),
                        "service": node.get('service')
                    })
        return orphans
    
    def find_duplicate_subgraphs(self) -> List[Dict]:
        """Find potentially duplicate code patterns"""
        # Group functions by similar structure
        func_signatures = {}
        for node_id, node in self.nodes.items():
            if node.get('type') in ['function', 'class']:
                # Create a signature based on connections
                outgoing = [c['target_id'] for c in self.connections if c['source_id'] == node_id]
                sig = f"{node.get('type')}:{len(outgoing)}"
                func_signatures.setdefault(sig, []).append(node_id)
        
        duplicates = []
        for sig, nodes in func_signatures.items():
            if len(nodes) > 1:
                duplicates.append({
                    "signature": sig,
                    "count": len(nodes),
                    "nodes": nodes[:10]
                })
        return duplicates

    # ==================== CLASSIFICATION ACTIONS ====================
    
    def tag_subgraph(self, node_id: str, tag: str) -> ActionContract:
        """Tag a subgraph (dead|dormant|experimental)"""
        if tag not in ['dead', 'dormant', 'experimental', 'deprecated', 'critical']:
            raise ValueError(f"Invalid tag: {tag}")
        
        meta = self.metadata.get(node_id)
        if not meta:
            raise ValueError(f"Node not found: {node_id}")
        
        before_tags = meta.tags.copy()
        
        action = ActionContract(
            action_id=str(uuid.uuid4()),
            action_type="TAG_SUBGRAPH",
            category=ActionCategory.CLASSIFICATION,
            target_node=node_id,
            before_metrics={"tags": before_tags},
            after_metrics_simulated={"tags": before_tags + [tag]},
            risk_score=0.5,
            utility_score=1.0,
            rollback_token=self._generate_rollback_token({"node": node_id, "tag": tag})
        )
        
        self.pending_proposals.append(action)
        return action
    
    def mark_execution_root(self, node_id: str) -> ActionContract:
        """Mark a node as an execution root"""
        meta = self.metadata.get(node_id)
        if not meta:
            raise ValueError(f"Node not found: {node_id}")
        
        action = ActionContract(
            action_id=str(uuid.uuid4()),
            action_type="MARK_EXECUTION_ROOT",
            category=ActionCategory.CLASSIFICATION,
            target_node=node_id,
            before_metrics={"execution_root": meta.execution_root},
            after_metrics_simulated={"execution_root": True},
            risk_score=1.0,
            utility_score=2.0,
            rollback_token=self._generate_rollback_token({"node": node_id, "root": True})
        )
        
        self.pending_proposals.append(action)
        return action

    # ==================== RESTRUCTURING ACTIONS ====================
    
    def propose_isolate_subgraph(self, root_id: str) -> ActionContract:
        """Propose isolating a subgraph"""
        meta = self.metadata.get(root_id)
        if not meta:
            raise ValueError(f"Node not found: {root_id}")
        
        # Calculate metrics
        incoming = sum(1 for c in self.connections if c['target_id'] == root_id)
        outgoing = sum(1 for c in self.connections if c['source_id'] == root_id)
        
        # Simulate isolation
        current_reachability = sum(1 for m in self.metadata.values() if m.reachable) / len(self.metadata)
        simulated_reachability = current_reachability  # Isolation doesn't change reachability directly
        
        risk = meta.mutation_risk
        utility = self._calculate_utility(0.01, risk, meta.blast_radius)
        
        action = ActionContract(
            action_id=str(uuid.uuid4()),
            action_type="ISOLATE_SUBGRAPH",
            category=ActionCategory.RESTRUCTURING,
            target_node=root_id,
            before_metrics={
                "reachability": round(current_reachability, 4),
                "incoming_connections": incoming,
                "outgoing_connections": outgoing
            },
            after_metrics_simulated={
                "reachability": round(simulated_reachability, 4),
                "incoming_connections": 0,
                "outgoing_connections": outgoing
            },
            risk_score=risk,
            utility_score=utility,
            rollback_token=self._generate_rollback_token({"node": root_id, "action": "isolate"})
        )
        
        self.pending_proposals.append(action)
        return action

    # ==================== DELETION ACTIONS (gated) ====================
    
    def propose_delete_subgraph(self, root_id: str) -> ActionContract:
        """Propose deletion of a subgraph - requires approval"""
        meta = self.metadata.get(root_id)
        if not meta:
            raise ValueError(f"Node not found: {root_id}")
        
        # Check prerequisites
        incoming = sum(1 for c in self.connections if c['target_id'] == root_id)
        if incoming > 0:
            raise ValueError(f"Cannot delete: {incoming} incoming connections exist")
        
        if 'dead' not in meta.tags and 'deprecated' not in meta.tags:
            raise ValueError("Node must be tagged as 'dead' or 'deprecated' before deletion")
        
        # Calculate impact
        outgoing = {}
        for conn in self.connections:
            outgoing.setdefault(conn['source_id'], []).append(conn['target_id'])
        
        affected = set()
        self._collect_affected(root_id, outgoing, affected)
        
        current_reachability = sum(1 for m in self.metadata.values() if m.reachable) / len(self.metadata)
        # Removing unreachable nodes improves reachability ratio
        new_total = len(self.metadata) - len(affected)
        new_reachable = sum(1 for nid, m in self.metadata.items() if m.reachable and nid not in affected)
        simulated_reachability = new_reachable / new_total if new_total > 0 else 0
        
        reachability_gain = simulated_reachability - current_reachability
        risk = meta.mutation_risk + 3  # Deletion is high risk
        utility = self._calculate_utility(reachability_gain, risk, len(affected))
        
        action = ActionContract(
            action_id=str(uuid.uuid4()),
            action_type="PROPOSE_DELETE_SUBGRAPH",
            category=ActionCategory.DELETION,
            target_node=root_id,
            before_metrics={
                "reachability": round(current_reachability, 4),
                "total_nodes": len(self.metadata),
                "nodes_to_delete": len(affected)
            },
            after_metrics_simulated={
                "reachability": round(simulated_reachability, 4),
                "total_nodes": new_total,
                "reachability_gain": f"+{round(reachability_gain * 100, 2)}%"
            },
            risk_score=risk,
            utility_score=utility,
            rollback_token=self._generate_rollback_token({"node": root_id, "action": "delete", "affected": list(affected)})
        )
        
        self.pending_proposals.append(action)
        return action

    # ==================== EXECUTION BRIDGE ====================
    
    def compile_to_patch(self, action_id: str) -> Dict:
        """Convert a graph action to a code patch"""
        action = next((a for a in self.pending_proposals if a.action_id == action_id), None)
        if not action:
            return {"error": "Action not found"}
        
        patch = {
            "action_id": action_id,
            "type": action.action_type,
            "target": action.target_node,
            "files_affected": [],
            "diffs": []
        }
        
        node = self.nodes.get(action.target_node)
        if node:
            file_path = node.get('file_path', '')
            if file_path:
                patch["files_affected"].append(file_path)
                
                if action.action_type == "PROPOSE_DELETE_SUBGRAPH":
                    patch["diffs"].append({
                        "file": file_path,
                        "operation": "delete_lines",
                        "description": f"Remove {node.get('type')} '{node.get('name')}'"
                    })
                elif action.action_type == "TAG_SUBGRAPH":
                    patch["diffs"].append({
                        "file": file_path,
                        "operation": "add_comment",
                        "description": f"Add deprecation/tag comment to {node.get('name')}"
                    })
        
        return patch
    
    def get_pending_proposals(self) -> List[Dict]:
        """Get all pending proposals"""
        return [p.to_dict() for p in self.pending_proposals]
    
    def approve_action(self, action_id: str) -> Dict:
        """Approve an action for execution"""
        action = next((a for a in self.pending_proposals if a.action_id == action_id), None)
        if not action:
            return {"error": "Action not found"}
        
        action.status = "approved"
        return {"status": "approved", "action_id": action_id}
    
    def reject_action(self, action_id: str) -> Dict:
        """Reject an action"""
        action = next((a for a in self.pending_proposals if a.action_id == action_id), None)
        if not action:
            return {"error": "Action not found"}
        
        action.status = "rejected"
        self.pending_proposals.remove(action)
        return {"status": "rejected", "action_id": action_id}
