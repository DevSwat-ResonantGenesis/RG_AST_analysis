"""
Agent Memory System
====================
Persistent memory that converts GJA from a tool into a living agent.

Requirements for agent "aliveness":
- Persistent memory of past proposals
- Awareness of rejected actions
- Longitudinal entropy tracking
- Proposal deduplication across time
- Identity / epoch versioning
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field, asdict
import hashlib


@dataclass
class ProposalRecord:
    """Persistent record of a proposal"""
    proposal_id: str
    action_id: str
    proposal_type: str
    target_node: str
    reason: str
    expected_gain: str
    risk: float
    utility: float
    status: str  # proposed|approved|rejected|executed|rolled_back
    created_at: str
    updated_at: str
    epoch: int
    analysis_id: str
    outcome_metrics: Optional[Dict] = None
    rejection_reason: Optional[str] = None
    execution_result: Optional[Dict] = None


@dataclass
class AgentEpoch:
    """Represents a single agent scan/run"""
    epoch_id: int
    analysis_id: str
    timestamp: str
    metrics_before: Dict
    metrics_after: Optional[Dict] = None
    proposals_generated: int = 0
    proposals_approved: int = 0
    proposals_rejected: int = 0
    proposals_executed: int = 0
    reachability_delta: float = 0.0
    entropy_delta: float = 0.0


class AgentMemory:
    """
    Persistent memory for the Graph Janitor Agent.
    
    This converts GJA from a stateless batch analyzer into a living agent
    that remembers past decisions and learns from outcomes.
    """
    
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent.parent / "data" / "agent_memory.db")
        
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        self._init_db()
    
    def _init_db(self):
        """Initialize the SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Proposals table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                proposal_id TEXT PRIMARY KEY,
                action_id TEXT,
                proposal_type TEXT,
                target_node TEXT,
                target_node_hash TEXT,
                reason TEXT,
                expected_gain TEXT,
                risk REAL,
                utility REAL,
                status TEXT DEFAULT 'proposed',
                created_at TEXT,
                updated_at TEXT,
                epoch INTEGER,
                analysis_id TEXT,
                outcome_metrics TEXT,
                rejection_reason TEXT,
                execution_result TEXT
            )
        """)
        
        # Epochs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS epochs (
                epoch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT,
                timestamp TEXT,
                metrics_before TEXT,
                metrics_after TEXT,
                proposals_generated INTEGER DEFAULT 0,
                proposals_approved INTEGER DEFAULT 0,
                proposals_rejected INTEGER DEFAULT 0,
                proposals_executed INTEGER DEFAULT 0,
                reachability_delta REAL DEFAULT 0.0,
                entropy_delta REAL DEFAULT 0.0
            )
        """)
        
        # Metrics history table (for longitudinal tracking)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT,
                epoch INTEGER,
                timestamp TEXT,
                reachability_score REAL,
                unreachable_nodes INTEGER,
                isolated_nodes INTEGER,
                orphan_endpoints INTEGER,
                total_nodes INTEGER,
                health_score REAL
            )
        """)
        
        # Node history table (track changes to specific nodes)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS node_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT,
                node_hash TEXT,
                epoch INTEGER,
                action_taken TEXT,
                status_before TEXT,
                status_after TEXT,
                timestamp TEXT
            )
        """)
        
        # Indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_proposals_target ON proposals(target_node_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_proposals_epoch ON proposals(epoch)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_analysis ON metrics_history(analysis_id)")
        
        conn.commit()
        conn.close()
    
    def _hash_node(self, node_id: str) -> str:
        """Create a stable hash for a node ID"""
        return hashlib.sha256(node_id.encode()).hexdigest()[:16]
    
    def start_epoch(self, analysis_id: str, metrics: Dict) -> int:
        """Start a new agent epoch/run"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO epochs (analysis_id, timestamp, metrics_before)
            VALUES (?, ?, ?)
        """, (analysis_id, datetime.now().isoformat(), json.dumps(metrics)))
        
        epoch_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return epoch_id
    
    def end_epoch(self, epoch_id: int, metrics_after: Dict, stats: Dict):
        """End an epoch with final metrics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE epochs SET
                metrics_after = ?,
                proposals_generated = ?,
                proposals_approved = ?,
                proposals_rejected = ?,
                proposals_executed = ?,
                reachability_delta = ?,
                entropy_delta = ?
            WHERE epoch_id = ?
        """, (
            json.dumps(metrics_after),
            stats.get('generated', 0),
            stats.get('approved', 0),
            stats.get('rejected', 0),
            stats.get('executed', 0),
            stats.get('reachability_delta', 0.0),
            stats.get('entropy_delta', 0.0),
            epoch_id
        ))
        
        conn.commit()
        conn.close()
    
    def record_proposal(self, proposal: Dict, epoch: int, analysis_id: str) -> str:
        """Record a new proposal"""
        import uuid
        proposal_id = str(uuid.uuid4())
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO proposals (
                proposal_id, action_id, proposal_type, target_node, target_node_hash,
                reason, expected_gain, risk, utility, status, created_at, updated_at,
                epoch, analysis_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?)
        """, (
            proposal_id,
            proposal.get('action', {}).get('action_id', ''),
            proposal.get('proposal'),
            proposal.get('root'),
            self._hash_node(proposal.get('root', '')),
            proposal.get('reason'),
            proposal.get('expected_gain'),
            proposal.get('risk', 0),
            proposal.get('utility', 0),
            datetime.now().isoformat(),
            datetime.now().isoformat(),
            epoch,
            analysis_id
        ))
        
        conn.commit()
        conn.close()
        
        return proposal_id
    
    def update_proposal_status(self, proposal_id: str, status: str, 
                               reason: Optional[str] = None,
                               result: Optional[Dict] = None):
        """Update proposal status"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if status == 'rejected' and reason:
            cursor.execute("""
                UPDATE proposals SET status = ?, rejection_reason = ?, updated_at = ?
                WHERE proposal_id = ?
            """, (status, reason, datetime.now().isoformat(), proposal_id))
        elif status == 'executed' and result:
            cursor.execute("""
                UPDATE proposals SET status = ?, execution_result = ?, updated_at = ?
                WHERE proposal_id = ?
            """, (status, json.dumps(result), datetime.now().isoformat(), proposal_id))
        else:
            cursor.execute("""
                UPDATE proposals SET status = ?, updated_at = ?
                WHERE proposal_id = ?
            """, (status, datetime.now().isoformat(), proposal_id))
        
        conn.commit()
        conn.close()
    
    def record_metrics(self, analysis_id: str, epoch: int, metrics: Dict):
        """Record metrics for longitudinal tracking"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO metrics_history (
                analysis_id, epoch, timestamp, reachability_score,
                unreachable_nodes, isolated_nodes, orphan_endpoints,
                total_nodes, health_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            analysis_id,
            epoch,
            datetime.now().isoformat(),
            metrics.get('reachability_score', 0),
            metrics.get('unreachable_nodes', 0),
            metrics.get('isolated_nodes', 0),
            metrics.get('orphan_endpoints', 0),
            metrics.get('total_nodes', 0),
            metrics.get('health_score', 0)
        ))
        
        conn.commit()
        conn.close()
    
    def is_duplicate_proposal(self, target_node: str, proposal_type: str, 
                              lookback_epochs: int = 5) -> bool:
        """Check if this proposal was already made recently"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        node_hash = self._hash_node(target_node)
        
        cursor.execute("""
            SELECT COUNT(*) FROM proposals
            WHERE target_node_hash = ? 
            AND proposal_type = ?
            AND epoch >= (SELECT MAX(epoch_id) FROM epochs) - ?
            AND status != 'rejected'
        """, (node_hash, proposal_type, lookback_epochs))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count > 0
    
    def get_rejected_nodes(self, lookback_epochs: int = 10) -> set:
        """Get nodes that were recently rejected - avoid re-proposing"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT target_node FROM proposals
            WHERE status = 'rejected'
            AND epoch >= (SELECT MAX(epoch_id) FROM epochs) - ?
        """, (lookback_epochs,))
        
        rejected = {row[0] for row in cursor.fetchall()}
        conn.close()
        
        return rejected
    
    def get_proposal_history(self, target_node: str) -> List[Dict]:
        """Get all proposals ever made for a node"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        node_hash = self._hash_node(target_node)
        
        cursor.execute("""
            SELECT * FROM proposals
            WHERE target_node_hash = ?
            ORDER BY created_at DESC
        """, (node_hash,))
        
        columns = [desc[0] for desc in cursor.description]
        history = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        
        return history
    
    def get_metrics_trend(self, analysis_id: str, limit: int = 20) -> List[Dict]:
        """Get metrics trend over time"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM metrics_history
            WHERE analysis_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (analysis_id, limit))
        
        columns = [desc[0] for desc in cursor.description]
        trend = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        
        return list(reversed(trend))  # Oldest first
    
    def get_epoch_history(self, analysis_id: str, limit: int = 10) -> List[Dict]:
        """Get epoch history"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM epochs
            WHERE analysis_id = ?
            ORDER BY epoch_id DESC
            LIMIT ?
        """, (analysis_id, limit))
        
        columns = [desc[0] for desc in cursor.description]
        history = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        
        return history
    
    def get_agent_stats(self) -> Dict:
        """Get overall agent statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        stats = {}
        
        # Total epochs
        cursor.execute("SELECT COUNT(*) FROM epochs")
        stats['total_epochs'] = cursor.fetchone()[0]
        
        # Proposal stats
        cursor.execute("""
            SELECT status, COUNT(*) FROM proposals GROUP BY status
        """)
        stats['proposals_by_status'] = dict(cursor.fetchall())
        
        # Success rate
        cursor.execute("""
            SELECT COUNT(*) FROM proposals WHERE status = 'executed'
        """)
        executed = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) FROM proposals WHERE status IN ('approved', 'executed')
        """)
        approved = cursor.fetchone()[0]
        
        stats['execution_rate'] = executed / max(approved, 1)
        
        # Average reachability improvement
        cursor.execute("""
            SELECT AVG(reachability_delta) FROM epochs WHERE reachability_delta > 0
        """)
        avg_delta = cursor.fetchone()[0]
        stats['avg_reachability_improvement'] = avg_delta or 0
        
        conn.close()
        
        return stats
    
    def get_current_epoch(self, analysis_id: str) -> Optional[int]:
        """Get the current epoch for an analysis"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT MAX(epoch_id) FROM epochs WHERE analysis_id = ?
        """, (analysis_id,))
        
        result = cursor.fetchone()[0]
        conn.close()
        
        return result
    
    def calculate_false_positive_rate(self, lookback_epochs: int = 20) -> float:
        """Calculate the false positive rate (rejected / total proposed)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                COUNT(*) as total
            FROM proposals
            WHERE epoch >= (SELECT MAX(epoch_id) FROM epochs) - ?
        """, (lookback_epochs,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row and row[1] > 0:
            return row[0] / row[1]
        return 0.0
