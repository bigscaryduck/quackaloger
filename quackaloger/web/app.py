"""FastAPI application exposing the full organizer over a self-hosted web UI.

No auth by design (intended for a trusted LAN / behind the user's reverse proxy).
Server-rendered (Jinja2 + HTMX); long runs go through the single background worker
and stream progress over SSE.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# Rich renders spinner/progress glyphs (e.g. the braille ⠋) through the worker's
# UI console. On Windows the server's stdout defaults to cp1252, which can't encode
# them -- mirror the CLI's UTF-8 guard so background jobs never crash on output.
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None and hasattr(_stream, "reconfigure") \
                and (_stream.encoding or "").lower() not in ("utf-8", "utf8"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from quackaloger import __version__, service
from quackaloger import user_config as user_cfg
from quackaloger.web import actions, state
from quackaloger.web.jobs import manager
from quackaloger.web.watcher import watcher

try:
    from fastapi import FastAPI, Form, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "The web UI requires extra dependencies. Install them with:\n"
        "    pip install 'quackaloger[web]'\n"
        f"(import error: {e})"
    )

_HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(_HERE, "templates")
STATIC_DIR = os.path.join(_HERE, "static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _fmt_ts(value) -> str:
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


templates.env.filters["ts"] = _fmt_ts
templates.env.globals["version"] = __version__


@asynccontextmanager
async def lifespan(app: "FastAPI"):
    manager.start()
    service.prune_old_plans()
    watcher.start()
    try:
        yield
    finally:
        watcher.stop()


app = FastAPI(title="Quackaloger", version=__version__, lifespan=lifespan)
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render(request: "Request", name: str, ctx: dict) -> HTMLResponse:
    # Starlette's modern signature: TemplateResponse(request, name, context).
    return templates.TemplateResponse(request, name, ctx)


def _library_view(lib: dict) -> dict:
    runs = service.runs_for(lib["path"]) if os.path.isdir(lib["path"]) else []
    tmdb_missing = (
        lib["domain"] in service.DOMAINS_NEEDING_TMDB
        and not service.tmdb_key_available(lib["path"])
    )
    return {
        **lib,
        "exists": os.path.isdir(lib["path"]),
        "busy": manager.library_busy(lib["id"]),
        "last_run": runs[0] if runs else None,
        "run_count": len(runs),
        "tmdb_missing": tmdb_missing,
    }


def _overrides_from_form(
    confidence: str, no_ai: bool, no_audible: bool, llm_provider: str
) -> dict:
    overrides: dict = {}
    if confidence:
        try:
            overrides["confidence"] = float(confidence)
        except ValueError:
            pass
    if no_ai:
        overrides["no_ai"] = True
    if no_audible:
        overrides["no_audible"] = True
    if llm_provider:
        overrides["llm_provider"] = llm_provider
    return overrides


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    libs = [_library_view(lib) for lib in state.list_libraries()]
    return _render(request, "dashboard.html", {
        "libraries": libs,
        "jobs": [j.to_dict() for j in manager.recent_jobs(10)],
        "watcher": watcher.status(),
    })


@app.get("/healthz", response_class=JSONResponse)
def healthz():
    return {"status": "ok", "version": __version__}


# ---------------------------------------------------------------------------
# Libraries CRUD
# ---------------------------------------------------------------------------

@app.get("/libraries/new", response_class=HTMLResponse)
def library_new(request: Request):
    return _render(request, "library_form.html", {
        "lib": None, "domains": state.VALID_DOMAINS, "watch_modes": state.WATCH_MODES,
    })


@app.get("/libraries/{lib_id}/edit", response_class=HTMLResponse)
def library_edit(request: Request, lib_id: str):
    lib = state.get_library(lib_id)
    if lib is None:
        return RedirectResponse("/", status_code=303)
    return _render(request, "library_form.html", {
        "lib": lib, "domains": state.VALID_DOMAINS, "watch_modes": state.WATCH_MODES,
    })


@app.post("/libraries")
def library_create(
    name: str = Form(""),
    path: str = Form(...),
    domain: str = Form(...),
    confidence: str = Form(""),
    no_ai: bool = Form(False),
    no_audible: bool = Form(False),
    llm_provider: str = Form(""),
    watch_enabled: bool = Form(False),
    watch_mode: str = Form("scan-only"),
    debounce_seconds: int = Form(30),
):
    state.add_library(
        name=name, path=path, domain=domain,
        overrides=_overrides_from_form(confidence, no_ai, no_audible, llm_provider),
        watch={"enabled": watch_enabled, "mode": watch_mode, "debounce_seconds": debounce_seconds},
    )
    watcher.reload()
    return RedirectResponse("/", status_code=303)


@app.post("/libraries/{lib_id}")
def library_update(
    lib_id: str,
    name: str = Form(""),
    path: str = Form(...),
    domain: str = Form(...),
    confidence: str = Form(""),
    no_ai: bool = Form(False),
    no_audible: bool = Form(False),
    llm_provider: str = Form(""),
    watch_enabled: bool = Form(False),
    watch_mode: str = Form("scan-only"),
    debounce_seconds: int = Form(30),
):
    state.update_library(
        lib_id, name=name, path=path, domain=domain,
        overrides=_overrides_from_form(confidence, no_ai, no_audible, llm_provider),
        watch={"enabled": watch_enabled, "mode": watch_mode, "debounce_seconds": debounce_seconds},
    )
    watcher.reload()
    return RedirectResponse("/", status_code=303)


@app.post("/libraries/{lib_id}/delete")
def library_delete(lib_id: str):
    state.delete_library(lib_id)
    watcher.reload()
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Scan / Execute
# ---------------------------------------------------------------------------

@app.post("/libraries/{lib_id}/scan")
def library_scan(request: Request, lib_id: str):
    lib = state.get_library(lib_id)
    if lib is None:
        return RedirectResponse("/", status_code=303)
    if lib["domain"] in service.DOMAINS_NEEDING_TMDB and not service.tmdb_key_available(lib["path"]):
        return _render(request, "message.html", {
            "title": "TMDB API key required",
            "body": (
                f"The '{lib['domain']}' domain needs a TMDB API key to match titles, "
                "but none is set. Add one on the Settings page (or via the "
                "QUACK_TMDB_API_KEY / TMDB_API_KEY environment variable), then scan again."
            ),
        })
    job = actions.submit_scan(lib)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@app.get("/plans/{plan_id}", response_class=HTMLResponse)
def plan_review(request: Request, plan_id: str):
    bundle = service.load_bundle(plan_id)
    if bundle is None:
        return _render(request, "message.html", {
            "title": "Plan expired",
            "body": "This plan is no longer available. Run a new scan.",
        })
    summary = service.summarize_bundle(bundle)
    lib = next((l for l in state.list_libraries() if l["path"] == bundle.library_path), None)
    return _render(request, "plan_review.html", {"plan": summary, "library": lib})


@app.post("/plans/{plan_id}/execute")
async def plan_execute(request: Request, plan_id: str):
    form = await request.form()
    bundle = service.load_bundle(plan_id)
    if bundle is None:
        return RedirectResponse("/", status_code=303)

    total = len(bundle.report.moves)
    selected = sorted({int(v) for v in form.getlist("selected")})
    include_quarantine = form.get("include_quarantine") in ("on", "true", "1")

    # All moves checked == a full run (also trashes stale sidecars).
    selected_indexes = None if (total and len(selected) == total) else selected

    lib = next((l for l in state.list_libraries() if l["path"] == bundle.library_path), None)
    lib_id = lib["id"] if lib else bundle.library_path
    job = actions.submit_execute(
        lib_id, plan_id,
        selected_indexes=selected_indexes,
        include_quarantine=include_quarantine,
    )
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


# ---------------------------------------------------------------------------
# Jobs + SSE
# ---------------------------------------------------------------------------

@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_view(request: Request, job_id: str):
    job = manager.get_job(job_id)
    if job is None:
        return RedirectResponse("/", status_code=303)
    return _render(request, "job.html", {"job": job.to_dict()})


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    async def stream():
        since = 0
        while True:
            snap = manager.snapshot(job_id, since)
            if snap is None:
                yield "event: error\ndata: {}\n\n"
                return
            since = snap["last_seq"]
            yield f"data: {json.dumps(snap)}\n\n"
            if snap["status"] in ("done", "error"):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Runs / history / undo
# ---------------------------------------------------------------------------

@app.get("/runs", response_class=HTMLResponse)
def runs_list(request: Request):
    rows = []
    for lib in state.list_libraries():
        if not os.path.isdir(lib["path"]):
            continue
        for run in service.runs_for(lib["path"]):
            rows.append({"library": lib, "run": run})
    rows.sort(key=lambda r: r["run"].started_at, reverse=True)
    return _render(request, "runs.html", {"rows": rows})


@app.post("/runs/{lib_id}/{run_id}/undo")
def run_undo(lib_id: str, run_id: str):
    lib = state.get_library(lib_id)
    if lib is not None:
        service.undo(lib["path"], run_id)
    return RedirectResponse("/runs", status_code=303)


# ---------------------------------------------------------------------------
# Settings (global user config)
# ---------------------------------------------------------------------------

def _key_set(value: str) -> bool:
    return bool(value and str(value).strip())


@app.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request):
    data = user_cfg.load_user_yaml()
    ai = data.get("ai", {}) if isinstance(data.get("ai"), dict) else {}
    keys = data.get("api_keys", {}) if isinstance(data.get("api_keys"), dict) else {}
    return _render(request, "settings.html", {
        "provider": ai.get("provider", "openai"),
        "enable_ai": ai.get("enable", True),
        "model": ai.get("model") or "",
        "has_openai": _key_set(keys.get("openai")) or _key_set(os.environ.get("OPENAI_API_KEY")),
        "has_anthropic": _key_set(keys.get("anthropic")) or _key_set(os.environ.get("ANTHROPIC_API_KEY")),
        "has_tmdb": _key_set(keys.get("tmdb")) or _key_set(os.environ.get("TMDB_API_KEY")),
    })


@app.post("/settings")
def settings_save(
    provider: str = Form("openai"),
    enable_ai: bool = Form(False),
    model: str = Form(""),
    openai_key: str = Form(""),
    anthropic_key: str = Form(""),
    tmdb_key: str = Form(""),
):
    data = user_cfg.load_user_yaml() or {}
    data.setdefault("version", 1)
    ai = data.get("ai") if isinstance(data.get("ai"), dict) else {}
    ai["provider"] = provider
    ai["enable"] = bool(enable_ai)
    ai["model"] = model.strip() or None
    data["ai"] = ai

    keys = data.get("api_keys") if isinstance(data.get("api_keys"), dict) else {}
    # Only overwrite a key when the user actually typed a new value.
    if openai_key.strip():
        keys["openai"] = openai_key.strip()
    if anthropic_key.strip():
        keys["anthropic"] = anthropic_key.strip()
    if tmdb_key.strip():
        keys["tmdb"] = tmdb_key.strip()
    data["api_keys"] = keys

    user_cfg.save_user_yaml(data)
    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------------------------
# Directory browser (sandboxed)
# ---------------------------------------------------------------------------

def _browse_roots() -> list:
    raw = os.environ.get("QUACK_BROWSE_ROOTS")
    if raw:
        roots = [r for r in raw.split(os.pathsep) if r.strip()]
    elif os.path.isdir("/data"):
        roots = ["/data"]
    else:
        roots = [os.path.expanduser("~")]
    out = []
    for r in roots:
        try:
            out.append(os.path.realpath(r))
        except Exception:
            continue
    return out


def _within_roots(path: str, roots: list) -> bool:
    rp = os.path.realpath(path)
    for root in roots:
        try:
            if os.path.commonpath([rp, root]) == root:
                return True
        except ValueError:
            continue
    return False


@app.get("/api/browse", response_class=JSONResponse)
def api_browse(path: str = ""):
    roots = _browse_roots()
    if not path:
        return {
            "path": "",
            "roots": roots,
            "parent": None,
            "dirs": [{"name": r, "path": r} for r in roots],
        }
    if not _within_roots(path, roots):
        return JSONResponse({"error": "Path outside allowed roots"}, status_code=403)
    rp = os.path.realpath(path)
    if not os.path.isdir(rp):
        return JSONResponse({"error": "Not a directory"}, status_code=404)
    dirs = []
    try:
        for name in sorted(os.listdir(rp)):
            full = os.path.join(rp, name)
            if os.path.isdir(full) and not name.startswith("."):
                dirs.append({"name": name, "path": full})
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    parent = os.path.dirname(rp)
    if not _within_roots(parent, roots):
        parent = None
    return {"path": rp, "roots": roots, "parent": parent, "dirs": dirs}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn

    host = os.environ.get("QUACK_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("QUACK_WEB_PORT", "8080"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
