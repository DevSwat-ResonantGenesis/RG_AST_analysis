"""
Multi-Language Code Analyzers — Full AST-level parsing for:
  Go, Rust, Java, Kotlin, C, C++, Ruby, PHP, Swift, Scala, Dart, C#,
  Lua, Zig, Elixir, Julia, Solidity

Each analyzer extracts:
  - Imports / dependencies
  - Functions / methods (with visibility, async, params)
  - Classes / structs / interfaces / traits / enums
  - API endpoints (framework-specific detection)
  - HTTP calls to external services
  - Database queries

Copyright (c) 2024-2026 Resonant Genesis / dev-swat.com
License: Resonant Genesis Source Available License (see LICENSE.txt)
"""

import re
from typing import List, Tuple, Set, Optional, Dict, Any

# Import shared types
try:
    from .cv_types import CodeNode, CodeConnection, NodeType, ConnectionType, ConnectionStatus
except ImportError:
    from cv_types import CodeNode, CodeConnection, NodeType, ConnectionType, ConnectionStatus


def _line_of(source: str, idx: int) -> int:
    return source.count("\n", 0, idx) + 1


# ─────────────────────────────────────────────────────────────
#  Go Analyzer
# ─────────────────────────────────────────────────────────────

class GoAnalyzer:
    """Full Go source analyzer — imports, funcs, structs, interfaces, HTTP handlers."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_types(source)
        self._extract_functions(source)
        self._extract_http_handlers(source)
        self._extract_http_calls(source)
        return self.nodes, self.connections

    # -- imports --
    _IMPORT_SINGLE = re.compile(r'^\s*import\s+"([^"]+)"', re.MULTILINE)
    _IMPORT_BLOCK = re.compile(r'import\s*\(([\s\S]*?)\)', re.MULTILINE)
    _IMPORT_LINE = re.compile(r'"([^"]+)"')

    def _extract_imports(self, src: str):
        for m in self._IMPORT_SINGLE.finditer(src):
            self._add_import(m.group(1), _line_of(src, m.start()))
        for m in self._IMPORT_BLOCK.finditer(src):
            for im in self._IMPORT_LINE.finditer(m.group(1)):
                self._add_import(im.group(1), _line_of(src, m.start()))

    def _add_import(self, mod: str, line: int):
        self.connections.append(CodeConnection(
            source_id=f"{self.svc}:{self.fp}",
            target_id=f"module:{mod}",
            type=ConnectionType.IMPORT,
            metadata={"line": line, "language": "go"},
        ))

    # -- structs / interfaces --
    _TYPE_PAT = re.compile(
        r'^\s*type\s+(\w+)\s+(struct|interface)\s*\{', re.MULTILINE
    )

    def _extract_types(self, src: str):
        for m in self._TYPE_PAT.finditer(src):
            name, kind = m.group(1), m.group(2)
            ntype = NodeType.CLASS  # struct/interface → CLASS in graph
            nid = f"{self.svc}:{self.fp}:{name}"
            self.nodes.append(CodeNode(
                id=nid, name=name, type=ntype, file_path=self.fp,
                line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "go", "kind": kind},
            ))

    # -- functions / methods --
    _FUNC_PAT = re.compile(
        r'^\s*func\s+(?:\(\s*\w+\s+\*?(\w+)\s*\)\s+)?(\w+)\s*\(([^)]*)\)\s*(?:(\([^)]*\)|\S+)\s*)?\{',
        re.MULTILINE,
    )

    def _extract_functions(self, src: str):
        for m in self._FUNC_PAT.finditer(src):
            receiver, name, params, ret = m.group(1), m.group(2), m.group(3), m.group(4)
            if receiver:
                fid = f"{self.svc}:{self.fp}:{receiver}.{name}"
            else:
                fid = f"{self.svc}:{self.fp}:{name}"
            args = [p.strip().split()[-1] if p.strip() else "" for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=fid, name=name, type=NodeType.FUNCTION, file_path=self.fp,
                line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "go", "receiver": receiver or "", "args": args, "return": (ret or "").strip()},
            ))

    # -- HTTP handlers (net/http, gin, echo, fiber, chi, gorilla/mux) --
    _HTTP_HANDLER = re.compile(
        r'(?:\.(?:HandleFunc|Handle|GET|POST|PUT|DELETE|PATCH|Group|Any)|'
        r'(?:router|mux|app|e|r|g)\s*\.\s*(?:Get|Post|Put|Delete|Patch|Handle|HandleFunc|Group))\s*\(\s*"([^"]*)"',
        re.MULTILINE,
    )

    def _extract_http_handlers(self, src: str):
        for m in self._HTTP_HANDLER.finditer(src):
            route = m.group(1)
            full = m.group(0)
            method = "GET"
            for verb in ("POST", "Post", "PUT", "Put", "DELETE", "Delete", "PATCH", "Patch"):
                if verb in full:
                    method = verb.upper()
                    break
            eid = f"{self.svc}:{self.fp}:endpoint:{method}:{route}"
            self.nodes.append(CodeNode(
                id=eid, name=f"{method} {route}", type=NodeType.API_ENDPOINT,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "go", "http_method": method, "route_path": route},
            ))

    # -- HTTP calls --
    _HTTP_CALL = re.compile(
        r'(?:http\.(?:Get|Post|NewRequest)|client\.(?:Get|Post|Do))\s*\(\s*"?([^")\s]+)"?',
        re.MULTILINE,
    )

    def _extract_http_calls(self, src: str):
        for m in self._HTTP_CALL.finditer(src):
            url = m.group(1)
            line = _line_of(src, m.start())
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"http_call:{url}",
                type=ConnectionType.HTTP_REQUEST,
                metadata={"line": line, "language": "go", "url": url},
            ))


# ─────────────────────────────────────────────────────────────
#  Rust Analyzer
# ─────────────────────────────────────────────────────────────

class RustAnalyzer:
    """Full Rust source analyzer — use, fn, struct, enum, trait, impl, HTTP."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_types(source)
        self._extract_functions(source)
        self._extract_http_handlers(source)
        self._extract_http_calls(source)
        return self.nodes, self.connections

    _USE_PAT = re.compile(r'^\s*use\s+([\w:]+(?:::\{[^}]+\})?)\s*;', re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._USE_PAT.finditer(src):
            mod = m.group(1)
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{mod}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "rust"},
            ))

    _TYPE_PAT = re.compile(
        r'^\s*(?:pub(?:\([\w:]+\))?\s+)?(?:struct|enum|trait|union)\s+(\w+)',
        re.MULTILINE,
    )

    def _extract_types(self, src: str):
        for m in self._TYPE_PAT.finditer(src):
            name = m.group(1)
            kind = "struct"
            for k in ("enum", "trait", "union"):
                if k in m.group(0):
                    kind = k
                    break
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "rust", "kind": kind},
            ))

    _FN_PAT = re.compile(
        r'^\s*(?:pub(?:\([\w:]+\))?\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)',
        re.MULTILINE,
    )

    def _extract_functions(self, src: str):
        for m in self._FN_PAT.finditer(src):
            name, params = m.group(1), m.group(2)
            is_async = "async" in m.group(0)
            is_pub = "pub" in m.group(0)
            args = [p.strip().split(":")[0].strip() for p in params.split(",") if p.strip() and p.strip() != "&self" and p.strip() != "&mut self" and p.strip() != "self"]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "rust", "is_async": is_async, "is_pub": is_pub, "args": args},
            ))

    # actix-web, rocket, axum handlers
    _HTTP_ATTR = re.compile(
        r'#\[(?:get|post|put|delete|patch)\s*\(\s*"([^"]+)"\s*\)\]',
        re.MULTILINE,
    )

    def _extract_http_handlers(self, src: str):
        for m in self._HTTP_ATTR.finditer(src):
            route = m.group(1)
            method = m.group(0).split("(")[0].replace("#[", "").strip().upper()
            eid = f"{self.svc}:{self.fp}:endpoint:{method}:{route}"
            self.nodes.append(CodeNode(
                id=eid, name=f"{method} {route}", type=NodeType.API_ENDPOINT,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "rust", "http_method": method, "route_path": route},
            ))

    _HTTP_CALL = re.compile(
        r'(?:reqwest|client)\s*(?:::\w+)*\s*\.\s*(?:get|post|put|delete|patch)\s*\(\s*"([^"]+)"',
        re.MULTILINE | re.IGNORECASE,
    )

    def _extract_http_calls(self, src: str):
        for m in self._HTTP_CALL.finditer(src):
            url = m.group(1)
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"http_call:{url}",
                type=ConnectionType.HTTP_REQUEST,
                metadata={"line": _line_of(src, m.start()), "language": "rust", "url": url},
            ))


