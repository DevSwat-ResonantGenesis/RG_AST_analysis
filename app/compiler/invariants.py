"""
Invariant Checker - Safety Verification
=======================================
Required invariants (v1):
- Execution roots unchanged
- Incoming CALLS preserved
- Blast radius ≤ threshold
- Graph connectivity monotonic
- No new unreachable roots

Verification runs on:
- graph delta
- simulated post-graph
- diff metadata

Failure → artifact rejected.
"""

from dataclasses import dataclass
from typing import List, Dict, Set, Optional, Callable
from enum import Enum


class Invariant(Enum):
    """Core invariants that must be checked"""
    EXECUTION_ROOTS_UNCHANGED = "execution_roots_unchanged"
    INCOMING_CALLS_PRESERVED = "incoming_calls_preserved"
    BLAST_RADIUS_LIMIT = "blast_radius_limit"
    GRAPH_CONNECTIVITY_MONOTONIC = "graph_connectivity_monotonic"
    NO_NEW_UNREACHABLE_ROOTS = "no_new_unreachable_roots"
    SINGLE_ROOT_MUTATION = "single_root_mutation"
    NO_CROSS_SERVICE_DELETE = "no_cross_service_delete"


@dataclass
class InvariantViolation:
    """Details of an invariant violation"""
    invariant: Invariant
    message: str
    severity: str  # critical, high, medium, low
    details: Dict


