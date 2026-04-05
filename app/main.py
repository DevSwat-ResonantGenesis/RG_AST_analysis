import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, List
from contextlib import asynccontextmanager
import mimetypes
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from .analyzer import CodebaseAnalyzer, NodeType, analyze_codebase
from .comparison_analyzer import MultiProjectComparator
from .governance import GovernanceEngine
from .startup import init_database_connections, close_database_connections
from .agents.janitor import GraphJanitorAgent
from . import startup as _startup_mod
from .db import async_session_maker, SavedAnalysis, init_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-plan storage limits (max saved analyses per user)
# ---------------------------------------------------------------------------
_PRIVILEGED_ROLES = {"owner", "platform_owner", "admin", "superuser"}

_STORAGE_LIMITS: Dict[str, Optional[int]] = {
    "owner": None,
    "platform_owner": None,
    "admin": None,
    "superuser": None,
    "plus": 20,
    "pro": 20,
    "user": 5,
    "basic": 5,
    "free": 2,
}

# Deterministic sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Credit costs from pricing.yaml
_DEFAULT_CREDIT_COSTS: Dict[str, int] = {
    "codebase_analysis": 200,
    "governance_check": 50,
    "graph_export": 20,
}

CREDIT_COSTS: Dict[str, int] = dict(_DEFAULT_CREDIT_COSTS)

BILLING_SERVICE_URL = os.getenv("BILLING_SERVICE_URL", "http://billing_service:8000")


async def _refresh_credit_costs() -> None:
    """Refresh CREDIT_COSTS from billing_service pricing.yaml (best-effort)."""
    global CREDIT_COSTS
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{BILLING_SERVICE_URL}/billing/pricing/credit-costs")
        if resp.status_code != 200:
            return
        data = resp.json() or {}
        codeviz = data.get("code_visualizer") if isinstance(data, dict) else None
        if not isinstance(codeviz, dict):
            return

        updated = dict(_DEFAULT_CREDIT_COSTS)
        for key in updated:
            if isinstance(codeviz.get(key), int):
                updated[key] = int(codeviz[key])
        CREDIT_COSTS = updated
    except Exception:
        return


def _inject_github_token(repo_url: str, token: Optional[str]) -> str:
    """Inject token for GitHub HTTPS clone URLs.

    Also strips any embedded credentials to avoid stale username/password auth.
    """
    url = (repo_url or "").strip()
    tok = (token or "").strip()
    if not url:
        return url
    if not url.startswith("https://"):
        return url

    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host != "github.com":
        return url

    # Remove embedded creds if present; optionally inject PAT credentials.
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    if tok:
        netloc = f"x-access-token:{quote(tok, safe='')}@{netloc}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _resolve_github_token(request: Request, payload_token: Optional[str]) -> Optional[str]:
    token = (payload_token or "").strip()
    if token:
        return token

    header_token = (request.headers.get("x-github-token") or "").strip()
    if header_token:
        return header_token

    auth_header = (request.headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            return bearer

    for env_key in ["AST_ANALYSIS_GITHUB_TOKEN", "CODE_VISUALIZER_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"]:
        env_token = (os.getenv(env_key) or "").strip()
        if env_token:
            return env_token

    return None


def _extract_billing_identity(request: Request) -> tuple[Optional[str], str, bool, bool]:
    user_id = (request.headers.get("x-user-id") or "").strip() or None
    user_role = (request.headers.get("x-user-role") or "").strip() or "user"
    is_superuser = (request.headers.get("x-is-superuser") or "").strip().lower() in ("true", "1", "yes")
    unlimited_credits = (request.headers.get("x-unlimited-credits") or "").strip().lower() in ("true", "1", "yes")
    return user_id, user_role, is_superuser, unlimited_credits


async def deduct_credits(
    user_id: str,
    amount: int,
    reference_type: str,
    description: str,
    user_role: str = "user",
    is_superuser: bool = False,
    unlimited_credits: bool = False,
) -> dict:
    """Deduct credits from user's balance via billing service.

    STRICT: if billing rejects (e.g. insufficient credits), raise HTTPException.
    """
    if amount <= 0:
        return {"success": True, "amount_deducted": 0}

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(
            f"{BILLING_SERVICE_URL}/billing/credits/deduct",
            json={
                "amount": amount,
                "reference_type": reference_type,
                "description": description,
            },
            headers={
                "X-User-Id": user_id,
                "X-User-Role": user_role,
                "X-Is-Superuser": str(is_superuser).lower(),
                "X-Unlimited-Credits": str(unlimited_credits).lower(),
            },
        )

    if response.status_code >= 300:
        detail = "Credit deduction failed"
        try:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("detail"):
                detail = str(payload["detail"])
        except Exception:
            if response.text:
                detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)

    try:
        return response.json()
    except Exception:
        return {"success": True}