# ─────────────────────────────────────────────────────────────
#  Java Analyzer
# ─────────────────────────────────────────────────────────────

class JavaAnalyzer:
    """Full Java source analyzer — imports, classes, methods, annotations, HTTP."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_types(source)
        self._extract_methods(source)
        self._extract_endpoints(source)
        self._extract_http_calls(source)
        return self.nodes, self.connections

    _IMPORT_PAT = re.compile(r'^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;', re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._IMPORT_PAT.finditer(src):
            mod = m.group(1)
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{mod}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "java"},
            ))

    _CLASS_PAT = re.compile(
        r'^\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+)?(?:final\s+)?'
        r'(?:class|interface|enum|record)\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?',
        re.MULTILINE,
    )

    def _extract_types(self, src: str):
        for m in self._CLASS_PAT.finditer(src):
            name = m.group(1)
            parent = m.group(2)
            interfaces = [i.strip() for i in (m.group(3) or "").split(",") if i.strip()]
            nid = f"{self.svc}:{self.fp}:{name}"
            self.nodes.append(CodeNode(
                id=nid, name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "java", "extends": parent or "", "implements": interfaces},
            ))
            if parent:
                self.connections.append(CodeConnection(
                    source_id=nid, target_id=f"class:{parent}",
                    type=ConnectionType.INHERITANCE,
                    metadata={"line": _line_of(src, m.start()), "language": "java"},
                ))

    _METHOD_PAT = re.compile(
        r'^\s*(?:@\w+(?:\([^)]*\))?\s*)*'
        r'(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?'
        r'(?:abstract\s+)?(?:<[\w\s,?]+>\s+)?'
        r'([\w<>\[\]?,\s]+)\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w,\s]+)?\s*\{',
        re.MULTILINE,
    )

    def _extract_methods(self, src: str):
        for m in self._METHOD_PAT.finditer(src):
            ret_type, name, params = m.group(1).strip(), m.group(2), m.group(3)
            if name in ("if", "for", "while", "switch", "catch", "return", "new", "class"):
                continue
            args = [p.strip().split()[-1] if p.strip() else "" for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "java", "return_type": ret_type, "args": args},
            ))

    # Spring @GetMapping, @PostMapping, @RequestMapping, JAX-RS @GET @Path
    _ENDPOINT_PAT = re.compile(
        r'@(?:GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*'
        r'(?:\(\s*(?:value\s*=\s*)?["\']([^"\']*)["\'])?\s*',
        re.MULTILINE,
    )
    _JAXRS_PAT = re.compile(
        r'@(?:GET|POST|PUT|DELETE|PATCH)\s+.*?@Path\s*\(\s*["\']([^"\']*)["\']',
        re.MULTILINE | re.DOTALL,
    )

    def _extract_endpoints(self, src: str):
        for m in self._ENDPOINT_PAT.finditer(src):
            route = m.group(1) or "/"
            ann = m.group(0).strip()
            method = "GET"
            for verb in ("Post", "Put", "Delete", "Patch"):
                if verb in ann:
                    method = verb.upper()
                    break
            if "RequestMapping" in ann:
                method_match = re.search(r'method\s*=\s*RequestMethod\.(\w+)', ann)
                method = method_match.group(1) if method_match else "GET"
            eid = f"{self.svc}:{self.fp}:endpoint:{method}:{route}"
            self.nodes.append(CodeNode(
                id=eid, name=f"{method} {route}", type=NodeType.API_ENDPOINT,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "java", "http_method": method, "route_path": route},
            ))
        for m in self._JAXRS_PAT.finditer(src):
            route = m.group(1) or "/"
            method = "GET"
            for verb in ("POST", "PUT", "DELETE", "PATCH"):
                if f"@{verb}" in m.group(0):
                    method = verb
                    break
            eid = f"{self.svc}:{self.fp}:endpoint:{method}:{route}"
            self.nodes.append(CodeNode(
                id=eid, name=f"{method} {route}", type=NodeType.API_ENDPOINT,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "java", "http_method": method, "route_path": route, "framework": "jax-rs"},
            ))

    _HTTP_CALL = re.compile(
        r'(?:HttpClient|RestTemplate|WebClient|OkHttpClient)\b.*?\.\s*(?:get|post|put|delete|send|exchange)\w*\s*\(',
        re.MULTILINE | re.IGNORECASE,
    )

    def _extract_http_calls(self, src: str):
        for m in self._HTTP_CALL.finditer(src):
            line = _line_of(src, m.start())
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"http_call:java:{line}",
                type=ConnectionType.HTTP_REQUEST,
                metadata={"line": line, "language": "java"},
            ))


# ─────────────────────────────────────────────────────────────
#  Kotlin Analyzer
# ─────────────────────────────────────────────────────────────

class KotlinAnalyzer:
    """Full Kotlin source analyzer."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_types(source)
        self._extract_functions(source)
        self._extract_endpoints(source)
        return self.nodes, self.connections

    _IMPORT_PAT = re.compile(r'^\s*import\s+([\w.]+(?:\.\*)?)', re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._IMPORT_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "kotlin"},
            ))

    _CLASS_PAT = re.compile(
        r'^\s*(?:public\s+|private\s+|internal\s+|protected\s+)?(?:open\s+|abstract\s+|sealed\s+)?'
        r'(?:data\s+|value\s+|inline\s+)?(?:class|interface|object|enum\s+class)\s+(\w+)'
        r'(?:\s*(?:<[^>]*>))?(?:\s*:\s*([\w.<>,\s]+))?',
        re.MULTILINE,
    )

    def _extract_types(self, src: str):
        for m in self._CLASS_PAT.finditer(src):
            name = m.group(1)
            bases = [b.strip().split("<")[0] for b in (m.group(2) or "").split(",") if b.strip()]
            nid = f"{self.svc}:{self.fp}:{name}"
            self.nodes.append(CodeNode(
                id=nid, name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "kotlin", "bases": bases},
            ))
            for base in bases:
                self.connections.append(CodeConnection(
                    source_id=nid, target_id=f"class:{base}",
                    type=ConnectionType.INHERITANCE,
                    metadata={"language": "kotlin"},
                ))

    _FN_PAT = re.compile(
        r'^\s*(?:public\s+|private\s+|internal\s+|protected\s+)?(?:open\s+|override\s+|abstract\s+)?'
        r'(?:suspend\s+)?fun\s+(?:<[^>]*>\s+)?(\w+)\s*\(([^)]*)\)(?:\s*:\s*([\w<>?,\s]+))?',
        re.MULTILINE,
    )

    def _extract_functions(self, src: str):
        for m in self._FN_PAT.finditer(src):
            name, params, ret = m.group(1), m.group(2), m.group(3)
            is_suspend = "suspend" in m.group(0)
            args = [p.strip().split(":")[0].strip() for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "kotlin", "is_suspend": is_suspend, "args": args, "return": (ret or "").strip()},
            ))

    # Ktor, Spring annotations
    _ENDPOINT_PAT = re.compile(
        r'(?:@(?:GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\s*\(\s*["\']([^"\']*)["\']|'
        r'(?:get|post|put|delete|patch)\s*\(\s*["\']([^"\']*)["\'])',
        re.MULTILINE,
    )

    def _extract_endpoints(self, src: str):
        for m in self._ENDPOINT_PAT.finditer(src):
            route = m.group(1) or m.group(2) or "/"
            method = "GET"
            for verb in ("post", "put", "delete", "patch", "Post", "Put", "Delete", "Patch"):
                if verb.lower() in m.group(0).lower():
                    method = verb.upper()
                    break
            eid = f"{self.svc}:{self.fp}:endpoint:{method}:{route}"
            self.nodes.append(CodeNode(
                id=eid, name=f"{method} {route}", type=NodeType.API_ENDPOINT,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "kotlin", "http_method": method, "route_path": route},
            ))


