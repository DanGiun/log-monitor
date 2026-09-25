from __future__ import annotations

import asyncio
import json
import logging
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .aggregation import aggregate
from .config import ConfigStore
from .filters import FilterValidationError, validate_filter
from .incidents import IncidentStore
from .manager import SourceManager
from .models import (
    AggregationRequest,
    AppConfig,
    AppSettings,
    DiagnosticReport,
    IncidentRecord,
    IncidentSettings,
    QueryRequest,
    SavedFilter,
    SourceConfig,
    Workspace,
)
from .storage import EventStore

LOGGER = logging.getLogger("log_viewer")


def create_app(
    config_path: Path | None = None,
    cache_dir: Path | None = None,
    cleanup_cache: bool = True,
    incident_path: Path | None = None,
) -> FastAPI:
    config_store = ConfigStore(config_path)
    target_cache = cache_dir or Path.home() / ".cache" / "log-viewer" / "session"
    if cleanup_cache and target_cache.exists():
        shutil.rmtree(target_cache, ignore_errors=True)
    event_store = EventStore(target_cache, config_store.get().settings.disk_buffer_bytes)
    target_incidents = incident_path or config_store.path.parent / "incidents.sqlite3"
    incident_store = IncidentStore(target_incidents)
    incident_store.cleanup(config_store.get().incident_settings.retention_hours)
    manager = SourceManager(config_store, event_store, incident_store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await manager.start_all()
        try:
            yield
        finally:
            await manager.stop_all()
            event_store.close(cleanup=cleanup_cache)
            incident_store.close()

    app = FastAPI(title="Log Viewer", version=__version__, lifespan=lifespan)
    app.state.config_store = config_store
    app.state.event_store = event_store
    app.state.incident_store = incident_store
    app.state.manager = manager

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def root() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "cache_bytes": event_store.logical_bytes(),
            "events": event_store.count(),
            "incidents": incident_store.count(),
        }

    @app.get("/api/config", response_model=AppConfig)
    async def get_config() -> AppConfig:
        return config_store.get()

    @app.put("/api/settings", response_model=AppSettings)
    async def update_settings(settings: AppSettings) -> AppSettings:
        config_store.update(lambda cfg: setattr(cfg, "settings", settings))
        event_store.max_bytes = settings.disk_buffer_bytes
        return settings

    @app.put("/api/incident-settings", response_model=IncidentSettings)
    async def update_incident_settings(settings: IncidentSettings) -> IncidentSettings:
        config_store.update(lambda cfg: setattr(cfg, "incident_settings", settings))
        await asyncio.to_thread(incident_store.cleanup, settings.retention_hours)
        return settings

    @app.get("/api/incidents", response_model=list[IncidentRecord])
    async def incidents(
        workspace_id: str | None = None,
        limit: int = Query(default=1000, ge=1, le=10_000),
    ) -> list[IncidentRecord]:
        config = config_store.get()
        target_workspace_id = workspace_id or config.active_workspace_id
        workspace = next(
            (item for item in config.workspaces if item.id == target_workspace_id),
            None,
        )
        if workspace is None:
            raise HTTPException(404, "Workspace not found")
        source_ids = sorted(
            {
                source_id
                for panel in workspace.panels
                for source_id in panel.source_ids
            }
        )
        return await asyncio.to_thread(
            incident_store.list_for_sources,
            source_ids,
            config.incident_settings.retention_hours,
            limit,
        )

    @app.get("/api/statuses")
    async def statuses():
        return manager.get_statuses()

    @app.post("/api/sources", response_model=SourceConfig, status_code=201)
    async def create_source(source: SourceConfig) -> SourceConfig:
        config = config_store.get()
        if any(existing.id == source.id for existing in config.sources):
            raise HTTPException(409, "Source id already exists")
        config_store.update(lambda cfg: cfg.sources.append(source))
        if source.enabled:
            await manager.start(source)
        return source

    @app.put("/api/sources/{source_id}", response_model=SourceConfig)
    async def update_source(source_id: str, source: SourceConfig) -> SourceConfig:
        if source.id != source_id:
            raise HTTPException(400, "Source id in body must match path")
        config = config_store.get()
        previous = next((item for item in config.sources if item.id == source_id), None)
        if previous is None:
            raise HTTPException(404, "Source not found")

        def mutate(cfg: AppConfig) -> None:
            cfg.sources = [source if item.id == source_id else item for item in cfg.sources]

        definition_changed = any(
            (
                previous.kind != source.kind,
                previous.path != source.path,
                previous.parser != source.parser,
                previous.ssh != source.ssh,
            )
        )
        runtime_changed = definition_changed or any(
            (
                previous.timezone_offset_hours != source.timezone_offset_hours,
                previous.history_events != source.history_events,
                previous.poll_interval_ms != source.poll_interval_ms,
            )
        )
        restart_required = source.enabled and (not previous.enabled or runtime_changed)
        if previous.enabled and (not source.enabled or runtime_changed):
            await manager.stop(source.id)
        config_store.update(mutate)
        if definition_changed:
            await asyncio.to_thread(event_store.delete_source, source.id)
        if previous.timezone_offset_hours != source.timezone_offset_hours:
            await asyncio.to_thread(
                event_store.shift_source, source.id, source.timezone_offset_hours
            )
        if previous.history_events != source.history_events:
            await asyncio.to_thread(
                event_store.trim_source, source.id, source.history_events
            )
        if restart_required:
            if not definition_changed:
                await asyncio.to_thread(
                    event_store.delete_latest, source.id, source.history_events
                )
            await manager.start(source)
        return source

    @app.delete("/api/sources/{source_id}", status_code=204)
    async def delete_source(source_id: str) -> None:
        config = config_store.get()
        if not any(item.id == source_id for item in config.sources):
            raise HTTPException(404, "Source not found")
        await manager.stop(source_id)

        def mutate(cfg: AppConfig) -> None:
            cfg.sources = [item for item in cfg.sources if item.id != source_id]
            for workspace in cfg.workspaces:
                for panel in workspace.panels:
                    panel.source_ids = [item for item in panel.source_ids if item != source_id]

        config_store.update(mutate)
        await asyncio.to_thread(event_store.delete_source, source_id)

    @app.post("/api/sources/{source_id}/start")
    async def start_source(source_id: str):
        config = config_store.get()
        source = next((item for item in config.sources if item.id == source_id), None)
        if source is None:
            raise HTTPException(404, "Source not found")
        if not source.enabled:
            source.enabled = True
            await update_source(source_id, source)
        else:
            await manager.start(source)
        return {"status": "started"}

    @app.post("/api/sources/{source_id}/stop")
    async def stop_source(source_id: str):
        config = config_store.get()
        source = next((item for item in config.sources if item.id == source_id), None)
        if source is None:
            raise HTTPException(404, "Source not found")
        source.enabled = False
        await update_source(source_id, source)
        return {"status": "stopped"}

    @app.post("/api/query")
    async def query_events(request: QueryRequest):
        try:
            events = await asyncio.to_thread(
                event_store.query,
                request.source_ids,
                request.filter,
                request.limit,
                request.before_timestamp,
                request.include_without_timestamp,
            )
        except FilterValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        return events

    @app.post("/api/aggregations")
    async def aggregations(request: AggregationRequest):
        filter_group = request.filter if request.apply_filter else request.filter.model_copy(update={"conditions": [], "groups": []})
        try:
            events = await asyncio.to_thread(
                event_store.query,
                request.source_ids,
                filter_group,
                20_000,
                None,
                True,
            )
        except FilterValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        return aggregate(events, request.interval, request.top_n)

    @app.get("/api/workspaces", response_model=list[Workspace])
    async def list_workspaces():
        return config_store.get().workspaces

    @app.post("/api/workspaces", response_model=Workspace, status_code=201)
    async def create_workspace(workspace: Workspace):
        config = config_store.get()
        if any(item.id == workspace.id or item.name == workspace.name for item in config.workspaces):
            raise HTTPException(409, "Workspace id or name already exists")
        config_store.update(lambda cfg: cfg.workspaces.append(workspace))
        return workspace

    @app.put("/api/workspaces/{workspace_id}", response_model=Workspace)
    async def update_workspace(workspace_id: str, workspace: Workspace):
        if workspace.id != workspace_id:
            raise HTTPException(400, "Workspace id in body must match path")
        config = config_store.get()
        if not any(item.id == workspace_id for item in config.workspaces):
            raise HTTPException(404, "Workspace not found")
        config_store.update(
            lambda cfg: setattr(
                cfg,
                "workspaces",
                [workspace if item.id == workspace_id else item for item in cfg.workspaces],
            )
        )
        return workspace

    @app.delete("/api/workspaces/{workspace_id}", status_code=204)
    async def delete_workspace(workspace_id: str):
        if workspace_id == "default":
            raise HTTPException(400, "The default workspace cannot be deleted")
        config = config_store.get()
        if not any(item.id == workspace_id for item in config.workspaces):
            raise HTTPException(404, "Workspace not found")

        def mutate(cfg: AppConfig) -> None:
            cfg.workspaces = [item for item in cfg.workspaces if item.id != workspace_id]
            if cfg.active_workspace_id == workspace_id:
                cfg.active_workspace_id = "default"

        config_store.update(mutate)

    @app.put("/api/active-workspace/{workspace_id}")
    async def set_active_workspace(workspace_id: str):
        config = config_store.get()
        if not any(item.id == workspace_id for item in config.workspaces):
            raise HTTPException(404, "Workspace not found")
        config_store.update(lambda cfg: setattr(cfg, "active_workspace_id", workspace_id))
        return {"active_workspace_id": workspace_id}

    @app.get("/api/filters", response_model=list[SavedFilter])
    async def saved_filters():
        return config_store.get().saved_filters

    @app.post("/api/filters", response_model=SavedFilter, status_code=201)
    async def save_filter(saved: SavedFilter):
        config = config_store.get()
        if any(item.id == saved.id or item.name == saved.name for item in config.saved_filters):
            raise HTTPException(409, "Filter id or name already exists")
        try:
            validate_filter(saved.filter)
        except FilterValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        config_store.update(lambda cfg: cfg.saved_filters.append(saved))
        return saved

    @app.delete("/api/filters/{filter_id}", status_code=204)
    async def delete_filter(filter_id: str):
        config = config_store.get()
        if not any(item.id == filter_id for item in config.saved_filters):
            raise HTTPException(404, "Filter not found")
        config_store.update(
            lambda cfg: setattr(
                cfg, "saved_filters", [item for item in cfg.saved_filters if item.id != filter_id]
            )
        )

    @app.get("/api/diagnostics")
    async def diagnostics():
        config = config_store.get()
        report = DiagnosticReport(
            version=__version__,
            generated_at=datetime.now(timezone.utc),
            cache_bytes=event_store.logical_bytes(),
            source_statuses=manager.get_statuses(),
            source_count=len(config.sources),
            workspace_count=len(config.workspaces),
            settings=config.settings,
        )
        return JSONResponse(
            content=json.loads(report.model_dump_json()),
            headers={"Content-Disposition": "attachment; filename=log-viewer-diagnostics.json"},
        )

    @app.websocket("/ws")
    async def websocket_stream(websocket: WebSocket, source_ids: str = Query(default="")):
        await websocket.accept()
        ids = [item for item in source_ids.split(",") if item]
        stream = manager.subscribe(ids)
        buffer = []
        deadline: float | None = None
        loop = asyncio.get_running_loop()

        async def flush() -> None:
            nonlocal buffer, deadline
            buffer.sort(
                key=lambda event: (
                    event.display_timestamp or event.received_at,
                    event.received_at,
                    event.sequence,
                    event.id,
                )
            )
            for event in buffer:
                await websocket.send_text(event.model_dump_json())
            buffer = []
            deadline = None

        try:
            while True:
                delay = config_store.get().settings.sort_buffer_seconds
                if buffer and deadline is not None:
                    timeout = max(0.0, deadline - loop.time())
                    try:
                        event = await asyncio.wait_for(anext(stream), timeout=timeout)
                        buffer.append(event)
                    except asyncio.TimeoutError:
                        await flush()
                else:
                    event = await anext(stream)
                    if delay <= 0:
                        await websocket.send_text(event.model_dump_json())
                    else:
                        buffer.append(event)
                        deadline = loop.time() + delay
        except (WebSocketDisconnect, StopAsyncIteration, asyncio.CancelledError):
            return
        finally:
            await stream.aclose()

    return app
