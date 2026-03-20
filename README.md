# RG AST Analysis Service

> **Part of the [ResonantGenesis](https://dev-swat.com) platform** — standalone AST-based code analysis microservice.

[![Status: Production](https://img.shields.io/badge/Status-Production-brightgreen.svg)]()
[![Docker: rg_ast_analysis](https://img.shields.io/badge/Docker-rg__ast__analysis-blue.svg)]()
[![Port: 8000](https://img.shields.io/badge/Port-8000-orange.svg)]()
[![License: RG Source Available](https://img.shields.io/badge/License-RG%20Source%20Available-blue.svg)](LICENSE.txt)

Standalone AST-based code analysis microservice for ResonantGenesis. Parses Python, JavaScript, and TypeScript codebases into navigable graph structures with governance checks, multi-repo comparison, and agent-powered cleanup. Deployed as standalone Docker container `rg_ast_analysis`.

## Architecture

```
User → Nginx → Gateway → rg_ast_analysis (this service, port 8000)
                              ├── PostgreSQL (saved analyses, agent memory)
                              ├── billing_service (credit deduction per scan)
                              ├── memory_service (project persistence)
                              ├── storage_service (archive uploads)
                              └── LLM Service (agent reasoning)
```

## Features

- **AST Parsing** — Python, JavaScript, TypeScript analysis via `ast` module and regex-based JS/TS analyzer
- **Codebase Graph** — Nodes (services, files, classes, functions, endpoints) and connections (imports, calls, inheritance)
- **GitHub Scanning** — Clone and analyze repos (single or multi-repo comparison)
- **Archive Upload** — Upload `.zip`/`.tar.gz` codebases for analysis
- **Governance Engine** — Reachability contracts, dead code classification, drift detection
- **Multi-Project Comparison** — Diff, heat maps, instability metrics, evolution timeline
- **Graph Janitor Agent** — Autonomous graph cleanup proposals
- **Saved Analyses** — PostgreSQL persistence with per-user storage limits

## Quick Start

```bash
# Clone
git clone git@github-devswat:DevSwat-ResonantGenesis/RG_AST_analysis.git
cd RG_AST_analysis

# Install dependencies
pip install -r requirements.txt

# Run locally
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Docker

```bash
docker build -t rg_ast_analysis .
docker run -p 8000:8000 --env-file .env rg_ast_analysis
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | **Yes** | PostgreSQL connection URL |
| `CODE_VISUALIZER_DATABASE_URL` | No | Dedicated DB for agent memory/learning |
| `AST_ANALYSIS_GATEWAY_SECRET` | No | Gateway shared secret for auth |
| `BILLING_SERVICE_URL` | No | Billing service for credit deduction |
| `MEMORY_SERVICE_URL` | No | Memory service for project persistence |
| `STORAGE_SERVICE_URL` | No | Object storage for uploads |
| `AUTH_FRONTEND_URL` | No | Frontend URL for CORS |
| `LLM_SERVICE_URL` | No | LLM service for agent reasoning |
| `REDIS_URL` | No | Redis for caching |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/v1/status` | Service status |
| `POST` | `/api/analyze` | Analyze local codebase path |
| `POST` | `/api/v1/scan/github` | Scan GitHub repo |
| `POST` | `/api/v1/scan/github/multi` | Multi-repo scan + comparison |
| `POST` | `/api/v1/scan/upload` | Upload archive for analysis |
| `GET` | `/api/analysis/{id}` | Get analysis result |
| `POST` | `/api/analysis/{id}/trace` | Trace execution flow |
| `POST` | `/api/analysis/{id}/governance` | Run governance check |
| `GET` | `/api/v1/analyses` | List saved analyses |
| `DELETE` | `/api/v1/analyses/{id}` | Delete saved analysis |

## Gateway Integration

The gateway proxies code analysis requests to this standalone service:
```
/code-visualizer/*        → http://rg_ast_analysis:8000/*
/api/v1/scan/*            → http://rg_ast_analysis:8000/api/v1/scan/*
```

## Related Modules

| Module | Repo | Relationship |
|--------|------|-------------|
| Registered Users Agentic Chat | [`RG_Registered_Users_Agentic_Chat`](https://github.com/DevSwat-ResonantGenesis/RG_Registered_Users_Agentic_Chat) | Chat tools proxy `code_visualizer_*` to this service |
| Internal Invariants SIM | [`RG_Internal_Invarients_SIM`](https://github.com/DevSwat-ResonantGenesis/RG_Internal_Invarients_SIM) | RARA uses AST analysis for invariant enforcement |
| Unified LLM Client | [`RG_UnifiedLLMClient`](https://github.com/DevSwat-ResonantGenesis/RG_UnifiedLLMClient) | Shared LLM client (not volume-mounted — uses LLM service proxy) |
| Resonant IDE | [`RG_IDE`](https://github.com/DevSwat-ResonantGenesis/RG_IDE) | IDE extension has `code_visualizer_*` tools that call this service |

## Deployment Status

- **Status**: ✅ **Production** — deployed as standalone Docker container `rg_ast_analysis`
- **Extracted from**: `genesis2026_production_backend/code_visualizer_service` (entire directory deleted from monolith)
- **Server path**: `/home/deploy/RG_AST_analysis` (cloned from DevSwat GitHub)
- **Docker service**: `rg_ast_analysis` in `docker-compose.unified.yml`
- **Port**: 8000 (internal Docker network)

---

**Organization**: [DevSwat-ResonantGenesis](https://github.com/DevSwat-ResonantGenesis)
**Platform**: [dev-swat.com](https://dev-swat.com)
