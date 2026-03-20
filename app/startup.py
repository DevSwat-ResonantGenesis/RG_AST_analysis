"""
Startup initialization for RG AST Analysis service
Handles async database initialization for PostgreSQL
"""

import logging
from .agents.memory_postgres import PostgresAgentMemory
from .agents.learning_postgres import PostgresLearningEngine

logger = logging.getLogger(__name__)

# Global instances (will be initialized on startup)
agent_memory: PostgresAgentMemory = None
learning_engine: PostgresLearningEngine = None


async def init_database_connections():
    """Initialize PostgreSQL connections for agents"""
    global agent_memory, learning_engine
    
    try:
        logger.info("Initializing PostgreSQL connections for AST Analysis...")
        
        # Initialize agent memory
        agent_memory = PostgresAgentMemory(agent_id='janitor_agent')
        await agent_memory.init()
        logger.info("✅ Agent memory initialized")
        
        # Initialize learning engine
        learning_engine = PostgresLearningEngine()
        await learning_engine.init()
        logger.info("✅ Learning engine initialized")
        
        logger.info("✅ All PostgreSQL connections initialized successfully")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize PostgreSQL connections: {e}")
        logger.warning("⚠️ AST Analysis will start WITHOUT database features (agent memory/learning disabled)")
        agent_memory = None
        learning_engine = None


async def close_database_connections():
    """Close PostgreSQL connections"""
    global agent_memory, learning_engine
    
    try:
        if agent_memory:
            await agent_memory.close()
            logger.info("Agent memory connection closed")
        
        if learning_engine:
            await learning_engine.close()
            logger.info("Learning engine connection closed")
            
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")


def get_agent_memory() -> PostgresAgentMemory:
    """Get initialized agent memory instance"""
    if agent_memory is None:
        raise RuntimeError("Agent memory not initialized. Call init_database_connections() first.")
    return agent_memory


def get_learning_engine() -> PostgresLearningEngine:
    """Get initialized learning engine instance"""
    if learning_engine is None:
        raise RuntimeError("Learning engine not initialized. Call init_database_connections() first.")
    return learning_engine