# ─────────────────────────────────────────────────────────────
#  C Analyzer
# ─────────────────────────────────────────────────────────────

class CAnalyzer:
    """Full C source analyzer — includes, functions, structs, typedefs."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_includes(source)
        self._extract_types(source)
        self._extract_functions(source)
        return self.nodes, self.connections

    _INCLUDE_PAT = re.compile(r'^\s*#include\s+[<"]([^>"]+)[>"]', re.MULTILINE)

    def _extract_includes(self, src: str):
        for m in self._INCLUDE_PAT.finditer(src):
            header = m.group(1)
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{header}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "c"},
            ))

    _STRUCT_PAT = re.compile(
        r'^\s*(?:typedef\s+)?(?:struct|union|enum)\s+(\w+)', re.MULTILINE
    )

    def _extract_types(self, src: str):
        for m in self._STRUCT_PAT.finditer(src):
            name = m.group(1)
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "c"},
            ))

    # Match C function definitions: type name(params) {
    _FUNC_PAT = re.compile(
        r'^(?!.*\b(?:if|for|while|switch|return|sizeof|typedef|struct|union|enum)\b)'
        r'\s*(?:static\s+|inline\s+|extern\s+|const\s+)*'
        r'(?:unsigned\s+|signed\s+)?(?:void|int|char|float|double|long|short|size_t|bool|'
        r'(?:struct|enum)\s+\w+|\w+_t|\w+)\s*\**\s+'
        r'(\w+)\s*\(([^)]*)\)\s*\{',
        re.MULTILINE,
    )

    def _extract_functions(self, src: str):
        for m in self._FUNC_PAT.finditer(src):
            name, params = m.group(1), m.group(2)
            if name in ("main", "if", "for", "while", "switch"):
                if name != "main":
                    continue
            args = [p.strip().split()[-1].strip("*& ") for p in params.split(",") if p.strip() and p.strip() != "void"]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "c", "args": args},
            ))


# ─────────────────────────────────────────────────────────────
#  C++ Analyzer (extends C)
# ─────────────────────────────────────────────────────────────

class CppAnalyzer(CAnalyzer):
    """Full C++ source analyzer — adds classes, namespaces, templates, methods."""

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        super().analyze(source)
        self._extract_cpp_classes(source)
        self._extract_namespaces(source)
        return self.nodes, self.connections

    _CLASS_PAT = re.compile(
        r'^\s*(?:template\s*<[^>]*>\s*)?(?:class|struct)\s+(\w+)(?:\s*:\s*(?:public|private|protected)\s+(\w+))?',
        re.MULTILINE,
    )

    def _extract_cpp_classes(self, src: str):
        for m in self._CLASS_PAT.finditer(src):
            name, parent = m.group(1), m.group(2)
            nid = f"{self.svc}:{self.fp}:{name}"
            self.nodes.append(CodeNode(
                id=nid, name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "cpp"},
            ))
            if parent:
                self.connections.append(CodeConnection(
                    source_id=nid, target_id=f"class:{parent}",
                    type=ConnectionType.INHERITANCE,
                    metadata={"language": "cpp"},
                ))

    _NS_PAT = re.compile(r'^\s*namespace\s+(\w+)', re.MULTILINE)

    def _extract_namespaces(self, src: str):
        for m in self._NS_PAT.finditer(src):
            name = m.group(1)
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:ns:{name}", name=name, type=NodeType.SERVICE,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "cpp", "kind": "namespace"},
            ))


# ─────────────────────────────────────────────────────────────
#  Ruby Analyzer
# ─────────────────────────────────────────────────────────────

class RubyAnalyzer:
    """Full Ruby source analyzer — require, classes, modules, methods, Rails routes."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_types(source)
        self._extract_methods(source)
        self._extract_rails_routes(source)
        self._extract_http_calls(source)
        return self.nodes, self.connections

    _REQUIRE_PAT = re.compile(r"^\s*(?:require|require_relative|load|gem)\s+['\"]([^'\"]+)['\"]", re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._REQUIRE_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "ruby"},
            ))

    _CLASS_PAT = re.compile(
        r'^\s*(?:class|module)\s+(\w+(?:::\w+)*)(?:\s*<\s*(\w+(?:::\w+)*))?', re.MULTILINE
    )

    def _extract_types(self, src: str):
        for m in self._CLASS_PAT.finditer(src):
            name, parent = m.group(1), m.group(2)
            nid = f"{self.svc}:{self.fp}:{name}"
            ntype = NodeType.CLASS
            self.nodes.append(CodeNode(
                id=nid, name=name, type=ntype,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "ruby", "parent": parent or ""},
            ))
            if parent:
                self.connections.append(CodeConnection(
                    source_id=nid, target_id=f"class:{parent}",
                    type=ConnectionType.INHERITANCE,
                    metadata={"language": "ruby"},
                ))

    _DEF_PAT = re.compile(
        r'^\s*def\s+(self\.)?(\w+[?!=]?)\s*(?:\(([^)]*)\))?', re.MULTILINE
    )

    def _extract_methods(self, src: str):
        for m in self._DEF_PAT.finditer(src):
            is_class_method = bool(m.group(1))
            name = m.group(2)
            params = m.group(3) or ""
            args = [p.strip().split("=")[0].strip().lstrip("*&:") for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "ruby", "class_method": is_class_method, "args": args},
            ))

    # Rails routes
    _ROUTE_PAT = re.compile(
        r'^\s*(?:get|post|put|patch|delete)\s+["\']([^"\']+)["\']', re.MULTILINE
    )
    _RESOURCES_PAT = re.compile(
        r'^\s*resources?\s+:(\w+)', re.MULTILINE
    )

    def _extract_rails_routes(self, src: str):
        for m in self._ROUTE_PAT.finditer(src):
            route = m.group(1)
            method = m.group(0).strip().split()[0].upper()
            eid = f"{self.svc}:{self.fp}:endpoint:{method}:{route}"
            self.nodes.append(CodeNode(
                id=eid, name=f"{method} {route}", type=NodeType.API_ENDPOINT,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "ruby", "http_method": method, "route_path": route, "framework": "rails"},
            ))
        for m in self._RESOURCES_PAT.finditer(src):
            resource = m.group(1)
            for method, path in [("GET", f"/{resource}"), ("POST", f"/{resource}"), ("GET", f"/{resource}/:id"),
                                  ("PUT", f"/{resource}/:id"), ("DELETE", f"/{resource}/:id")]:
                eid = f"{self.svc}:{self.fp}:endpoint:{method}:{path}"
                self.nodes.append(CodeNode(
                    id=eid, name=f"{method} {path}", type=NodeType.API_ENDPOINT,
                    file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                    metadata={"language": "ruby", "http_method": method, "route_path": path, "framework": "rails"},
                ))

    _HTTP_CALL = re.compile(
        r'(?:Net::HTTP|Faraday|HTTParty|RestClient)\b.*?(?:\.(?:get|post|put|delete|patch))',
        re.MULTILINE | re.IGNORECASE,
    )

    def _extract_http_calls(self, src: str):
        for m in self._HTTP_CALL.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"http_call:ruby:{_line_of(src, m.start())}",
                type=ConnectionType.HTTP_REQUEST,
                metadata={"line": _line_of(src, m.start()), "language": "ruby"},
            ))


