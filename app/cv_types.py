"""
Shared types for Code Visualizer analyzers.

Copyright (c) 2024-2026 Resonant Genesis / dev-swat.com
License: Resonant Genesis Source Available License (see LICENSE.txt)
"""

from typing import Dict, List, Any
from dataclasses import dataclass, field
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
