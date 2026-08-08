"""FastAPI 后台（对齐 xiaohongshu-matrices-cli dashboard）。

提供账号矩阵、人设库、跨账号发布编排与编排器状态的可视化管理界面。
访问 http://127.0.0.1:8765 （由 dy-dashboard 启动）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import DashboardConfig
from .db import Database
from .orchestrator import Orchestrator
from .utils import json_dumps, json_loads, split_terms
from .qr_login import QrLoginManager

from ..account_bridge import legacy_cookie_file, verify_account_cookies

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _render(name: str, **kw: Any) -> HTMLResponse:
    return HTMLResponse(_env.get_template(name).render(**kw))


def create_app() -> FastAPI:
    app = FastAPI(title="douyin-matrices 后台")
    cfg = DashboardConfig.load()
    db = Database(cfg.database_path)
    orch = Orchestrator(db, cfg)
    orch.start()  # 无操作，除非 DY_ORCHESTRATOR=1
    orch.install(app)
    # 网页内扫码绑定：无头浏览器会话管理器（不在导入时启动浏览器）
    qr_manager = QrLoginManager()
    app.state.qr_manager = qr_manager

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        accounts = db.list_accounts()
        personas = db.list_personas()
        tasks = db.list_publish_tasks()[:20]
        return _render("dashboard.html", accounts=accounts, personas=personas, tasks=tasks, orch_enabled=orch.enabled)

    @app.get("/accounts", response_class=HTMLResponse)
    def accounts_page():
        accounts = db.list_accounts()
        personas = db.list_personas()
        return _render("accounts.html", accounts=accounts, personas=personas)

    @app.post("/accounts/{account_id}/update")
    def update_account_post(
        account_id: int,
        group: str = Form(""),
        enabled: int = Form(1),
        persona_id: int = Form(0),
    ):
        changes: dict[str, Any] = {"group_name": group, "enabled": enabled}
        changes["persona_id"] = persona_id if persona_id else None
        db.update("accounts", account_id, **changes)
        return RedirectResponse("/accounts", status_code=303)

    @app.post("/accounts/{account_id}/verify")
    def verify_account_post(account_id: int):
        acc = db.get_account(account_id)
        if acc:
            ok, reason = verify_account_cookies(acc["alias"])
            if ok:
                db.mark_verified(account_id)
            else:
                db.set_login_status(account_id, "unbound", error=reason)
        return RedirectResponse("/accounts", status_code=303)

    @app.post("/accounts/{account_id}/toggle")
    def toggle_account_post(account_id: int):
        acc = db.get_account(account_id)
        if acc:
            db.update("accounts", account_id, enabled=0 if acc.get("enabled") else 1)
        return RedirectResponse("/accounts", status_code=303)

    @app.post("/accounts/{account_id}/delete")
    def delete_account_post(account_id: int):
        db.delete_account(account_id)
        return RedirectResponse("/accounts", status_code=303)

    @app.post("/accounts/{account_id}/bind")
    def bind_account_post(account_id: int):
        acc = db.get_account(account_id)
        if acc:
            # 标记待重新绑定：下次 `dy login --account <alias>` 会重新拉起浏览器登录
            db.set_login_status(account_id, "unbound")
        return RedirectResponse("/accounts", status_code=303)

    # ── 网页内扫码绑定（无头二维码）────────────────────────────────────────────
    @app.get("/bind-qr", response_class=HTMLResponse)
    def bind_qr_new():
        return _render("bind_qr.html", account=None)

    @app.get("/accounts/{account_id}/bind-qr", response_class=HTMLResponse)
    def bind_qr_existing(account_id: int):
        acc = db.get_account(account_id)
        if not acc:
            return RedirectResponse("/accounts", status_code=303)
        return _render("bind_qr.html", account=acc)

    @app.post("/api/accounts/qr-start")
    def api_qr_start(account_id: int | None = Form(None), alias: str = Form("")):
        # 确定别名：优先用表单，其次用已有账号行的别名
        if not alias and account_id:
            acc = db.get_account(account_id)
            if acc:
                alias = acc["alias"]
        if not alias:
            return JSONResponse({"ok": False, "error": "缺少账号别名"}, status_code=400)
        # 解析/创建账号行的 cookie 路径
        acc = db.get_account_by_alias(alias)
        cookie_file = (acc or {}).get("cookie_file") or legacy_cookie_file(alias)
        result = app.state.qr_manager.start(alias, cookie_file)
        if result.get("error"):
            return JSONResponse({"ok": False, "error": result["error"]})
        return JSONResponse(
            {
                "ok": True,
                "session_id": result["session_id"],
                "qr_image_url": result["qr_image_url"],
            }
        )

    @app.get("/api/accounts/qr-status")
    def api_qr_status(session: str = ""):
        if not session:
            return JSONResponse({"status": "gone"})
        return JSONResponse(app.state.qr_manager.status(session))

    @app.get("/api/accounts/qr-image")
    def api_qr_image(session: str = ""):
        if not session:
            return JSONResponse({"error": "missing session"}, status_code=400)
        path = app.state.qr_manager.qr_image(session)
        if not path or not Path(path).is_file():
            return JSONResponse({"error": "not ready"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.get("/api/accounts")
    def api_accounts():
        accounts = [db.get_account_health(int(a["id"])) for a in db.list_accounts()]
        return JSONResponse({"ok": True, "accounts": accounts})

    @app.get("/api/health")
    def api_health():
        accounts = db.list_accounts()
        healthy = sum(1 for a in accounts if db.get_account_health(int(a["id"]))["healthy"])
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "total_accounts": len(accounts),
                    "healthy_accounts": healthy,
                    "unhealthy_accounts": len(accounts) - healthy,
                },
            }
        )

    @app.get("/personas", response_class=HTMLResponse)
    def personas_page():
        personas = db.list_personas()
        return _render("personas.html", personas=personas)

    @app.post("/personas/create")
    def create_persona_post(
        name: str = Form(...),
        tone: str = Form(""),
        bio: str = Form(""),
        topics: str = Form(""),
        forbidden: str = Form(""),
    ):
        db.create_persona(
            name=name,
            tone=tone,
            bio=bio,
            topics=split_terms(topics),
            forbidden_words=split_terms(forbidden),
        )
        return RedirectResponse("/personas", status_code=303)

    @app.get("/publish", response_class=HTMLResponse)
    def publish_page():
        accounts = db.list_accounts(enabled_only=True)
        return _render("publish.html", accounts=accounts)

    @app.post("/api/publish")
    async def api_publish(request: Request):
        data = await request.form()
        spec = {
            "title": str(data.get("title", "")),
            "body": str(data.get("body", "")),
            "media_type": str(data.get("media_type", "video")),
            "topics": [t.strip() for t in str(data.get("topics") or "").split(",") if t.strip()],
            "media_paths": [p.strip() for p in str(data.get("media_paths") or "").splitlines() if p.strip()],
        }
        group = data.get("group") or None
        account_ids = None
        if data.get("account_ids"):
            account_ids = [int(x) for x in str(data["account_ids"]).split(",") if x.strip()]
        created = orch.batch_publish(spec, account_ids=account_ids, group=group)
        return JSONResponse({"ok": True, "created_task_ids": created})

    @app.get("/orchestrator", response_class=HTMLResponse)
    def orchestrator_page():
        mode_auto = orch.auto_publish_mode or "未设置（待审任务不自动执行）"
        return _render(
            "orchestrator.html",
            orch_enabled=orch.enabled,
            mode=orch.mode,
            daily_limit=orch.daily_publish_limit,
            mode_auto=mode_auto,
            tick_seconds=orch.tick_seconds,
        )

    @app.get("/engagement", response_class=HTMLResponse)
    def engagement_page():
        status = orch.engagement.status()
        blocked = db.fetchall(
            "SELECT external_user_id, block_reason, last_contact_at FROM target_contacts WHERE blocked=1 ORDER BY updated_at DESC"
        )
        return _render("engagement.html", mode=status["mode"], rule=status["rule"], blocked=blocked)

    @app.get("/searches", response_class=HTMLResponse)
    def searches_page():
        jobs = db.list_search_jobs()
        return _render("searches.html", jobs=jobs)

    @app.post("/searches")
    def create_search_job_post(
        name: str = Form(...),
        keywords: str = Form(""),
        topics: str = Form(""),
        media_type: str = Form("all"),
        max_pages: int = Form(3),
        min_likes: int = Form(0),
    ):
        from .utils import split_terms

        jid = db.create_search_job(
            {
                "name": name,
                "keywords_json": json_dumps(split_terms(keywords)),
                "topics_json": json_dumps(split_terms(topics)),
                "media_type": media_type,
                "max_pages": max_pages,
                "min_likes": min_likes,
            }
        )
        return RedirectResponse("/searches", status_code=303)

    @app.post("/searches/{job_id}/run")
    def run_search_job_post(job_id: int):
        orch.queue.start()  # 确保持久队列在运行以执行采集
        orch.run_search_job(job_id)
        return RedirectResponse("/searches", status_code=303)

    @app.get("/api/searches")
    def api_searches():
        jobs = db.list_search_jobs()
        return JSONResponse({"ok": True, "jobs": jobs})

    # ── 数据分析 / 消息 / 评论（对齐 xhs 的 analytics / messages 能力）──────
    def _first_ready_alias():
        try:
            for a in db.list_accounts():
                if (a.get("login_status") or "") == "ready":
                    return a["alias"]
        except Exception:
            pass
        return None

    @app.get("/analytics", response_class=HTMLResponse)
    def analytics_page():
        return _render("analytics.html")

    @app.get("/messages", response_class=HTMLResponse)
    def messages_page():
        return _render("messages.html")

    @app.get("/comments", response_class=HTMLResponse)
    def comments_page():
        return _render("comments.html")

    @app.get("/api/analytics")
    def api_analytics():
        alias = _first_ready_alias()
        if not alias:
            return JSONResponse({"ok": False, "error": "无就绪账号，请先扫码绑定"}, status_code=400)
        try:
            from ..engines.playwright_client import PlaywrightClient

            return JSONResponse({"ok": True, "data": PlaywrightClient(account=alias).get_analytics()})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/messages")
    def api_messages():
        alias = _first_ready_alias()
        if not alias:
            return JSONResponse({"ok": False, "error": "无就绪账号，请先扫码绑定"}, status_code=400)
        try:
            from ..engines.playwright_client import PlaywrightClient

            return JSONResponse({"ok": True, "data": PlaywrightClient(account=alias).get_notifications()})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/comments")
    def api_comments(aweme_id: str = ""):
        if not aweme_id:
            return JSONResponse({"ok": False, "error": "缺少 aweme_id"}, status_code=400)
        alias = _first_ready_alias()
        if not alias:
            return JSONResponse({"ok": False, "error": "无就绪账号，请先扫码绑定"}, status_code=400)
        try:
            from ..engines.playwright_client import PlaywrightClient

            return JSONResponse({"ok": True, "data": PlaywrightClient(account=alias).get_comments(aweme_id)})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    return app