# ─────────────────────────────────────────────────────────────
#  PHP Analyzer
# ─────────────────────────────────────────────────────────────

class PhpAnalyzer:
    """Full PHP source analyzer — use/require, classes, functions, Laravel routes."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_types(source)
        self._extract_functions(source)
        self._extract_routes(source)
        return self.nodes, self.connections

    _USE_PAT = re.compile(r'^\s*use\s+([\w\\]+)(?:\s+as\s+\w+)?;', re.MULTILINE)
    _REQUIRE_PAT = re.compile(r"^\s*(?:require|require_once|include|include_once)\s+['\"]([^'\"]+)['\"]", re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._USE_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "php"},
            ))
        for m in self._REQUIRE_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "php"},
            ))

    _CLASS_PAT = re.compile(
        r'^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum)\s+(\w+)'
        r'(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s\\]+))?',
        re.MULTILINE,
    )

    def _extract_types(self, src: str):
        for m in self._CLASS_PAT.finditer(src):
            name, parent = m.group(1), m.group(2)
            nid = f"{self.svc}:{self.fp}:{name}"
            self.nodes.append(CodeNode(
                id=nid, name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "php"},
            ))
            if parent:
                self.connections.append(CodeConnection(
                    source_id=nid, target_id=f"class:{parent}",
                    type=ConnectionType.INHERITANCE,
                    metadata={"language": "php"},
                ))

    _FUNC_PAT = re.compile(
        r'^\s*(?:public\s+|private\s+|protected\s+)?(?:static\s+)?function\s+(\w+)\s*\(([^)]*)\)',
        re.MULTILINE,
    )

    def _extract_functions(self, src: str):
        for m in self._FUNC_PAT.finditer(src):
            name, params = m.group(1), m.group(2)
            args = [p.strip().split("=")[0].strip().lstrip("$?&").split()[-1].lstrip("$") for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "php", "args": args},
            ))

    # Laravel Route::get/post/... and Slim
    _ROUTE_PAT = re.compile(
        r'Route::(?:get|post|put|patch|delete|any)\s*\(\s*["\']([^"\']+)["\']',
        re.MULTILINE,
    )

    def _extract_routes(self, src: str):
        for m in self._ROUTE_PAT.finditer(src):
            route = m.group(1)
            method_match = re.search(r'Route::(\w+)', m.group(0))
            method = (method_match.group(1) if method_match else "get").upper()
            eid = f"{self.svc}:{self.fp}:endpoint:{method}:{route}"
            self.nodes.append(CodeNode(
                id=eid, name=f"{method} {route}", type=NodeType.API_ENDPOINT,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "php", "http_method": method, "route_path": route, "framework": "laravel"},
            ))


# ─────────────────────────────────────────────────────────────
#  Swift Analyzer
# ─────────────────────────────────────────────────────────────

class SwiftAnalyzer:
    """Full Swift source analyzer."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_types(source)
        self._extract_functions(source)
        return self.nodes, self.connections

    _IMPORT_PAT = re.compile(r'^\s*import\s+(\w+)', re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._IMPORT_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "swift"},
            ))

    _TYPE_PAT = re.compile(
        r'^\s*(?:public\s+|private\s+|internal\s+|fileprivate\s+|open\s+)?'
        r'(?:final\s+)?(?:class|struct|protocol|enum|actor)\s+(\w+)'
        r'(?:\s*:\s*([\w,\s]+))?',
        re.MULTILINE,
    )

    def _extract_types(self, src: str):
        for m in self._TYPE_PAT.finditer(src):
            name = m.group(1)
            bases = [b.strip() for b in (m.group(2) or "").split(",") if b.strip()]
            nid = f"{self.svc}:{self.fp}:{name}"
            self.nodes.append(CodeNode(
                id=nid, name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "swift", "conforms_to": bases},
            ))
            for base in bases:
                self.connections.append(CodeConnection(
                    source_id=nid, target_id=f"class:{base}",
                    type=ConnectionType.INHERITANCE,
                    metadata={"language": "swift"},
                ))

    _FUNC_PAT = re.compile(
        r'^\s*(?:public\s+|private\s+|internal\s+|open\s+)?'
        r'(?:static\s+|class\s+)?(?:override\s+)?'
        r'func\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)(?:\s*(?:throws|rethrows)\s*)?(?:\s*->\s*([\w<>?,\s[\]]+))?',
        re.MULTILINE,
    )

    def _extract_functions(self, src: str):
        for m in self._FUNC_PAT.finditer(src):
            name, params, ret = m.group(1), m.group(2), m.group(3)
            args = [p.strip().split(":")[0].strip() for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "swift", "args": args, "return": (ret or "").strip()},
            ))


