#!/usr/bin/env python3
"""
Migration script: SQLite to PostgreSQL for Code Visualizer
Migrates data from local SQLite files to PostgreSQL Main DB
"""

import asyncio
import sqlite3
import asyncpg
import os
import sys
from pathlib import Path
from datetime import datetime

# Database configuration
POSTGRES_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{os.getenv('DB_USER', 'doadmin')}:"
    f"{os.getenv('DB_PASSWORD', '')}@"
    f"{os.getenv('DB_HOST', 'resonant-db-do-user-18031534-0.g.db.ondigitalocean.com')}:"
    f"{os.getenv('DB_PORT', '25060')}/"
    f"{os.getenv('DB_NAME', 'defaultdb')}?ssl=require"
)

SQLITE_LEARNING_DB = "/app/data/learning.db"
SQLITE_MEMORY_DB = "/app/data/agent_memory.db"


async def create_postgres_schema(conn):
    """Create PostgreSQL tables"""
    print("Creating PostgreSQL schema...")
    
    # Read and execute schema file
    schema_file = Path(__file__).parent / "migrations" / "001_create_postgresql_schema.sql"
    
    if schema_file.exists():
        with open(schema_file, 'r') as f:
            schema_sql = f.read()
        await conn.execute(schema_sql)
        print("✅ Schema created successfully")
    else:
        print("⚠️  Schema file not found, tables may already exist")


async def migrate_learning_db(conn):
    """Migrate learning.db from SQLite to PostgreSQL"""
    if not os.path.exists(SQLITE_LEARNING_DB):
        print(f"⚠️  SQLite learning.db not found at {SQLITE_LEARNING_DB}")
        return
    
    print(f"Migrating learning.db from {SQLITE_LEARNING_DB}...")
    
    sqlite_conn = sqlite3.connect(SQLITE_LEARNING_DB)
    sqlite_cursor = sqlite_conn.cursor()
    
    # Migrate outcomes table
    try:
        sqlite_cursor.execute("SELECT * FROM outcomes")
        outcomes = sqlite_cursor.fetchall()
        
        if outcomes:
            print(f"  Migrating {len(outcomes)} outcomes...")
            for outcome in outcomes:
                await conn.execute("""
                    INSERT INTO code_visualizer_outcomes (
                        patch_id, action_id, action_type, target_node, node_type,
                        blast_radius, applied, rolled_back, human_rejected,
                        reachability_delta, entropy_delta, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT (patch_id) DO NOTHING
                """,
                    outcome[1],  # patch_id
                    outcome[2],  # action_id
                    outcome[3],  # action_type
                    outcome[4],  # target_node
                    outcome[5],  # node_type
                    outcome[6],  # blast_radius
                    bool(outcome[7]),  # applied
                    bool(outcome[8]),  # rolled_back
                    bool(outcome[9]),  # human_rejected
                    float(outcome[10]) if outcome[10] else 0.0,  # reachability_delta
                    float(outcome[11]) if outcome[11] else 0.0,  # isolated_nodes_delta
                    outcome[16] if outcome[16] else datetime.now()  # created_at
                )
            print(f"  ✅ Migrated {len(outcomes)} outcomes")
        else:
            print("  No outcomes to migrate")
    except sqlite3.OperationalError as e:
        print(f"  ⚠️  Outcomes table not found or empty: {e}")
    
    # Migrate model_state table
    try:
        sqlite_cursor.execute("SELECT * FROM model_state")
        models = sqlite_cursor.fetchall()
        
        if models:
            print(f"  Migrating {len(models)} model states...")
            for model in models:
                import json
                await conn.execute("""
                    INSERT INTO code_visualizer_model_state (model_name, parameters, updated_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (model_name) DO UPDATE SET
                        parameters = $2,
                        updated_at = $3
                """,
                    model[0],  # model_name
                    json.loads(model[1]),  # parameters (convert from JSON string)
                    model[2] if model[2] else datetime.now()  # updated_at
                )
            print(f"  ✅ Migrated {len(models)} model states")
        else:
            print("  No model states to migrate")
    except sqlite3.OperationalError as e:
        print(f"  ⚠️  Model state table not found or empty: {e}")
    
    sqlite_conn.close()
    print("✅ Learning DB migration complete")


