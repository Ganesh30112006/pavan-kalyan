"""GitHub webhooks: deploy on push, and pull-request preview deployments.

Each application has its own webhook secret. A delivery is accepted only when its
X-Hub-Signature-256 matches, is for the application's repository, and has not been handled before.
Pull requests from forks are ignored: previews run the pull request's code with the application's
settings and secret references, so only branches of the repository itself get one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, wake_worker
from app.errors import ApiError
from app.models import Application, ApplicationSpec, WebhookDelivery
from app.services import apps
from app.services.deployments import source_snapshot, start_deployment

logger = logging.getLogger(__name__)
router = APIRouter(tags=["hooks"])
_SHA = re.compile(r"(?a)^[0-9a-f]{40}$")
_DELIVERY = re.compile(r"(?a)^[0-9A-Za-z-]{1,64}$")


def _repo_key(url: str) -> str:
    """github.com/owner/repo, lowercased, without scheme or .git (how GitHub URLs are compared)."""
    value = url.strip().lower()
    for prefix in ("https://", "http://", "git://", "ssh://git@", "git@"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    value = value.replace("github.com:", "github.com/")
    return value.removesuffix("/").removesuffix(".git")


def _signature_ok(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


def _deploy_or_defer(
    db: Session, app: Application, source: dict[str, Any], label: str, actor: str, trigger: str
) -> str:
    """Starts a deployment of `source`, or remembers it until the running deployment finishes."""
    if app.paused:
        return "skipped: application paused"
    if app.active_deployment_id is not None or app.delete_requested_at is not None:
        app.pending_auto_deploy = {"source": source, "label": label, "actor": actor, "trigger": trigger}
        return "deferred: a deployment is running"
    spec = db.get(ApplicationSpec, app.id)
    if spec is None:
        return "skipped: no configuration"
    dep = start_deployment(
        db,
        app=app,
        spec=spec,
        kind="deploy",
        target=None,
        actor=None,
        actor_label=actor,
        trigger=trigger,
        source_override=source,
        source_ref_label=label,
    )
    return f"deployment {dep.id} {dep.status}"


def _commit_source(app: Application, sha: str) -> dict[str, Any]:
    return {**source_snapshot(app), "ref_type": "commit", "git_ref": sha}


def _push(db: Session, app: Application, payload: dict[str, Any]) -> str:
    if not app.auto_deploy:
        return "ignored: auto-deploy is off"
    branch = app.auto_deploy_branch or (app.git_ref if app.ref_type == "branch" else None)
    ref = str(payload.get("ref", ""))
    if not branch or ref != f"refs/heads/{branch}":
        return f"ignored: push to {ref.removeprefix('refs/heads/') or 'unknown ref'}"
    sha = str(payload.get("after", ""))
    if payload.get("deleted") or not _SHA.match(sha) or sha == "0" * 40:
        return "ignored: branch deleted"
    pusher = (payload.get("pusher") or {}).get("name") or (payload.get("sender") or {}).get("login") or "GitHub"
    return _deploy_or_defer(
        db, app, _commit_source(app, sha), f"{branch}@{sha[:12]}", f"Push by {str(pusher)[:60]}", "push"
    )


def _pull_request(db: Session, app: Application, payload: dict[str, Any]) -> str:
    if not app.previews_enabled:
        return "ignored: previews are off"
    action = str(payload.get("action", ""))
    pr = payload.get("pull_request") or {}
    number = payload.get("number") or pr.get("number")
    if not isinstance(number, int) or number <= 0:
        return "ignored: no pull request number"
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    if (head.get("repo") or {}).get("full_name") != (base.get("repo") or {}).get("full_name"):
        return "ignored: pull request from a fork"
    preview = apps.find_preview(db, app, number)
    if action == "closed":
        if preview is None:
            return "ignored: no preview"
        outcome = apps.request_delete(db, preview, None)
        if outcome == "busy":
            preview.pending_auto_deploy = {"delete": True}
            return "deferred: the preview is deleted after its deployment"
        return f"preview {outcome}"
    if action not in ("opened", "reopened", "synchronize"):
        return f"ignored: {action or 'unknown'} action"
    sha = str(head.get("sha", ""))
    if not _SHA.match(sha):
        return "ignored: no head commit"
    if preview is None:
        preview = apps.create_preview(db, app, number, sha, str(pr.get("title") or "")[:200])
    else:
        preview.git_ref = sha
    return _deploy_or_defer(
        db,
        preview,
        _commit_source(preview, sha),
        f"PR #{number}@{sha[:12]}",
        f"Pull request #{number}",
        "pull_request",
    )


@router.post("/hooks/github/{application_id}", name="github_webhook")
async def github_webhook(application_id: str, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    body = await request.body()
    app = db.scalar(
        select(Application).where(
            Application.id == application_id,
            Application.deleted_at.is_(None),
            Application.webhook_secret.is_not(None),
        )
    )
    # The same answer for "no such application" and "bad signature", so the endpoint reveals nothing.
    if app is None or not _signature_ok(str(app.webhook_secret), body, request.headers.get("x-hub-signature-256")):
        raise ApiError(401, "invalid_signature", "The webhook signature is missing or does not match.")
    event = request.headers.get("x-github-event", "")
    delivery = request.headers.get("x-github-delivery", "")
    if not _DELIVERY.match(delivery):
        raise ApiError(400, "invalid_delivery", "The webhook has no delivery ID.")
    if db.get(WebhookDelivery, delivery) is not None:
        return JSONResponse({"outcome": "duplicate delivery"}, status_code=200)
    try:
        payload = json.loads(body)
    except ValueError:
        raise ApiError(400, "invalid_json", "The webhook body is not valid JSON.") from None
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_json", "The webhook body is not a JSON object.")

    repository = payload.get("repository") or {}
    urls = [str(repository.get(k) or "") for k in ("clone_url", "html_url", "ssh_url")]
    if event != "ping" and _repo_key(app.repository_url or "") not in {_repo_key(u) for u in urls if u}:
        outcome = "ignored: webhook is for another repository"
    elif event == "ping":
        outcome = "pong"
    elif event == "push":
        outcome = _push(db, app, payload)
    elif event == "pull_request":
        outcome = _pull_request(db, app, payload)
    else:
        outcome = f"ignored: {event or 'unknown'} event"
    db.add(WebhookDelivery(id=delivery, application_id=app.id, event=event[:40], outcome=outcome[:80]))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return JSONResponse({"outcome": "duplicate delivery"}, status_code=200)
    logger.info("GitHub webhook handled", extra={"application_id": app.id, "event": event, "outcome": outcome})
    if outcome.startswith(("deployment", "preview")):
        wake_worker(request)
    return JSONResponse({"outcome": outcome}, status_code=202 if outcome.startswith("deployment") else 200)