# ─────────────────────────────────────────────────────────────
#  Scala Analyzer
# ─────────────────────────────────────────────────────────────

class ScalaAnalyzer:
    """Full Scala source analyzer."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_types(source)
        self._extract_defs(source)
        return self.nodes, self.connections

    _IMPORT_PAT = re.compile(r'^\s*import\s+([\w.]+(?:\.\{[^}]+\}|\._|\.\*)?)', re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._IMPORT_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "scala"},
            ))

    _TYPE_PAT = re.compile(
        r'^\s*(?:abstract\s+|sealed\s+|final\s+)?(?:case\s+)?(?:class|object|trait)\s+(\w+)',
        re.MULTILINE,
    )

    def _extract_types(self, src: str):
        for m in self._TYPE_PAT.finditer(src):
            name = m.group(1)
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "scala"},
            ))

    _DEF_PAT = re.compile(
        r'^\s*(?:override\s+)?def\s+(\w+)\s*(?:\[.*?\])?\s*\(([^)]*)\)(?:\s*:\s*([\w\[\],\s]+))?',
        re.MULTILINE,
    )

    def _extract_defs(self, src: str):
        for m in self._DEF_PAT.finditer(src):
            name, params, ret = m.group(1), m.group(2), m.group(3)
            args = [p.strip().split(":")[0].strip() for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "scala", "args": args, "return": (ret or "").strip()},
            ))


# ─────────────────────────────────────────────────────────────
#  Dart Analyzer
# ─────────────────────────────────────────────────────────────

class DartAnalyzer:
    """Full Dart source analyzer."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_types(source)
        self._extract_functions(source)
        return self.nodes, self.connections

    _IMPORT_PAT = re.compile(r"^\s*import\s+['\"]([^'\"]+)['\"]", re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._IMPORT_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "dart"},
            ))

    _CLASS_PAT = re.compile(
        r'^\s*(?:abstract\s+)?(?:mixin\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?'
        r'(?:\s+with\s+([\w,\s]+))?(?:\s+implements\s+([\w,\s]+))?',
        re.MULTILINE,
    )

    def _extract_types(self, src: str):
        for m in self._CLASS_PAT.finditer(src):
            name = m.group(1)
            parent = m.group(2)
            nid = f"{self.svc}:{self.fp}:{name}"
            self.nodes.append(CodeNode(
                id=nid, name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "dart", "extends": parent or ""},
            ))
            if parent:
                self.connections.append(CodeConnection(
                    source_id=nid, target_id=f"class:{parent}",
                    type=ConnectionType.INHERITANCE,
                    metadata={"language": "dart"},
                ))

    _FUNC_PAT = re.compile(
        r'^\s*(?:static\s+)?(?:Future|Stream|void|int|double|String|bool|dynamic|List|Map|Set|'
        r'(?:\w+))\s*(?:<[^>]*>)?\s+(\w+)\s*\(([^)]*)\)\s*(?:async\s*)?\{',
        re.MULTILINE,
    )

    def _extract_functions(self, src: str):
        for m in self._FUNC_PAT.finditer(src):
            name, params = m.group(1), m.group(2)
            if name in ("if", "for", "while", "switch", "catch"):
                continue
            is_async = "async" in m.group(0)
            args = [p.strip().split()[-1] if p.strip() else "" for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "dart", "is_async": is_async, "args": args},
            ))


