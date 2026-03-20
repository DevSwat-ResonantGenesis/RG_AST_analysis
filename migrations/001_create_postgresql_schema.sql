-- Code Visualizer PostgreSQL Schema Migration
-- Migrates from local SQLite to PostgreSQL Main DB
-- Date: 2026-01-19

-- Learning Outcomes Table (replaces learning.db outcomes table)
CREATE TABLE IF NOT EXISTS code_visualizer_outcomes (
    id SERIAL PRIMARY KEY,
    patch_id TEXT UNIQUE NOT NULL,
    action_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_node TEXT,
    node_type TEXT,
    blast_radius INTEGER DEFAULT 0,
    applied BOOLEAN DEFAULT FALSE,
    rolled_back BOOLEAN DEFAULT FALSE,
    human_rejected BOOLEAN DEFAULT FALSE,
    reachability_delta FLOAT DEFAULT 0.0,
    entropy_delta FLOAT DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Model State Table (replaces learning.db model_state table)
CREATE TABLE IF NOT EXISTS code_visualizer_model_state (
    model_name TEXT PRIMARY KEY,
    parameters JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Agent Memory Table (replaces agent_memory.db)
CREATE TABLE IF NOT EXISTS code_visualizer_agent_memory (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    memory_value JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agent_id, memory_type, memory_key)
);

-- Proposal History Table
CREATE TABLE IF NOT EXISTS code_visualizer_proposals (
    id SERIAL PRIMARY KEY,
    proposal_id TEXT UNIQUE NOT NULL,
    agent_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_node TEXT,
    status TEXT DEFAULT 'pending',
    risk_score FLOAT DEFAULT 0.0,
    utility_score FLOAT DEFAULT 0.0,
    proposal_data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_outcomes_patch_id ON code_visualizer_outcomes(patch_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_action_id ON code_visualizer_outcomes(action_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_created_at ON code_visualizer_outcomes(created_at);
CREATE INDEX IF NOT EXISTS idx_outcomes_applied ON code_visualizer_outcomes(applied);

CREATE INDEX IF NOT EXISTS idx_memory_agent_id ON code_visualizer_agent_memory(agent_id);
CREATE INDEX IF NOT EXISTS idx_memory_type ON code_visualizer_agent_memory(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_created_at ON code_visualizer_agent_memory(created_at);

CREATE INDEX IF NOT EXISTS idx_proposals_agent_id ON code_visualizer_proposals(agent_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON code_visualizer_proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_created_at ON code_visualizer_proposals(created_at);

-- Update trigger for updated_at columns
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_outcomes_updated_at BEFORE UPDATE ON code_visualizer_outcomes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_model_state_updated_at BEFORE UPDATE ON code_visualizer_model_state
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_agent_memory_updated_at BEFORE UPDATE ON code_visualizer_agent_memory
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_proposals_updated_at BEFORE UPDATE ON code_visualizer_proposals
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
