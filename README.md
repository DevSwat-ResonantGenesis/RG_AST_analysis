# RG AST Analysis Service

Standalone AST-based code analysis microservice for ResonantGenesis. Extracted from the monolithic `code_visualizer_service`.

## Features

- **AST Parsing** — Python, JavaScript, TypeScript analysis via `ast` module and regex-based JS/TS analyzer
- **Codebase Graph** — Nodes (services, files, classes, functions, endpoints) and connections (imports, calls, inheritance)
- **GitHub Scanning** — Clone and analyze repos (single or multi-repo comparison)
- **Archive Upload** — Upload `.zip`/`.tar.gz` codebases for analysis
- **Governance Engine** — Reachability contracts, dead code classification, drift detection
- **Multi-Project Comparison** — Diff, heat maps, instability metrics, evolution timeline
- **Graph Janitor Agent** — Autonomous graph cleanup proposals
- **Saved Analyses** — PostgreSQL persistence with per-user storage limits

## Running Locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Docker

```bash
docker build -t rg_ast_analysis .
docker run -p 8000:8000 --env-file .env rg_ast_analysis
```

## Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URL |
| `CODE_VISUALIZER_DATABASE_URL` | Dedicated DB for agent memory/learning |
| `AST_ANALYSIS_GATEWAY_SECRET` | Gateway shared secret for auth |
| `BILLING_SERVICE_URL` | Billing service for credit deduction |
| `MEMORY_SERVICE_URL` | Memory service for project persistence |
| `STORAGE_SERVICE_URL` | Object storage for uploads |
| `AUTH_FRONTEND_URL` | Frontend URL for CORS |

## API Endpoints

- `GET /health` — Health check
- `GET /api/v1/status` — Service status
- `POST /api/analyze` — Analyze local codebase path
- `POST /api/v1/scan/github` — Scan GitHub repo
- `POST /api/v1/scan/github/multi` — Multi-repo scan
- `POST /api/v1/scan/upload` — Upload archive for analysis
- `GET /api/analysis/{id}` — Get analysis result
- `POST /api/analysis/{id}/trace` — Trace execution flow
- `POST /api/analysis/{id}/governance` — Run governance check
- `GET /api/v1/analyses` — List saved analyses
- `DELETE /api/v1/analyses/{id}` — Delete saved analysis