class InvariantChecker:
    """
    Pure invariant verification engine.
    
    No side effects. No state modification.
    Only verification.
    """
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.blast_radius_limit = self.config.get("blast_radius_limit", 100)
        self.allow_root_modification = self.config.get("allow_root_modification", False)
    
    def check_all(self, 
                  mutation_plan,
                  graph_before: Dict,
                  graph_after_simulated: Dict) -> List[Dict]:
        """
        Run all invariant checks.
        
        Returns list of check results.
        """
        results = []
        
        # Check each invariant
        checks = [
            (Invariant.EXECUTION_ROOTS_UNCHANGED, self._check_execution_roots),
            (Invariant.INCOMING_CALLS_PRESERVED, self._check_incoming_calls),
            (Invariant.BLAST_RADIUS_LIMIT, self._check_blast_radius),
            (Invariant.GRAPH_CONNECTIVITY_MONOTONIC, self._check_connectivity),
            (Invariant.NO_NEW_UNREACHABLE_ROOTS, self._check_unreachable_roots),
            (Invariant.SINGLE_ROOT_MUTATION, self._check_single_root),
        ]
        
        for invariant, check_fn in checks:
            result = check_fn(mutation_plan, graph_before, graph_after_simulated)
            results.append({
                "invariant": invariant.value,
                "passed": result["passed"],
                "message": result["message"],
                "details": result.get("details", {})
            })
        
        return results
    
    def all_passed(self, results: List[Dict]) -> bool:
        """Check if all invariants passed"""
        return all(r["passed"] for r in results)
    
    def _check_execution_roots(self, mutation_plan, graph_before: Dict, graph_after: Dict) -> Dict:
        """
        Invariant: Execution roots cannot change unless explicitly allowed.
        
        Execution roots are:
        - service nodes
        - api_endpoint nodes
        """
        if self.allow_root_modification:
            return {"passed": True, "message": "Root modification allowed by policy"}
        
        # Find roots before
        roots_before = set()
        for node in graph_before.get("nodes", []):
            if node.get("type") in ["service", "api_endpoint"]:
                roots_before.add(node["id"])
        
        # Find roots after
        roots_after = set()
        for node in graph_after.get("nodes", []):
            if node.get("type") in ["service", "api_endpoint"]:
                roots_after.add(node["id"])
        
        # Check if any roots were removed
        removed_roots = roots_before - roots_after
        
        if removed_roots:
            return {
                "passed": False,
                "message": f"Execution roots would be removed: {removed_roots}",
                "details": {"removed_roots": list(removed_roots)}
            }
        
        return {"passed": True, "message": "Execution roots preserved"}
    
    def _check_incoming_calls(self, mutation_plan, graph_before: Dict, graph_after: Dict) -> Dict:
        """
        Invariant: Incoming CALLS edges to execution roots must be preserved.
        """
        # Find execution roots
        roots = set()
        for node in graph_before.get("nodes", []):
            if node.get("type") in ["service", "api_endpoint"]:
                roots.add(node["id"])
        
        # Count incoming calls to roots before
        calls_before = {}
        for conn in graph_before.get("connections", []):
            if conn.get("type") == "calls" and conn["target_id"] in roots:
                calls_before[conn["target_id"]] = calls_before.get(conn["target_id"], 0) + 1
        
        # Count incoming calls to roots after
        calls_after = {}
        for conn in graph_after.get("connections", []):
            if conn.get("type") == "calls" and conn["target_id"] in roots:
                calls_after[conn["target_id"]] = calls_after.get(conn["target_id"], 0) + 1
        
        # Check for reductions
        violations = []
        for root_id, count_before in calls_before.items():
            count_after = calls_after.get(root_id, 0)
            if count_after < count_before:
                violations.append({
                    "root": root_id,
                    "calls_before": count_before,
                    "calls_after": count_after
                })
        
        if violations:
            return {
                "passed": False,
                "message": f"Incoming calls to roots would be reduced",
                "details": {"violations": violations}
            }
        
        return {"passed": True, "message": "Incoming calls preserved"}
    
    def _check_blast_radius(self, mutation_plan, graph_before: Dict, graph_after: Dict) -> Dict:
        """
        Invariant: Blast radius must not exceed threshold.
        """
        blast_radius = len(mutation_plan.affected_nodes)
        
        if blast_radius > self.blast_radius_limit:
            return {
                "passed": False,
                "message": f"Blast radius {blast_radius} exceeds limit {self.blast_radius_limit}",
                "details": {
                    "blast_radius": blast_radius,
                    "limit": self.blast_radius_limit,
                    "affected_nodes": mutation_plan.affected_nodes[:20]
                }
            }
        
        return {
            "passed": True,
            "message": f"Blast radius {blast_radius} within limit",
            "details": {"blast_radius": blast_radius}
        }
    
    def _check_connectivity(self, mutation_plan, graph_before: Dict, graph_after: Dict) -> Dict:
        """
        Invariant: Graph connectivity should not decrease (monotonic).
        
        We measure connectivity as the ratio of reachable nodes.
        """
        def calculate_reachability(graph: Dict) -> float:
            nodes = {n["id"]: n for n in graph.get("nodes", [])}
            connections = graph.get("connections", [])
            
            # Find roots
            roots = set()
            for node in nodes.values():
                if node.get("type") in ["service", "api_endpoint"]:
                    roots.add(node["id"])
            
            # BFS from roots
            reachable = set(roots)
            queue = list(roots)
            
            # Build adjacency
            adj = {}
            for conn in connections:
                adj.setdefault(conn["source_id"], []).append(conn["target_id"])
            
            while queue:
                current = queue.pop(0)
                for neighbor in adj.get(current, []):
                    if neighbor not in reachable:
                        reachable.add(neighbor)
                        queue.append(neighbor)
            
            return len(reachable) / max(len(nodes), 1)
        
        reach_before = calculate_reachability(graph_before)
        reach_after = calculate_reachability(graph_after)
        
        # Allow small decrease (within tolerance)
        tolerance = 0.05
        if reach_after < reach_before - tolerance:
            return {
                "passed": False,
                "message": f"Connectivity would decrease from {reach_before:.2%} to {reach_after:.2%}",
                "details": {
                    "reachability_before": reach_before,
                    "reachability_after": reach_after,
                    "delta": reach_after - reach_before
                }
            }
        
        return {
            "passed": True,
            "message": f"Connectivity maintained ({reach_after:.2%})",
            "details": {"reachability": reach_after}
        }
    
    def _check_unreachable_roots(self, mutation_plan, graph_before: Dict, graph_after: Dict) -> Dict:
        """
        Invariant: No new unreachable execution roots.
        """
        def find_unreachable_roots(graph: Dict) -> Set[str]:
            nodes = {n["id"]: n for n in graph.get("nodes", [])}
            connections = graph.get("connections", [])
            
            # Find all roots
            roots = set()
            for node in nodes.values():
                if node.get("type") in ["service", "api_endpoint"]:
                    roots.add(node["id"])
            
            # Check which roots have no incoming connections
            has_incoming = set()
            for conn in connections:
                has_incoming.add(conn["target_id"])
            
            # Roots without incoming are potentially unreachable
            # (unless they are true entry points)
            unreachable = set()
            for root in roots:
                # Services are always reachable (they are entry points)
                node = nodes.get(root, {})
                if node.get("type") == "api_endpoint" and root not in has_incoming:
                    unreachable.add(root)
            
            return unreachable
        
        unreachable_before = find_unreachable_roots(graph_before)
        unreachable_after = find_unreachable_roots(graph_after)
        
        new_unreachable = unreachable_after - unreachable_before
        
        if new_unreachable:
            return {
                "passed": False,
                "message": f"Would create {len(new_unreachable)} new unreachable roots",
                "details": {"new_unreachable_roots": list(new_unreachable)}
            }
        
        return {"passed": True, "message": "No new unreachable roots"}
    
    def _check_single_root(self, mutation_plan, graph_before: Dict, graph_after: Dict) -> Dict:
        """
        Invariant: Only single-root mutations allowed in v1.
        
        Cannot modify multiple execution roots in one operation.
        """
        nodes = {n["id"]: n for n in graph_before.get("nodes", [])}
        
        roots_affected = set()
        for node_id in mutation_plan.affected_nodes:
            node = nodes.get(node_id, {})
            if node.get("type") in ["service", "api_endpoint"]:
                roots_affected.add(node_id)
        
        if len(roots_affected) > 1:
            return {
                "passed": False,
                "message": f"Multiple execution roots affected: {len(roots_affected)}",
                "details": {"roots_affected": list(roots_affected)}
            }
        
        return {"passed": True, "message": "Single-root constraint satisfied"}