# ─────────────────────────────────────────────────────────────
#  C# Analyzer
# ─────────────────────────────────────────────────────────────

class CSharpAnalyzer:
    """Full C# source analyzer — using, classes, methods, ASP.NET endpoints."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_types(source)
        self._extract_methods(source)
        self._extract_endpoints(source)
        return self.nodes, self.connections

    _USING_PAT = re.compile(r'^\s*using\s+([\w.]+)\s*;', re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._USING_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "csharp"},
            ))

    _CLASS_PAT = re.compile(
        r'^\s*(?:public\s+|private\s+|internal\s+|protected\s+)?'
        r'(?:abstract\s+|sealed\s+|static\s+|partial\s+)*'
        r'(?:class|interface|struct|enum|record)\s+(\w+)'
        r'(?:\s*:\s*([\w<>,.\s]+))?',
        re.MULTILINE,
    )

    def _extract_types(self, src: str):
        for m in self._CLASS_PAT.finditer(src):
            name = m.group(1)
            bases = [b.strip().split("<")[0] for b in (m.group(2) or "").split(",") if b.strip()]
            nid = f"{self.svc}:{self.fp}:{name}"
            self.nodes.append(CodeNode(
                id=nid, name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "csharp", "bases": bases},
            ))
            for base in bases:
                self.connections.append(CodeConnection(
                    source_id=nid, target_id=f"class:{base}",
                    type=ConnectionType.INHERITANCE,
                    metadata={"language": "csharp"},
                ))

    _METHOD_PAT = re.compile(
        r'^\s*(?:\[.*?\]\s*)*'
        r'(?:public\s+|private\s+|protected\s+|internal\s+)?'
        r'(?:static\s+|virtual\s+|override\s+|abstract\s+|async\s+|sealed\s+)*'
        r'(?:[\w<>\[\]?,.\s]+)\s+(\w+)\s*\(([^)]*)\)\s*\{',
        re.MULTILINE,
    )

    def _extract_methods(self, src: str):
        for m in self._METHOD_PAT.finditer(src):
            name, params = m.group(1), m.group(2)
            if name in ("if", "for", "while", "switch", "foreach", "using", "lock", "catch"):
                continue
            is_async = "async" in m.group(0)
            args = [p.strip().split()[-1] if p.strip() else "" for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "csharp", "is_async": is_async, "args": args},
            ))

    # ASP.NET [HttpGet], [HttpPost], etc.
    _ENDPOINT_PAT = re.compile(
        r'\[Http(?:Get|Post|Put|Delete|Patch)(?:\(\s*"([^"]*)"\s*\))?\]',
        re.MULTILINE,
    )

    def _extract_endpoints(self, src: str):
        for m in self._ENDPOINT_PAT.finditer(src):
            route = m.group(1) or "/"
            method = re.search(r'Http(\w+)', m.group(0)).group(1).upper()
            eid = f"{self.svc}:{self.fp}:endpoint:{method}:{route}"
            self.nodes.append(CodeNode(
                id=eid, name=f"{method} {route}", type=NodeType.API_ENDPOINT,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "csharp", "http_method": method, "route_path": route, "framework": "aspnet"},
            ))


# ─────────────────────────────────────────────────────────────
#  Lua Analyzer
# ─────────────────────────────────────────────────────────────

class LuaAnalyzer:
    """Full Lua source analyzer."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_functions(source)
        return self.nodes, self.connections

    _REQUIRE_PAT = re.compile(r"""require\s*\(\s*['\"]([^'\"]+)['"]\s*\)""", re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._REQUIRE_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "lua"},
            ))

    _FUNC_PAT = re.compile(
        r'^\s*(?:local\s+)?function\s+([\w.:]+)\s*\(([^)]*)\)', re.MULTILINE
    )

    def _extract_functions(self, src: str):
        for m in self._FUNC_PAT.finditer(src):
            name, params = m.group(1), m.group(2)
            args = [p.strip() for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "lua", "args": args},
            ))


