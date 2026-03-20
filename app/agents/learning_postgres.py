"""
Learning Engine - PostgreSQL Version
Migrated from SQLite to PostgreSQL for production use
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
import asyncpg
import os


@dataclass
class OutcomeRecord:
    """Record of a proposal outcome"""
    patch_id: str
    action_id: str
    action_type: str
    target_node: str
    node_type: str
    blast_radius: int
    applied: bool
    rolled_back: bool
    human_rejected: bool
    reachability_delta: float
    isolated_nodes_delta: int
    approval_latency_seconds: int
    post_epoch_stability: bool
    proposed_at: str
    resolved_at: str


class PostgresLearningEngine:
    """
    Learning Engine using PostgreSQL instead of SQLite.
    Maintains same API as original but uses async PostgreSQL.
    """
    
    def __init__(self, db_url: Optional[str] = None):
        # asyncpg uses postgresql:// (not postgresql+asyncpg://)
        # Also needs sslmode= not ssl=
        # Use dedicated code_visualizer_db
        db_url_raw = db_url or os.getenv(
            "CODE_VISUALIZER_DATABASE_URL",
            f"postgresql://{os.getenv('DB_USER', 'doadmin')}:"
            f"{os.getenv('DB_PASSWORD', '')}@"
            f"{os.getenv('DB_HOST', 'resonant-db-do-user-18031534-0.g.db.ondigitalocean.com')}:"
            f"{os.getenv('DB_PORT', '25060')}/"
            f"code_visualizer_db?sslmode=require"
        )
        # Remove SQLAlchemy-specific prefix and fix SSL parameter
        self.db_url = db_url_raw.replace("postgresql+asyncpg://", "postgresql://").replace("?ssl=", "?sslmode=")
        self.pool: Optional[asyncpg.Pool] = None
        self.memory_window_days = 30
        
        # Models (same as original)
        self.action_prior = ActionPriorModel()
        self.node_stability = NodeStabilityModel()
        self.impact_model = ImpactModel()
        self.cost_model = CostModel(alpha=0.5)
    
    async def init(self):
        """Initialize database connection pool"""
        self.pool = await asyncpg.create_pool(
            self.db_url,
            min_size=1,
            max_size=10,
            command_timeout=60
        )
        await self._load_models()
    
    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
    
    async def _load_models(self):
        """Load model state from database"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT model_name, parameters FROM code_visualizer_model_state"
            )
            
            for row in rows:
                model_name = row['model_name']
                params = row['parameters']  # Already parsed as dict
                
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
    
    async def _save_models(self):
        """Save model state to database"""
        models = [
            ("action_prior", self.action_prior.parameters),
            ("node_stability", {
                "stability": self.node_stability.parameters,
                "touch_count": self.node_stability.touch_count
            }),
            ("impact", self.impact_model.parameters),
            ("cost", {
                "latency": self.cost_model.latency_params,
                "rollback": self.cost_model.rollback_params
            })
        ]
        
        async with self.pool.acquire() as conn:
            for model_name, params in models:
                await conn.execute("""
                    INSERT INTO code_visualizer_model_state (model_name, parameters, updated_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (model_name) 
                    DO UPDATE SET parameters = $2, updated_at = $3
                """, model_name, params, datetime.now())
    
    async def record_outcome(self, outcome: OutcomeRecord):
        """Record an outcome and update all models"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO code_visualizer_outcomes (
                    patch_id, action_id, action_type, target_node, node_type,
                    blast_radius, applied, rolled_back, human_rejected,
                    reachability_delta, entropy_delta, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (patch_id) DO UPDATE SET
                    applied = $7,
                    rolled_back = $8,
                    human_rejected = $9,
                    updated_at = CURRENT_TIMESTAMP
            """,
                outcome.patch_id,
                outcome.action_id,
                outcome.action_type,
                outcome.target_node,
                outcome.node_type,
                outcome.blast_radius,
                outcome.applied,
                outcome.rolled_back,
                outcome.human_rejected,
                outcome.reachability_delta,
                float(outcome.isolated_nodes_delta),
                datetime.now()
            )
        
        # Update models
        approved = outcome.applied and not outcome.human_rejected
        
        self.action_prior.record_outcome(
            outcome.action_type,
            outcome.node_type,
            outcome.blast_radius,
            approved
        )
        
        invariant_stress = outcome.blast_radius > 50
        self.node_stability.record_outcome(
            outcome.target_node,
            outcome.rolled_back,
            invariant_stress
        )
        
        self.impact_model.record_outcome(
            outcome.action_type,
            outcome.reachability_delta
        )
        
        self.cost_model.record_outcome(
            outcome.approval_latency_seconds,
            outcome.rolled_back
        )
        
        await self._save_models()
    
    async def compute_utility(self, proposal: Dict) -> float:
        """Compute learned utility for a proposal"""
        action_type = proposal.get("action_type", "unknown")
        node_type = proposal.get("node_type", "unknown")
        blast_radius = proposal.get("blast_radius", 0)
        target_node = proposal.get("target_node", "")
        
        # Approval probability
        approval_prob = self.action_prior.predict_approval(
            action_type, node_type, blast_radius
        )
        
        # Expected impact
        expected_impact = self.impact_model.predict_impact(action_type)
        
        # Stability penalty
        stability_penalty = self.node_stability.predict_instability(target_node)
        
        # Expected cost
        expected_cost = self.cost_model.predict_cost()
        
        # Utility = approval_prob * (impact - stability_penalty) - cost
        utility = approval_prob * (expected_impact - stability_penalty) - expected_cost
        
        return utility
    
    async def get_stats(self) -> Dict:
        """Get learning statistics"""
        async with self.pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM code_visualizer_outcomes"
            )
            
            approved = await conn.fetchval(
                "SELECT COUNT(*) FROM code_visualizer_outcomes WHERE applied = TRUE AND human_rejected = FALSE"
            )
            
            rolled_back = await conn.fetchval(
                "SELECT COUNT(*) FROM code_visualizer_outcomes WHERE rolled_back = TRUE"
            )
            
            avg_delta = await conn.fetchval(
                "SELECT AVG(reachability_delta) FROM code_visualizer_outcomes WHERE applied = TRUE"
            ) or 0.0
        
        approval_rate = (approved / total) if total > 0 else 0.0
        rollback_rate = (rolled_back / total) if total > 0 else 0.0
        
        return {
            "total_outcomes": total,
            "approved": approved,
            "approval_rate": approval_rate,
            "rolled_back": rolled_back,
            "rollback_rate": rollback_rate,
            "avg_reachability_delta": float(avg_delta)
        }
    
    async def cleanup_old_outcomes(self):
        """Remove outcomes older than memory window"""
        cutoff = datetime.now() - timedelta(days=self.memory_window_days)
        
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM code_visualizer_outcomes WHERE created_at < $1",
                cutoff
            )


