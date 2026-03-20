"""
Code Analyzer - Parses Python/JS/TS files to extract:
- Imports and dependencies
- Function/class definitions
- API endpoints
- Database queries
- Inter-service calls
"""

import ast
import os
import re
import json
from pathlib import Path
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum


class NodeType(str, Enum):
    SERVICE = "service"
    FILE = "file"
    FUNCTION = "function"
    CLASS = "class"
    API_ENDPOINT = "api_endpoint"
    DATABASE = "database"
    EXTERNAL_SERVICE = "external_service"


class ConnectionType(str, Enum):
    IMPORT = "import"
    FUNCTION_CALL = "function_call"
    API_CALL = "api_call"
    DATABASE_QUERY = "database_query"
    WEBSOCKET = "websocket"
    HTTP_REQUEST = "http_request"
    INHERITANCE = "inheritance"


class ConnectionStatus(str, Enum):
    ACTIVE = "active"
    BROKEN = "broken"
    DEAD = "dead"
    UNUSED = "unused"
    CIRCULAR = "circular"


@dataclass
class CodeNode:
    id: str
    name: str
    type: NodeType
    file_path: str
    line_start: int = 0
    line_end: int = 0
    service: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "service": self.service,
            "metadata": self.metadata
        }


@dataclass
class CodeConnection:
    source_id: str
    target_id: str
    type: ConnectionType
    status: ConnectionStatus = ConnectionStatus.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self):
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "type": self.type.value,
            "status": self.status.value,
            "metadata": self.metadata
        }


@dataclass
class Pipeline:
    name: str
    description: str
    nodes: List[str] = field(default_factory=list)
    connections: List[str] = field(default_factory=list)
    color: str = "#667eea"
    
    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "nodes": self.nodes,
            "connections": self.connections,
            "color": self.color
        }


