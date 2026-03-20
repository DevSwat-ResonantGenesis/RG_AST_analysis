"""
Code Governance Engine - Reachability Contracts & Dead Code Classification

Enforces:
1. Reachability contracts from authoritative roots
2. Dead-code classification (LIVE, Dormant, Experimental, Deprecated, Invalid)
3. Directional dependency rules
4. Drift thresholds and review requirements
5. CI-ready enforcement output
"""

from typing import Dict, List, Set, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import json
import re


class NodeStatus(str, Enum):
    """Classification for code nodes based on reachability"""
    LIVE = "live"
    DORMANT = "dormant"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class ViolationType(str, Enum):
    """Types of governance violations"""
    UNREACHABLE_CODE = "unreachable_code"
    FORBIDDEN_DEPENDENCY = "forbidden_dependency"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    ISOLATED_NODE = "isolated_node"
    DRIFT_THRESHOLD = "drift_threshold"
    MISSING_JUSTIFICATION = "missing_justification"
    INVALID_DIRECTION = "invalid_direction"


class Severity(str, Enum):
    """Violation severity levels"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class AuthoritativeRoot:
    """Defines an entry point for reachability analysis"""
    name: str
    service: str
    entry_file: str
    entry_function: str = "app"
    description: str = ""
    
    def to_dict(self):
        return {
            "name": self.name,
            "service": self.service,
            "entry_file": self.entry_file,
            "entry_function": self.entry_function,
            "description": self.description
        }


@dataclass
class DependencyRule:
    """Defines allowed/forbidden dependency directions"""
    name: str
    from_pattern: str
    to_pattern: str
    allowed: bool = True
    severity: Severity = Severity.HIGH
    message: str = ""
    
    def to_dict(self):
        return {
            "name": self.name,
            "from_pattern": self.from_pattern,
            "to_pattern": self.to_pattern,
            "allowed": self.allowed,
            "severity": self.severity.value,
            "message": self.message
        }


@dataclass
class Violation:
    """Represents a governance violation"""
    type: ViolationType
    severity: Severity
    node_id: str
    message: str
    file_path: str = ""
    line: int = 0
    suggestion: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self):
        return {
            "type": self.type.value,
            "severity": self.severity.value,
            "node_id": self.node_id,
            "message": self.message,
            "file_path": self.file_path,
            "line": self.line,
            "suggestion": self.suggestion,
            "metadata": self.metadata
        }


@dataclass
class GovernanceReport:
    """Complete governance analysis report"""
    timestamp: str
    total_nodes: int
    live_nodes: int
    dormant_nodes: int
    experimental_nodes: int
    deprecated_nodes: int
    invalid_nodes: int
    violations: List[Violation]
    ci_pass: bool
    drift_score: float
    reachability_score: float
    
    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "summary": {
                "total_nodes": self.total_nodes,
                "live_nodes": self.live_nodes,
                "dormant_nodes": self.dormant_nodes,
                "experimental_nodes": self.experimental_nodes,
                "deprecated_nodes": self.deprecated_nodes,
                "invalid_nodes": self.invalid_nodes,
                "reachability_score": round(self.reachability_score, 2),
                "drift_score": round(self.drift_score, 2)
            },
            "ci_pass": self.ci_pass,
            "violations": [v.to_dict() for v in self.violations],
            "violation_counts": {
                "critical": len([v for v in self.violations if v.severity == Severity.CRITICAL]),
                "high": len([v for v in self.violations if v.severity == Severity.HIGH]),
                "medium": len([v for v in self.violations if v.severity == Severity.MEDIUM]),
                "low": len([v for v in self.violations if v.severity == Severity.LOW])
            }
        }


class GovernanceEngine:
    """Main governance engine for code reachability and dependency enforcement."""
    
    DEFAULT_ROOTS = [
        AuthoritativeRoot("Gateway Entry", "gateway", "app/main.py", "app", "Main API gateway"),
        AuthoritativeRoot("Auth Entry", "auth_service", "app/main.py", "app", "Auth service"),
        AuthoritativeRoot("Chat Entry", "chat_service", "app/main.py", "app", "Chat service"),
        AuthoritativeRoot("Memory Entry", "memory_service", "app/main.py", "app", "Memory service"),
        AuthoritativeRoot("Agent Engine", "agent_engine_service", "app/main.py", "app", "Agent executor"),
        AuthoritativeRoot("IDE Entry", "ide_service", "api/main.py", "app", "IDE service"),
    ]
    
    DEFAULT_RULES = [
        DependencyRule("No service->gateway", r"(auth|chat|memory|agent)_service", r"gateway", False, Severity.HIGH, "Services should not depend on gateway"),
        DependencyRule("No frontend->backend internals", r"frontend|components", r"_service/app/(db|models)", False, Severity.CRITICAL, "Frontend cannot import backend internals"),
    ]
    
    def __init__(self, roots=None, rules=None, drift_threshold=20.0, justification_file=".code-justifications.json"):
        self.roots = roots or self.DEFAULT_ROOTS
        self.rules = rules or self.DEFAULT_RULES
        self.drift_threshold = drift_threshold
        self.justification_file = justification_file
        self.nodes: Dict[str, Any] = {}
        self.connections: List[Any] = []
        self.node_status: Dict[str, NodeStatus] = {}
        self.justifications: Dict[str, str] = {}
        self.violations: List[Violation] = []
        
    def load_justifications(self, base_path: str) -> Dict[str, str]:
        import os
        path = os.path.join(base_path, self.justification_file)
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {}
    
    def analyze(self, nodes: Dict[str, Any], connections: List[Any], base_path: str = "") -> GovernanceReport:
        self.nodes = nodes
        self.connections = connections
        self.justifications = self.load_justifications(base_path)
        self.violations = []

        # Build adjacency index ONCE for O(N+E) BFS instead of O(N*E)
        self._adj: Dict[str, Set[str]] = {}
        for conn in self.connections:
            src = conn.get('source_id', '') if isinstance(conn, dict) else getattr(conn, 'source_id', '')
            tgt = conn.get('target_id', '') if isinstance(conn, dict) else getattr(conn, 'target_id', '')
            if src:
                self._adj.setdefault(src, set()).add(tgt)
            if tgt:
                self._adj.setdefault(tgt, set()).add(src)

        live_nodes = self._compute_reachability()
        self._classify_nodes(live_nodes)
        self._check_dependency_rules()
        self._check_isolated_nodes()
        
        return self._generate_report()
    
    def _compute_reachability(self) -> Set[str]:
        live_nodes: Set[str] = set()
        for root in self.roots:
            root_id = self._find_root_node(root)
            if root_id:
                reachable = self._traverse_from_node(root_id)
                live_nodes.update(reachable)
        return live_nodes
    
    def _find_root_node(self, root: AuthoritativeRoot) -> Optional[str]:
        for node_id, node in self.nodes.items():
            service = node.get('service', '') if isinstance(node, dict) else getattr(node, 'service', '')
            file_path = node.get('file_path', '') if isinstance(node, dict) else getattr(node, 'file_path', '')
            name = node.get('name', '') if isinstance(node, dict) else getattr(node, 'name', '')
            
            if root.service in service and root.entry_file in file_path:
                return node_id
        return None
    
    def _traverse_from_node(self, start_id: str, max_depth: int = 100) -> Set[str]:
        from collections import deque
        visited: Set[str] = set()
        queue: deque = deque([(start_id, 0)])
        
        while queue:
            node_id, depth = queue.popleft()
            if node_id in visited or depth > max_depth:
                continue
            visited.add(node_id)
            
            for neighbor in self._adj.get(node_id, ()):
                if neighbor not in visited:
                    queue.append((neighbor, depth + 1))
        
        return visited
    
    def _classify_nodes(self, live_nodes: Set[str]):
        for node_id in self.nodes:
            if node_id in live_nodes:
                self.node_status[node_id] = NodeStatus.LIVE
            elif node_id in self.justifications:
                j = self.justifications[node_id].lower()
                if 'experimental' in j or 'wip' in j:
                    self.node_status[node_id] = NodeStatus.EXPERIMENTAL
                elif 'deprecated' in j:
                    self.node_status[node_id] = NodeStatus.DEPRECATED
                else:
                    self.node_status[node_id] = NodeStatus.DORMANT
            else:
                self.node_status[node_id] = NodeStatus.INVALID
                node = self.nodes[node_id]
                file_path = node.get('file_path', '') if isinstance(node, dict) else getattr(node, 'file_path', '')
                name = node.get('name', '') if isinstance(node, dict) else getattr(node, 'name', '')
                
                if len(self.violations) < 500:
                    self.violations.append(Violation(
                        type=ViolationType.UNREACHABLE_CODE,
                        severity=Severity.MEDIUM,
                        node_id=node_id,
                        message=f"'{name}' not reachable from any root",
                        file_path=file_path,
                        suggestion=f"Add justification or remove dead code"
                    ))
    
    def _check_dependency_rules(self):
        for conn in self.connections:
            source = conn.get('source_id', '') if isinstance(conn, dict) else getattr(conn, 'source_id', '')
            target = conn.get('target_id', '') if isinstance(conn, dict) else getattr(conn, 'target_id', '')
            
            for rule in self.rules:
                if not rule.allowed:
                    if re.search(rule.from_pattern, source) and re.search(rule.to_pattern, target):
                        self.violations.append(Violation(
                            type=ViolationType.FORBIDDEN_DEPENDENCY,
                            severity=rule.severity,
                            node_id=source,
                            message=f"{rule.name}: {rule.message}",
                            metadata={"source": source, "target": target, "rule": rule.name},
                            suggestion=f"Remove dependency from {source} to {target}"
                        ))
    
    def _check_isolated_nodes(self):
        connected: Set[str] = set()
        for conn in self.connections:
            source = conn.get('source_id', '') if isinstance(conn, dict) else getattr(conn, 'source_id', '')
            target = conn.get('target_id', '') if isinstance(conn, dict) else getattr(conn, 'target_id', '')
            connected.add(source)
            connected.add(target)
        
        for node_id in self.nodes:
            if node_id not in connected:
                node = self.nodes[node_id]
                node_type = node.get('type', '') if isinstance(node, dict) else getattr(node, 'type', '')
                if node_type == 'service':
                    continue
                name = node.get('name', '') if isinstance(node, dict) else getattr(node, 'name', '')
                file_path = node.get('file_path', '') if isinstance(node, dict) else getattr(node, 'file_path', '')
                
                if node_id not in self.justifications:
                    self.violations.append(Violation(
                        type=ViolationType.ISOLATED_NODE,
                        severity=Severity.LOW,
                        node_id=node_id,
                        message=f"'{name}' is isolated (no connections)",
                        file_path=file_path,
                        suggestion="Connect or add justification"
                    ))
    
    def _generate_report(self) -> GovernanceReport:
        total = len(self.nodes)
        live = len([n for n, s in self.node_status.items() if s == NodeStatus.LIVE])
        dormant = len([n for n, s in self.node_status.items() if s == NodeStatus.DORMANT])
        experimental = len([n for n, s in self.node_status.items() if s == NodeStatus.EXPERIMENTAL])
        deprecated = len([n for n, s in self.node_status.items() if s == NodeStatus.DEPRECATED])
        invalid = len([n for n, s in self.node_status.items() if s == NodeStatus.INVALID])
        
        reachability_score = (live / total * 100) if total > 0 else 0
        drift_score = (invalid / total * 100) if total > 0 else 0
        
        critical = [v for v in self.violations if v.severity in [Severity.CRITICAL, Severity.HIGH]]
        ci_pass = len(critical) == 0 and drift_score <= self.drift_threshold
        
        return GovernanceReport(
            timestamp=datetime.now().isoformat(),
            total_nodes=total,
            live_nodes=live,
            dormant_nodes=dormant,
            experimental_nodes=experimental,
            deprecated_nodes=deprecated,
            invalid_nodes=invalid,
            violations=self.violations,
            ci_pass=ci_pass,
            drift_score=drift_score,
            reachability_score=reachability_score
        )
    
    def get_node_status(self, node_id: str) -> NodeStatus:
        return self.node_status.get(node_id, NodeStatus.UNKNOWN)
    
    def get_live_nodes(self) -> List[str]:
        return [n for n, s in self.node_status.items() if s == NodeStatus.LIVE]
    
    def get_invalid_nodes(self) -> List[str]:
        return [n for n, s in self.node_status.items() if s == NodeStatus.INVALID]
    
    def export_ci_report(self, report: GovernanceReport) -> str:
        return json.dumps(report.to_dict(), indent=2)
    
    def export_github_annotations(self, report: GovernanceReport) -> List[str]:
        annotations = []
        for v in report.violations:
            level = "error" if v.severity in [Severity.CRITICAL, Severity.HIGH] else "warning"
            annotations.append(f"::{level} file={v.file_path},line={v.line}::{v.message}")
        return annotations


def analyze_governance(nodes: Dict[str, Any], connections: List[Any], base_path: str = "", 
                       custom_roots=None, custom_rules=None, drift_threshold=20.0) -> Dict[str, Any]:
    """Main entry point for governance analysis."""
    roots = [AuthoritativeRoot(**r) for r in custom_roots] if custom_roots else None
    rules = [DependencyRule(**r) for r in custom_rules] if custom_rules else None
    
    engine = GovernanceEngine(roots=roots, rules=rules, drift_threshold=drift_threshold)
    report = engine.analyze(nodes, connections, base_path)
    
    return {
        "report": report.to_dict(),
        "node_status": {k: v.value for k, v in engine.node_status.items()},
        "live_nodes": engine.get_live_nodes(),
        "invalid_nodes": engine.get_invalid_nodes(),
        "ci_pass": report.ci_pass
    }
