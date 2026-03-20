"""
Agent Memory - PostgreSQL Version
Migrated from SQLite to PostgreSQL for production use
"""

import json
from datetime import datetime
from typing import Dict, List, Optional, Any
import asyncpg
import os


class PostgresAgentMemory:
    """
    Agent Memory using PostgreSQL instead of SQLite.
    Stores agent state, proposals, and learning history.
    """
    
    def __init__(self, agent_id: str, db_url: Optional[str] = None):
        self.agent_id = agent_id
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
    
    async def init(self):
        """Initialize database connection pool"""
        self.pool = await asyncpg.create_pool(
            self.db_url,
            min_size=1,
            max_size=10,
            command_timeout=60
        )
    
    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
    
    async def store(self, memory_type: str, memory_key: str, memory_value: Any):
        """Store a memory entry"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO code_visualizer_agent_memory (
                    agent_id, memory_type, memory_key, memory_value, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $5)
                ON CONFLICT (agent_id, memory_type, memory_key)
                DO UPDATE SET 
                    memory_value = $4,
                    updated_at = $5
            """,
                self.agent_id,
                memory_type,
                memory_key,
                memory_value,
                datetime.now()
            )
    
    async def retrieve(self, memory_type: str, memory_key: str) -> Optional[Any]:
        """Retrieve a memory entry"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT memory_value FROM code_visualizer_agent_memory
                WHERE agent_id = $1 AND memory_type = $2 AND memory_key = $3
            """, self.agent_id, memory_type, memory_key)
            
            return row['memory_value'] if row else None
    
    async def retrieve_all(self, memory_type: str) -> Dict[str, Any]:
        """Retrieve all memories of a specific type"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT memory_key, memory_value FROM code_visualizer_agent_memory
                WHERE agent_id = $1 AND memory_type = $2
                ORDER BY updated_at DESC
            """, self.agent_id, memory_type)
            
            return {row['memory_key']: row['memory_value'] for row in rows}
    
    async def delete(self, memory_type: str, memory_key: str):
        """Delete a memory entry"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM code_visualizer_agent_memory
                WHERE agent_id = $1 AND memory_type = $2 AND memory_key = $3
            """, self.agent_id, memory_type, memory_key)
    
    async def store_proposal(self, proposal_id: str, proposal_data: Dict):
        """Store a proposal in memory"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO code_visualizer_proposals (
                    proposal_id, agent_id, action_type, target_node, 
                    status, risk_score, utility_score, proposal_data, created_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (proposal_id)
                DO UPDATE SET
                    status = $5,
                    risk_score = $6,
                    utility_score = $7,
                    proposal_data = $8,
                    updated_at = CURRENT_TIMESTAMP
            """,
                proposal_id,
                self.agent_id,
                proposal_data.get('action_type', 'unknown'),
                proposal_data.get('target_node', ''),
                proposal_data.get('status', 'pending'),
                proposal_data.get('risk', 0.0),
                proposal_data.get('utility', 0.0),
                proposal_data,
                datetime.now()
            )
    
    async def update_proposal_status(self, proposal_id: str, status: str, reason: Optional[str] = None, result: Optional[Dict] = None):
        """Update proposal status"""
        async with self.pool.acquire() as conn:
            # Get current proposal data
            row = await conn.fetchrow(
                "SELECT proposal_data FROM code_visualizer_proposals WHERE proposal_id = $1",
                proposal_id
            )
            
            if row:
                proposal_data = row['proposal_data']
                proposal_data['status'] = status
                if reason:
                    proposal_data['reason'] = reason
                if result:
                    proposal_data['result'] = result
                
                await conn.execute("""
                    UPDATE code_visualizer_proposals
                    SET status = $1, proposal_data = $2, updated_at = CURRENT_TIMESTAMP
                    WHERE proposal_id = $3
                """, status, proposal_data, proposal_id)
    
    async def get_proposal(self, proposal_id: str) -> Optional[Dict]:
        """Get a specific proposal"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT proposal_id, agent_id, action_type, target_node,
                       status, risk_score, utility_score, proposal_data,
                       created_at, updated_at
                FROM code_visualizer_proposals
                WHERE proposal_id = $1
            """, proposal_id)
            
            if row:
                return {
                    'proposal_id': row['proposal_id'],
                    'agent_id': row['agent_id'],
                    'action_type': row['action_type'],
                    'target_node': row['target_node'],
                    'status': row['status'],
                    'risk_score': row['risk_score'],
                    'utility_score': row['utility_score'],
                    'proposal_data': row['proposal_data'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at']
                }
            return None
    
    async def get_proposals_by_status(self, status: str, limit: int = 100) -> List[Dict]:
        """Get proposals by status"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT proposal_id, agent_id, action_type, target_node,
                       status, risk_score, utility_score, proposal_data,
                       created_at, updated_at
                FROM code_visualizer_proposals
                WHERE agent_id = $1 AND status = $2
                ORDER BY created_at DESC
                LIMIT $3
            """, self.agent_id, status, limit)
            
            return [
                {
                    'proposal_id': row['proposal_id'],
                    'agent_id': row['agent_id'],
                    'action_type': row['action_type'],
                    'target_node': row['target_node'],
                    'status': row['status'],
                    'risk_score': row['risk_score'],
                    'utility_score': row['utility_score'],
                    'proposal_data': row['proposal_data'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at']
                }
                for row in rows
            ]
    
    async def get_all_proposals(self, limit: int = 100) -> List[Dict]:
        """Get all proposals for this agent"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT proposal_id, agent_id, action_type, target_node,
                       status, risk_score, utility_score, proposal_data,
                       created_at, updated_at
                FROM code_visualizer_proposals
                WHERE agent_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            """, self.agent_id, limit)
            
            return [
                {
                    'proposal_id': row['proposal_id'],
                    'agent_id': row['agent_id'],
                    'action_type': row['action_type'],
                    'target_node': row['target_node'],
                    'status': row['status'],
                    'risk_score': row['risk_score'],
                    'utility_score': row['utility_score'],
                    'proposal_data': row['proposal_data'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at']
                }
                for row in rows
            ]
    
    async def get_stats(self) -> Dict:
        """Get memory statistics"""
        async with self.pool.acquire() as conn:
            total_memories = await conn.fetchval(
                "SELECT COUNT(*) FROM code_visualizer_agent_memory WHERE agent_id = $1",
                self.agent_id
            )
            
            total_proposals = await conn.fetchval(
                "SELECT COUNT(*) FROM code_visualizer_proposals WHERE agent_id = $1",
                self.agent_id
            )
            
            pending_proposals = await conn.fetchval(
                "SELECT COUNT(*) FROM code_visualizer_proposals WHERE agent_id = $1 AND status = 'pending'",
                self.agent_id
            )
            
            approved_proposals = await conn.fetchval(
                "SELECT COUNT(*) FROM code_visualizer_proposals WHERE agent_id = $1 AND status = 'approved'",
                self.agent_id
            )
            
            executed_proposals = await conn.fetchval(
                "SELECT COUNT(*) FROM code_visualizer_proposals WHERE agent_id = $1 AND status = 'executed'",
                self.agent_id
            )
        
        return {
            'agent_id': self.agent_id,
            'total_memories': total_memories,
            'total_proposals': total_proposals,
            'pending_proposals': pending_proposals,
            'approved_proposals': approved_proposals,
            'executed_proposals': executed_proposals
        }