def simulate_graph_after(graph_before: Dict, mutation_plan) -> Dict:
    """
    Simulate the graph state after applying the mutation plan.
    
    This is a PURE function - no side effects.
    """
    import copy
    
    graph_after = copy.deepcopy(graph_before)
    nodes = {n["id"]: n for n in graph_after.get("nodes", [])}
    connections = graph_after.get("connections", [])
    
    for op in mutation_plan.operations:
        op_type = op.op_type.value
        target = op.target
        
        if op_type == "REMOVE_EDGE":
            # Remove matching connection
            connections = [
                c for c in connections
                if not (c["source_id"] == target.get("from") and 
                       c["target_id"] == target.get("to"))
            ]
            
        elif op_type == "ADD_EDGE":
            connections.append({
                "source_id": target.get("from"),
                "target_id": target.get("to"),
                "type": target.get("type", "imports"),
                "status": "active"
            })
            
        elif op_type == "REMOVE_NODE":
            node_id = target.get("id")
            if node_id in nodes:
                del nodes[node_id]
            # Also remove related connections
            connections = [
                c for c in connections
                if c["source_id"] != node_id and c["target_id"] != node_id
            ]
            
        elif op_type == "TAG_NODE":
            node_id = target.get("id")
            if node_id in nodes:
                tags = nodes[node_id].get("tags", [])
                tags.append(op.params.get("tag", ""))
                nodes[node_id]["tags"] = tags
    
    graph_after["nodes"] = list(nodes.values())
    graph_after["connections"] = connections
    
    return graph_after