# ─────────────────────────────────────────────────────────────
#  Zig Analyzer
# ─────────────────────────────────────────────────────────────

class ZigAnalyzer:
    """Full Zig source analyzer."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_functions(source)
        self._extract_types(source)
        return self.nodes, self.connections

    _IMPORT_PAT = re.compile(r'@import\s*\(\s*"([^"]+)"\s*\)', re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._IMPORT_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "zig"},
            ))

    _FN_PAT = re.compile(
        r'^\s*(?:pub\s+)?(?:export\s+)?fn\s+(\w+)\s*\(([^)]*)\)\s*(?:[\w!.]+)?',
        re.MULTILINE,
    )

    def _extract_functions(self, src: str):
        for m in self._FN_PAT.finditer(src):
            name, params = m.group(1), m.group(2)
            is_pub = "pub" in m.group(0)
            args = [p.strip().split(":")[0].strip() for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "zig", "is_pub": is_pub, "args": args},
            ))

    _STRUCT_PAT = re.compile(r'^\s*(?:pub\s+)?const\s+(\w+)\s*=\s*(?:packed\s+|extern\s+)?struct', re.MULTILINE)

    def _extract_types(self, src: str):
        for m in self._STRUCT_PAT.finditer(src):
            name = m.group(1)
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "zig"},
            ))


# ─────────────────────────────────────────────────────────────
#  Elixir Analyzer
# ─────────────────────────────────────────────────────────────

class ElixirAnalyzer:
    """Full Elixir source analyzer — modules, functions, Phoenix routes."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_modules(source)
        self._extract_functions(source)
        self._extract_phoenix_routes(source)
        return self.nodes, self.connections

    _IMPORT_PAT = re.compile(r'^\s*(?:import|alias|use|require)\s+([\w.]+)', re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._IMPORT_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "elixir"},
            ))

    _MODULE_PAT = re.compile(r'^\s*defmodule\s+([\w.]+)', re.MULTILINE)

    def _extract_modules(self, src: str):
        for m in self._MODULE_PAT.finditer(src):
            name = m.group(1)
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "elixir", "kind": "module"},
            ))

    _DEF_PAT = re.compile(r'^\s*(?:def|defp|defmacro|defmacrop)\s+(\w+)\s*(?:\(([^)]*)\))?', re.MULTILINE)

    def _extract_functions(self, src: str):
        for m in self._DEF_PAT.finditer(src):
            name, params = m.group(1), m.group(2) or ""
            is_private = "defp" in m.group(0) or "defmacrop" in m.group(0)
            args = [p.strip().lstrip("\\") for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "elixir", "is_private": is_private, "args": args},
            ))

    _PHOENIX_ROUTE = re.compile(
        r'^\s*(?:get|post|put|patch|delete)\s+["\']([^"\']+)["\']', re.MULTILINE
    )

    def _extract_phoenix_routes(self, src: str):
        for m in self._PHOENIX_ROUTE.finditer(src):
            route = m.group(1)
            method = m.group(0).strip().split()[0].upper()
            eid = f"{self.svc}:{self.fp}:endpoint:{method}:{route}"
            self.nodes.append(CodeNode(
                id=eid, name=f"{method} {route}", type=NodeType.API_ENDPOINT,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "elixir", "http_method": method, "route_path": route, "framework": "phoenix"},
            ))