async def refund_credits(user_id: str, amount: int, original_tx_id: str, reason: str) -> None:
    if amount <= 0:
        return
    if not original_tx_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{BILLING_SERVICE_URL}/billing/credits/refund",
                json={
                    "amount": amount,
                    "original_tx_id": original_tx_id,
                    "reason": reason,
                },
                headers={"X-User-Id": user_id},
            )
    except Exception as e:
        logger.warning(f"Credit refund failed: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown"""
    # Startup: Initialize PostgreSQL connections
    await init_database_connections()
    await init_db()  # Create cv_saved_analyses table if not exists
    await _refresh_credit_costs()
    try:
        CODE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    _cleanup_cache_best_effort()
    yield
    # Shutdown: Close PostgreSQL connections
    await close_database_connections()

# Single service entrypoint
app = FastAPI(
    title="RG AST Analysis Service",
    description="Standalone AST Analysis microservice for Genesis2026",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
frontend_url = os.getenv("AUTH_FRONTEND_URL", "").strip()
allowed_origins = ["*"] if not frontend_url else [frontend_url]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _RequireGatewayIdentityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        expected_secret = (os.getenv("AST_ANALYSIS_GATEWAY_SECRET") or os.getenv("CODE_VISUALIZER_GATEWAY_SECRET") or "").strip()

        # Allow health and root shell without identity.
        if path in {"/", "/health", "/api/v1/status"}:
            return await call_next(request)

        # Require gateway-injected identity for all API endpoints.
        if path.startswith("/api/") or path.startswith("/api"):
            if expected_secret:
                provided = (request.headers.get("x-ast-analysis-gateway-secret") or request.headers.get("x-code-visualizer-gateway-secret") or "").strip()
                if provided != expected_secret:
                    return JSONResponse({"detail": "Unauthorized"}, status_code=401)

            if not (request.headers.get("x-user-id") or "").strip():
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        return await call_next(request)


app.add_middleware(_RequireGatewayIdentityMiddleware)

STORAGE_SERVICE_URL = (
    os.getenv("STORAGE_SERVICE_URL")
    or os.getenv("STORAGE_URL")
    or "http://storage_service:8000/api/storage"
)
MEMORY_SERVICE_URL = os.getenv("MEMORY_SERVICE_URL", "http://memory_service:8000").rstrip("/")
CODE_UPLOAD_BUCKET = os.getenv("CODE_UPLOAD_BUCKET", "code-uploads")
CODE_UPLOAD_MAX_MB = int(os.getenv("CODE_UPLOAD_MAX_MB", "100"))
CODE_UPLOAD_MAX_FILES = int(os.getenv("CODE_UPLOAD_MAX_FILES", "10000"))
CODE_UPLOAD_MAX_UNPACKED_MB = int(os.getenv("CODE_UPLOAD_MAX_UNPACKED_MB", "500"))
CODE_UPLOAD_SCAN_TIMEOUT_SEC = int(os.getenv("CODE_UPLOAD_SCAN_TIMEOUT_SEC", "120"))
CODE_CACHE_DIR = Path(os.getenv("CODE_VISUALIZER_CACHE_DIR", "/tmp/codeviz-cache"))
CODE_CACHE_TTL_HOURS = int(os.getenv("CODE_VISUALIZER_CACHE_TTL_HOURS", "72"))
CODE_GITHUB_CLONE_TIMEOUT_SEC = int(os.getenv("CODE_VISUALIZER_GITHUB_CLONE_TIMEOUT_SEC", "180"))

MAX_UPLOAD_BYTES = CODE_UPLOAD_MAX_MB * 1024 * 1024
MAX_UNPACKED_BYTES = CODE_UPLOAD_MAX_UNPACKED_MB * 1024 * 1024

MAX_SAVE_FILE_BYTES = int(os.getenv("CODE_UPLOAD_MAX_SAVE_FILE_BYTES", str(1024 * 1024)))  # 1MB


def _analysis_cache_dir(analysis_id: str) -> Path:
    safe = "".join(ch for ch in (analysis_id or "") if ch.isalnum())
    if not safe:
        safe = uuid.uuid4().hex
    return CODE_CACHE_DIR / safe


def _ensure_empty_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def _cleanup_cache_best_effort() -> None:
    try:
        if not CODE_CACHE_DIR.exists():
            return
        cutoff = (asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else None)
    except Exception:
        cutoff = None

    try:
        import time

        cutoff_ts = time.time() - (CODE_CACHE_TTL_HOURS * 3600)
        for child in CODE_CACHE_DIR.iterdir():
            try:
                if not child.is_dir():
                    continue
                if child.stat().st_mtime < cutoff_ts:
                    shutil.rmtree(child, ignore_errors=True)
            except Exception:
                continue
    except Exception:
        return


def _guess_language(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    mapping = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".json": "json",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".md": "markdown",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".rb": "ruby",
        ".php": "php",
        ".cs": "csharp",
        ".swift": "swift",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".toml": "toml",
        ".ini": "ini",
        ".sh": "shell",
        ".sql": "sql",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".hpp": "cpp",
        ".scala": "scala",
        ".dart": "dart",
        ".lua": "lua",
        ".zig": "zig",
        ".ex": "elixir",
        ".exs": "elixir",
        ".sol": "solidity",
        ".jl": "julia",
        ".r": "r",
        ".R": "r",
        ".v": "v",
    }
    if suffix in mapping:
        return mapping[suffix]
    # Fallback: try mime
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("text/"):
        return "text"
    return None


def _should_skip_dir(dir_path: Path) -> bool:
    name = dir_path.name
    return name in {
        ".git",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".cache",
        "__pycache__",
        ".venv",
        "venv",
    }


def _looks_binary(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:4096]
    return b"\x00" in sample


def _build_project_report_text(
    project_name: str,
    project_id: str,
    analysis_id: str,
    analysis: Dict[str, Any],
) -> str:
    stats = analysis.get("stats") or {}
    nodes = analysis.get("nodes") or []
    endpoints = [n for n in nodes if isinstance(n, dict) and n.get("type") == "endpoint"]

    lines = []
    lines.append(f"PROJECT REPORT: {project_name}")
    lines.append(f"project_id: {project_id}")
    lines.append(f"analysis_id: {analysis_id}")
    lines.append("")
    lines.append("STATS")
    for k in [
        "total_files",
        "total_services",
        "total_connections",
        "total_functions",
        "total_endpoints",
        "broken_connections",
        "cross_project_connections",
    ]:
        if k in stats:
            lines.append(f"- {k}: {stats.get(k)}")

    lines.append("")
    lines.append("ENDPOINTS")
    for ep in endpoints[:200]:
        label = ep.get("label") or ep.get("name") or ep.get("id")
        route = ep.get("route") or ep.get("path") or ""
        method = ep.get("method") or ""
        svc = ep.get("service") or ""
        lines.append(f"- {method} {route} ({svc}) :: {label}")

    text = "\n".join(lines)
    if len(text) > 50_000:
        text = text[:50_000] + "\n...TRUNCATED..."
    return text


async def _persist_project_from_dir(
    *,
    user_id: str,
    org_id: Optional[str],
    chat_id: Optional[str],
    project_id: str,
    project_name: str,
    analysis_id: str,
    extract_dir: Path,
    analysis: Dict[str, Any],
) -> Dict[str, Any]:
    saved_files = 0
    skipped_files = 0
    errors = 0
    report_saved = False

    sem = asyncio.Semaphore(10)

    async def _ingest_one(rel_path: str, content: str, language: Optional[str]) -> None:
        nonlocal saved_files, errors
        async with sem:
            ingest_payload: Dict[str, Any] = {
                "user_id": user_id,
                "org_id": org_id,
                "source": "project",
                "content": content,
                "metadata": {
                    "project_id": project_id,
                    "project_name": project_name,
                    "file_path": rel_path,
                    "type": "file",
                    "language": language,
                    "is_archived": False,
                    "saved_from": "ast_analysis",
                    "analysis_id": analysis_id,
                },
                "generate_embedding": False,
            }

            try:
                async with httpx.AsyncClient(timeout=20.0) as mem_client:
                    resp = await mem_client.post(
                        f"{MEMORY_SERVICE_URL}/memory/ingest",
                        json=ingest_payload,
                    )
                if resp.status_code >= 300:
                    errors += 1
                    return
                saved_files += 1
            except Exception:
                errors += 1

    tasks = []
    for root, dirs, files in os.walk(extract_dir):
        root_path = Path(root)
        pruned = []
        for d in dirs:
            if _should_skip_dir(Path(d)):
                continue
            pruned.append(d)
        dirs[:] = pruned

        for fname in files:
            fpath = root_path / fname
            try:
                rel_path = str(fpath.relative_to(extract_dir).as_posix())
            except Exception:
                skipped_files += 1
                continue

            try:
                size = fpath.stat().st_size
                if size > MAX_SAVE_FILE_BYTES:
                    skipped_files += 1
                    continue
                data = fpath.read_bytes()
            except Exception:
                skipped_files += 1
                continue

            if _looks_binary(data):
                skipped_files += 1
                continue

            try:
                content = data.decode("utf-8")
            except Exception:
                content = data.decode("utf-8", errors="replace")

            language = _guess_language(fpath)
            tasks.append(_ingest_one(rel_path, content, language))

    if tasks:
        await asyncio.gather(*tasks)

    report_text = _build_project_report_text(project_name, project_id, analysis_id, analysis)
    report_payload: Dict[str, Any] = {
        "user_id": user_id,
        "org_id": org_id,
        "chat_id": chat_id,
        "source": "ast_analysis",
        "content": report_text,
        "metadata": {
            "type": "project_report",
            "project_id": project_id,
            "project_name": project_name,
            "analysis_id": analysis_id,
        },
        "generate_embedding": True,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as mem_client:
            resp = await mem_client.post(
                f"{MEMORY_SERVICE_URL}/memory/ingest",
                json=report_payload,
            )
        report_saved = resp.status_code < 300
    except Exception:
        report_saved = False

    return {
        "project_id": project_id,
        "project_name": project_name,
        "saved_files": saved_files,
        "skipped_files": skipped_files,
        "errors": errors,
        "report_saved": report_saved,
    }


def _archive_suffix(filename: str) -> Optional[str]:
    lower = filename.lower()
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return ".tar.gz"
    if lower.endswith(".tar"):
        return ".tar"
    if lower.endswith(".zip"):
        return ".zip"
    return None


def _is_safe_path(name: str) -> bool:
    if not name or name.startswith(("/", "\\")):
        return False
    parts = Path(name).parts
    return ".." not in parts


def _ensure_within(base: Path, target: Path) -> None:
    base_resolved = base.resolve()
    target_resolved = target.resolve()
    if not str(target_resolved).startswith(str(base_resolved) + os.sep):
        raise HTTPException(status_code=400, detail="Archive contains invalid paths.")


def _safe_extract_zip(archive_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive_path) as zf:
        total_unpacked = 0
        file_count = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not _is_safe_path(info.filename):
                raise HTTPException(status_code=400, detail="Archive contains invalid paths.")
            # Detect symlinks in zip (unix mode bits)
            is_symlink = (info.external_attr >> 16) & 0o120000 == 0o120000
            if is_symlink:
                raise HTTPException(status_code=400, detail="Archive contains symlinks.")
            file_count += 1
            if file_count > CODE_UPLOAD_MAX_FILES:
                raise HTTPException(status_code=400, detail="Archive has too many files.")
            total_unpacked += info.file_size
            if total_unpacked > MAX_UNPACKED_BYTES:
                raise HTTPException(status_code=400, detail="Archive is too large when extracted.")
            target = dest / info.filename
            _ensure_within(dest, target)
        zf.extractall(dest)


def _safe_extract_tar(archive_path: Path, dest: Path) -> None:
    with tarfile.open(archive_path) as tf:
        total_unpacked = 0
        file_count = 0
        for member in tf.getmembers():
            if member.isdir():
                continue
            if member.issym() or member.islnk():
                raise HTTPException(status_code=400, detail="Archive contains symlinks.")
            if not _is_safe_path(member.name):
                raise HTTPException(status_code=400, detail="Archive contains invalid paths.")
            file_count += 1
            if file_count > CODE_UPLOAD_MAX_FILES:
                raise HTTPException(status_code=400, detail="Archive has too many files.")
            total_unpacked += member.size
            if total_unpacked > MAX_UNPACKED_BYTES:
                raise HTTPException(status_code=400, detail="Archive is too large when extracted.")
            target = dest / member.name
            _ensure_within(dest, target)
        tf.extractall(dest)


def _extract_archive(archive_path: Path, dest: Path) -> None:
    suffix = _archive_suffix(archive_path.name)
    if suffix == ".zip":
        _safe_extract_zip(archive_path, dest)
        return
    if suffix in {".tar", ".tar.gz"}:
        _safe_extract_tar(archive_path, dest)
        return
    raise HTTPException(status_code=400, detail="Unsupported archive format.")

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "rg_ast_analysis"}

# Root endpoint
@app.get("/")
async def root():
    frontend_path = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
    if frontend_path.exists():
        return FileResponse(str(frontend_path))
    return JSONResponse({"message": "AST Analysis frontend not found"}, status_code=404)

# Service-specific endpoint
@app.get("/api/v1/status")
async def status():
    return {"service": "rg_ast_analysis", "status": "active", "version": "1.0.0"}


# =====================================================
# UI COMPATIBILITY API (/api/*)
# =====================================================

_analysis_store: Dict[str, Dict[str, Any]] = {}
_analysis_meta: Dict[str, Dict[str, Any]] = {}


def _store_analysis(analysis_id: str, analysis: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> None:
    _analysis_store[analysis_id] = analysis
    _analysis_meta[analysis_id] = meta or {}


def _get_analysis_or_404(analysis_id: str) -> Dict[str, Any]:
    if analysis_id not in _analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return _analysis_store[analysis_id]


# ---------------------------------------------------------------------------
# DB persistence helpers
# ---------------------------------------------------------------------------

async def _db_save_analysis(
    analysis_id: str,
    user_id: str,
    user_role: str,
    is_superuser: bool,
    project_name: str,
    source: str,
    repo_url: str,
    analysis: Dict[str, Any],
    meta: Dict[str, Any],
) -> None:
    """Persist analysis to PostgreSQL. Best-effort — never raises."""
    try:
        raw_json = json.dumps(analysis)
        storage_bytes = len(raw_json.encode("utf-8"))
        stats = analysis.get("stats") or {}
        async with async_session_maker() as session:
            row = SavedAnalysis(
                analysis_id=analysis_id,
                user_id=user_id,
                user_role=user_role,
                is_superuser=is_superuser,
                project_name=project_name or "",
                source=source,
                repo_url=repo_url or "",
                stats_json=stats,
                meta_json={k: v for k, v in (meta or {}).items() if k != "path"},
                analysis_json=analysis,
                storage_bytes=storage_bytes,
            )
            session.add(row)
            await session.commit()
        logger.info(f"✅ Analysis {analysis_id} persisted to DB ({storage_bytes // 1024}KB)")
    except Exception as e:
        logger.warning(f"⚠️ Failed to persist analysis {analysis_id} to DB: {e}")


async def _ensure_analysis_loaded(analysis_id: str) -> bool:
    """Load analysis from DB into in-memory store if not already there. Returns True if available."""
    if analysis_id in _analysis_store:
        return True
    try:
        from sqlalchemy import select
        async with async_session_maker() as session:
            result = await session.execute(
                select(SavedAnalysis).where(
                    SavedAnalysis.analysis_id == analysis_id,
                    SavedAnalysis.deleted_at.is_(None),
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            _store_analysis(analysis_id, row.analysis_json or {}, row.meta_json or {})
            logger.info(f"📂 Analysis {analysis_id} loaded from DB into memory")
            return True
    except Exception as e:
        logger.warning(f"⚠️ Failed to load analysis {analysis_id} from DB: {e}")
        return False


async def _db_check_storage_limit(user_id: str, user_role: str, is_superuser: bool, unlimited_credits: bool = False) -> None:
    """Raise 429 if user has hit their saved-analysis storage limit."""
    if is_superuser or unlimited_credits or user_role.lower() in _PRIVILEGED_ROLES:
        return
    limit = _STORAGE_LIMITS.get(user_role.lower(), _STORAGE_LIMITS["user"])
    if limit is None:
        return
    try:
        from sqlalchemy import select, func
        async with async_session_maker() as session:
            result = await session.execute(
                select(func.count(SavedAnalysis.analysis_id)).where(
                    SavedAnalysis.user_id == user_id,
                    SavedAnalysis.deleted_at.is_(None),
                )
            )
            count = result.scalar() or 0
        if count >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"Storage limit reached: your plan allows {limit} saved analyses. Delete some to continue."
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"⚠️ Storage limit check failed (allowing): {e}")


def _trace_stored_analysis(
    analysis: Dict[str, Any],
    start_node: str,
    max_depth: int = 10,
) -> Dict[str, Any]:
    nodes_list = analysis.get("nodes") or []
    conns_list = analysis.get("connections") or []
    nodes_by_id: Dict[str, Dict[str, Any]] = {
        str(n.get("id")): n for n in nodes_list if isinstance(n, dict) and n.get("id")
    }

    # Build undirected adjacency by default (legacy UI expects "both").
    adj: Dict[str, set] = {}
    conns_by_node: Dict[str, list] = {}
    for c in conns_list:
        if not isinstance(c, dict):
            continue
        s = c.get("source_id")
        t = c.get("target_id")
        if not s or not t:
            continue
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)
        conns_by_node.setdefault(s, []).append(c)
        conns_by_node.setdefault(t, []).append(c)

    visited = {start_node}
    frontier = {start_node}
    for _ in range(max_depth):
        nxt = set()
        for nid in frontier:
            for nb in adj.get(nid, set()):
                if nb not in visited:
                    visited.add(nb)
                    nxt.add(nb)
        if not nxt:
            break
        frontier = nxt

    # Return only nodes/conns in visited.
    out_nodes = [nodes_by_id[nid] for nid in visited if nid in nodes_by_id]
    out_conns = [
        c
        for c in conns_list
        if isinstance(c, dict)
        and c.get("source_id") in visited
        and c.get("target_id") in visited
    ]

    return {
        "start": start_node,
        "nodes": out_nodes,
        "connections": out_conns,
        "max_depth": max_depth,
    }


@app.post("/api/analyze")
async def api_analyze(request: Request, payload: Dict[str, Any] = Body(...)):
    path = (payload.get("path") or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="Missing path")

    analysis_id = uuid.uuid4().hex
    user_id, user_role, is_superuser, unlimited_credits = _extract_billing_identity(request)
    tx_id: Optional[str] = None
    credits_deducted = 0
    if user_id:
        resp = await deduct_credits(
            user_id,
            CREDIT_COSTS["codebase_analysis"],
            "ast_analysis",
            f"Codebase analysis: {Path(path).name}",
            user_role=user_role,
            is_superuser=is_superuser,
            unlimited_credits=unlimited_credits,
        )
        tx_id = str(resp.get("transaction_id") or resp.get("id") or "") or None
        credits_deducted = CREDIT_COSTS["codebase_analysis"]

    try:
        analysis = await asyncio.to_thread(analyze_codebase, path)
    except Exception:
        if user_id and tx_id:
            await refund_credits(
                user_id,
                credits_deducted,
                tx_id,
                f"Refund codebase analysis: {Path(path).name}",
            )
        raise
    _store_analysis(analysis_id, analysis, {"path": path})
    return {"analysis_id": analysis_id}


@app.post("/api/analyze-multi")
async def api_analyze_multi(request: Request, payload: Dict[str, Any] = Body(...)):
    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        raise HTTPException(status_code=400, detail="Missing paths")

    user_id, user_role, is_superuser, unlimited_credits = _extract_billing_identity(request)
    tx_id: Optional[str] = None
    credits_deducted = 0
    if user_id:
        resp = await deduct_credits(
            user_id,
            CREDIT_COSTS["codebase_analysis"],
            "ast_analysis",
            "Codebase analysis: multi-project",
            user_role=user_role,
            is_superuser=is_superuser,
            unlimited_credits=unlimited_credits,
        )
        tx_id = str(resp.get("transaction_id") or resp.get("id") or "") or None
        credits_deducted = CREDIT_COSTS["codebase_analysis"]

    # Treat as unified by creating a synthetic folder tree is out-of-scope;
    # We approximate by analyzing each path separately and then merging graphs.
    # UI mostly needs a single analysis_id to render.
    analysis_id = uuid.uuid4().hex
    merged_nodes: Dict[str, Any] = {}
    merged_connections: list = []
    merged_services: Dict[str, Any] = {}
    merged_pipelines: Dict[str, Any] = {}
    stats = {
        "total_files": 0,
        "total_services": 0,
        "total_connections": 0,
        "total_functions": 0,
        "total_endpoints": 0,
        "broken_connections": 0,
        "cross_project_connections": 0,
    }

    try:
        projects = []
        for item in paths:
            p = (item.get("path") if isinstance(item, dict) else "")
            label = (item.get("label") if isinstance(item, dict) else None) or "project"
            if not p:
                continue
            projects.append(label)
            data = await asyncio.to_thread(analyze_codebase, p)
            # Namespace node IDs with label to avoid collisions.
            for n in data.get("nodes", []):
                nid = f"{label}::{n['id']}"
                nn = dict(n)
                nn["id"] = nid
                nn["service"] = f"{label}:{n.get('service','')}"
                merged_nodes[nid] = nn
            for c in data.get("connections", []):
                merged_connections.append(
                    {
                        **c,
                        "source_id": f"{label}::{c['source_id']}",
                        "target_id": f"{label}::{c['target_id']}",
                    }
                )
            for svc, files in (data.get("services") or {}).items():
                merged_services[f"{label}:{svc}"] = files
            for pname, p in (data.get("pipelines") or {}).items():
                merged_pipelines[f"{label}:{pname}"] = p
            s = data.get("stats") or {}
            for k in ["total_files", "total_services", "total_connections", "total_functions", "total_endpoints", "broken_connections"]:
                stats[k] += int(s.get(k, 0) or 0)
    except Exception:
        if user_id and tx_id:
            await refund_credits(
                user_id,
                credits_deducted,
                tx_id,
                "Refund codebase analysis: multi-project",
            )
        raise

    merged = {
        "nodes": list(merged_nodes.values()),
        "connections": merged_connections,
        "services": merged_services,
        "pipelines": merged_pipelines,
        "stats": stats,
    }
    _store_analysis(analysis_id, merged, {"paths": paths})
    return {"analysis_id": analysis_id, "projects": projects, "stats": stats}


class GitHubScanRequest(BaseModel):
    repo_url: str
    token: Optional[str] = None
    branch: Optional[str] = None
    project_name: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@app.post("/api/v1/scan/github")
async def scan_github_repo(request: Request, payload: GitHubScanRequest):
    repo_url = (payload.repo_url or "").strip()
    if not repo_url:
        raise HTTPException(status_code=400, detail="Missing repo_url")

    meta = payload.metadata or {}
    safe_name = (payload.project_name or "repo").strip().replace(" ", "-")
    analysis_id = uuid.uuid4().hex

    user_id, user_role, is_superuser, unlimited_credits = _extract_billing_identity(request)
    tx_id: Optional[str] = None
    credits_deducted = 0
    if user_id:
        resp = await deduct_credits(
            user_id,
            CREDIT_COSTS["codebase_analysis"],
            "ast_analysis",
            f"Codebase analysis: {safe_name}",
            user_role=user_role,
            is_superuser=is_superuser,
        )
        tx_id = str(resp.get("transaction_id") or resp.get("id") or "") or None
        credits_deducted = CREDIT_COSTS["codebase_analysis"]

    cache_dir = _analysis_cache_dir(analysis_id)
    clone_dir = cache_dir / "repo"

    try:
        CODE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cleanup_cache_best_effort()
        _ensure_empty_dir(cache_dir)

        github_token = _resolve_github_token(request, payload.token)
        clone_url = _inject_github_token(repo_url, github_token)
        cmd = ["git", "clone", "--depth", "1"]
        if payload.branch:
            cmd.extend(["--branch", payload.branch])
        cmd.extend([clone_url, str(clone_dir)])

        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=CODE_GITHUB_CLONE_TIMEOUT_SEC,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if proc.returncode != 0:
            stderr_text = (proc.stderr or proc.stdout or "").strip()
            hint = ""
            lower_err = stderr_text.lower()
            if (
                "authentication failed" in lower_err
                or "invalid username or token" in lower_err
                or "repository not found" in lower_err
                or "could not read username" in lower_err
                or "terminal prompts disabled" in lower_err
            ):
                hint = (
                    " GitHub authentication failed. Provide a valid PAT via request token, "
                    "x-github-token header, or CODE_VISUALIZER_GITHUB_TOKEN/GITHUB_TOKEN env."
                )
            raise HTTPException(status_code=400, detail=f"Git clone failed: {stderr_text[:500]}{hint}")

        analysis = await asyncio.wait_for(
            asyncio.to_thread(analyze_codebase, str(clone_dir)),
            timeout=CODE_UPLOAD_SCAN_TIMEOUT_SEC,
        )
    except Exception:
        if user_id and tx_id:
            await refund_credits(
                user_id,
                credits_deducted,
                tx_id,
                f"Refund codebase analysis: {safe_name}",
            )
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise

    scan_meta = {
        "path": str(clone_dir),
        "project_name": safe_name,
        "metadata": meta,
        "source": "github",
        "repo_url": repo_url,
    }
    _store_analysis(analysis_id, analysis, scan_meta)

    # Persist to DB (fire-and-forget, best-effort)
    if user_id:
        asyncio.ensure_future(_db_save_analysis(
            analysis_id=analysis_id,
            user_id=user_id,
            user_role=user_role,
            is_superuser=is_superuser,
            project_name=safe_name,
            source="github",
            repo_url=repo_url,
            analysis=analysis,
            meta=scan_meta,
        ))

    return {
        "status": "ok",
        "analysis_id": analysis_id,
        "metadata": meta,
        "analysis": analysis,
        "credits_deducted": credits_deducted,
    }


class GitHubMultiRepoItem(BaseModel):
    repo_url: str
    label: Optional[str] = None
    branch: Optional[str] = None
    token: Optional[str] = None


class GitHubMultiScanRequest(BaseModel):
    repos: List[GitHubMultiRepoItem]
    metadata: Optional[Dict[str, Any]] = None


@app.post("/api/v1/scan/github/multi")
async def scan_github_multi(request: Request, payload: GitHubMultiScanRequest):
    repos = payload.repos or []
    if len(repos) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 repos")

    meta = payload.metadata or {}
    analysis_id = uuid.uuid4().hex

    user_id, user_role, is_superuser, unlimited_credits = _extract_billing_identity(request)
    tx_id: Optional[str] = None
    credits_deducted = 0
    if user_id:
        resp = await deduct_credits(
            user_id,
            CREDIT_COSTS["codebase_analysis"],
            "ast_analysis",
            "Codebase analysis: multi-github",
            user_role=user_role,
            is_superuser=is_superuser,
            unlimited_credits=unlimited_credits,
        )
        tx_id = str(resp.get("transaction_id") or resp.get("id") or "") or None
        credits_deducted = CREDIT_COSTS["codebase_analysis"]

    cache_dir = _analysis_cache_dir(analysis_id)
    merged_nodes: Dict[str, Any] = {}
    merged_connections: list = []
    merged_services: Dict[str, Any] = {}
    merged_pipelines: Dict[str, Any] = {}
    stats = {
        "total_files": 0,
        "total_services": 0,
        "total_connections": 0,
        "total_functions": 0,
        "total_endpoints": 0,
        "broken_connections": 0,
        "cross_project_connections": 0,
    }

    used_labels: Dict[str, int] = {}
    projects: List[str] = []
    repo_meta: List[Dict[str, Any]] = []

    try:
        CODE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cleanup_cache_best_effort()
        _ensure_empty_dir(cache_dir)

        for idx, item in enumerate(repos, start=1):
            repo_url = (item.repo_url or "").strip()
            if not repo_url:
                continue

            raw_label = (item.label or "").strip() or Path(repo_url.rstrip("/")).name or f"repo-{idx}"
            safe_label = "".join(ch if (ch.isalnum() or ch in "-._") else "-" for ch in raw_label).strip("-") or f"repo-{idx}"
            count = used_labels.get(safe_label, 0)
            used_labels[safe_label] = count + 1
            label = safe_label if count == 0 else f"{safe_label}-{count + 1}"

            project_cache = cache_dir / label
            clone_dir = project_cache / "repo"
            project_cache.mkdir(parents=True, exist_ok=True)

            github_token = _resolve_github_token(request, item.token)
            clone_url = _inject_github_token(repo_url, github_token)
            cmd = ["git", "clone", "--depth", "1"]
            if item.branch:
                cmd.extend(["--branch", item.branch])
            cmd.extend([clone_url, str(clone_dir)])

            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=CODE_GITHUB_CLONE_TIMEOUT_SEC,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if proc.returncode != 0:
                stderr_text = (proc.stderr or proc.stdout or "").strip()
                hint = ""
                lower_err = stderr_text.lower()
                if (
                    "authentication failed" in lower_err
                    or "invalid username or token" in lower_err
                    or "repository not found" in lower_err
                    or "could not read username" in lower_err
                    or "terminal prompts disabled" in lower_err
                ):
                    hint = (
                        " GitHub authentication failed. Provide a valid PAT via request token, "
                        "x-github-token header, or CODE_VISUALIZER_GITHUB_TOKEN/GITHUB_TOKEN env."
                    )
                raise HTTPException(status_code=400, detail=f"Git clone failed for {label}: {stderr_text[:500]}{hint}")

            data = await asyncio.wait_for(
                asyncio.to_thread(analyze_codebase, str(clone_dir)),
                timeout=CODE_UPLOAD_SCAN_TIMEOUT_SEC,
            )

            projects.append(label)
            repo_meta.append({"label": label, "repo_url": repo_url, "branch": item.branch})

            for n in data.get("nodes", []):
                nid = f"{label}::{n['id']}"
                nn = dict(n)
                nn["id"] = nid
                nn["service"] = f"{label}:{n.get('service', '')}"
                merged_nodes[nid] = nn
            for c in data.get("connections", []):
                merged_connections.append(
                    {
                        **c,
                        "source_id": f"{label}::{c['source_id']}",
                        "target_id": f"{label}::{c['target_id']}",
                    }
                )
            for svc, files in (data.get("services") or {}).items():
                merged_services[f"{label}:{svc}"] = files
            for pname, p in (data.get("pipelines") or {}).items():
                merged_pipelines[f"{label}:{pname}"] = p
            s = data.get("stats") or {}
            for k in ["total_files", "total_services", "total_connections", "total_functions", "total_endpoints", "broken_connections"]:
                stats[k] += int(s.get(k, 0) or 0)
    except Exception:
        if user_id and tx_id:
            await refund_credits(
                user_id,
                credits_deducted,
                tx_id,
                "Refund codebase analysis: multi-github",
            )
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise

    merged = {
        "nodes": list(merged_nodes.values()),
        "connections": merged_connections,
        "services": merged_services,
        "pipelines": merged_pipelines,
        "stats": stats,
    }
    _store_analysis(
        analysis_id,
        merged,
        {
            "path": str(cache_dir),
            "project_name": "multi-github",
            "metadata": meta,
            "source": "github_multi",
            "repos": repo_meta,
        },
    )

    return {
        "status": "ok",
        "analysis_id": analysis_id,
        "projects": projects,
        "stats": stats,
        "analysis": merged,
        "metadata": meta,
        "credits_deducted": credits_deducted,
    }


class MergeAnalysisItem(BaseModel):
    analysis_id: str
    label: str


class MergeAnalysesRequest(BaseModel):
    analyses: List[MergeAnalysisItem]


@app.post("/api/analysis/merge")
async def api_merge_analyses(payload: MergeAnalysesRequest):
    items = payload.analyses or []
    if len(items) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 analyses")

    analysis_id = uuid.uuid4().hex
    merged_nodes: Dict[str, Any] = {}
    merged_connections: list = []
    merged_services: Dict[str, Any] = {}
    merged_pipelines: Dict[str, Any] = {}
    stats = {
        "total_files": 0,
        "total_services": 0,
        "total_connections": 0,
        "total_functions": 0,
        "total_endpoints": 0,
        "broken_connections": 0,
        "cross_project_connections": 0,
    }

    projects: List[str] = []
    for it in items:
        src_id = (it.analysis_id or "").strip()
        label = (it.label or "project").strip() or "project"
        if not src_id:
            continue
        data = _get_analysis_or_404(src_id)
        projects.append(label)
        for n in data.get("nodes", []):
            nid = f"{label}::{n['id']}"
            nn = dict(n)
            nn["id"] = nid
            nn["service"] = f"{label}:{n.get('service','')}"
            merged_nodes[nid] = nn
        for c in data.get("connections", []):
            merged_connections.append(
                {
                    **c,
                    "source_id": f"{label}::{c['source_id']}",
                    "target_id": f"{label}::{c['target_id']}",
                }
            )
        for svc, files in (data.get("services") or {}).items():
            merged_services[f"{label}:{svc}"] = files
        for pname, p in (data.get("pipelines") or {}).items():
            merged_pipelines[f"{label}:{pname}"] = p
        s = data.get("stats") or {}
        for k in ["total_files", "total_services", "total_connections", "total_functions", "total_endpoints", "broken_connections"]:
            stats[k] += int(s.get(k, 0) or 0)

    merged = {
        "nodes": list(merged_nodes.values()),
        "connections": merged_connections,
        "services": merged_services,
        "pipelines": merged_pipelines,
        "stats": stats,
    }
    _store_analysis(analysis_id, merged, {"analyses": [i.model_dump() for i in items]})
    return {"analysis_id": analysis_id, "projects": projects, "stats": stats}


@app.post("/api/compare-by-analysis")
async def api_compare_by_analysis(payload: Dict[str, Any] = Body(...)):
    projects = payload.get("projects")
    if not isinstance(projects, list) or len(projects) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 projects")

    comparator = MultiProjectComparator()
    for idx, p in enumerate(projects[:3]):
        aid = (p.get("analysis_id") or "").strip() if isinstance(p, dict) else ""
        label = (p.get("label") or aid) if isinstance(p, dict) else aid
        if not aid:
            continue
        meta = _analysis_meta.get(aid) or {}
        path = (meta.get("path") or "").strip() if isinstance(meta, dict) else ""
        if not path:
            raise HTTPException(status_code=400, detail=f"Missing cached path for analysis {aid}")
        data = _get_analysis_or_404(aid)
        comparator.projects.append(
            {
                "index": idx,
                "label": label,
                "analysis_id": aid,
                "path": path,
                "data": data,
                "analyzer": None,
            }
        )

    comparison_id = uuid.uuid4().hex
    data = await asyncio.to_thread(comparator.compare_all)
    _analysis_store[f"comparison:{comparison_id}"] = data
    return {"comparison_id": comparison_id}


@app.post("/api/compare")
async def api_compare(payload: Dict[str, Any] = Body(...)):
    projects = payload.get("projects")
    if not isinstance(projects, list) or len(projects) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 projects")

    comparator = MultiProjectComparator()
    for p in projects[:3]:
        comparator.add_project(p.get("path"), p.get("label") or p.get("path"))

    comparison_id = uuid.uuid4().hex
    data = await asyncio.to_thread(comparator.compare_all)
    # Store comparison payload in same store to allow retrieval.
    _analysis_store[f"comparison:{comparison_id}"] = data
    return {"comparison_id": comparison_id}


@app.get("/api/comparison/{comparison_id}")
async def api_get_comparison(comparison_id: str):
    key = f"comparison:{comparison_id}"
    if key not in _analysis_store:
        raise HTTPException(status_code=404, detail="Comparison not found")
    return _analysis_store[key]


@app.get("/api/analysis/{analysis_id}")
async def api_get_analysis(analysis_id: str):
    await _ensure_analysis_loaded(analysis_id)
    return _get_analysis_or_404(analysis_id)


@app.get("/api/analysis/{analysis_id}/graph-structure")
async def api_graph_structure(analysis_id: str):
    await _ensure_analysis_loaded(analysis_id)
    data = _get_analysis_or_404(analysis_id)
    nodes = data.get("nodes") or []
    conns = data.get("connections") or []
    node_count = max(len(nodes), 1)
    density = round(len(conns) / (node_count * node_count), 4)
    metrics = {
        "connection_density": density,
        "hub_count": 0,
        "isolated_count": 0,
    }
    return {
        "layout_type": "radial",
        "layout_explanation": "Services form a ring; files and functions cluster near their service.",
        "metrics": metrics,
    }


@app.post("/api/analysis/{analysis_id}/filter")
async def api_filter_analysis(analysis_id: str, payload: Dict[str, Any] = Body(...)):
    await _ensure_analysis_loaded(analysis_id)
    data = _get_analysis_or_404(analysis_id)
    pipeline = payload.get("pipeline")
    if pipeline:
        # Attempt to re-run filter using analyzer when possible.
        meta = _analysis_meta.get(analysis_id) or {}
        path = meta.get("path")
        if path:
            analyzer = CodebaseAnalyzer(path)
            analyzer.analyze()
            return analyzer.filter_by_pipeline(pipeline)
    return data


@app.get("/api/analysis/{analysis_id}/by-type/{node_type}")
async def api_by_type(analysis_id: str, node_type: str):
    await _ensure_analysis_loaded(analysis_id)
    data = _get_analysis_or_404(analysis_id)
    node_type = (node_type or "").lower()
    nodes = [n for n in (data.get("nodes") or []) if (n.get("type") == node_type)]
    node_ids = {n.get("id") for n in nodes}
    conns = [c for c in (data.get("connections") or []) if c.get("source_id") in node_ids or c.get("target_id") in node_ids]
    return {"nodes": nodes, "connections": conns}


@app.post("/api/analysis/{analysis_id}/trace")
async def api_trace(analysis_id: str, payload: Dict[str, Any] = Body(...)):
    await _ensure_analysis_loaded(analysis_id)
    start_node = payload.get("start_node")
    max_depth = int(payload.get("max_depth", 10))
    meta = _analysis_meta.get(analysis_id) or {}
    path = meta.get("path")
    if path:
        analyzer = CodebaseAnalyzer(path)
        analyzer.analyze()
        return analyzer.trace_execution(start_node, max_depth=max_depth)
    analysis = _get_analysis_or_404(analysis_id)
    return _trace_stored_analysis(analysis, start_node, max_depth=max_depth)


@app.post("/api/analysis/{analysis_id}/full-pipeline")
async def api_full_pipeline(analysis_id: str, payload: Dict[str, Any] = Body(...)):
    await _ensure_analysis_loaded(analysis_id)
    start_node = payload.get("start_node")
    max_depth = int(payload.get("max_depth", 50))
    meta = _analysis_meta.get(analysis_id) or {}
    path = meta.get("path")
    if path:
        analyzer = CodebaseAnalyzer(path)
        analyzer.analyze()
        return analyzer.trace_execution(start_node, max_depth=max_depth)
    analysis = _get_analysis_or_404(analysis_id)
    return _trace_stored_analysis(analysis, start_node, max_depth=max_depth)


@app.get("/api/analysis/{analysis_id}/functions")
async def api_functions(analysis_id: str):
    await _ensure_analysis_loaded(analysis_id)
    data = _get_analysis_or_404(analysis_id)
    funcs = [n for n in (data.get("nodes") or []) if n.get("type") == "function"]
    return {"functions": funcs}


@app.post("/api/analysis/{analysis_id}/governance")
async def api_governance(request: Request, analysis_id: str, payload: Dict[str, Any] = Body(...)):
    await _ensure_analysis_loaded(analysis_id)
    meta = _analysis_meta.get(analysis_id) or {}
    path = meta.get("path")

    user_id, user_role, is_superuser, unlimited_credits = _extract_billing_identity(request)
    tx_id: Optional[str] = None
    credits_deducted = 0
    if user_id:
        resp = await deduct_credits(
            user_id,
            CREDIT_COSTS["governance_check"],
            "ast_analysis_governance",
            f"Governance check: {Path(path).name if path else analysis_id}",
            user_role=user_role,
            is_superuser=is_superuser,
            unlimited_credits=unlimited_credits,
        )
        tx_id = str(resp.get("transaction_id") or resp.get("id") or "") or None
        credits_deducted = CREDIT_COSTS["governance_check"]
    try:
        drift_threshold = float((payload or {}).get("drift_threshold", 20.0))
    except (TypeError, ValueError):
        drift_threshold = 20.0
    analysis = _get_analysis_or_404(analysis_id)
    nodes = {
        n.get("id"): n
        for n in (analysis.get("nodes") or [])
        if isinstance(n, dict) and isinstance(n.get("id"), str) and n.get("id")
    }
    connections = analysis.get("connections") or []
    engine = GovernanceEngine(drift_threshold=drift_threshold)

    try:
        report = await asyncio.to_thread(engine.analyze, nodes, connections, path or "")
    except Exception as exc:
        if user_id and tx_id:
            await refund_credits(
                user_id,
                credits_deducted,
                tx_id,
                f"Refund governance check: {Path(path).name if path else analysis_id}",
            )
        logger.exception("Governance analysis failed for analysis_id=%s", analysis_id)
        raise HTTPException(status_code=500, detail=f"Governance analysis failed: {str(exc)[:300]}")
    report_dict = report.to_dict()
    resp: Dict[str, Any] = dict(report_dict)
    # Legacy UI compatibility.
    resp["governance"] = report_dict
    resp["node_status"] = dict(engine.node_status)
    resp["live_count"] = getattr(report, "live_nodes", None)
    resp["invalid_count"] = getattr(report, "invalid_nodes", None)
    resp["credits_deducted"] = credits_deducted

    _analysis_store[f"governance:{analysis_id}"] = resp
    return resp


@app.get("/api/analysis/{analysis_id}/governance/live-nodes")
async def api_governance_live_nodes(analysis_id: str):
    rep = _analysis_store.get(f"governance:{analysis_id}")
    if not rep:
        return {"live_nodes": []}
    # Governance engine currently doesn't return the node lists; keep endpoint for UI.
    return {"live_nodes": []}


@app.get("/api/analysis/{analysis_id}/governance/invalid-nodes")
async def api_governance_invalid_nodes(analysis_id: str):
    rep = _analysis_store.get(f"governance:{analysis_id}")
    if not rep:
        return {"invalid_nodes": []}
    return {"invalid_nodes": []}


@app.post("/api/analysis/{analysis_id}/agent/scan")
async def api_agent_scan(analysis_id: str, payload: Dict[str, Any] = Body(...)):
    """Full Graph Janitor Agent scan with PostgreSQL memory for learning.

    Uses GraphJanitorAgent.async_scan() when memory is available (dedup,
    epoch tracking, proposal persistence). Falls back to stateless scan()
    if the DB is not initialised.
    """
    await _ensure_analysis_loaded(analysis_id)
    analysis = _get_analysis_or_404(analysis_id)

    try:
        max_proposals = int((payload or {}).get("max_proposals", 15) or 15)
    except (TypeError, ValueError):
        max_proposals = 15

    # Build graph data dict expected by GAL / Janitor
    graph_data = {
        "nodes": analysis.get("nodes") or [],
        "connections": analysis.get("connections") or [],
    }

    agent = GraphJanitorAgent(
        graph_data=graph_data,
        config={"max_proposals": max_proposals},
        analysis_id=analysis_id,
    )

    # Use async_scan with memory if DB is available; else stateless scan
    memory = _startup_mod.agent_memory
    try:
        if memory and memory.pool:
            report = await agent.async_scan(memory)
            agent_label = "Graph Janitor Agent (learning)"
        else:
            report = await asyncio.to_thread(agent.scan)
            agent_label = "Graph Janitor Agent"
    except Exception as exc:
        logger.exception("Agent scan failed for analysis_id=%s", analysis_id)
        raise HTTPException(status_code=500, detail=f"Graph Janitor scan failed: {str(exc)[:300]}")

    result = report.to_dict()
    result["agent"] = agent_label
    return result


@app.post("/api/upload")
async def api_upload(
    request: Request,
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(None),
    project_name: Optional[str] = Form(None),
):
    """Upload an archive and return an analysis_id compatible with the UI's /api/analysis/{id} fetch flow."""
    result = await upload_and_scan(
        request=request,
        file=file,
        metadata=metadata,
        project_name=project_name,
    )
    return {"analysis_id": result.get("analysis_id")}


@app.post("/api/v1/scan/upload")
async def upload_and_scan(
    request: Request,
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(None),
    project_name: Optional[str] = Form(None),
    auto_save: Optional[bool] = Form(False),
    chat_id: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
):
    suffix = _archive_suffix(file.filename or "")
    if not suffix:
        raise HTTPException(status_code=400, detail="Upload must be .zip, .tar, or .tar.gz.")

    meta: Dict[str, Any] = {}
    if metadata:
        try:
            meta = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid metadata JSON.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Upload exceeds size limit.")

    safe_name = (project_name or "code").strip().replace(" ", "-")
    analysis_id = uuid.uuid4().hex
    object_key = f"{safe_name}-{analysis_id}{suffix}"

    user_id, user_role, is_superuser, unlimited_credits = _extract_billing_identity(request)
    tx_id: Optional[str] = None
    credits_deducted = 0

    if user_id:
        resp = await deduct_credits(
            user_id,
            CREDIT_COSTS["codebase_analysis"],
            "ast_analysis",
            f"Codebase analysis: {safe_name}",
            user_role=user_role,
            is_superuser=is_superuser,
            unlimited_credits=unlimited_credits,
        )
        tx_id = str(resp.get("transaction_id") or resp.get("id") or "") or None
        credits_deducted = CREDIT_COSTS["codebase_analysis"]

    cache_dir = _analysis_cache_dir(analysis_id)
    extract_dir = cache_dir / "extracted"

    try:
        CODE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cleanup_cache_best_effort()
        _ensure_empty_dir(extract_dir)

        storage_ok = False
        storage_error: Optional[str] = None
        archive_bytes = data
        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                upload_resp = await client.post(
                    f"{STORAGE_SERVICE_URL}/upload",
                    params={"bucket": CODE_UPLOAD_BUCKET, "key": object_key},
                    files={"file": (file.filename, data, file.content_type or "application/octet-stream")},
                )
                if upload_resp.status_code < 300:
                    storage_ok = True
                else:
                    storage_error = f"Storage upload failed: {upload_resp.text}"
            except Exception as e:
                storage_error = f"Storage upload failed: {str(e)}"

        archive_path = cache_dir / f"upload{suffix}"
        archive_path.write_bytes(archive_bytes)
        _extract_archive(archive_path, extract_dir)

        analysis = await asyncio.wait_for(
            asyncio.to_thread(analyze_codebase, str(extract_dir)),
            timeout=CODE_UPLOAD_SCAN_TIMEOUT_SEC,
        )

        auto_save_result = None
        if auto_save:
            pid = (project_id or "").strip() or uuid.uuid4().hex
            pname = safe_name
            auto_save_result = await _persist_project_from_dir(
                user_id=user_id or "",
                org_id=(request.headers.get("x-org-id") or "").strip() or None,
                chat_id=(chat_id or "").strip() or None,
                project_id=pid,
                project_name=pname,
                analysis_id=analysis_id,
                extract_dir=extract_dir,
                analysis=analysis,
            )
    except Exception:
        if user_id and tx_id:
            await refund_credits(
                user_id,
                credits_deducted,
                tx_id,
                f"Refund codebase analysis: {safe_name}",
            )
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise

    # Deduct credits for codebase analysis
    if user_id and not tx_id:
        await deduct_credits(
            user_id,
            CREDIT_COSTS["codebase_analysis"],
            "ast_analysis",
            f"Codebase analysis: {safe_name}",
            user_role=user_role,
            is_superuser=is_superuser,
            unlimited_credits=unlimited_credits,
        )
        logger.info(f"💳 Deducted {CREDIT_COSTS['codebase_analysis']} credits for codebase analysis")

    upload_meta = {
        "upload": {"bucket": CODE_UPLOAD_BUCKET, "key": object_key, "stored": storage_ok},
        "project_name": safe_name,
        "metadata": meta,
        "path": str(extract_dir),
    }
    _store_analysis(analysis_id, analysis, upload_meta)

    # Persist to DB (fire-and-forget, best-effort)
    if user_id:
        asyncio.ensure_future(_db_save_analysis(
            analysis_id=analysis_id,
            user_id=user_id,
            user_role=user_role,
            is_superuser=is_superuser,
            project_name=safe_name,
            source="upload",
            repo_url="",
            analysis=analysis,
            meta=upload_meta,
        ))

    response = {
        "status": "ok",
        "analysis_id": analysis_id,
        "bucket": CODE_UPLOAD_BUCKET if storage_ok else None,
        "key": object_key if storage_ok else None,
        "metadata": meta,
        "analysis": analysis,
        "credits_deducted": credits_deducted,
        "storage_ok": storage_ok,
    }

    if storage_error:
        response["storage_error"] = storage_error

    if auto_save and "auto_save_result" in locals() and auto_save_result:
        response["auto_save"] = True
        response["project_id"] = auto_save_result.get("project_id")
        response["project_name"] = auto_save_result.get("project_name")
        response["saved_files"] = auto_save_result.get("saved_files")
        response["skipped_files"] = auto_save_result.get("skipped_files")
        response["save_errors"] = auto_save_result.get("errors")
        response["report_saved"] = auto_save_result.get("report_saved")

    return response


# ---------------------------------------------------------------------------
# Saved Analyses CRUD endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v1/analyses")
async def list_saved_analyses(request: Request, limit: int = 50, offset: int = 0):
    """List all saved analyses for the authenticated user."""
    user_id, user_role, is_superuser, unlimited_credits = _extract_billing_identity(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    try:
        from sqlalchemy import select
        async with async_session_maker() as session:
            q = (
                select(
                    SavedAnalysis.analysis_id,
                    SavedAnalysis.project_name,
                    SavedAnalysis.source,
                    SavedAnalysis.repo_url,
                    SavedAnalysis.stats_json,
                    SavedAnalysis.storage_bytes,
                    SavedAnalysis.created_at,
                )
                .where(
                    SavedAnalysis.user_id == user_id,
                    SavedAnalysis.deleted_at.is_(None),
                )
                .order_by(SavedAnalysis.created_at.desc())
                .offset(offset)
                .limit(min(limit, 100))
            )
            result = await session.execute(q)
            rows = result.fetchall()
        analyses = [
            {
                "analysis_id": r.analysis_id,
                "project_name": r.project_name,
                "source": r.source,
                "repo_url": r.repo_url,
                "stats": r.stats_json or {},
                "storage_bytes": r.storage_bytes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        storage_limit = None if (is_superuser or user_role.lower() in _PRIVILEGED_ROLES) \
            else _STORAGE_LIMITS.get(user_role.lower(), _STORAGE_LIMITS["user"])
        return {
            "analyses": analyses,
            "total": len(analyses),
            "storage_limit": storage_limit,
        }
    except Exception as e:
        logger.error(f"Failed to list analyses: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list analyses: {str(e)[:200]}")


@app.get("/api/v1/analyses/{analysis_id}")
async def get_saved_analysis(request: Request, analysis_id: str):
    """Get a specific saved analysis (loads from DB into memory if needed)."""
    user_id, user_role, is_superuser, unlimited_credits = _extract_billing_identity(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")

    # Try DB first
    try:
        from sqlalchemy import select
        async with async_session_maker() as session:
            result = await session.execute(
                select(SavedAnalysis).where(
                    SavedAnalysis.analysis_id == analysis_id,
                    SavedAnalysis.user_id == user_id,
                    SavedAnalysis.deleted_at.is_(None),
                )
            )
            row = result.scalar_one_or_none()
        if row:
            _store_analysis(analysis_id, row.analysis_json or {}, row.meta_json or {})
            return {
                "analysis_id": row.analysis_id,
                "project_name": row.project_name,
                "source": row.source,
                "repo_url": row.repo_url,
                "stats": row.stats_json or {},
                "storage_bytes": row.storage_bytes,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "analysis": row.analysis_json,
            }
    except Exception as e:
        logger.warning(f"DB fetch for {analysis_id} failed: {e}")

    # Fall back to in-memory
    if analysis_id not in _analysis_store:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"analysis_id": analysis_id, "analysis": _analysis_store[analysis_id]}


@app.delete("/api/v1/analyses/{analysis_id}")
async def delete_saved_analysis(request: Request, analysis_id: str):
    """Soft-delete a saved analysis. Only the owner or privileged users can delete."""
    user_id, user_role, is_superuser, unlimited_credits = _extract_billing_identity(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    try:
        from sqlalchemy import select
        from datetime import datetime
        async with async_session_maker() as session:
            result = await session.execute(
                select(SavedAnalysis).where(
                    SavedAnalysis.analysis_id == analysis_id,
                    SavedAnalysis.deleted_at.is_(None),
                )
            )
            row = result.scalar_one_or_none()
            if not row:
                raise HTTPException(status_code=404, detail="Analysis not found")
            # Only owner or privileged roles can delete any; others only their own
            if row.user_id != user_id and not (is_superuser or user_role.lower() in _PRIVILEGED_ROLES):
                raise HTTPException(status_code=403, detail="Not authorized to delete this analysis")
            row.deleted_at = datetime.utcnow()
            await session.commit()
        # Remove from in-memory cache too
        _analysis_store.pop(analysis_id, None)
        _analysis_meta.pop(analysis_id, None)
        return {"deleted": True, "analysis_id": analysis_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)[:200]}")


class SaveProjectRequest(BaseModel):
    analysis_id: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    chat_id: Optional[str] = None


class SaveProjectResponse(BaseModel):
    project_id: str
    project_name: str
    saved_files: int
    skipped_files: int
    errors: int
    report_saved: bool = False


@app.post("/api/v1/scan/save", response_model=SaveProjectResponse)
async def save_project(request: Request, payload: SaveProjectRequest):
    user_id = (request.headers.get("x-user-id") or "").strip() or None
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    org_id = (request.headers.get("x-org-id") or "").strip() or None

    analysis_id = (payload.analysis_id or "").strip()
    if not analysis_id:
        raise HTTPException(status_code=400, detail="Missing analysis_id")

    meta = _analysis_meta.get(analysis_id) or {}
    upload_meta = meta.get("upload") if isinstance(meta, dict) else None
    if not isinstance(upload_meta, dict):
        raise HTTPException(status_code=400, detail="Save requires an upload-based analysis")

    bucket = (upload_meta.get("bucket") or CODE_UPLOAD_BUCKET) if isinstance(upload_meta, dict) else CODE_UPLOAD_BUCKET
    key = (upload_meta.get("key") or "").strip() if isinstance(upload_meta, dict) else ""
    if not key:
        raise HTTPException(status_code=400, detail="Missing upload key")

    project_id = (payload.project_id or "").strip() or uuid.uuid4().hex
    project_name = (payload.project_name or "").strip() or Path(key).name

    # Download archive from storage
    async with httpx.AsyncClient(timeout=60.0) as client:
        download_resp = await client.get(
            f"{STORAGE_SERVICE_URL}/download/{key}",
            params={"bucket": bucket},
        )
        if download_resp.status_code >= 300:
            raise HTTPException(status_code=502, detail=f"Storage download failed: {download_resp.text}")

    suffix = _archive_suffix(key)
    if not suffix:
        suffix = ".zip"

    saved_files = 0
    skipped_files = 0
    errors = 0
    report_saved = False

    sem = asyncio.Semaphore(10)

    async def _ingest_one(rel_path: str, content: str, language: Optional[str]) -> None:
        nonlocal saved_files, errors
        async with sem:
            ingest_payload: Dict[str, Any] = {
                "user_id": user_id,
                "org_id": org_id,
                "source": "project",
                "content": content,
                "metadata": {
                    "project_id": project_id,
                    "project_name": project_name,
                    "file_path": rel_path,
                    "type": "file",
                    "language": language,
                    "is_archived": False,
                    "saved_from": "ast_analysis",
                    "analysis_id": analysis_id,
                },
                "generate_embedding": False,
            }

            try:
                async with httpx.AsyncClient(timeout=20.0) as mem_client:
                    resp = await mem_client.post(
                        f"{MEMORY_SERVICE_URL}/memory/ingest",
                        json=ingest_payload,
                    )
                if resp.status_code >= 300:
                    errors += 1
                    return
                saved_files += 1
            except Exception:
                errors += 1

    with tempfile.TemporaryDirectory(prefix="codeviz-save-") as tmpdir:
        tmp_path = Path(tmpdir)
        archive_path = tmp_path / f"upload{suffix}"
        archive_path.write_bytes(download_resp.content)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        _extract_archive(archive_path, extract_dir)

        tasks = []
        for root, dirs, files in os.walk(extract_dir):
            root_path = Path(root)
            # prune dirs
            pruned = []
            for d in dirs:
                if _should_skip_dir(Path(d)):
                    continue
                pruned.append(d)
            dirs[:] = pruned

            for fname in files:
                fpath = root_path / fname
                try:
                    rel_path = str(fpath.relative_to(extract_dir).as_posix())
                except Exception:
                    skipped_files += 1
                    continue

                try:
                    size = fpath.stat().st_size
                    if size > MAX_SAVE_FILE_BYTES:
                        skipped_files += 1
                        continue
                    data = fpath.read_bytes()
                except Exception:
                    skipped_files += 1
                    continue

                if _looks_binary(data):
                    skipped_files += 1
                    continue

                try:
                    content = data.decode("utf-8")
                except Exception:
                    content = data.decode("utf-8", errors="replace")

                language = _guess_language(fpath)
                tasks.append(_ingest_one(rel_path, content, language))

        if tasks:
            await asyncio.gather(*tasks)

    analysis = _analysis_store.get(analysis_id)
    if isinstance(analysis, dict):
        report_text = _build_project_report_text(project_name, project_id, analysis_id, analysis)
        report_payload: Dict[str, Any] = {
            "user_id": user_id,
            "org_id": org_id,
            "chat_id": payload.chat_id,
            "source": "ast_analysis",
            "content": report_text,
            "metadata": {
                "type": "project_report",
                "project_id": project_id,
                "project_name": project_name,
                "analysis_id": analysis_id,
            },
            "generate_embedding": True,
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as mem_client:
                resp = await mem_client.post(
                    f"{MEMORY_SERVICE_URL}/memory/ingest",
                    json=report_payload,
                )
            report_saved = resp.status_code < 300
        except Exception:
            report_saved = False

    return SaveProjectResponse(
        project_id=project_id,
        project_name=project_name,
        saved_files=saved_files,
        skipped_files=skipped_files,
        errors=errors,
        report_saved=report_saved,
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