# Model classes (same as original, just for reference)
class ActionPriorModel:
    def __init__(self):
        self.parameters = {}
    
    def record_outcome(self, action_type, node_type, blast_radius, approved):
        key = f"{action_type}_{node_type}_{blast_radius//10}"
        if key not in self.parameters:
            self.parameters[key] = {"approved": 0, "total": 0}
        self.parameters[key]["total"] += 1
        if approved:
            self.parameters[key]["approved"] += 1
    
    def predict_approval(self, action_type, node_type, blast_radius):
        key = f"{action_type}_{node_type}_{blast_radius//10}"
        if key in self.parameters:
            return self.parameters[key]["approved"] / max(1, self.parameters[key]["total"])
        return 0.5


class NodeStabilityModel:
    def __init__(self):
        self.parameters = {}
        self.touch_count = {}
    
    def record_outcome(self, node, rolled_back, invariant_stress):
        if node not in self.parameters:
            self.parameters[node] = {"rollbacks": 0, "touches": 0}
        self.parameters[node]["touches"] += 1
        if rolled_back:
            self.parameters[node]["rollbacks"] += 1
    
    def predict_instability(self, node):
        if node in self.parameters:
            return self.parameters[node]["rollbacks"] / max(1, self.parameters[node]["touches"])
        return 0.0


class ImpactModel:
    def __init__(self):
        self.parameters = {}
    
    def record_outcome(self, action_type, reachability_delta):
        if action_type not in self.parameters:
            self.parameters[action_type] = {"total_delta": 0.0, "count": 0}
        self.parameters[action_type]["total_delta"] += reachability_delta
        self.parameters[action_type]["count"] += 1
    
    def predict_impact(self, action_type):
        if action_type in self.parameters:
            return self.parameters[action_type]["total_delta"] / max(1, self.parameters[action_type]["count"])
        return 0.0


class CostModel:
    def __init__(self, alpha=0.5):
        self.alpha = alpha
        self.latency_params = {"total": 0.0, "count": 0}
        self.rollback_params = {"total": 0, "count": 0}
    
    def record_outcome(self, latency, rolled_back):
        self.latency_params["total"] += latency
        self.latency_params["count"] += 1
        self.rollback_params["count"] += 1
        if rolled_back:
            self.rollback_params["total"] += 1
    
    def predict_cost(self):
        avg_latency = self.latency_params["total"] / max(1, self.latency_params["count"])
        rollback_prob = self.rollback_params["total"] / max(1, self.rollback_params["count"])
        return self.alpha * avg_latency + (1 - self.alpha) * rollback_prob * 100
