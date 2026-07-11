# Backend Layout

`backend/` is now the primary home for backend runtime code, tasks, and deployment assets.

Current runtime entrypoints:

- `backend/app/main.py`: ASGI entrypoint for FastAPI
- `backend/app/tasks/sync_runner.py`: sync task CLI
- `backend/Dockerfile`: backend container build file
- `backend/init_pg.sql`: PostgreSQL bootstrap script
- `backend/scripts/`: scheduled task launchers

Current module split:

- `backend/app/api/`: API-facing dependencies and router namespace
- `backend/app/api/routers/catalog.py`: backend-owned public catalog router replacement
- `backend/app/core/`: config, logging, scheduler, security wrappers
- `backend/app/db/`: DB access bridge
- `backend/app/integrations/`: external datasource bridge
- `backend/app/services/`: business-domain service wrappers
- `backend/app/api/routers/admin_llm.py`: first backend-owned runtime router replacement
- `backend/app/api/routers/auth.py`: backend-owned auth router replacement
- `backend/app/api/routers/admin_mcp_token.py`: backend-owned MCP token admin router replacement
- `backend/app/api/routers/mcp_export_request.py`: backend-owned MCP export request router replacement
- `backend/app/api/routers/admin_platform.py`: backend-owned platform admin router replacement
- `backend/app/api/routers/admin_datasource_control.py`: backend-owned datasource control router replacement
- `backend/app/api/routers/admin_datasource.py`: backend-owned datasource CRUD router replacement
- `backend/db.py`: shared database adapter implementation
- `backend/sync_data.py`: sync workflow implementation

Legacy root runtime files have been removed from the active code path. `backend/app/main.py`
now creates the FastAPI app directly and registers backend-owned routers.

Next migration target:

1. Continue replacing broad service modules with smaller domain repositories.
2. Remove SQLite compatibility once local development no longer needs it.
