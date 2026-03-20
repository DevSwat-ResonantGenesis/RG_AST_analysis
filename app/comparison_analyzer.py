"""
Multi-Project Comparison Analyzer
Compares up to 3 codebases to detect:
- Code drift
- Breaking changes
- Evolution timeline
- Instability patterns
"""

import os
import hashlib
import difflib
from pathlib import Path
from typing import Dict, List, Set, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from .analyzer import CodebaseAnalyzer, CodeNode, CodeConnection, NodeType, ConnectionType


class ChangeType(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"
    MOVED = "moved"


class SeverityLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class FileChange:
    file_path: str
    change_type: ChangeType
    old_lines: int = 0
    new_lines: int = 0
    diff_percent: float = 0.0
    breaking_risk: SeverityLevel = SeverityLevel.INFO
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self):
        return {
            "file_path": self.file_path,
            "change_type": self.change_type.value,
            "old_lines": self.old_lines,
            "new_lines": self.new_lines,
            "diff_percent": self.diff_percent,
            "breaking_risk": self.breaking_risk.value,
            "details": self.details
        }


@dataclass
class ConnectionChange:
    source: str
    target: str
    change_type: ChangeType
    connection_type: str
    project_index: int
    
    def to_dict(self):
        return {
            "source": self.source,
            "target": self.target,
            "change_type": self.change_type.value,
            "connection_type": self.connection_type,
            "project_index": self.project_index
        }


@dataclass
class InstabilityMetric:
    file_path: str
    score: float  # 0-100, higher = more unstable
    reasons: List[str] = field(default_factory=list)
    change_frequency: int = 0
    dependency_count: int = 0
    dependent_count: int = 0
    
    def to_dict(self):
        return {
            "file_path": self.file_path,
            "score": self.score,
            "reasons": self.reasons,
            "change_frequency": self.change_frequency,
            "dependency_count": self.dependency_count,
            "dependent_count": self.dependent_count
        }


@dataclass
class HeatMapData:
    node_id: str
    heat_value: float  # 0-1
    category: str  # "changes", "connections", "instability", "weight"
    
    def to_dict(self):
        return {
            "node_id": self.node_id,
            "heat_value": self.heat_value,
            "category": self.category
        }