class PythonAnalyzer(ast.NodeVisitor):
    """AST-based Python code analyzer"""
    
    def __init__(self, file_path: str, service_name: str):
        self.file_path = file_path
        self.service_name = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []
        self.imports: Dict[str, str] = {}
        self.functions: Dict[str, CodeNode] = {}
        self.classes: Dict[str, CodeNode] = {}
        self.api_endpoints: List[CodeNode] = []
        self.current_class: Optional[str] = None
        
    def analyze(self, source_code: str) -> tuple:
        try:
            tree = ast.parse(source_code)
            self.visit(tree)
        except SyntaxError as e:
            pass
        return self.nodes, self.connections
    
    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module_name = alias.name
            local_name = alias.asname or alias.name
            self.imports[local_name] = module_name
            
            conn = CodeConnection(
                source_id=f"{self.service_name}:{self.file_path}",
                target_id=f"module:{module_name}",
                type=ConnectionType.IMPORT,
                metadata={"line": node.lineno}
            )
            self.connections.append(conn)
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        for alias in node.names:
            local_name = alias.asname or alias.name
            full_name = f"{module}.{alias.name}" if module else alias.name
            self.imports[local_name] = full_name
            
            conn = CodeConnection(
                source_id=f"{self.service_name}:{self.file_path}",
                target_id=f"module:{full_name}",
                type=ConnectionType.IMPORT,
                metadata={"line": node.lineno}
            )
            self.connections.append(conn)
        self.generic_visit(node)
    
    def visit_ClassDef(self, node: ast.ClassDef):
        class_id = f"{self.service_name}:{self.file_path}:{node.name}"
        class_node = CodeNode(
            id=class_id,
            name=node.name,
            type=NodeType.CLASS,
            file_path=self.file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            service=self.service_name,
            metadata={
                "bases": [self._get_name(base) for base in node.bases],
                "decorators": [self._get_decorator_name(d) for d in node.decorator_list]
            }
        )
        self.nodes.append(class_node)
        self.classes[node.name] = class_node
        
        for base in node.bases:
            base_name = self._get_name(base)
            if base_name:
                conn = CodeConnection(
                    source_id=class_id,
                    target_id=f"class:{base_name}",
                    type=ConnectionType.INHERITANCE,
                    metadata={"line": node.lineno}
                )
                self.connections.append(conn)
        
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class
    
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._process_function(node)
    
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._process_function(node, is_async=True)
    
    def _process_function(self, node, is_async=False):
        func_name = node.name
        if self.current_class:
            func_id = f"{self.service_name}:{self.file_path}:{self.current_class}.{func_name}"
        else:
            func_id = f"{self.service_name}:{self.file_path}:{func_name}"
        
        decorators = [self._get_decorator_name(d) for d in node.decorator_list]
        
        is_endpoint = False
        http_method = None
        route_path = None
        
        for dec in node.decorator_list:
            dec_name = self._get_decorator_name(dec)
            if dec_name in ["get", "post", "put", "delete", "patch", "websocket"]:
                is_endpoint = True
                http_method = dec_name.upper()
                if isinstance(dec, ast.Call) and dec.args:
                    route_path = self._get_string_value(dec.args[0])
            elif "router." in dec_name or "app." in dec_name:
                is_endpoint = True
                if "get" in dec_name.lower():
                    http_method = "GET"
                elif "post" in dec_name.lower():
                    http_method = "POST"
                elif "put" in dec_name.lower():
                    http_method = "PUT"
                elif "delete" in dec_name.lower():
                    http_method = "DELETE"
                elif "websocket" in dec_name.lower():
                    http_method = "WEBSOCKET"
        
        node_type = NodeType.API_ENDPOINT if is_endpoint else NodeType.FUNCTION
        
        func_node = CodeNode(
            id=func_id,
            name=func_name,
            type=node_type,
            file_path=self.file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            service=self.service_name,
            metadata={
                "is_async": is_async,
                "decorators": decorators,
                "http_method": http_method,
                "route_path": route_path,
                "args": [arg.arg for arg in node.args.args],
                "class": self.current_class
            }
        )
        self.nodes.append(func_node)
        self.functions[func_name] = func_node
        
        if is_endpoint:
            self.api_endpoints.append(func_node)
        
        self.generic_visit(node)
    
    def visit_Call(self, node: ast.Call):
        func_name = self._get_name(node.func)
        if func_name:
            if "httpx" in func_name or "requests" in func_name or "aiohttp" in func_name:
                conn = CodeConnection(
                    source_id=f"{self.service_name}:{self.file_path}",
                    target_id=f"http_call:{func_name}",
                    type=ConnectionType.HTTP_REQUEST,
                    metadata={"line": node.lineno}
                )
                self.connections.append(conn)
            
            elif "execute" in func_name or "query" in func_name or "session" in func_name.lower():
                conn = CodeConnection(
                    source_id=f"{self.service_name}:{self.file_path}",
                    target_id=f"database:{func_name}",
                    type=ConnectionType.DATABASE_QUERY,
                    metadata={"line": node.lineno}
                )
                self.connections.append(conn)
        
        self.generic_visit(node)
    
    def _get_name(self, node) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value = self._get_name(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        elif isinstance(node, ast.Subscript):
            return self._get_name(node.value)
        return None
    
    def _get_decorator_name(self, node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return self._get_name(node) or ""
        elif isinstance(node, ast.Call):
            return self._get_name(node.func) or ""
        return ""
    
    def _get_string_value(self, node) -> Optional[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None


class JavaScriptAnalyzer:
    """Lightweight regex-based JS/TS analyzer (no TS compiler required)."""

    _HTTP_METHODS = {"get", "post", "put", "delete", "patch"}

    def __init__(self, file_path: str, service_name: str):
        self.file_path = file_path
        self.service_name = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []
        self._http_node_ids: Set[str] = set()

    def analyze(self, source_code: str) -> tuple:
        self._extract_imports(source_code)
        self._extract_symbols(source_code)
        self._extract_http_calls(source_code)
        return self.nodes, self.connections

    def _line_of(self, source_code: str, idx: int) -> int:
        return source_code.count("\n", 0, idx) + 1

    def _normalize_js_string(self, raw: str) -> str:
        s = (raw or "").strip()
        if len(s) >= 2 and s[0] in {'"', "'"} and s[-1] == s[0]:
            s = s[1:-1]
        elif len(s) >= 2 and s[0] == "`" and s[-1] == "`":
            s = s[1:-1]
        s = re.sub(r"\$\{[^\}]+\}", "{...}", s)
        return s.strip()

    def _extract_imports(self, source_code: str) -> None:
        patterns = [
            re.compile(r"(^|\n)\s*import\s+(?:type\s+)?[\s\S]*?\s+from\s+['\"]([^'\"]+)['\"]", re.MULTILINE),
            re.compile(r"(^|\n)\s*import\s+['\"]([^'\"]+)['\"]", re.MULTILINE),
            re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"),
        ]

        for pat in patterns:
            for m in pat.finditer(source_code):
                module_name = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
                module_name = (module_name or "").strip()
                if not module_name:
                    continue

                line = self._line_of(source_code, m.start())
                self.connections.append(
                    CodeConnection(
                        source_id=f"{self.service_name}:{self.file_path}",
                        target_id=f"module:{module_name}",
                        type=ConnectionType.IMPORT,
                        metadata={"line": line, "language": "js"},
                    )
                )

    def _extract_symbols(self, source_code: str) -> None:
        class_pat = re.compile(r"(^|\n)\s*(?:export\s+)?class\s+([A-Za-z0-9_$]+)\b", re.MULTILINE)
        func_pat = re.compile(
            r"(^|\n)\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(",
            re.MULTILINE,
        )
        arrow_pat = re.compile(
            r"(^|\n)\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^\)]*\)|[A-Za-z0-9_$]+)?\s*=>",
            re.MULTILINE,
        )

        for m in class_pat.finditer(source_code):
            name = m.group(2)
            if not name:
                continue
            line = self._line_of(source_code, m.start())
            node_id = f"{self.service_name}:{self.file_path}:{name}"
            self.nodes.append(
                CodeNode(
                    id=node_id,
                    name=name,
                    type=NodeType.CLASS,
                    file_path=self.file_path,
                    line_start=line,
                    line_end=line,
                    service=self.service_name,
                    metadata={"language": "js"},
                )
            )

        for m in func_pat.finditer(source_code):
            name = m.group(2)
            if not name:
                continue
            line = self._line_of(source_code, m.start())
            node_id = f"{self.service_name}:{self.file_path}:{name}"
            self.nodes.append(
                CodeNode(
                    id=node_id,
                    name=name,
                    type=NodeType.FUNCTION,
                    file_path=self.file_path,
                    line_start=line,
                    line_end=line,
                    service=self.service_name,
                    metadata={"language": "js"},
                )
            )

        for m in arrow_pat.finditer(source_code):
            name = m.group(2)
            if not name:
                continue
            line = self._line_of(source_code, m.start())
            node_id = f"{self.service_name}:{self.file_path}:{name}"
            self.nodes.append(
                CodeNode(
                    id=node_id,
                    name=name,
                    type=NodeType.FUNCTION,
                    file_path=self.file_path,
                    line_start=line,
                    line_end=line,
                    service=self.service_name,
                    metadata={"language": "js", "style": "arrow"},
                )
            )

    def _extract_http_calls(self, source_code: str) -> None:
        def ensure_http_node(method: str, url: str, line: int) -> str:
            method_u = (method or "GET").upper()
            node_id = f"http:{method_u}:{url}"
            if node_id in self._http_node_ids:
                return node_id
            self._http_node_ids.add(node_id)
            self.nodes.append(
                CodeNode(
                    id=node_id,
                    name=f"{method_u} {url}",
                    type=NodeType.EXTERNAL_SERVICE,
                    file_path=self.file_path,
                    line_start=line,
                    line_end=line,
                    service=self.service_name,
                    metadata={"language": "js", "method": method_u, "url": url},
                )
            )
            return node_id

        fetch_pat = re.compile(r"\bfetch\s*\(\s*([`\"'][\s\S]*?[`\"'])", re.MULTILINE)
        axios_pat = re.compile(
            r"\baxios\s*\.\s*(get|post|put|delete|patch)\s*\(\s*([`\"'][\s\S]*?[`\"'])",
            re.IGNORECASE | re.MULTILINE,
        )
        client_pat = re.compile(
            r"\b([A-Za-z0-9_$]+)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*([`\"'][\s\S]*?[`\"'])",
            re.IGNORECASE | re.MULTILINE,
        )

        for m in fetch_pat.finditer(source_code):
            raw = m.group(1)
            url = self._normalize_js_string(raw)
            if not url:
                continue
            line = self._line_of(source_code, m.start())
            target_id = ensure_http_node("GET", url, line)
            self.connections.append(
                CodeConnection(
                    source_id=f"{self.service_name}:{self.file_path}",
                    target_id=target_id,
                    type=ConnectionType.HTTP_REQUEST,
                    metadata={"line": line, "method": "GET", "url": url, "language": "js", "client": "fetch"},
                )
            )

        for m in axios_pat.finditer(source_code):
            method = (m.group(1) or "").lower()
            raw = m.group(2)
            url = self._normalize_js_string(raw)
            if not url:
                continue
            line = self._line_of(source_code, m.start())
            target_id = ensure_http_node(method, url, line)
            self.connections.append(
                CodeConnection(
                    source_id=f"{self.service_name}:{self.file_path}",
                    target_id=target_id,
                    type=ConnectionType.HTTP_REQUEST,
                    metadata={"line": line, "method": method.upper(), "url": url, "language": "js", "client": "axios"},
                )
            )

        for m in client_pat.finditer(source_code):
            client = (m.group(1) or "").strip()
            method = (m.group(2) or "").lower()
            raw = m.group(3)
            if method not in self._HTTP_METHODS:
                continue
            url = self._normalize_js_string(raw)
            if not url:
                continue
            line = self._line_of(source_code, m.start())
            target_id = ensure_http_node(method, url, line)
            self.connections.append(
                CodeConnection(
                    source_id=f"{self.service_name}:{self.file_path}",
                    target_id=target_id,
                    type=ConnectionType.HTTP_REQUEST,
                    metadata={
                        "line": line,
                        "method": method.upper(),
                        "url": url,
                        "language": "js",
                        "client": client,
                    },
                )
            )


class CodebaseAnalyzer:
    """Main analyzer that processes entire codebase"""
    
    def __init__(self, root_path: str):
        self.root_path = Path(root_path)
        self.nodes: Dict[str, CodeNode] = {}
        self.connections: List[CodeConnection] = []
        self.services: Dict[str, List[str]] = {}
        self.pipelines: Dict[str, Pipeline] = {}
        self.file_contents: Dict[str, str] = {}
        
    def analyze(self) -> Dict[str, Any]:
        """Analyze entire codebase"""
        self._discover_services()
        self._analyze_files()
        self._resolve_connections()
        self._detect_dead_code()
        self._detect_pipelines()
        
        return self.get_graph_data()
    
    def _discover_services(self):
        """Find all services/folders in the codebase"""
        # First check if root has Python files directly
        root_py_files = list(self.root_path.glob("*.py"))
        if root_py_files:
            self.services["root"] = []
            service_node = CodeNode(
                id="service:root",
                name=self.root_path.name,
                type=NodeType.SERVICE,
                file_path=str(self.root_path),
                service="root"
            )
            self.nodes[service_node.id] = service_node
        
        # Then discover all subdirectories as potential services
        for item in self.root_path.iterdir():
            if item.is_dir() and not item.name.startswith('.') and item.name not in ['__pycache__', 'venv', 'node_modules', '.git', 'dist', 'build']:
                # Check if folder has any code files
                has_code = any(item.rglob("*.py")) or any(item.rglob("*.js")) or any(item.rglob("*.ts"))
                if has_code:
                    self.services[item.name] = []
                    
                    service_node = CodeNode(
                        id=f"service:{item.name}",
                        name=item.name,
                        type=NodeType.SERVICE,
                        file_path=str(item),
                        service=item.name
                    )
                    self.nodes[service_node.id] = service_node
    
    def _analyze_files(self):
        """Analyze all code files (Python, JS, TS)"""
        for service_name in self.services:
            if service_name == "root":
                service_path = self.root_path
            else:
                service_path = self.root_path / service_name
            
            # Analyze Python files
            for py_file in service_path.rglob("*.py"):
                if "__pycache__" in str(py_file) or "venv" in str(py_file):
                    continue
                
                rel_path = str(py_file.relative_to(self.root_path))
                if rel_path not in self.services[service_name]:
                    self.services[service_name].append(rel_path)
                
                file_node = CodeNode(
                    id=f"{service_name}:{rel_path}",
                    name=py_file.name,
                    type=NodeType.FILE,
                    file_path=rel_path,
                    service=service_name
                )
                self.nodes[file_node.id] = file_node
                
                conn = CodeConnection(
                    source_id=f"service:{service_name}",
                    target_id=file_node.id,
                    type=ConnectionType.IMPORT
                )
                self.connections.append(conn)
                
                try:
                    with open(py_file, 'r', encoding='utf-8') as f:
                        source = f.read()
                        self.file_contents[rel_path] = source
                    
                    analyzer = PythonAnalyzer(rel_path, service_name)
                    nodes, connections = analyzer.analyze(source)
                    
                    for node in nodes:
                        self.nodes[node.id] = node
                    self.connections.extend(connections)
                    
                except Exception as e:
                    file_node.metadata["error"] = str(e)

            # Analyze JS/TS files (frontend and any JS utilities)
            for pattern in ("*.js", "*.jsx", "*.ts", "*.tsx"):
                for js_file in service_path.rglob(pattern):
                    p = str(js_file)
                    if (
                        "/node_modules/" in p
                        or "/dist/" in p
                        or "/build/" in p
                        or "/.next/" in p
                        or "/.turbo/" in p
                        or "/.git/" in p
                    ):
                        continue
                    if "__pycache__" in p or "venv" in p:
                        continue

                    rel_path = str(js_file.relative_to(self.root_path))
                    if rel_path not in self.services[service_name]:
                        self.services[service_name].append(rel_path)

                    file_node_id = f"{service_name}:{rel_path}"
                    file_node = CodeNode(
                        id=file_node_id,
                        name=js_file.name,
                        type=NodeType.FILE,
                        file_path=rel_path,
                        service=service_name,
                        metadata={"language": "js"},
                    )
                    self.nodes[file_node.id] = file_node

                    self.connections.append(
                        CodeConnection(
                            source_id=f"service:{service_name}",
                            target_id=file_node.id,
                            type=ConnectionType.IMPORT,
                            metadata={"language": "js"},
                        )
                    )

                    try:
                        with open(js_file, "r", encoding="utf-8", errors="ignore") as f:
                            source = f.read()
                            self.file_contents[rel_path] = source

                        analyzer = JavaScriptAnalyzer(rel_path, service_name)
                        nodes, connections = analyzer.analyze(source)

                        for node in nodes:
                            self.nodes[node.id] = node
                        self.connections.extend(connections)
                    except Exception as e:
                        file_node.metadata["error"] = str(e)
    
    def _resolve_connections(self):
        """Resolve import connections to actual files and add file-to-function connections"""
        # Known external/stdlib packages that should NOT be resolved to internal files
        # These are terminal nodes, not broken edges
        EXTERNAL_PACKAGES = {
            # JWT libraries
            'jwt', 'pyjwt', 'jose', 'python-jose',
            # Web frameworks
            'fastapi', 'pydantic', 'pydantic_settings', 'starlette', 'uvicorn',
            # Database/ORM
            'sqlalchemy', 'alembic', 'databases', 'asyncpg', 'psycopg2',
            # Caching/messaging
            'redis', 'celery', 'kombu', 'amqp',
            # HTTP clients
            'httpx', 'aiohttp', 'requests', 'urllib', 'urllib3',
            # Python stdlib
            'typing', 'os', 'sys', 'json', 'datetime', 'pathlib', 'enum', 
            'dataclasses', 'asyncio', 'logging', 'uuid', 'hashlib', 'time',
            'collections', 'functools', 'itertools', 'copy', 'random', 'math',
            'base64', 'secrets', 'tempfile', 'shutil', 're', 'ast', 'inspect',
            'io', 'struct', 'pickle', 'csv', 'xml', 'html', 'email', 'mimetypes',
            'contextlib', 'abc', 'typing_extensions', 'warnings', 'traceback',
            'threading', 'multiprocessing', 'concurrent', 'queue', 'subprocess',
            'signal', 'socket', 'ssl', 'select', 'selectors',
            # Testing
            'pytest', 'unittest', 'mock', 'hypothesis',
            # Auth/crypto
            'stripe', 'bcrypt', 'passlib', 'cryptography', 'nacl',
            # Storage
            'minio', 'boto3', 'botocore', 's3fs',
            # Blockchain/web3
            'web3', 'eth_account', 'eth_utils', 'eth_abi',
            # AI/ML
            'openai', 'anthropic', 'google', 'groq', 'together', 'cohere',
            'transformers', 'torch', 'tensorflow', 'numpy', 'pandas', 'scipy',
            'sklearn', 'scikit-learn',
            # Misc
            'yaml', 'toml', 'dotenv', 'python-dotenv', 'click', 'typer',
            'rich', 'colorama', 'tqdm', 'pillow', 'PIL',
            # Internal relative imports that look like modules
            'config', 'models', 'routers', 'schemas', 'utils', 'db', 'main',
            'app', 'core', 'api', 'services', 'middleware', 'dependencies',
            # Additional stdlib
            '__future__', 'argparse', 'contextvars', 'decimal', 'difflib', 'fnmatch',
            'heapq', 'hmac', 'shlex', 'smtplib', 'sqlite3', 'statistics', 'string',
            'tracemalloc', 'webbrowser', 'zipfile', 'builtin',
            # External packages
            'opentelemetry', 'otel_middleware', 'wasmer', 'wasmtime', 'firebase_admin',
            'aioapns', 'aiofiles', 'aiosmtplib', 'croniter', 'docker', 'grpc', 'hvac',
            'jinja2', 'locust', 'psutil', 'pywebpush', 'sendgrid', 'sentry_sdk',
            'tiktoken', 'tree_sitter', 'tree_sitter_languages', 'watchdog', 'websockets',
            'setuptools', 'dateutil', 'sqlmodel', 'sse_starlette', 'mnemonic', 'responses',
            # Internal service modules (relative imports - all valid internal references)
            'ide_core', 'shared', 'resonant_node', 'autonomy_mode', 'wallet',
            'domain', 'facade', 'models_billing', 'negotiation', 'executor',
            'governance', 'domain_hasher', 'reconstruction', 'goal_generation',
            'proof_service', 'network_protocol', 'reputation_trust', 'agent_memory',
            'planner', 'exceptions', 'dsid', 'execution_gate', 'messaging',
            'agent_economy', 'models_autonomy', 'crypto_wallet', 'agent_executor',
            'agent_reasoning', 'agent_resources', 'agent_wallet', 'autonomous_daemon',
            'autonomous_queue', 'auto_startup', 'blockchain_integration', 'celery_app',
            'goal_engine', 'goal_pursuit', 'learning_loop', 'multi_agent_orchestrator',
            'policy_engine', 'repo_to_agent', 'self_improvement', 'self_trigger',
            'shared_state', 'survival_system', 'swarm_controller', 'system_watchdog',
            'tasks', 'temporal_planner', 'value_drift_monitor', 'websocket_streaming',
            'minio_client', 'audit', 'chain', 'consensus', 'cbor_codec', 'grpc_service',
            'p2p_network', 'smart_contract', 'smart_contracts', 'zero_knowledge',
            'routers_advanced', 'routers_autonomy', 'routers_billing', 'routers_execution',
            'routers_orchestration', 'routers_autonomous', 'routers_max_autonomy',
            'routers_ultimate', 'routers_distributed', 'routers_advanced_blockchain',
            'settings_routes', 'jwt_rotation', 'rate_limiter', 'auth_middleware',
            # Additional internal modules from broken connections analysis
            'action_router', 'admin_routes', 'adoption_strategy', 'advanced_refactor',
            'agent', 'agent_brain', 'agent_collaboration', 'agent_consciousness',
            'agent_engine', 'agent_lifecycle', 'agent_network', 'agent_personality',
            'agent_resilience', 'agent_router', 'agent_service', 'agents', 'analyzer',
            'anchors_routes', 'anomaly_detector', 'api_keys', 'api_versioning',
            'architecture_analyzer', 'audit_db', 'autonomous_error_correction',
            'autonomous_executor', 'autonomous_planner', 'backpressure', 'base',
            'batch_executor', 'benchmarking', 'billing_service', 'blockchain_anchor',
            'causal_reasoning', 'checkpoint_manager', 'circuit_breaker', 'client',
            'cluster_engine', 'code_context', 'code_executor', 'code_indexer',
            'code_parser', 'code_routes', 'codegen_engine', 'commercialization',
            'commit_manager', 'comparison_analyzer', 'compiler', 'compliance_audit',
            'compliance_routes', 'concurrency', 'context_injector', 'contracts',
            'controller', 'cost_tracker', 'credits', 'cross_chain', 'csrf',
            'debate_engine', 'debugger_service', 'delegation', 'deterministic_universe',
            'diff_engine', 'diff_generator', 'distributed_chain', 'docker_sandbox',
            'docker_tools', 'dual_class_blocks', 'dual_memory_engine', 'economic_model',
            'embedding_versioning', 'embeddings', 'emergent_intelligence',
            'emotional_normalizer', 'encrypted_payload', 'entropy', 'ethical_governance',
            'events', 'evidence_graph', 'federation_sovereignty', 'filesystem',
            'finance_routes', 'folder_generator', 'formal_invariants', 'full_autonomy',
            'gal', 'git_controller', 'git_routes', 'git_tools', 'gitops', 'gpc',
            'handlers', 'hash_sphere', 'hashsphere_api_v1', 'hashsphere_routes',
            'hybrid_memory_ranker', 'ide_service', 'idempotency', 'idempotency_redis',
            'identity', 'implementation_guide', 'infrastructure_deployment',
            'insight_seed_engine', 'intent_engine', 'interoperability', 'invariants',
            'invoices', 'janitor', 'knowledge_graph', 'latent_intent_predictor',
            'learning', 'legal_regulatory', 'llm', 'llm_router', 'logging_middleware',
            'long_tasks', 'loop_stabilizer', 'lsp_proxy', 'magnetic_pull', 'manager',
            'memory', 'memory_merge', 'merkle_audit', 'metering', 'metrics',
            'ml_routes', 'models_schedule', 'monitor', 'multi_agent', 'multi_ai_router',
            'multi_file_writer', 'multi_timeline_engine', 'mutation_plan',
            'narrative_continuity_engine', 'neural_gravity_engine', 'node_sandbox',
            'openapi_governance', 'orchestration_manager', 'orchestrator', 'ordering',
            'orgs_routes', 'parallel_agent_runtime', 'patch_applier', 'patch_artifact',
            'paths', 'payments', 'persistence', 'personality_dna', 'physics', 'pipeline',
            'pmi_layer', 'policies_routes', 'predictions_routes', 'preview',
            'proactive_behavior', 'probe_expectations', 'project_controller',
            'project_service', 'proof_persistence', 'protocol_roadmap', 'provider_manager',
            'providers', 'push_service', 'python_sandbox', 'quotas', 'rag_engine',
            'registry', 'request_signing', 'request_tracing', 'resolution',
            'resonance_hashing', 'resonant_chat', 'response_cache', 'resume_engine',
            'retrieval_config', 'retry', 'reverse_proxy', 'reviewer_agent', 'roles',
            'rollback_service', 'routers_full_autonomy', 'routers_teams', 'run_loop',
            'safety', 'safety_filters', 'safety_validator', 'sandbox', 'sandbox_executor',
            'sandboxes', 'scaling_performance', 'scheduler', 'security',
            'security_architecture', 'security_threat_model', 'seed_manager',
            'self_improving_agent', 'semantic_taxonomy', 'settings', 'sharding',
            'shell_executor', 'slo', 'standards_positioning', 'streaming',
            'structured_logging', 'subscriptions', 'supervisor_agent', 'task_catalog',
            'task_planner', 'task_queue', 'task_service', 'task_state',
            'technical_specification', 'temporal_thread_engine', 'terminal_routes',
            'terminal_service', 'test_tools', 'thought_branching', 'token_optimizer',
            'token_revocation', 'tokenization', 'tool_executor', 'tools', 'tracing',
            'tree_sitter_parser', 'usage_routes', 'usage_service', 'user_api_keys',
            'user_routes', 'vector_schema', 'verification_service', 'verifier',
            'waitlist', 'websocket', 'websocket_bridge', 'websocket_manager',
            'websocket_server', 'worker_agent', 'workers', 'workforce_simulation',
            'world_model', 'strategic_partnerships',
            # RARA service internal modules (all valid - relative imports)
            'snapshot_engine', 'capability_engine', 'invariant_engine', 'governance_engine',
            'mutation_executor', 'agent_coordinator', 'kill_switch', 'invariant_classes',
            'compliance', 'capability_enforcer',
            # Unix-only modules (valid in Docker/Linux)
            'fcntl',
            # Frontend draft modules (not production)
            'memory_worker', 'database',
            # Route modules (relative imports with .router suffix)
            'owner_auth', 'rara_routes'
        }
        
        # First, resolve module imports
        for conn in self.connections:
            if conn.type == ConnectionType.IMPORT:
                # JS/TS imports are not resolved to Python files. Mark them active and skip.
                if (conn.metadata or {}).get("language") == "js":
                    conn.status = ConnectionStatus.ACTIVE
                    continue
                target = conn.target_id
                if target.startswith("module:"):
                    module_name = target.replace("module:", "")
                    base_module = module_name.split('.')[0]
                    
                    # Skip external packages - do NOT resolve to internal files
                    if base_module.lower() in EXTERNAL_PACKAGES:
                        conn.status = ConnectionStatus.ACTIVE  # External import, not broken
                        continue
                    
                    # Only resolve if it looks like an internal import (has dots or matches service pattern)
                    resolved = False
                    for service_name, files in self.services.items():
                        for file_path in files:
                            # More strict matching: module path must match file structure
                            module_path = module_name.replace(".", "/")
                            if file_path.endswith(f"{module_path}.py") or f"/{module_path}/" in file_path:
                                conn.target_id = f"{service_name}:{file_path}"
                                conn.status = ConnectionStatus.ACTIVE
                                resolved = True
                                break
                        if resolved:
                            break
                    
                    if not resolved:
                        conn.status = ConnectionStatus.BROKEN
        
        # Add connections from files to their functions/classes
        for node_id, node in self.nodes.items():
            if node.type in [NodeType.FUNCTION, NodeType.CLASS, NodeType.API_ENDPOINT]:
                file_node_id = f"{node.service}:{node.file_path}"
                if file_node_id in self.nodes:
                    conn = CodeConnection(
                        source_id=file_node_id,
                        target_id=node_id,
                        type=ConnectionType.FUNCTION_CALL,
                        status=ConnectionStatus.ACTIVE
                    )
                    self.connections.append(conn)
    
    def _detect_dead_code(self):
        """Detect unused/dead code"""
        referenced_ids: Set[str] = set()
        
        for conn in self.connections:
            referenced_ids.add(conn.target_id)
        
        for node_id, node in self.nodes.items():
            if node.type == NodeType.FUNCTION:
                if node_id not in referenced_ids:
                    if not node.name.startswith('_') and node.name not in ['main', '__init__']:
                        node.metadata["status"] = "potentially_unused"
    
    def _detect_pipelines(self):
        """Auto-detect common pipelines"""
        pipeline_patterns = {
            "user_registration": {
                "keywords": ["register", "signup", "create_user"],
                "color": "#FF6B6B"
            },
            "user_login": {
                "keywords": ["login", "authenticate", "token", "jwt"],
                "color": "#4ECDC4"
            },
            "chat_flow": {
                "keywords": ["chat", "message", "conversation", "resonant"],
                "color": "#9B59B6"
            },
            "memory_pipeline": {
                "keywords": ["memory", "hash_sphere", "vector", "embedding"],
                "color": "#96CEB4"
            },
            "agent_execution": {
                "keywords": ["agent", "executor", "planner", "tool"],
                "color": "#2ECC71"
            },
            "billing_flow": {
                "keywords": ["billing", "subscription", "payment", "stripe", "quota"],
                "color": "#F1C40F"
            }
        }
        
        for pipeline_name, config in pipeline_patterns.items():
            pipeline = Pipeline(
                name=pipeline_name,
                description=f"Auto-detected {pipeline_name.replace('_', ' ')} pipeline",
                color=config["color"]
            )
            
            for node_id, node in self.nodes.items():
                name_lower = node.name.lower()
                file_lower = node.file_path.lower()
                
                for keyword in config["keywords"]:
                    if keyword in name_lower or keyword in file_lower:
                        pipeline.nodes.append(node_id)
                        break
            
            for conn in self.connections:
                if conn.source_id in pipeline.nodes or conn.target_id in pipeline.nodes:
                    conn_id = f"{conn.source_id}->{conn.target_id}"
                    pipeline.connections.append(conn_id)
            
            self.pipelines[pipeline_name] = pipeline
    
    def get_graph_data(self) -> Dict[str, Any]:
        """Get data formatted for 3D visualization"""
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "connections": [conn.to_dict() for conn in self.connections],
            "services": self.services,
            "pipelines": {name: p.to_dict() for name, p in self.pipelines.items()},
            "stats": {
                "total_files": sum(len(files) for files in self.services.values()),
                "total_services": len(self.services),
                "total_connections": len(self.connections),
                "total_functions": len([n for n in self.nodes.values() if n.type == NodeType.FUNCTION]),
                "total_endpoints": len([n for n in self.nodes.values() if n.type == NodeType.API_ENDPOINT]),
                "broken_connections": len([c for c in self.connections if c.status == ConnectionStatus.BROKEN])
            }
        }
    
    def filter_by_pipeline(self, pipeline_name: str) -> Dict[str, Any]:
        """Get graph data filtered to a specific pipeline"""
        if pipeline_name not in self.pipelines:
            return {"error": f"Pipeline {pipeline_name} not found"}
        
        pipeline = self.pipelines[pipeline_name]
        
        filtered_nodes = [
            self.nodes[node_id].to_dict() 
            for node_id in pipeline.nodes 
            if node_id in self.nodes
        ]
        
        filtered_connections = [
            conn.to_dict() 
            for conn in self.connections
            if conn.source_id in pipeline.nodes or conn.target_id in pipeline.nodes
        ]
        
        return {
            "pipeline": pipeline.to_dict(),
            "nodes": filtered_nodes,
            "connections": filtered_connections
        }
    
    def trace_execution(self, start_node_id: str, max_depth: int = 10) -> Dict[str, Any]:
        """Trace execution path from a starting node - follows both directions"""
        visited: Set[str] = set()
        trace_nodes: List[str] = []
        trace_connections: List[Dict] = []
        
        def trace_outgoing(node_id: str, depth: int):
            """Trace what this node calls/imports (outgoing)"""
            if depth > max_depth or node_id in visited:
                return
            visited.add(node_id)
            trace_nodes.append(node_id)
            
            for conn in self.connections:
                if conn.source_id == node_id:
                    trace_connections.append(conn.to_dict())
                    trace_outgoing(conn.target_id, depth + 1)
        
        def trace_incoming(node_id: str, depth: int):
            """Trace what calls/imports this node (incoming)"""
            if depth > max_depth or node_id in visited:
                return
            visited.add(node_id)
            trace_nodes.append(node_id)
            
            for conn in self.connections:
                if conn.target_id == node_id:
                    trace_connections.append(conn.to_dict())
                    trace_incoming(conn.source_id, depth + 1)
        
        # Trace both directions from start node
        trace_outgoing(start_node_id, 0)
        visited_outgoing = visited.copy()
        
        # Reset visited for incoming trace but keep the start node
        visited = {start_node_id}
        trace_incoming(start_node_id, 0)
        
        # Combine results
        all_nodes = list(set(trace_nodes))
        
        return {
            "start": start_node_id,
            "nodes": [self.nodes[nid].to_dict() for nid in all_nodes if nid in self.nodes],
            "connections": trace_connections,
            "outgoing_count": len(visited_outgoing) - 1,
            "incoming_count": len(visited) - 1
        }


def analyze_codebase(path: str) -> Dict[str, Any]:
    """Main entry point for codebase analysis"""
    analyzer = CodebaseAnalyzer(path)
    return analyzer.analyze()
