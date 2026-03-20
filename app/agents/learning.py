"""
Constrained Learning Loop (CLL)
================================
Formal Specification v1.0

Learning answers exactly one question:
Given only compiler-verified actions, how does an agent improve its future 
proposals without ever violating safety?

Key constraint:
- Learning may NOT change what is ALLOWED
- Learning may ONLY change what is PREFERRED

LEARNING BOUNDARIES (hard rules):
The agent CANNOT learn:
- new action types
- new mutation primitives
- new invariant exceptions
- new execution authority
- new graph semantics

The agent CAN learn:
- which proposals are more likely to be approved
- which mutations produce measurable graph improvement
- which nodes are persistently unstable
- which actions have poor long-term payoff

This keeps learning orthogonal to safety.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from enum import Enum
import json
import sqlite3
import hashlib
import math
from pathlib import Path


@dataclass
class OutcomeRecord:
    """
    Outcome signal for a patch artifact.
    
    No subjective signals. No hidden rewards.
    """
    patch_id: str
    action_id: str
    action_type: str
    target_node: str
    node_type: str
    blast_radius: int
    
    # Outcome signals
    applied: bool = False
    rolled_back: bool = False
    human_rejected: bool = False
    
    # Metrics
    reachability_delta: float = 0.0
    isolated_nodes_delta: int = 0
    approval_latency_seconds: Optional[int] = None
    post_epoch_stability: bool = True
    
    # Timestamps
    proposed_at: str = ""
    resolved_at: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "patch_id": self.patch_id,
            "action_id": self.action_id,
            "action_type": self.action_type,
            "target_node": self.target_node,
            "node_type": self.node_type,
            "blast_radius": self.blast_radius,
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "human_rejected": self.human_rejected,
            "reachability_delta": self.reachability_delta,
            "isolated_nodes_delta": self.isolated_nodes_delta,
            "approval_latency_seconds": self.approval_latency_seconds,
            "post_epoch_stability": self.post_epoch_stability,
            "proposed_at": self.proposed_at,
            "resolved_at": self.resolved_at
        }


class LearningModel:
    """Base class for learning models"""
    
    def __init__(self, alpha: float = 0.1):
        """
        Initialize with fixed, non-adaptive learning rate.
        
        Alpha is small and fixed to prevent runaway optimization.
        """
        self.alpha = alpha
        self.parameters: Dict = {}
    
    def update(self, key: str, observed: float, predicted: float):
        """
        Simple update rule:
        M ← M + α · (observed_outcome − predicted_outcome)
        
        No gradient descent. No cross-model coupling.
        """
        current = self.parameters.get(key, 0.5)  # Default prior
        delta = self.alpha * (observed - predicted)
        self.parameters[key] = max(0.0, min(1.0, current + delta))
    
    def get(self, key: str, default: float = 0.5) -> float:
        return self.parameters.get(key, default)
    
    def decay(self, factor: float = 0.99):
        """Apply exponential decay to prevent stale beliefs"""
        for key in self.parameters:
            # Decay toward neutral (0.5)
            self.parameters[key] = 0.5 + factor * (self.parameters[key] - 0.5)


@dataclass
class ActionPriorModel(LearningModel):
    """
    Model 1: P(approve | action_type, node_type, blast_radius)
    
    Purpose: Stop proposing actions humans always reject
    """
    
    def __init__(self, alpha: float = 0.1):
        super().__init__(alpha)
        self.parameters = {}
    
    def get_key(self, action_type: str, node_type: str, blast_radius: int) -> str:
        """Create composite key for lookup"""
        br_bucket = "small" if blast_radius < 10 else "medium" if blast_radius < 50 else "large"
        return f"{action_type}:{node_type}:{br_bucket}"
    
    def predict_approval(self, action_type: str, node_type: str, blast_radius: int) -> float:
        """Predict probability of approval"""
        key = self.get_key(action_type, node_type, blast_radius)
        return self.get(key, 0.5)
    
    def record_outcome(self, action_type: str, node_type: str, blast_radius: int, approved: bool):
        """Update model based on outcome"""
        key = self.get_key(action_type, node_type, blast_radius)
        predicted = self.get(key, 0.5)
        observed = 1.0 if approved else 0.0
        self.update(key, observed, predicted)


@dataclass
class NodeStabilityModel(LearningModel):
    """
    Model 2: Stability(node) ∈ [0,1]
    
    Derived from:
    - repeated rollback
    - repeated rejection
    - repeated invariant stress
    
    Purpose: Avoid touching unstable areas prematurely
    """
    
    def __init__(self, alpha: float = 0.1):
        super().__init__(alpha)
        self.parameters = {}
        self.touch_count: Dict[str, int] = {}
    
    def get_stability(self, node_id: str) -> float:
        """Get stability score for a node (1.0 = stable, 0.0 = unstable)"""
        return self.get(node_id, 0.8)  # Default: assume stable
    
    def record_outcome(self, node_id: str, rolled_back: bool, rejected: bool, invariant_stress: bool):
        """Update stability based on outcome"""
        # Track touch count
        self.touch_count[node_id] = self.touch_count.get(node_id, 0) + 1
        
        # Calculate instability signal
        instability = 0.0
        if rolled_back:
            instability += 0.4
        if rejected:
            instability += 0.3
        if invariant_stress:
            instability += 0.3
        
        # Update stability (inverse of instability)
        predicted = self.get(node_id, 0.8)
        observed = 1.0 - instability
        self.update(node_id, observed, predicted)
    
    def get_touch_count(self, node_id: str) -> int:
        return self.touch_count.get(node_id, 0)


@dataclass
class ImpactModel(LearningModel):
    """
    Model 3: E[Δreachability | action, context]
    
    Purely empirical.
    
    Purpose: Prefer actions that actually improve structure
    """
    
    def __init__(self, alpha: float = 0.1):
        super().__init__(alpha)
        self.parameters = {}
    
    def get_key(self, action_type: str, node_type: str) -> str:
        return f"{action_type}:{node_type}"
    
    def predict_impact(self, action_type: str, node_type: str) -> float:
        """Predict expected reachability improvement"""
        key = self.get_key(action_type, node_type)
        return self.get(key, 0.0)  # Default: no expected impact
    
    def record_outcome(self, action_type: str, node_type: str, reachability_delta: float):
        """Update model based on observed impact"""
        key = self.get_key(action_type, node_type)
        predicted = self.get(key, 0.0)
        # Normalize delta to [0, 1] range
        observed = max(-1.0, min(1.0, reachability_delta * 10))  # Scale small deltas
        self.update(key, observed, predicted)


@dataclass
class CostModel(LearningModel):
    """
    Model 4: Cost(action) = approval_latency + rollback_probability
    
    Purpose:
    - Minimize human friction
    - Avoid high-maintenance changes
    """
    
    def __init__(self, alpha: float = 0.1):
        super().__init__(alpha)
        self.latency_params: Dict[str, float] = {}
        self.rollback_params: Dict[str, float] = {}
    
    def get_key(self, action_type: str) -> str:
        return action_type
    
    def predict_cost(self, action_type: str) -> float:
        """Predict total cost (lower is better)"""
        key = self.get_key(action_type)
        latency_cost = self.latency_params.get(key, 0.5)
        rollback_cost = self.rollback_params.get(key, 0.2)
        return latency_cost + rollback_cost
    
    def record_outcome(self, action_type: str, latency_seconds: Optional[int], rolled_back: bool):
        """Update cost model"""
        key = self.get_key(action_type)
        
        # Update latency (normalize to hours, cap at 1.0)
        if latency_seconds is not None:
            latency_hours = latency_seconds / 3600
            normalized_latency = min(1.0, latency_hours / 24)  # 24h = max cost
            current_latency = self.latency_params.get(key, 0.5)
            self.latency_params[key] = current_latency + self.alpha * (normalized_latency - current_latency)
        
        # Update rollback probability
        current_rollback = self.rollback_params.get(key, 0.2)
        observed_rollback = 1.0 if rolled_back else 0.0
        self.rollback_params[key] = current_rollback + self.alpha * (observed_rollback - current_rollback)


class ConstrainedLearningLoop:
    """
    The Constrained Learning Loop (CLL).
    
    Learning does NOT generate actions.
    It only reorders and filters already-legal proposals.
    
    Correct pipeline:
    1. Graph Scan
    2. Generate all legal GAL actions
    3. Filter by invariants (compiler)
    4. Rank by learned utility
    5. Propose top-K
    
    If learning fails, agent degrades gracefully to rule-based behavior.
    """
    
    def __init__(self, db_path: Optional[str] = None, alpha: float = 0.1):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent.parent / "data" / "learning.db")
        
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize models with fixed, non-adaptive learning rate
        self.action_prior = ActionPriorModel(alpha)
        self.node_stability = NodeStabilityModel(alpha)
        self.impact_model = ImpactModel(alpha)
        self.cost_model = CostModel(alpha)
        
        # Anti-divergence: bounded memory window
        self.memory_window_days = 30
        
        self._init_db()
        self._load_models()
    
    def _init_db(self):
        """Initialize outcome storage"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patch_id TEXT UNIQUE,
                action_id TEXT,
                action_type TEXT,
                target_node TEXT,
                node_type TEXT,
                blast_radius INTEGER,
                applied INTEGER,
                rolled_back INTEGER,
                human_rejected INTEGER,
                reachability_delta REAL,
                isolated_nodes_delta INTEGER,
                approval_latency_seconds INTEGER,
                post_epoch_stability INTEGER,
                proposed_at TEXT,
                resolved_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_state (
                model_name TEXT PRIMARY KEY,
                parameters TEXT,
                updated_at TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    def _load_models(self):
        """Load model state from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT model_name, parameters FROM model_state")
        for row in cursor.fetchall():
            model_name, params_json = row
            params = json.loads(params_json)
            
            if model_name == "action_prior":
                self.action_prior.parameters = params
            elif model_name == "node_stability":
                self.node_stability.parameters = params.get("stability", {})
                self.node_stability.touch_count = params.get("touch_count", {})
            elif model_name == "impact":
                self.impact_model.parameters = params
            elif model_name == "cost":
                self.cost_model.latency_params = params.get("latency", {})
                self.cost_model.rollback_params = params.get("rollback", {})
        
        conn.close()
    
    def _save_models(self):
        """Save model state to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        models = [
            ("action_prior", json.dumps(self.action_prior.parameters)),
            ("node_stability", json.dumps({
                "stability": self.node_stability.parameters,
                "touch_count": self.node_stability.touch_count
            })),
            ("impact", json.dumps(self.impact_model.parameters)),
            ("cost", json.dumps({
                "latency": self.cost_model.latency_params,
                "rollback": self.cost_model.rollback_params
            }))
        ]
        
        for model_name, params in models:
            cursor.execute("""
                INSERT OR REPLACE INTO model_state (model_name, parameters, updated_at)
                VALUES (?, ?, ?)
            """, (model_name, params, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
    
    def record_outcome(self, outcome: OutcomeRecord):
        """
        Record an outcome and update all models.
        
        This is the main learning entry point.
        """
        # Store outcome
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO outcomes (
                patch_id, action_id, action_type, target_node, node_type,
                blast_radius, applied, rolled_back, human_rejected,
                reachability_delta, isolated_nodes_delta, approval_latency_seconds,
                post_epoch_stability, proposed_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            outcome.patch_id,
            outcome.action_id,
            outcome.action_type,
            outcome.target_node,
            outcome.node_type,
            outcome.blast_radius,
            1 if outcome.applied else 0,
            1 if outcome.rolled_back else 0,
            1 if outcome.human_rejected else 0,
            outcome.reachability_delta,
            outcome.isolated_nodes_delta,
            outcome.approval_latency_seconds,
            1 if outcome.post_epoch_stability else 0,
            outcome.proposed_at,
            outcome.resolved_at
        ))
        
        conn.commit()
        conn.close()
        
        # Update models
        approved = outcome.applied and not outcome.human_rejected
        
        # Model 1: Action Prior
        self.action_prior.record_outcome(
            outcome.action_type,
            outcome.node_type,
            outcome.blast_radius,
            approved
        )
        
        # Model 2: Node Stability
        invariant_stress = outcome.blast_radius > 50  # Proxy for stress
        self.node_stability.record_outcome(
            outcome.target_node,
            outcome.rolled_back,
            outcome.human_rejected,
            invariant_stress
        )
        
        # Model 3: Impact
        if outcome.applied:
            self.impact_model.record_outcome(
                outcome.action_type,
                outcome.node_type,
                outcome.reachability_delta
            )
        
        # Model 4: Cost
        self.cost_model.record_outcome(
            outcome.action_type,
            outcome.approval_latency_seconds,
            outcome.rolled_back
        )
        
        # Persist models
        self._save_models()
    
    def compute_utility(self, proposal: Dict) -> float:
        """
        Compute learned utility for a proposal.
        
        This is used to RANK proposals, not to FILTER them.
        Filtering is done by the compiler/invariants.
        
        Utility = approval_prob * expected_impact - cost
        """
        action_type = proposal.get("proposal", "")
        node_type = proposal.get("node_type", "file")
        node_id = proposal.get("root", "")
        blast_radius = proposal.get("blast_radius", 1)
        
        # Get model predictions
        approval_prob = self.action_prior.predict_approval(action_type, node_type, blast_radius)
        stability = self.node_stability.get_stability(node_id)
        expected_impact = self.impact_model.predict_impact(action_type, node_type)
        cost = self.cost_model.predict_cost(action_type)
        
        # Compute utility
        # Higher approval prob = better
        # Higher stability = safer to touch
        # Higher impact = more valuable
        # Lower cost = better
        
        utility = (
            approval_prob * 0.3 +
            stability * 0.2 +
            (expected_impact + 1) / 2 * 0.3 +  # Normalize to [0,1]
            (1 - cost) * 0.2
        )
        
        return utility
    
    def rank_proposals(self, proposals: List[Dict]) -> List[Dict]:
        """
        Rank proposals by learned utility.
        
        Learning does NOT generate actions.
        It only reorders already-legal proposals.
        """
        # Compute utility for each proposal
        for proposal in proposals:
            proposal["learned_utility"] = self.compute_utility(proposal)
        
        # Sort by utility (descending)
        ranked = sorted(proposals, key=lambda p: p.get("learned_utility", 0), reverse=True)
        
        return ranked
    
    def apply_decay(self):
        """
        Apply exponential decay to all models.
        
        This prevents stale beliefs and ensures bounded memory.
        """
        self.action_prior.decay()
        self.node_stability.decay()
        self.impact_model.decay()
        self.cost_model.decay()
        self._save_models()
    
    def get_model_stats(self) -> Dict:
        """Get current model statistics"""
        return {
            "action_prior": {
                "parameters": len(self.action_prior.parameters),
                "sample": dict(list(self.action_prior.parameters.items())[:5])
            },
            "node_stability": {
                "parameters": len(self.node_stability.parameters),
                "touch_counts": len(self.node_stability.touch_count),
                "sample": dict(list(self.node_stability.parameters.items())[:5])
            },
            "impact": {
                "parameters": len(self.impact_model.parameters),
                "sample": dict(list(self.impact_model.parameters.items())[:5])
            },
            "cost": {
                "latency_params": len(self.cost_model.latency_params),
                "rollback_params": len(self.cost_model.rollback_params)
            }
        }
    
    def get_outcome_stats(self) -> Dict:
        """Get outcome statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Total outcomes
        cursor.execute("SELECT COUNT(*) FROM outcomes")
        total = cursor.fetchone()[0]
        
        # Approval rate
        cursor.execute("SELECT COUNT(*) FROM outcomes WHERE applied = 1 AND human_rejected = 0")
        approved = cursor.fetchone()[0]
        
        # Rollback rate
        cursor.execute("SELECT COUNT(*) FROM outcomes WHERE rolled_back = 1")
        rolled_back = cursor.fetchone()[0]
        
        # Average reachability delta
        cursor.execute("SELECT AVG(reachability_delta) FROM outcomes WHERE applied = 1")
        avg_delta = cursor.fetchone()[0] or 0
        
        conn.close()
        
        return {
            "total_outcomes": total,
            "approval_rate": approved / max(total, 1),
            "rollback_rate": rolled_back / max(total, 1),
            "avg_reachability_delta": avg_delta
        }
    
    def cleanup_old_data(self):
        """Remove data older than memory window (anti-divergence)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cutoff = (datetime.now() - timedelta(days=self.memory_window_days)).isoformat()
        
        cursor.execute("DELETE FROM outcomes WHERE created_at < ?", (cutoff,))
        
        conn.commit()
        conn.close()