class MultiProjectComparator:
    """Compare up to 3 projects and analyze evolution"""
    
    def __init__(self):
        self.projects: List[Dict[str, Any]] = []
        self.analyzers: List[CodebaseAnalyzer] = []
        self.file_changes: List[FileChange] = []
        self.connection_changes: List[ConnectionChange] = []
        self.instability_metrics: Dict[str, InstabilityMetric] = {}
        self.heat_maps: Dict[str, List[HeatMapData]] = {}
        
    def add_project(self, path: str, label: str) -> int:
        """Add a project to compare (max 3)"""
        if len(self.projects) >= 3:
            raise ValueError("Maximum 3 projects can be compared")
        
        analyzer = CodebaseAnalyzer(path)
        data = analyzer.analyze()
        
        project_index = len(self.projects)
        self.projects.append({
            "index": project_index,
            "path": path,
            "label": label,
            "data": data,
            "analyzer": analyzer
        })
        self.analyzers.append(analyzer)
        
        return project_index
    
    def compare_all(self) -> Dict[str, Any]:
        """Compare all added projects"""
        if len(self.projects) < 2:
            raise ValueError("Need at least 2 projects to compare")
        
        # Compare files between projects
        self._compare_files()
        
        # Compare connections
        self._compare_connections()
        
        # Calculate instability metrics
        self._calculate_instability()
        
        # Generate heat maps
        self._generate_heat_maps()
        
        # Analyze graph structure
        graph_analysis = self._analyze_graph_structure()
        
        return {
            "projects": [
                {
                    "index": p["index"],
                    "label": p["label"],
                    "analysis_id": p.get("analysis_id"),
                    "path": p["path"],
                    "stats": p["data"]["stats"]
                }
                for p in self.projects
            ],
            "file_changes": [fc.to_dict() for fc in self.file_changes],
            "connection_changes": [cc.to_dict() for cc in self.connection_changes],
            "instability_metrics": {k: v.to_dict() for k, v in self.instability_metrics.items()},
            "heat_maps": {k: [h.to_dict() for h in v] for k, v in self.heat_maps.items()},
            "graph_analysis": graph_analysis,
            "evolution_timeline": self._build_evolution_timeline(),
            "breaking_changes": self._detect_breaking_changes(),
            "drift_analysis": self._analyze_code_drift()
        }
    
    def _compare_files(self):
        """Compare files between projects"""
        all_files: Set[str] = set()
        project_files: List[Set[str]] = []
        
        for project in self.projects:
            files = set()
            for node in project["data"]["nodes"]:
                if node["type"] == "file":
                    # Normalize path to be relative
                    rel_path = node["file_path"]
                    files.add(rel_path)
            project_files.append(files)
            all_files.update(files)
        
        for file_path in all_files:
            presence = [file_path in pf for pf in project_files]
            
            if all(presence):
                # File exists in all projects - check for modifications
                change = self._analyze_file_modification(file_path)
                if change:
                    self.file_changes.append(change)
            elif presence[0] and not presence[-1]:
                # File was removed
                self.file_changes.append(FileChange(
                    file_path=file_path,
                    change_type=ChangeType.REMOVED,
                    breaking_risk=SeverityLevel.HIGH if "router" in file_path or "main" in file_path else SeverityLevel.MEDIUM
                ))
            elif not presence[0] and presence[-1]:
                # File was added
                self.file_changes.append(FileChange(
                    file_path=file_path,
                    change_type=ChangeType.ADDED,
                    breaking_risk=SeverityLevel.LOW
                ))
    
    def _analyze_file_modification(self, file_path: str) -> Optional[FileChange]:
        """Analyze if a file was modified between projects"""
        contents = []
        
        for project in self.projects:
            full_path = Path(project["path"]) / file_path
            if full_path.exists():
                try:
                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        contents.append(f.read())
                except:
                    contents.append("")
            else:
                contents.append("")
        
        if len(contents) >= 2 and contents[0] != contents[-1]:
            # Calculate diff percentage
            matcher = difflib.SequenceMatcher(None, contents[0], contents[-1])
            diff_percent = (1 - matcher.ratio()) * 100
            
            # Determine breaking risk
            risk = SeverityLevel.LOW
            if "router" in file_path or "main" in file_path:
                risk = SeverityLevel.HIGH
            elif "model" in file_path or "schema" in file_path:
                risk = SeverityLevel.MEDIUM
            elif diff_percent > 50:
                risk = SeverityLevel.MEDIUM
            
            return FileChange(
                file_path=file_path,
                change_type=ChangeType.MODIFIED,
                old_lines=len(contents[0].splitlines()),
                new_lines=len(contents[-1].splitlines()),
                diff_percent=round(diff_percent, 2),
                breaking_risk=risk
            )
        
        return None
    
    def _compare_connections(self):
        """Compare connections between projects"""
        for i, project in enumerate(self.projects):
            conn_set = set()
            for conn in project["data"]["connections"]:
                key = f"{conn['source_id']}|{conn['target_id']}|{conn['type']}"
                conn_set.add(key)
            
            if i > 0:
                prev_conn_set = set()
                for conn in self.projects[i-1]["data"]["connections"]:
                    key = f"{conn['source_id']}|{conn['target_id']}|{conn['type']}"
                    prev_conn_set.add(key)
                
                # Find added connections
                for key in conn_set - prev_conn_set:
                    parts = key.split("|")
                    self.connection_changes.append(ConnectionChange(
                        source=parts[0],
                        target=parts[1],
                        change_type=ChangeType.ADDED,
                        connection_type=parts[2],
                        project_index=i
                    ))
                
                # Find removed connections
                for key in prev_conn_set - conn_set:
                    parts = key.split("|")
                    self.connection_changes.append(ConnectionChange(
                        source=parts[0],
                        target=parts[1],
                        change_type=ChangeType.REMOVED,
                        connection_type=parts[2],
                        project_index=i
                    ))
    
    def _calculate_instability(self):
        """Calculate instability metrics for each file"""
        if not self.projects:
            return
        
        latest = self.projects[-1]
        
        # Count how many times each file changed
        change_count: Dict[str, int] = {}
        for fc in self.file_changes:
            change_count[fc.file_path] = change_count.get(fc.file_path, 0) + 1
        
        # Count dependencies
        dependency_count: Dict[str, int] = {}
        dependent_count: Dict[str, int] = {}
        
        for conn in latest["data"]["connections"]:
            source = conn["source_id"]
            target = conn["target_id"]
            dependency_count[source] = dependency_count.get(source, 0) + 1
            dependent_count[target] = dependent_count.get(target, 0) + 1
        
        # Calculate instability score
        for node in latest["data"]["nodes"]:
            if node["type"] != "file":
                continue
            
            file_path = node["file_path"]
            node_id = node["id"]
            
            changes = change_count.get(file_path, 0)
            deps = dependency_count.get(node_id, 0)
            dependents = dependent_count.get(node_id, 0)
            
            # Instability formula: high changes + high deps + low dependents = unstable
            score = 0
            reasons = []
            
            if changes > 2:
                score += 30
                reasons.append(f"Changed {changes} times")
            
            if deps > 10:
                score += 20
                reasons.append(f"Has {deps} dependencies")
            
            if dependents == 0 and deps > 0:
                score += 25
                reasons.append("No dependents (possibly dead code)")
            
            if "test" not in file_path.lower() and dependents < 2:
                score += 15
                reasons.append("Low usage")
            
            # Check for broken connections
            broken = sum(1 for c in latest["data"]["connections"] 
                        if c["source_id"] == node_id and c["status"] == "broken")
            if broken > 0:
                score += 10 * min(broken, 5)
                reasons.append(f"{broken} broken imports")
            
            if score > 0:
                self.instability_metrics[file_path] = InstabilityMetric(
                    file_path=file_path,
                    score=min(score, 100),
                    reasons=reasons,
                    change_frequency=changes,
                    dependency_count=deps,
                    dependent_count=dependents
                )
    
    def _generate_heat_maps(self):
        """Generate heat map data for visualization"""
        if not self.projects:
            return
        
        latest = self.projects[-1]
        
        # Changes heat map
        changes_heat = []
        max_changes = max((fc.diff_percent for fc in self.file_changes if fc.change_type == ChangeType.MODIFIED), default=1)
        
        for node in latest["data"]["nodes"]:
            if node["type"] == "file":
                file_path = node["file_path"]
                change = next((fc for fc in self.file_changes if fc.file_path == file_path), None)
                
                if change and change.change_type == ChangeType.MODIFIED:
                    heat = change.diff_percent / max(max_changes, 1)
                elif change and change.change_type == ChangeType.ADDED:
                    heat = 1.0
                elif change and change.change_type == ChangeType.REMOVED:
                    heat = 0.8
                else:
                    heat = 0.0
                
                changes_heat.append(HeatMapData(
                    node_id=node["id"],
                    heat_value=heat,
                    category="changes"
                ))
        
        self.heat_maps["changes"] = changes_heat
        
        # Instability heat map
        instability_heat = []
        for node in latest["data"]["nodes"]:
            if node["type"] == "file":
                file_path = node["file_path"]
                metric = self.instability_metrics.get(file_path)
                heat = metric.score / 100 if metric else 0.0
                
                instability_heat.append(HeatMapData(
                    node_id=node["id"],
                    heat_value=heat,
                    category="instability"
                ))
        
        self.heat_maps["instability"] = instability_heat
        
        # Connection density heat map
        connection_count: Dict[str, int] = {}
        for conn in latest["data"]["connections"]:
            connection_count[conn["source_id"]] = connection_count.get(conn["source_id"], 0) + 1
            connection_count[conn["target_id"]] = connection_count.get(conn["target_id"], 0) + 1
        
        max_conns = max(connection_count.values()) if connection_count else 1
        
        connections_heat = []
        for node in latest["data"]["nodes"]:
            count = connection_count.get(node["id"], 0)
            connections_heat.append(HeatMapData(
                node_id=node["id"],
                heat_value=count / max_conns,
                category="connections"
            ))
        
        self.heat_maps["connections"] = connections_heat
        
        # Code weight heat map (based on file size/complexity)
        weight_heat = []
        for node in latest["data"]["nodes"]:
            if node["type"] == "file":
                # Use line count as weight proxy
                lines = node.get("line_end", 0) - node.get("line_start", 0)
                # Normalize (assume max 1000 lines)
                heat = min(lines / 1000, 1.0) if lines > 0 else 0.1
                
                weight_heat.append(HeatMapData(
                    node_id=node["id"],
                    heat_value=heat,
                    category="weight"
                ))
        
        self.heat_maps["weight"] = weight_heat
    
    def _analyze_graph_structure(self) -> Dict[str, Any]:
        """Analyze the graph structure and provide explanations"""
        if not self.projects:
            return {}
        
        latest = self.projects[-1]
        nodes = latest["data"]["nodes"]
        connections = latest["data"]["connections"]
        
        # Calculate metrics
        total_nodes = len(nodes)
        total_connections = len(connections)
        
        # Hierarchy analysis
        in_degree: Dict[str, int] = {}
        out_degree: Dict[str, int] = {}
        
        for conn in connections:
            out_degree[conn["source_id"]] = out_degree.get(conn["source_id"], 0) + 1
            in_degree[conn["target_id"]] = in_degree.get(conn["target_id"], 0) + 1
        
        # Find root nodes (high out-degree, low in-degree)
        roots = [n["id"] for n in nodes if out_degree.get(n["id"], 0) > 5 and in_degree.get(n["id"], 0) < 2]
        
        # Find leaf nodes (low out-degree, high in-degree)
        leaves = [n["id"] for n in nodes if out_degree.get(n["id"], 0) < 2 and in_degree.get(n["id"], 0) > 3]
        
        # Find hub nodes (high both)
        hubs = [n["id"] for n in nodes if out_degree.get(n["id"], 0) > 10 and in_degree.get(n["id"], 0) > 5]
        
        # Determine layout type
        if len(roots) > total_nodes * 0.1:
            layout_type = "hierarchical_vertical"
            layout_explanation = "Vertical hierarchy: Clear top-down structure with entry points at top"
        elif len(hubs) > total_nodes * 0.05:
            layout_type = "hub_spoke"
            layout_explanation = "Hub-spoke pattern: Central nodes connect many peripheral nodes"
        elif total_connections / max(total_nodes, 1) > 5:
            layout_type = "dense_mesh"
            layout_explanation = "Dense mesh: High interconnectivity, complex dependencies"
        else:
            layout_type = "modular"
            layout_explanation = "Modular structure: Loosely coupled components"
        
        # Identify dominant areas
        service_weights: Dict[str, int] = {}
        for node in nodes:
            service = node.get("service", "unknown")
            service_weights[service] = service_weights.get(service, 0) + 1
        
        dominant_services = sorted(service_weights.items(), key=lambda x: x[1], reverse=True)[:5]
        
        return {
            "layout_type": layout_type,
            "layout_explanation": layout_explanation,
            "total_nodes": total_nodes,
            "total_connections": total_connections,
            "connection_density": round(total_connections / max(total_nodes, 1), 2),
            "root_nodes": roots[:10],
            "leaf_nodes": leaves[:10],
            "hub_nodes": hubs[:10],
            "dominant_services": [{"service": s, "node_count": c} for s, c in dominant_services],
            "hierarchy_depth": self._estimate_hierarchy_depth(connections),
            "modularity_score": self._calculate_modularity(nodes, connections),
            "explanations": {
                "circular": "Circular layout indicates no dominant hierarchy - all nodes are peers",
                "vertical": "Vertical layout shows clear top-down flow from entry points to utilities",
                "horizontal": "Horizontal layout shows parallel processing or microservices architecture",
                "clustered": "Clustered layout shows modular design with clear service boundaries",
                "hub_spoke": "Hub-spoke shows central coordinators with many dependent modules"
            }
        }
    
    def _estimate_hierarchy_depth(self, connections: List[Dict]) -> int:
        """Estimate the depth of the dependency hierarchy"""
        # Build adjacency list
        adj: Dict[str, List[str]] = {}
        for conn in connections:
            if conn["source_id"] not in adj:
                adj[conn["source_id"]] = []
            adj[conn["source_id"]].append(conn["target_id"])
        
        # BFS to find max depth
        max_depth = 0
        visited = set()
        
        for start in list(adj.keys())[:100]:  # Limit for performance
            if start in visited:
                continue
            
            queue = [(start, 0)]
            while queue:
                node, depth = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                max_depth = max(max_depth, depth)
                
                for neighbor in adj.get(node, [])[:10]:  # Limit neighbors
                    if neighbor not in visited:
                        queue.append((neighbor, depth + 1))
        
        return max_depth
    
    def _calculate_modularity(self, nodes: List[Dict], connections: List[Dict]) -> float:
        """Calculate modularity score (0-1, higher = more modular)"""
        # Group nodes by service
        services: Dict[str, Set[str]] = {}
        for node in nodes:
            service = node.get("service", "unknown")
            if service not in services:
                services[service] = set()
            services[service].add(node["id"])
        
        # Count intra-service vs inter-service connections
        intra = 0
        inter = 0
        
        for conn in connections:
            source_service = None
            target_service = None
            
            for service, node_ids in services.items():
                if conn["source_id"] in node_ids:
                    source_service = service
                if conn["target_id"] in node_ids:
                    target_service = service
            
            if source_service and target_service:
                if source_service == target_service:
                    intra += 1
                else:
                    inter += 1
        
        total = intra + inter
        if total == 0:
            return 0.5
        
        return round(intra / total, 2)
    
    def _build_evolution_timeline(self) -> List[Dict[str, Any]]:
        """Build timeline of how code evolved"""
        timeline = []
        
        for i, project in enumerate(self.projects):
            stats = project["data"]["stats"]
            
            entry = {
                "index": i,
                "label": project["label"],
                "stats": stats,
                "changes_from_previous": None
            }
            
            if i > 0:
                prev_stats = self.projects[i-1]["data"]["stats"]
                entry["changes_from_previous"] = {
                    "files_delta": stats["total_files"] - prev_stats["total_files"],
                    "connections_delta": stats["total_connections"] - prev_stats["total_connections"],
                    "functions_delta": stats["total_functions"] - prev_stats["total_functions"],
                    "endpoints_delta": stats["total_endpoints"] - prev_stats["total_endpoints"],
                    "broken_delta": stats["broken_connections"] - prev_stats["broken_connections"]
                }
            
            timeline.append(entry)
        
        return timeline
    
    def _detect_breaking_changes(self) -> List[Dict[str, Any]]:
        """Detect potential breaking changes"""
        breaking = []
        
        # Removed files that were imported
        for fc in self.file_changes:
            if fc.change_type == ChangeType.REMOVED:
                breaking.append({
                    "type": "file_removed",
                    "file": fc.file_path,
                    "severity": fc.breaking_risk.value,
                    "description": f"File {fc.file_path} was removed"
                })
        
        # Heavily modified routers/main files
        for fc in self.file_changes:
            if fc.change_type == ChangeType.MODIFIED and fc.diff_percent > 30:
                if "router" in fc.file_path or "main" in fc.file_path:
                    breaking.append({
                        "type": "major_modification",
                        "file": fc.file_path,
                        "severity": "high",
                        "description": f"Critical file {fc.file_path} changed by {fc.diff_percent}%"
                    })
        
        # Removed connections
        removed_conns = [cc for cc in self.connection_changes if cc.change_type == ChangeType.REMOVED]
        if len(removed_conns) > 50:
            breaking.append({
                "type": "mass_disconnection",
                "count": len(removed_conns),
                "severity": "critical",
                "description": f"{len(removed_conns)} connections were removed"
            })
        
        return breaking
    
    def _analyze_code_drift(self) -> Dict[str, Any]:
        """Analyze how code has drifted over time"""
        if len(self.projects) < 2:
            return {}
        
        first = self.projects[0]["data"]
        last = self.projects[-1]["data"]
        
        # Calculate overall drift
        first_files = {n["file_path"] for n in first["nodes"] if n["type"] == "file"}
        last_files = {n["file_path"] for n in last["nodes"] if n["type"] == "file"}
        
        common = first_files & last_files
        added = last_files - first_files
        removed = first_files - last_files
        
        # Modified files
        modified = [fc for fc in self.file_changes if fc.change_type == ChangeType.MODIFIED]
        
        drift_score = (len(added) + len(removed) + len(modified)) / max(len(first_files), 1) * 100
        
        return {
            "drift_score": round(min(drift_score, 100), 2),
            "drift_interpretation": self._interpret_drift(drift_score),
            "files_unchanged": len(common) - len(modified),
            "files_added": len(added),
            "files_removed": len(removed),
            "files_modified": len(modified),
            "most_changed_files": sorted(
                [fc.to_dict() for fc in modified],
                key=lambda x: x["diff_percent"],
                reverse=True
            )[:10],
            "stability_areas": self._find_stable_areas(common, modified),
            "volatile_areas": self._find_volatile_areas(modified)
        }
    
    def _interpret_drift(self, score: float) -> str:
        if score < 10:
            return "Minimal drift - codebase is stable"
        elif score < 30:
            return "Moderate drift - normal development pace"
        elif score < 50:
            return "Significant drift - major refactoring occurred"
        elif score < 70:
            return "High drift - substantial architectural changes"
        else:
            return "Extreme drift - near-complete rewrite"
    
    def _find_stable_areas(self, common: Set[str], modified: List[FileChange]) -> List[str]:
        """Find areas of the codebase that remained stable"""
        modified_paths = {fc.file_path for fc in modified}
        stable = common - modified_paths
        
        # Group by service/directory
        stable_dirs: Dict[str, int] = {}
        for path in stable:
            parts = path.split("/")
            if len(parts) > 1:
                dir_name = parts[0]
                stable_dirs[dir_name] = stable_dirs.get(dir_name, 0) + 1
        
        return sorted(stable_dirs.items(), key=lambda x: x[1], reverse=True)[:5]
    
    def _find_volatile_areas(self, modified: List[FileChange]) -> List[Dict[str, Any]]:
        """Find areas with most changes"""
        dir_changes: Dict[str, List[float]] = {}
        
        for fc in modified:
            parts = fc.file_path.split("/")
            if len(parts) > 1:
                dir_name = parts[0]
                if dir_name not in dir_changes:
                    dir_changes[dir_name] = []
                dir_changes[dir_name].append(fc.diff_percent)
        
        volatile = []
        for dir_name, changes in dir_changes.items():
            volatile.append({
                "directory": dir_name,
                "files_changed": len(changes),
                "avg_change_percent": round(sum(changes) / len(changes), 2)
            })
        
        return sorted(volatile, key=lambda x: x["avg_change_percent"], reverse=True)[:5]


def compare_projects(paths: List[Tuple[str, str]]) -> Dict[str, Any]:
    """Compare multiple projects
    
    Args:
        paths: List of (path, label) tuples
    
    Returns:
        Comparison analysis data
    """
    comparator = MultiProjectComparator()
    
    for path, label in paths:
        comparator.add_project(path, label)
    
    return comparator.compare_all()