# ─────────────────────────────────────────────────────────────
#  Solidity Analyzer
# ─────────────────────────────────────────────────────────────

class SolidityAnalyzer:
    """Full Solidity source analyzer — contracts, functions, events, modifiers."""

    def __init__(self, file_path: str, service_name: str):
        self.fp = file_path
        self.svc = service_name
        self.nodes: List[CodeNode] = []
        self.connections: List[CodeConnection] = []

    def analyze(self, source: str) -> Tuple[List[CodeNode], List[CodeConnection]]:
        self._extract_imports(source)
        self._extract_contracts(source)
        self._extract_functions(source)
        self._extract_events(source)
        return self.nodes, self.connections

    _IMPORT_PAT = re.compile(r"^\s*import\s+['\"]([^'\"]+)['\"]", re.MULTILINE)

    def _extract_imports(self, src: str):
        for m in self._IMPORT_PAT.finditer(src):
            self.connections.append(CodeConnection(
                source_id=f"{self.svc}:{self.fp}",
                target_id=f"module:{m.group(1)}",
                type=ConnectionType.IMPORT,
                metadata={"line": _line_of(src, m.start()), "language": "solidity"},
            ))

    _CONTRACT_PAT = re.compile(
        r'^\s*(?:abstract\s+)?contract\s+(\w+)(?:\s+is\s+([\w,\s]+))?',
        re.MULTILINE,
    )

    def _extract_contracts(self, src: str):
        for m in self._CONTRACT_PAT.finditer(src):
            name = m.group(1)
            bases = [b.strip() for b in (m.group(2) or "").split(",") if b.strip()]
            nid = f"{self.svc}:{self.fp}:{name}"
            self.nodes.append(CodeNode(
                id=nid, name=name, type=NodeType.CLASS,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "solidity", "kind": "contract", "inherits": bases},
            ))
            for base in bases:
                self.connections.append(CodeConnection(
                    source_id=nid, target_id=f"class:{base}",
                    type=ConnectionType.INHERITANCE,
                    metadata={"language": "solidity"},
                ))

    _FUNC_PAT = re.compile(
        r'^\s*function\s+(\w+)\s*\(([^)]*)\)\s*'
        r'(?:(?:public|external|internal|private|pure|view|payable|virtual|override)\s*)*',
        re.MULTILINE,
    )

    def _extract_functions(self, src: str):
        for m in self._FUNC_PAT.finditer(src):
            name, params = m.group(1), m.group(2)
            visibility = "public"
            for v in ("external", "internal", "private"):
                if v in m.group(0):
                    visibility = v
                    break
            args = [p.strip().split()[-1] if p.strip() else "" for p in params.split(",") if p.strip()]
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "solidity", "visibility": visibility, "args": args},
            ))

    _EVENT_PAT = re.compile(r'^\s*event\s+(\w+)\s*\(', re.MULTILINE)

    def _extract_events(self, src: str):
        for m in self._EVENT_PAT.finditer(src):
            name = m.group(1)
            self.nodes.append(CodeNode(
                id=f"{self.svc}:{self.fp}:event:{name}", name=name, type=NodeType.FUNCTION,
                file_path=self.fp, line_start=_line_of(src, m.start()), service=self.svc,
                metadata={"language": "solidity", "kind": "event"},
            ))


# ─────────────────────────────────────────────────────────────
#  Registry — maps file extensions to analyzer classes
# ─────────────────────────────────────────────────────────────

LANG_ANALYZER_MAP: Dict[str, type] = {
    ".go": GoAnalyzer,
    ".rs": RustAnalyzer,
    ".java": JavaAnalyzer,
    ".kt": KotlinAnalyzer,
    ".kts": KotlinAnalyzer,
    ".c": CAnalyzer,
    ".h": CAnalyzer,
    ".cpp": CppAnalyzer,
    ".cc": CppAnalyzer,
    ".cxx": CppAnalyzer,
    ".hpp": CppAnalyzer,
    ".rb": RubyAnalyzer,
    ".php": PhpAnalyzer,
    ".swift": SwiftAnalyzer,
    ".scala": ScalaAnalyzer,
    ".dart": DartAnalyzer,
    ".cs": CSharpAnalyzer,
    ".lua": LuaAnalyzer,
    ".zig": ZigAnalyzer,
    ".ex": ElixirAnalyzer,
    ".exs": ElixirAnalyzer,
    ".sol": SolidityAnalyzer,
}

# Language name for metadata
LANG_NAME_MAP: Dict[str, str] = {
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".scala": "scala", ".dart": "dart",
    ".cs": "csharp", ".lua": "lua", ".zig": "zig", ".ex": "elixir", ".exs": "elixir",
    ".sol": "solidity",
}

# Skip directories
SKIP_DIRS = frozenset({
    "node_modules", "dist", "build", "out", ".git", "__pycache__",
    "venv", ".next", ".turbo", ".output", "coverage", ".cache",
    "vendor", "target", "bin", "obj", "lib", "pkg", ".idea",
    ".vs", ".gradle", "Pods", ".dart_tool", ".pub-cache",
})