async def migrate_memory_db(conn):
    """Migrate agent_memory.db from SQLite to PostgreSQL"""
    if not os.path.exists(SQLITE_MEMORY_DB):
        print(f"⚠️  SQLite agent_memory.db not found at {SQLITE_MEMORY_DB}")
        return
    
    print(f"Migrating agent_memory.db from {SQLITE_MEMORY_DB}...")
    
    sqlite_conn = sqlite3.connect(SQLITE_MEMORY_DB)
    sqlite_cursor = sqlite_conn.cursor()
    
    # Get all tables
    sqlite_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = sqlite_cursor.fetchall()
    
    if not tables:
        print("  No tables found in agent_memory.db")
        sqlite_conn.close()
        return
    
    print(f"  Found {len(tables)} tables: {[t[0] for t in tables]}")
    
    # Migrate each table's data as agent memory
    for table in tables:
        table_name = table[0]
        if table_name == 'sqlite_sequence':
            continue
        
        try:
            sqlite_cursor.execute(f"SELECT * FROM {table_name}")
            rows = sqlite_cursor.fetchall()
            
            if rows:
                print(f"  Migrating {len(rows)} rows from {table_name}...")
                for i, row in enumerate(rows):
                    import json
                    await conn.execute("""
                        INSERT INTO code_visualizer_agent_memory (
                            agent_id, memory_type, memory_key, memory_value, created_at
                        ) VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (agent_id, memory_type, memory_key) DO UPDATE SET
                            memory_value = $4,
                            updated_at = CURRENT_TIMESTAMP
                    """,
                        'janitor_agent',  # Default agent_id
                        table_name,  # memory_type
                        f"{table_name}_{i}",  # memory_key
                        json.dumps(row),  # memory_value (store as JSON)
                        datetime.now()
                    )
                print(f"  ✅ Migrated {len(rows)} rows from {table_name}")
        except sqlite3.OperationalError as e:
            print(f"  ⚠️  Error migrating {table_name}: {e}")
    
    sqlite_conn.close()
    print("✅ Memory DB migration complete")


async def verify_migration(conn):
    """Verify migration was successful"""
    print("\nVerifying migration...")
    
    outcomes_count = await conn.fetchval(
        "SELECT COUNT(*) FROM code_visualizer_outcomes"
    )
    print(f"  Outcomes: {outcomes_count} rows")
    
    models_count = await conn.fetchval(
        "SELECT COUNT(*) FROM code_visualizer_model_state"
    )
    print(f"  Model states: {models_count} rows")
    
    memory_count = await conn.fetchval(
        "SELECT COUNT(*) FROM code_visualizer_agent_memory"
    )
    print(f"  Agent memories: {memory_count} rows")
    
    proposals_count = await conn.fetchval(
        "SELECT COUNT(*) FROM code_visualizer_proposals"
    )
    print(f"  Proposals: {proposals_count} rows")
    
    print("\n✅ Migration verification complete")


async def main():
    """Main migration function"""
    print("=" * 60)
    print("Code Visualizer: SQLite → PostgreSQL Migration")
    print("=" * 60)
    print()
    
    try:
        # Connect to PostgreSQL
        print(f"Connecting to PostgreSQL...")
        conn = await asyncpg.connect(POSTGRES_URL)
        print("✅ Connected to PostgreSQL")
        print()
        
        # Create schema
        await create_postgres_schema(conn)
        print()
        
        # Migrate learning.db
        await migrate_learning_db(conn)
        print()
        
        # Migrate agent_memory.db
        await migrate_memory_db(conn)
        print()
        
        # Verify migration
        await verify_migration(conn)
        
        await conn.close()
        
        print()
        print("=" * 60)
        print("✅ MIGRATION COMPLETE")
        print("=" * 60)
        print()
        print("Next steps:")
        print("1. Update code_visualizer_service to use PostgreSQL")
        print("2. Restart code_visualizer_service")
        print("3. Remove old SQLite files (optional)")
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
