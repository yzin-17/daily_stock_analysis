"""DSA 数据应用装配：正式数据、控制、OAuth、索引与健康接口。"""

from datetime import datetime
import logging
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from api.errors import error_body
from api.middlewares.error_handler import add_error_handlers
from api.thesis_ledger import router_v3
from api.thesis_ledger_auth import _request_id_context
from api.thesis_ledger_cache_context_v3 import router as cache_context_router
from api.thesis_ledger_chart_v3 import router as chart_router
from api.thesis_ledger_events_v3 import router as events_router
from api.thesis_ledger_nav_v3 import router as nav_router
from api.thesis_ledger_oauth import router as oauth_router
from api.thesis_ledger_rule_monitor import router as rule_monitor_router
from api.thesis_ledger_public_search import router as public_search_router
from src.data.stock_index_loader import find_existing_stock_index_path
from src.data_config import DataConfigurationError

logger = logging.getLogger(__name__)
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _add_cors(app: FastAPI) -> None:
    origins = ["http://localhost:5173", "http://127.0.0.1:5173",
               "http://localhost:3000", "http://127.0.0.1:3000"]
    origins.extend(value.strip() for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip())
    allow_all = os.getenv("CORS_ALLOW_ALL", "").lower() == "true"
    if allow_all:
        origins = ["*"]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=not allow_all,
                       allow_methods=["*"], allow_headers=["*"])


def create_app(*, lifespan=None) -> FastAPI:
    if lifespan is None:
        from api.data_lifespan import data_lifespan
        lifespan = data_lifespan
    app = FastAPI(title="DSA 数据服务 API", version="3.0.0", lifespan=lifespan)
    _add_cors(app)
    for router in (router_v3, chart_router, events_router, nav_router, cache_context_router, oauth_router, rule_monitor_router, public_search_router):
        app.include_router(router, prefix="/api/v3")
    add_error_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        token = _request_id_context.set(request.headers.get("x-request-id") or str(uuid.uuid4()))
        try:
            return await call_next(request)
        finally:
            _request_id_context.reset(token)

    @app.exception_handler(DataConfigurationError)
    async def configuration_error(request: Request, exc: DataConfigurationError):
        logger.error("数据配置加载失败，类型=%s", type(exc).__name__)
        return JSONResponse(status_code=503, content={
            **error_body("data_configuration_failed", "数据服务配置加载失败"), "detail": None,
        })

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logger.error("数据请求失败，类型=%s", type(exc).__name__)
        return JSONResponse(status_code=500, content={
            **error_body("internal_error", "服务器内部错误"), "detail": None,
        })

    @app.get("/health", tags=["Health"])
    @app.get("/api/health", tags=["Health"])
    async def health():
        return {"status": "ok", "timestamp": datetime.now().isoformat()}

    @app.get("/", include_in_schema=False)
    async def root():
        return {"service": "dsa-data", "status": "ok"}

    @app.api_route("/stocks.index.json", methods=["GET", "HEAD"], include_in_schema=False)
    async def stock_index():
        refresh = getattr(app.state, "schedule_stock_index_refresh", None)
        if refresh is not None:
            refresh("serve-stock-index")
        path = find_existing_stock_index_path()
        if path is None:
            return Response(content="stock index not found", status_code=404, media_type="text/plain")
        return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-cache"})

    @app.api_route("/api/v1", methods=_METHODS, include_in_schema=False)
    @app.api_route("/api/v2", methods=_METHODS, include_in_schema=False)
    @app.api_route("/api/v1/{legacy_path:path}", methods=_METHODS, include_in_schema=False)
    @app.api_route("/api/v2/{legacy_path:path}", methods=_METHODS, include_in_schema=False)
    async def retired_route(legacy_path: str = ""):
        return JSONResponse(status_code=404, content={"error": "not_found"})

    return app
