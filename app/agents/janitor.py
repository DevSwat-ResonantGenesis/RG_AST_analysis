"""
Graph Janitor Agent (GJA) - First Autonomous Agent
===================================================
Mission: Increase execution reachability while decreasing system entropy.

This is the only correct first agent.
Not a coder. Not a refactorer. Not a feature builder.

Allowed actions (initial phase):
✅ INSPECTION
✅ CLASSIFICATION  
✅ PROPOSE (never auto-delete)
❌ Direct deletion
❌ Multi-root mutation
❌ Cross-service rewrites

Agent Memory Requirements (for "aliveness"):
- Persistent memory of past proposals
- Awareness of rejected actions
- Longitudinal entropy tracking
- Proposal deduplication across time
- Identity / epoch versioning
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
import json

from .gal import GALEngine, ActionContract, ActionCategory
from .memory_postgres import PostgresAgentMemory


@dataclass
class AgentProposal:
    """Structured proposal from the agent"""
    proposal_type: str
    root_node: str
    reason: str
    expected_gain: str
    risk: float
    utility: float
    action_contract: Optional[ActionContract] = None
    
    def to_dict(self) -> Dict:
        return {
            "proposal": self.proposal_type,
            "root": self.root_node,
            "reason": self.reason,
            "expected_gain": self.expected_gain,
            "risk": self.risk,
            "utility": self.utility,
            "action": self.action_contract.to_dict() if self.action_contract else None
        }


@dataclass
class AgentReport:
    """Batch report from agent scan"""
    timestamp: datetime
    scan_duration_ms: int
    total_nodes: int
    reachable_nodes: int
    unreachable_nodes: int
    reachability_score: float
    isolated_nodes: int
    orphan_endpoints: int
    proposals: List[AgentProposal]
    health_indicators: Dict[str, Any]
    
    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "scan_duration_ms": self.scan_duration_ms,
            "metrics": {
                "total_nodes": self.total_nodes,
                "reachable_nodes": self.reachable_nodes,
                "unreachable_nodes": self.unreachable_nodes,
                "reachability_score": round(self.reachability_score * 100, 2),
                "isolated_nodes": self.isolated_nodes,
                "orphan_endpoints": self.orphan_endpoints
            },
            "health_indicators": self.health_indicators,
            "proposals": [p.to_dict() for p in self.proposals],
            "proposal_count": len(self.proposals)
        }


class GraphJanitorAgent:
    """
    Graph Janitor Agent - Autonomous graph cleanup agent
    
    Internal decision loop:
    1. Scan graph
    2. Identify unreachable subgraphs
    3. Classify subgraphs
    4. Simulate isolation or collapse
    5. Rank actions by reachability_gain / risk
    6. Propose top N actions
    """
    
    def __init__(self, graph_data: Dict, config: Optional[Dict] = None, analysis_id: Optional[str] = None):
        self.gal = GALEngine(graph_data)
        self.graph_data = graph_data
        self.config = config or {}
        self.analysis_id = analysis_id or "unknown"
        
        # Agent settings
        self.max_proposals = self.config.get('max_proposals', 10)
        self.min_utility_threshold = self.config.get('min_utility', 0)
        self.max_risk_threshold = self.config.get('max_risk', 8)
        self.blast_radius_limit = self.config.get('blast_radius_limit', 100)
        
        # Persistent memory (converts tool → agent)
        # Note: PostgresAgentMemory requires async init() call
        self.memory = None  # Will be initialized async in main.py
        self.agent_id = self.config.get('agent_id', 'janitor_agent')
        
        # Current epoch
        self.current_epoch: Optional[int] = None
        
        # State
        self.last_scan: Optional[AgentReport] = None
        self.proposal_history: List[AgentProposal] = []
        
        # Execution safety flags
        self.sandbox_mode = self.config.get('sandbox_mode', True)  # Default: sandbox only
        self.execution_enabled = self.config.get('execution_enabled', False)  # Default: disabled
    
    def scan(self) -> AgentReport:
        """
        Execute full scan and generate proposals
        This is the main entry point for the agent
        """
        start_time = datetime.now()
        
        # Step 0: Start new epoch in memory
        initial_metrics = {
            "total_nodes": len(self.gal.nodes),
            "reachable": sum(1 for m in self.gal.metadata.values() if m.reachable)
        }
        self.current_epoch = self.memory.start_epoch(self.analysis_id, initial_metrics)
        
        # Step 1: Gather metrics
        unreachable = self.gal.scan_unreachable()
        orphan_endpoints = self.gal.find_orphan_endpoints()
        
        total_nodes = len(self.gal.nodes)
        reachable_count = sum(1 for m in self.gal.metadata.values() if m.reachable)
        unreachable_count = total_nodes - reachable_count
        reachability_score = reachable_count / total_nodes if total_nodes > 0 else 0
        
        # Count isolated nodes (no connections at all)
        connected_nodes = set()
        for conn in self.gal.connections:
            connected_nodes.add(conn['source_id'])
            connected_nodes.add(conn['target_id'])
        isolated_count = total_nodes - len(connected_nodes)
        
        # Get rejected nodes from memory - avoid re-proposing
        rejected_nodes = self.memory.get_rejected_nodes(lookback_epochs=10)
        
        # Step 2: Identify candidates for action
        proposals = []
        
        # Analyze unreachable nodes
        for node_info in unreachable:
            # Skip if recently rejected
            if node_info['id'] in rejected_nodes:
                continue
            
            # Skip if duplicate proposal
            if self.memory.is_duplicate_proposal(node_info['id'], "TAG_DEAD"):
                continue
            
            proposal = self._analyze_unreachable_node(node_info)
            if proposal and proposal.utility >= self.min_utility_threshold:
                proposals.append(proposal)
        
        # Analyze orphan endpoints
        for endpoint in orphan_endpoints:
            if endpoint['id'] in rejected_nodes:
                continue
            
            proposal = self._analyze_orphan_endpoint(endpoint)
            if proposal and proposal.utility >= self.min_utility_threshold:
                proposals.append(proposal)
        
        # Step 3: Rank by utility
        proposals.sort(key=lambda p: p.utility, reverse=True)
        
        # Step 4: Take top N
        top_proposals = proposals[:self.max_proposals]
        
        # Step 5: Record proposals in memory
        for proposal in top_proposals:
            self.memory.record_proposal(proposal.to_dict(), self.current_epoch, self.analysis_id)
        
        # Calculate health indicators
        health = self._calculate_health_indicators(
            reachability_score, isolated_count, len(orphan_endpoints), total_nodes
        )
        
        # Add memory stats to health
        health['false_positive_rate'] = round(self.memory.calculate_false_positive_rate() * 100, 1)
        health['current_epoch'] = self.current_epoch
        
        # Record metrics for longitudinal tracking
        self.memory.record_metrics(self.analysis_id, self.current_epoch, {
            'reachability_score': reachability_score * 100,
            'unreachable_nodes': unreachable_count,
            'isolated_nodes': isolated_count,
            'orphan_endpoints': len(orphan_endpoints),
            'total_nodes': total_nodes,
            'health_score': health['health_score']
        })
        
        end_time = datetime.now()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
        
        report = AgentReport(
            timestamp=start_time,
            scan_duration_ms=duration_ms,
            total_nodes=total_nodes,
            reachable_nodes=reachable_count,
            unreachable_nodes=unreachable_count,
            reachability_score=reachability_score,
            isolated_nodes=isolated_count,
            orphan_endpoints=len(orphan_endpoints),
            proposals=top_proposals,
            health_indicators=health
        )
        
        self.last_scan = report
        self.proposal_history.extend(top_proposals)
        
        # End epoch with stats
        self.memory.end_epoch(self.current_epoch, {
            'reachability_score': reachability_score * 100
        }, {
            'generated': len(top_proposals)
        })
        
        return report
    
    def _analyze_unreachable_node(self, node_info: Dict) -> Optional[AgentProposal]:
        """Analyze an unreachable node and generate proposal"""
        node_id = node_info['id']
        node_type = node_info['type']
        blast_radius = node_info['blast_radius']
        
        # Skip if blast radius too large
        if blast_radius > self.blast_radius_limit:
            return None
        
        # Count connections
        incoming = sum(1 for c in self.gal.connections if c['target_id'] == node_id)
        outgoing = sum(1 for c in self.gal.connections if c['source_id'] == node_id)
        
        # Determine action type based on analysis
        if incoming == 0 and outgoing == 0:
            # Completely isolated - safe to tag as dead
            proposal_type = "TAG_DEAD"
            reason = f"Completely isolated node (0 connections)"
            expected_gain = "+0.1% entropy reduction"
            risk = 1.0
        elif incoming == 0 and outgoing > 0:
            # No callers but has dependencies - candidate for isolation
            proposal_type = "ISOLATE_SUBGRAPH"
            reason = f"0 incoming CALLS, {outgoing} outgoing IMPORTS"
            expected_gain = f"+{round(1/len(self.gal.nodes) * 100, 2)}% reachability"
            risk = min(5.0, blast_radius * 0.5)
        else:
            # Has some connections - needs investigation
            proposal_type = "TAG_DORMANT"
            reason = f"{incoming} incoming, {outgoing} outgoing - possibly dormant"
            expected_gain = "Classification only"
            risk = 0.5
        
        # Calculate utility
        utility = self._calculate_proposal_utility(proposal_type, blast_radius, risk)
        
        if risk > self.max_risk_threshold:
            return None
        
        # Create action contract if applicable
        action_contract = None
        try:
            if proposal_type == "TAG_DEAD":
                action_contract = self.gal.tag_subgraph(node_id, "dead")
            elif proposal_type == "TAG_DORMANT":
                action_contract = self.gal.tag_subgraph(node_id, "dormant")
            elif proposal_type == "ISOLATE_SUBGRAPH":
                action_contract = self.gal.propose_isolate_subgraph(node_id)
        except Exception as e:
            pass  # Action creation failed, still return proposal
        
        return AgentProposal(
            proposal_type=proposal_type,
            root_node=node_id,
            reason=reason,
            expected_gain=expected_gain,
            risk=risk,
            utility=utility,
            action_contract=action_contract
        )
    
    def _analyze_orphan_endpoint(self, endpoint: Dict) -> Optional[AgentProposal]:
        """Analyze an orphan endpoint"""
        node_id = endpoint['id']
        
        # Orphan endpoints are usually dead code
        proposal_type = "TAG_ORPHAN_ENDPOINT"
        reason = f"API endpoint with no internal callers"
        expected_gain = "Identification of unused API"
        risk = 2.0  # Endpoints might be called externally
        
        utility = self._calculate_proposal_utility(proposal_type, 1, risk)
        
        return AgentProposal(
            proposal_type=proposal_type,
            root_node=node_id,
            reason=reason,
            expected_gain=expected_gain,
            risk=risk,
            utility=utility
        )
    
    def _calculate_proposal_utility(self, proposal_type: str, blast_radius: int, risk: float) -> float:
        """Calculate utility score for a proposal"""
        base_utility = {
            "TAG_DEAD": 3.0,
            "TAG_DORMANT": 2.0,
            "TAG_ORPHAN_ENDPOINT": 2.5,
            "ISOLATE_SUBGRAPH": 4.0,
            "PROPOSE_DELETE": 5.0
        }.get(proposal_type, 1.0)
        
        # Apply penalties
        utility = base_utility - (risk * 0.5) - (blast_radius * 0.1)
        return max(0, utility)
    
    def _calculate_health_indicators(self, reachability: float, isolated: int, 
                                     orphans: int, total: int) -> Dict:
        """Calculate overall health indicators"""
        # Health score (0-100)
        health_score = (
            reachability * 40 +  # Reachability is 40% of health
            max(0, (1 - isolated / total) * 30) +  # Isolation penalty
            max(0, (1 - orphans / max(total * 0.1, 1)) * 30)  # Orphan penalty
        )
        
        # Status determination
        if health_score >= 80:
            status = "healthy"
            status_emoji = "🟢"
        elif health_score >= 50:
            status = "needs_attention"
            status_emoji = "🟡"
        else:
            status = "critical"
            status_emoji = "🔴"
        
        return {
            "health_score": round(health_score, 1),
            "status": status,
            "status_emoji": status_emoji,
            "recommendations": self._generate_recommendations(reachability, isolated, orphans)
        }
    
    def _generate_recommendations(self, reachability: float, isolated: int, orphans: int) -> List[str]:
        """Generate actionable recommendations"""
        recs = []
        
        if reachability < 0.25:
            recs.append("Critical: Less than 25% of code is reachable from entry points")
        
        if isolated > 10:
            recs.append(f"High isolation: {isolated} nodes have no connections")
        
        if orphans > 5:
            recs.append(f"Review {orphans} orphan API endpoints for removal")
        
        if reachability < 0.6:
            recs.append("Focus on connecting or removing unreachable code before adding features")
        
        if not recs:
            recs.append("Graph health is acceptable. Continue monitoring.")
        
        return recs
    
    def get_quick_actions(self) -> List[Dict]:
        """Get safe, quick actions that can be auto-approved"""
        if not self.last_scan:
            self.scan()
        
        quick_actions = []
        for proposal in self.last_scan.proposals:
            if proposal.risk <= 2.0 and proposal.proposal_type.startswith("TAG_"):
                quick_actions.append({
                    "type": proposal.proposal_type,
                    "target": proposal.root_node,
                    "risk": proposal.risk,
                    "auto_approvable": True
                })
        
        return quick_actions
    
    def execute_approved_actions(self) -> Dict:
        """
        Execute all approved actions.
        
        SAFETY ENFORCEMENT:
        - Execution disabled by default (sandbox_mode=True)
        - Must explicitly enable execution
        - All executions are logged to memory
        - Invariant checks before each action
        """
        # Safety gate: execution must be explicitly enabled
        if not self.execution_enabled:
            return {
                "executed": 0,
                "failed": 0,
                "blocked": True,
                "reason": "Execution disabled. Set execution_enabled=True in config to enable.",
                "sandbox_mode": self.sandbox_mode,
                "details": [],
                "errors": []
            }
        
        if self.sandbox_mode:
            return {
                "executed": 0,
                "failed": 0,
                "blocked": True,
                "reason": "Sandbox mode active. Actions simulated but not applied.",
                "sandbox_mode": True,
                "simulated": self._simulate_all_approved(),
                "details": [],
                "errors": []
            }
        
        executed = []
        failed = []
        
        for action in self.gal.pending_proposals:
            if action.status == "approved":
                # Invariant checks before execution
                invariant_check = self._check_execution_invariants(action)
                if not invariant_check['safe']:
                    failed.append({
                        "action_id": action.action_id,
                        "error": f"Invariant violation: {invariant_check['reason']}"
                    })
                    self.memory.update_proposal_status(
                        action.action_id, 'failed', 
                        reason=invariant_check['reason']
                    )
                    continue
                
                try:
                    # Compile to patch (pure transformation)
                    patch = self.gal.compile_to_patch(action.action_id)
                    
                    # Verify patch before applying
                    verification = self._verify_patch(patch)
                    if not verification['valid']:
                        raise ValueError(f"Patch verification failed: {verification['reason']}")
                    
                    # Mark as executed (actual file modification would happen here)
                    action.status = "executed"
                    
                    # Record in memory
                    self.memory.update_proposal_status(
                        action.action_id, 'executed',
                        result={'patch': patch, 'verification': verification}
                    )
                    
                    executed.append({
                        "action_id": action.action_id,
                        "type": action.action_type,
                        "patch": patch
                    })
                    
                except Exception as e:
                    failed.append({
                        "action_id": action.action_id,
                        "error": str(e)
                    })
                    self.memory.update_proposal_status(
                        action.action_id, 'failed',
                        reason=str(e)
                    )
        
        return {
            "executed": len(executed),
            "failed": len(failed),
            "blocked": False,
            "sandbox_mode": False,
            "details": executed,
            "errors": failed
        }
    
    def _check_execution_invariants(self, action: ActionContract) -> Dict:
        """
        Check invariants before execution.
        
        Required invariants:
        - execution_root preserved
        - blast_radius within limit
        - single-root mutation only
        """
        meta = self.gal.metadata.get(action.target_node)
        if not meta:
            return {"safe": False, "reason": "Target node not found in graph"}
        
        # Check blast radius limit
        if meta.blast_radius > self.blast_radius_limit:
            return {"safe": False, "reason": f"Blast radius {meta.blast_radius} exceeds limit {self.blast_radius_limit}"}
        
        # Check if trying to modify execution root
        if meta.execution_root and action.action_type in ["PROPOSE_DELETE_SUBGRAPH", "ISOLATE_SUBGRAPH"]:
            return {"safe": False, "reason": "Cannot delete or isolate execution root"}
        
        # Check mutation risk
        if meta.mutation_risk > 8:
            return {"safe": False, "reason": f"Mutation risk {meta.mutation_risk} too high"}
        
        return {"safe": True, "reason": None}
    
    def _verify_patch(self, patch: Dict) -> Dict:
        """Verify a patch before applying"""
        if not patch.get('target'):
            return {"valid": False, "reason": "No target specified"}
        
        if patch.get('error'):
            return {"valid": False, "reason": patch['error']}
        
        # Additional verification could check:
        # - File exists
        # - Diff is syntactically valid
        # - No unintended side effects
        
        return {"valid": True, "reason": None}
    
    def _simulate_all_approved(self) -> List[Dict]:
        """Simulate all approved actions without applying"""
        simulated = []
        for action in self.gal.pending_proposals:
            if action.status == "approved":
                patch = self.gal.compile_to_patch(action.action_id)
                simulated.append({
                    "action_id": action.action_id,
                    "type": action.action_type,
                    "would_affect": patch.get('files_affected', []),
                    "simulated_diffs": patch.get('diffs', [])
                })
        return simulated
    
    def get_status(self) -> Dict:
        """Get current agent status"""
        memory_stats = self.memory.get_agent_stats()
        
        return {
            "agent": "Graph Janitor Agent (GJA)",
            "version": "2.0",
            "mission": "Increase execution reachability while decreasing system entropy",
            "last_scan": self.last_scan.timestamp.isoformat() if self.last_scan else None,
            "current_epoch": self.current_epoch,
            "pending_proposals": len(self.gal.pending_proposals),
            "total_proposals_generated": len(self.proposal_history),
            "allowed_actions": ["INSPECTION", "CLASSIFICATION", "PROPOSE"],
            "restricted_actions": ["DIRECT_DELETE", "MULTI_ROOT_MUTATION", "CROSS_SERVICE_REWRITE"],
            "safety": {
                "sandbox_mode": self.sandbox_mode,
                "execution_enabled": self.execution_enabled
            },
            "memory": {
                "total_epochs": memory_stats.get('total_epochs', 0),
                "proposals_by_status": memory_stats.get('proposals_by_status', {}),
                "execution_rate": round(memory_stats.get('execution_rate', 0) * 100, 1),
                "avg_reachability_improvement": round(memory_stats.get('avg_reachability_improvement', 0), 2)
            }
        }
    
    def get_metrics_trend(self) -> List[Dict]:
        """Get longitudinal metrics trend"""
        return self.memory.get_metrics_trend(self.analysis_id)
    
    def get_epoch_history(self) -> List[Dict]:
        """Get epoch history"""
        return self.memory.get_epoch_history(self.analysis_id)
