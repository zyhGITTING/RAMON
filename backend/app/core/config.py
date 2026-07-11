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
