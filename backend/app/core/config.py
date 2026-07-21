from __future__ import annotations

import os
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parents[3]
BACKEND_DIR = WORKSPACE_DIR / "backend"
FRONTEND_DIR = WORKSPACE_DIR / "frontend"

APP_HOST = os.getenv("DATAMID_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("DATAMID_PORT", "8128"))
TOKEN_SECRET = os.getenv("DATAMID_TOKEN_SECRET", "").strip()
FRONTEND_HTML_PATH = FRONTEND_DIR / "index.html"
LOGO_PATH = FRONTEND_DIR / "logo.png"
LEIMO_PATH = FRONTEND_DIR / "leimo.png"

_MCP_MAX_PAGE_SIZE = int(os.getenv("DATAMID_MCP_MAX_PAGE_SIZE", "200") or "200")
MCP_MAX_PAGE_SIZE = max(1, _MCP_MAX_PAGE_SIZE)
_MCP_DEFAULT_PAGE_SIZE = int(os.getenv("DATAMID_MCP_DEFAULT_PAGE_SIZE", str(MCP_MAX_PAGE_SIZE)) or str(MCP_MAX_PAGE_SIZE))
MCP_DEFAULT_PAGE_SIZE = max(1, min(_MCP_DEFAULT_PAGE_SIZE, MCP_MAX_PAGE_SIZE))
