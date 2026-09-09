import os
import json
import re
import secrets
import time
from datetime import timedelta
from hashlib import sha256
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from core.command_parser import CommandError, parse_command
from core.prompt_loader import load_insight_prompt, prompt_binding
from core.task_manager import TaskManager

public_url = os.getenv("HH520_PUBLIC_URL", "").strip().rstrip("/")
app = FastAPI(
    title="HH520 Insight AI",
    description="重新采集历史数据、屏蔽目标赛果与赛后信息、按 HH520 V2.1-Test 重新预测，再生成回测与改进建议；不会自动修改预测模型。",
    version="Backtest V1.3 Fresh Run",
    servers=[{"url": public_url}] if public_url else None,
)
security = HTTPBearer(auto_error=False)
_manager: TaskManager | None = None
_insight_prompt = load_insight_prompt()


def get_manager() -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager()
    return _manager


def require_token(credentials: HTTPAuthorizationCredentials | None = Security(security)) -> None:
    expected = os.getenv("HH520_API_TOKEN", "").strip()
    if not expected:
        return
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")


class RunRequest(BaseModel):
    command: str
    prediction_commit_ids: list[str] | None = None


class ReplayTaskRequest(BaseModel):
    request_id: str
    command: str
    retry_attempt: int = Field(default=0, ge=0, le=4)


class ReplayRangeRequest(BaseModel):
    command: str
    task_ids: list[str] = Field(min_length=1, max_length=3)
    cursor: int = Field(default=0, ge=0)
    retry_attempt: int = Field(default=0, ge=0, le=4)


class ReplayRangeCompleteRequest(ReplayRangeRequest):
    p: list[str] = Field(
        min_length=1,
        max_length=6,
        description=(
            "One ultra-compact prediction per returned k, up to six matches in the current range page: "
            "k|score1,score2,score3|htft1,htft2,htft3|asian|ou|1x2|goals|confidence|13 module codes. "
            "Scores use 1:0; HTFT uses H/D/A pairs; asian H-0.5/A+0.5/P; ou O2.5/U2.5/P; "
            "1x2 H/D/A/HD/AD/P; confidence 0-100; module codes contain only C or D. "
            "A module may be C only when the match q mask is C; every q=D must remain D."
        ),
    )


class ReplayRangeBundleResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    ready: bool
    # Additive control marker: has_more describes pagination, not readiness.
    control_state: str = "WAIT_FOR_DATA"
    replay_mode: bool | None = None
    task_ids: list[str] = Field(default_factory=list)
    pending_task_ids: list[str] = Field(default_factory=list)
    cursor: int = 0
    next_cursor: int | None = None
    has_more: bool = False
    total_matches: int = 0
    prediction_prompt_bundle: dict | None = None
    matches: list[dict] = Field(default_factory=list)
    output_format: str | None = None
    must_continue: bool
    next_operation: str
    instruction: str


class ReplayCreatedTask(BaseModel):
    model_config = ConfigDict(extra="allow")
    date: str
    request_id: str
    command: str
    task_id: str
    status_url: str
    report_url: str
    created: bool
    must_continue: bool
    next_operation: str
    instruction: str
    prediction_prompt_bundle: dict | None = None
    insight_prompt_binding: dict


class ReplayCreationError(BaseModel):
    date: str
    request_id: str
    command: str
    http_status: int
    error: str


class ReplayCreateResponse(BaseModel):
    task_id: str | None = None
    status_url: str | None = None
    report_url: str | None = None
    created: bool = False
    must_continue: bool
    next_operation: str | None = None
    instruction: str
    prediction_prompt_bundle: dict | None = None
    insight_prompt_binding: dict
    execution_status: str | None = None
    range_run_id: str | None = None
    parent_request_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    day_count: int | None = None
    created_count: int | None = None
    failed_count: int | None = None
    tasks: list[ReplayCreatedTask] | None = None
    errors: list[ReplayCreationError] | None = None
    cancelled_previous_task_ids: list[str] = Field(default_factory=list)
    retry_attempt: int = 0


class ReplayStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    payload: dict
    status: str
    blockers: list[str]
    must_continue: bool
    next_operation: str
    instruction: str
    prediction_prompt_bundle: dict | None = None
    insight_prompt_binding: dict | None = None


class ReplayAnalysisPageResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    task_id: str
    status: str
    snapshot_id: str
    replay_mode: bool
    cursor: int
    next_cursor: int | None
    has_more: bool
    total_matches: int
    matches: list[dict]
    required_module_order: list[str]
    date_rule: str | None = None
    prediction_prompt_bundle: dict | None = None
    prompt_binding: dict | None = None
    insight_prompt_binding: dict | None = None
    runtime_instruction: str | None = None


class ReplayPredictionItem(BaseModel):
    n: int = Field(description="Copy the exact match_no integer from the current analysis-page match.")
    c: str = Field(description="Copy the exact code string from that same analysis-page match; never derive or reformat it.")
    m: str = Field(description="Exactly 13 characters, one per required_module_order item; C=completed, D=degraded.")
    e: list[str] = Field(description="Non-empty evidence references taken from the current match sections.")
    r: list[str] = Field(
        description=(
            "Exactly seven parseable strings in this fixed order and syntax: "
            "[1] '精准比分 Top3：1-0 / 1-1 / 2-0'; "
            "[2] '半全场 Top3：胜/胜 / 平/胜 / 平/平'; "
            "[3] '亚洲盘：主队 -0.75' (or an explicit PASS); "
            "[4] '大小球：大 2.5' (or an explicit PASS); "
            "[5] '胜平负：主胜' where the value is 主胜、平、客胜、主不败、客不败 or PASS; "
            "[6] '总进球：2-3球' (or one exact integer, or PASS); "
            "[7] '置信度：0.68'. Never replace a prediction with a reason or evidence summary."
        )
    )
    w: list[str] = Field(description="Warnings and degraded-data notes; use an empty list only when there are none.")
    p: str = Field(description="Concise prediction reason based only on the current masked evidence.")


class ReplayPredictionBatchRequest(BaseModel):
    p: list[ReplayPredictionItem] = Field(
        min_length=1,
        max_length=3,
        description="One to three current-page matches. For every item, n and c must be copied verbatim from the same match.",
    )


class ReplayPredictionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    task_id: str
    status: str
    is_prediction: bool
    saved_matches: list[dict] = []
    prediction_commit: dict | None = None
    remaining_match_nos: list[int] = []
    must_continue: bool = False
    next_operation: str | None = None
    instruction: str | None = None
    report_url: str | None = None


class ReplayReportResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    task_id: str
    status: str
    ready: bool
    is_prediction: bool
    report: str | None = None
    prediction_commit: dict | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    prompt_binding: dict


class TaskStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    task_id: str
    start_date: str
    end_date: str
    status: str
    snapshot_id: str | None
    created_time: str
    replay_mode: bool = False
    blockers: list[str] = Field(default_factory=list)
    must_continue: bool = False
    next_operation: str | None = None
    instruction: str | None = None
    prediction_commit: dict | None = None


class ReportResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    task_id: str
    status: str
    snapshot_id: str
    date_range: str
    pollution_status: str
    generated_time: str
    summary: dict
    improvement_plan: dict
    matches: list[dict]
    report_markdown: str
    prompt_bundle: dict
    runtime_instruction: str


class ReplayRangeCompleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    control_state: str = "PAGE_SAVED"
    ready: bool | None = None
    task_ids: list[str] = Field(default_factory=list)
    pending_task_ids: list[str] = Field(default_factory=list)
    replay_mode: bool | None = None
    cursor: int | None = None
    saved_keys: list[int] = Field(default_factory=list)
    next_cursor: int | None = None
    has_more: bool = False
    total_matches: int = 0
    prediction_prompt_bundle: dict | None = None
    matches: list[dict] = Field(default_factory=list)
    required_module_order: list[str] = Field(default_factory=list)
    output_format: str | None = None
    must_continue: bool
    next_operation: str
    instruction: str
    task_id: str | None = None
    snapshot_id: str | None = None
    date_range: str | None = None
    pollution_status: str | None = None
    generated_time: str | None = None
    summary: dict | None = None
    prediction_commit_ids: list[str] = Field(default_factory=list)
    prompt_bundle: dict | None = None
    report_url: str | None = None


class ReplayRangeReportPageResponse(BaseModel):
    task_id: str
    status: str
    cursor: int
    next_cursor: int | None
    has_more: bool
    content: str
    must_continue: bool
    next_operation: str
    instruction: str


@app.get("/health", operation_id="healthCheck", response_model=HealthResponse)
def health() -> HealthResponse:
    return {"status": "ok", "service": "HH520 Insight AI", "version": "Backtest V1.3 Fresh Run",
            "prompt_binding": prompt_binding(_insight_prompt)}


def _prompted_report(report: dict) -> dict:
    return {**report, "prompt_bundle": _insight_prompt,
            "runtime_instruction": (
                "Apply the complete prompt_bundle.content, without shortening or skipping any required section, to interpret "
                "this server-generated report. Produce a detailed Chinese final response with per-match comparison, all 13 "
                "module audits, A-E root causes, calibration, limitations, and testable reversible recommendations. Keep all "
                "metrics unchanged and treat recommendations as non-automatic candidates only."
            )}


@app.post(
    "/backtest/run",
    operation_id="runBacktest",
    summary="用一条中文命令运行完整回测",
    response_model=ReportResponse,
    openapi_extra={"x-openai-isConsequential": False},
)
def run_backtest(request: RunRequest, _: None = Security(require_token)) -> dict:
    try:
        return _prompted_report(get_manager().run(request.command, request.prediction_commit_ids))
    except CommandError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/backtest/evaluate-replay",
    operation_id="evaluateReplayBacktest",
    summary="评价本次重新采集并脱敏后生成的历史重放预测",
    response_model=ReportResponse,
    openapi_extra={"x-openai-isConsequential": False},
)
def evaluate_replay(request: RunRequest, _: None = Security(require_token)) -> dict:
    if not request.prediction_commit_ids:
        raise HTTPException(status_code=422, detail="REPLAY_PREDICTION_COMMIT_REQUIRED")
    return run_backtest(request, _)


def _prediction_request(method: str, path: str, body: dict | None = None) -> dict:
    base = os.getenv("HH520_PREDICTION_INTERNAL_URL", "http://gateway:8765").rstrip("/")
    token = os.getenv("HH520_PREDICTION_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="REPLAY_PREDICTION_GATEWAY_NOT_CONFIGURED")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    retryable_statuses = {429, 500, 502, 503, 504}
    retry_delays = (0.25, 0.5, 0.75)
    for attempt in range(len(retry_delays) + 1):
        try:
            with urlopen(Request(base + path, data=data, headers=headers, method=method), timeout=5) as response:
                raw = response.read()
                if not raw or not raw.strip():
                    raise ValueError("empty response")
                payload = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
                if not isinstance(payload, dict) or not payload:
                    raise ValueError("invalid response")
                return payload
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail = payload.get("error") or payload.get("detail") or "REPLAY_GATEWAY_ERROR"
            except Exception:
                detail = "REPLAY_GATEWAY_ERROR"
            if exc.code not in retryable_statuses or attempt == len(retry_delays):
                raise HTTPException(status_code=exc.code, detail=detail) from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt == len(retry_delays):
                raise HTTPException(status_code=503, detail="REPLAY_GATEWAY_UNAVAILABLE") from exc
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            if attempt == len(retry_delays):
                raise HTTPException(status_code=502, detail="REPLAY_GATEWAY_INVALID_JSON") from exc
        time.sleep(retry_delays[attempt])


def _is_transient_gateway_error(exc: HTTPException) -> bool:
    detail = str(exc.detail)
    transport_errors = {
        "REPLAY_GATEWAY_UNAVAILABLE", "REPLAY_GATEWAY_INVALID_JSON", "REPLAY_GATEWAY_ERROR",
    }
    return (
        detail in transport_errors
        or exc.status_code in {429, 502, 504}
        or (exc.status_code in {500, 503}
            and any(marker in detail.lower() for marker in ("busy", "temporary", "timeout")))
    )


def _range_retry_response(request: ReplayRangeRequest, operation: str, detail: object) -> dict:
    if request.retry_attempt >= 4:
        raise HTTPException(status_code=503, detail={
            "error": "REPLAY_GATEWAY_RETRY_EXHAUSTED",
            "stage": operation,
            "cursor": request.cursor,
            "task_ids": request.task_ids,
            "last_error": str(detail),
        })
    control_state = "RETRY_RANGE_BUNDLE" if operation == "getReplayRangeBundle" else "RETRY_RANGE_COMPLETE"
    response = {
        "ready": False,
        "control_state": control_state,
        "replay_mode": True,
        "task_ids": request.task_ids,
        "pending_task_ids": [],
        "cursor": request.cursor,
        "next_cursor": request.cursor,
        "retry_attempt": request.retry_attempt + 1,
        "has_more": False,
        "must_continue": True,
        "next_operation": operation,
        "instruction": (
            f"Temporary gateway failure ({detail}). Call {operation} again immediately with the exact same command, "
            f"task_ids, cursor={request.cursor}, retry_attempt={request.retry_attempt + 1}"
            + (" and the same page predictions." if operation == "completeReplayRange" else ".")
            + " Do not reply to the user or change the cursor."
        ),
    }
    if operation == "completeReplayRange":
        response["status"] = "RETRY_REQUIRED"
    return response


def _decorate_replay_create(response: dict) -> dict:
    response["prediction_prompt_bundle"] = response.pop("prompt_bundle", None)
    response["insight_prompt_binding"] = prompt_binding(_insight_prompt)
    response["instruction"] = (
        "Apply prediction_prompt_bundle.execution_prompt during the replay prediction, continue with next_operation, "
        "then apply the bound Insight prompt returned with the final evaluation report. Do not reply before completion."
    )
    return response


def _child_request_id(parent_request_id: str, replay_date: str) -> str:
    digest = sha256(f"{parent_request_id}:{replay_date}".encode("utf-8")).hexdigest()[:32]
    return f"replay-{digest}"


def _normalized_replay_command(command) -> str:
    start = command.start_date.isoformat()
    end = command.end_date.isoformat()
    return f"回测 {start} 全部比赛" if start == end else f"回测 {start} 至 {end}"


def _resume_active_replay(existing: dict, command) -> dict:
    task_ids = existing["task_ids"]
    tasks = []
    for offset, task_id in enumerate(task_ids):
        replay_date = (command.start_date + timedelta(days=offset)).isoformat()
        child_command = f"回测 {replay_date} 全部比赛"
        tasks.append({
            "date": replay_date, "request_id": _child_request_id(existing["parent_request_id"], replay_date),
            "command": child_command, "task_id": task_id,
            "status_url": f"/v1/tasks/{task_id}", "report_url": f"/v1/tasks/{task_id}/report",
            "created": False, "must_continue": True, "next_operation": "getReplayRangeBundle",
            "instruction": "Use this task_id in the single range bundle call; do not poll it separately.",
            "prediction_prompt_bundle": None, "insight_prompt_binding": prompt_binding(_insight_prompt),
        })
    response = {
        "created": True, "must_continue": True, "next_operation": "getReplayRangeBundle",
        "prompt_bundle": None, "execution_status": "RESUMED",
        "range_run_id": existing["range_run_id"], "parent_request_id": existing["parent_request_id"],
        "start_date": command.start_date.isoformat(), "end_date": command.end_date.isoformat(),
        "day_count": len(task_ids), "created_count": len(task_ids), "failed_count": 0,
        "tasks": tasks, "errors": [], "cancelled_previous_task_ids": [],
    }
    if len(tasks) == 1:
        response["task_id"] = tasks[0]["task_id"]
        response["status_url"] = tasks[0]["status_url"]
        response["report_url"] = tasks[0]["report_url"]
    return _decorate_replay_create(response)


def _find_resumable_replay(command) -> dict | None:
    normalized = _normalized_replay_command(command)
    database = get_manager().db
    expected = (command.end_date - command.start_date).days + 1
    expected_dates = [
        (command.start_date + timedelta(days=offset)).isoformat() for offset in range(expected)
    ]
    healthy_statuses = {
        "CREATED", "CHECKING_DATA", "STARTUP_CHECK", "COLLECTING", "SANITIZING",
        "SNAPSHOT_READY", "RUNNING", "EVALUATING", "AWAITING_GPT", "COMPLETED", "REPORT_READY",
    }
    existing = database.get_active_replay_run(normalized)
    if not existing:
        for candidate in database.list_active_replay_runs():
            task_ids = candidate.get("task_ids", [])
            if len(task_ids) != expected:
                continue
            matched = True
            for task_id, expected_date in zip(task_ids, expected_dates):
                try:
                    status = _prediction_request("GET", f"/v1/tasks/{task_id}")
                except HTTPException as exc:
                    if exc.status_code in {400, 404, 410}:
                        matched = False
                        break
                    raise
                payload = status.get("payload") if isinstance(status.get("payload"), dict) else {}
                if status.get("status") not in healthy_statuses or payload.get("date") != expected_date:
                    matched = False
                    break
            if matched:
                database.update_replay_run_command(candidate["range_run_id"], normalized)
                candidate["command"] = normalized
                return candidate
        return None
    task_ids = existing.get("task_ids", [])
    stale = len(task_ids) != expected
    if not stale:
        for task_id in task_ids:
            try:
                status = _prediction_request("GET", f"/v1/tasks/{task_id}")
            except HTTPException as exc:
                if exc.status_code in {400, 404, 410}:
                    stale = True
                    break
                raise
            if status.get("status") not in healthy_statuses:
                stale = True
                break
    if stale:
        for task_id in task_ids:
            try:
                _prediction_request("POST", f"/v1/tasks/{task_id}/cancel", {})
            except HTTPException:
                pass
        database.retire_replay_run(existing["range_run_id"])
        return None
    return existing


def _activate_latest_replay(parent_request_id: str, command: str,
                            task_ids: list[str]) -> tuple[str, list[str]]:
    range_run_id = f"range-{sha256(f'{parent_request_id}:{command}'.encode('utf-8')).hexdigest()[:20]}"
    previous = get_manager().db.activate_replay_run(range_run_id, parent_request_id, command, task_ids)
    cancelled = []
    for task_id in previous:
        try:
            _prediction_request("POST", f"/v1/tasks/{task_id}/cancel", {})
            cancelled.append(task_id)
        except HTTPException:
            # The DB ownership switch is authoritative. A temporary cleanup
            # failure must not prevent the new range from running.
            pass
    return range_run_id, cancelled


def _creation_retry_response(request: ReplayTaskRequest, command, exc: HTTPException) -> dict:
    detail = exc.detail
    if request.retry_attempt >= 4:
        raise HTTPException(status_code=503, detail={
            "error": "REPLAY_GATEWAY_RETRY_EXHAUSTED", "stage": "createReplayTask",
            "request_id": request.request_id, "last_error": str(detail),
        })
    return {
        "created": False, "must_continue": True, "next_operation": "createReplayTask",
        "instruction": (
            "Temporary gateway failure. Call createReplayTask again immediately with the exact same "
            f"request_id={request.request_id}, command and retry_attempt={request.retry_attempt + 1}; "
            "do not reply to the user."
        ),
        "insight_prompt_binding": prompt_binding(_insight_prompt),
        "execution_status": "RETRY_REQUIRED", "parent_request_id": request.request_id,
        "start_date": command.start_date.isoformat(), "end_date": command.end_date.isoformat(),
        "day_count": (command.end_date - command.start_date).days + 1,
        "created_count": 0, "failed_count": 1, "retry_attempt": request.retry_attempt + 1,
        "errors": [{
            "date": command.start_date.isoformat(), "request_id": request.request_id,
            "command": _normalized_replay_command(command), "http_status": exc.status_code,
            "error": str(detail),
        }],
    }


@app.post(
    "/replay/tasks",
    operation_id="createReplayTask",
    summary="启动一至七天历史重放；范围命令在服务器端拆成逐日全新任务",
    response_model=ReplayCreateResponse,
    response_model_exclude_none=True,
    openapi_extra={"x-openai-isConsequential": False},
)
def create_replay_task(request: ReplayTaskRequest, _: None = Security(require_token)) -> dict:
    try:
        command = parse_command(request.command)
    except CommandError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc

    day_count = (command.end_date - command.start_date).days + 1
    normalized_command = _normalized_replay_command(command)
    try:
        resumable = _find_resumable_replay(command)
    except HTTPException as exc:
        if _is_transient_gateway_error(exc):
            return _creation_retry_response(request, command, exc)
        raise
    if resumable:
        return _resume_active_replay(resumable, command)
    if day_count == 1:
        try:
            response = _decorate_replay_create(_prediction_request("POST", "/v1/tasks", {
                "request_id": request.request_id, "command": request.command,
            }))
        except HTTPException as exc:
            if not _is_transient_gateway_error(exc):
                raise
            return _creation_retry_response(request, command, exc)
        range_run_id, cancelled = _activate_latest_replay(
            request.request_id, normalized_command, [response["task_id"]]
        )
        response["range_run_id"] = range_run_id
        response["cancelled_previous_task_ids"] = cancelled
        return response

    tasks: list[dict] = []
    errors: list[dict] = []
    transient_only = True
    for offset in range(day_count):
        replay_date = (command.start_date + timedelta(days=offset)).isoformat()
        child_request_id = _child_request_id(request.request_id, replay_date)
        child_command = f"回测 {replay_date} 全部比赛"
        try:
            child = _prediction_request("POST", "/v1/tasks", {
                "request_id": child_request_id,
                "command": child_command,
            })
            tasks.append({
                "date": replay_date,
                "request_id": child_request_id,
                "command": child_command,
                "task_id": child["task_id"],
                "status_url": child["status_url"],
                "report_url": child["report_url"],
                "created": child.get("created", True),
                "must_continue": True,
                "next_operation": "getReplayRangeBundle",
                "instruction": "Use this task_id in the single range bundle call; do not poll it separately.",
                "prediction_prompt_bundle": None,
                "insight_prompt_binding": prompt_binding(_insight_prompt),
            })
        except HTTPException as exc:
            transient_only = transient_only and _is_transient_gateway_error(exc)
            errors.append({
                "date": replay_date,
                "request_id": child_request_id,
                "command": child_command,
                "http_status": exc.status_code,
                "error": str(exc.detail),
            })

    created_count = len(tasks)
    failed_count = len(errors)
    if failed_count and transient_only and request.retry_attempt >= 4:
        raise HTTPException(status_code=503, detail={
            "error": "REPLAY_GATEWAY_RETRY_EXHAUSTED", "stage": "createReplayTask",
            "request_id": request.request_id, "last_errors": errors,
        })
    if failed_count and not transient_only:
        for task in tasks:
            try:
                _prediction_request("POST", f"/v1/tasks/{task['task_id']}/cancel", {})
            except HTTPException:
                pass
    range_run_id, cancelled = (None, [])
    if created_count == day_count:
        range_run_id, cancelled = _activate_latest_replay(
            request.request_id, normalized_command, [task["task_id"] for task in tasks]
        )
    execution_status = (
        "CREATED" if failed_count == 0 else
        "RETRY_REQUIRED" if transient_only else
        ("PARTIAL" if created_count else "FAILED")
    )
    can_continue = created_count == day_count or (failed_count > 0 and transient_only)
    return {
        "created": created_count == day_count,
        "must_continue": can_continue,
        "next_operation": (
            "getReplayRangeBundle" if created_count == day_count else
            "createReplayTask" if transient_only else None
        ),
        "instruction": (
            "Temporary gateway failure: call createReplayTask again immediately with the exact same request_id and "
            f"command, using retry_attempt={request.retry_attempt + 1}; do not cancel created children and do not "
            "reply to the user."
            if failed_count and transient_only else
            "If errors is non-empty, stop and report each exact date, HTTP status, and error code. Otherwise call "
            "getReplayRangeBundle with the original command and every returned task_id in ascending date order. "
            "While must_continue=true, execute next_operation immediately in the same assistant turn and never reply "
            "with a waiting or pending message. Never poll tasks individually and never reuse an older task or commit."
        ),
        "insight_prompt_binding": prompt_binding(_insight_prompt),
        "execution_status": execution_status,
        "range_run_id": range_run_id,
        "cancelled_previous_task_ids": cancelled,
        "parent_request_id": request.request_id,
        "start_date": command.start_date.isoformat(),
        "end_date": command.end_date.isoformat(),
        "day_count": day_count,
        "created_count": created_count,
        "failed_count": failed_count,
        "retry_attempt": request.retry_attempt + 1 if failed_count and transient_only else 0,
        "tasks": tasks,
        "errors": errors,
    }


@app.get("/replay/tasks/{task_id}", operation_id="getReplayTask", summary="轮询重放采集状态", response_model=ReplayStatusResponse)
def get_replay_task(task_id: str, _: None = Security(require_token)) -> dict:
    response = _prediction_request("GET", f"/v1/tasks/{task_id}")
    # Keep model turns focused on useful state changes instead of rapid no-op
    # polling. Bound the wait so one Action request cannot hang indefinitely.
    deadline = time.monotonic() + 24
    while response.get("status") in {"CREATED", "STARTUP_CHECK", "COLLECTING"} and time.monotonic() < deadline:
        time.sleep(2)
        response = _prediction_request("GET", f"/v1/tasks/{task_id}")
    if response.get("status") in {"CREATED", "STARTUP_CHECK", "COLLECTING"}:
        response["must_continue"] = True
        response["next_operation"] = "getReplayTask"
        response["instruction"] = (
            "Collection is still running after server-side waiting. Call getReplayTask again immediately. "
            "Do not reply to the user and do not report a pending status."
        )
    if "prompt_bundle" in response:
        response["prediction_prompt_bundle"] = response.pop("prompt_bundle")
    response["insight_prompt_binding"] = prompt_binding(_insight_prompt)
    return response


def _range_task_ids(request: ReplayRangeRequest) -> None:
    command = parse_command(request.command)
    expected = (command.end_date - command.start_date).days + 1
    if len(request.task_ids) != expected or len(set(request.task_ids)) != expected:
        raise HTTPException(status_code=422, detail="RANGE_TASK_COUNT_MISMATCH")
    if any(not re.fullmatch(r"[a-f0-9]{32}", task_id) for task_id in request.task_ids):
        raise HTTPException(status_code=422, detail="INVALID_REPLAY_TASK_ID")


def _excerpt(value: object, limit: int = 90) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    head = max(36, int(limit * 0.68))
    tail = max(18, limit - head - 3)
    return value[:head] + " … " + value[-tail:]


def _compact_section(category: str, value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    if category == "mixed_data":
        match = re.search(r"([^\n/;]{1,28})\s+VS\s+([^\n/;。]{1,28})", text)
        return f"M:{match.group(1).strip()}/{match.group(2).strip()}" if match else ""
    if category == "asian_handicap_changes":
        odds = re.search(r"初盘主\s*([\d.]+)\s*初盘平\s*([\d.]+)\s*初盘客\s*([\d.]+)", text)
        waters = re.findall(r"(?:初盘|即时|样本)[主客]水:\s*(-?[\d.]+)", text)
        lines = re.findall(r"\|\s*([^|\n]{1,12})\s*\|\s*\d+\s*\|\s*(-?[\d.]+)\s*\|\s*(-?[\d.]+)\s*\|", text)
        parts = ["AH"]
        if odds:
            parts.append("o=" + "/".join(odds.groups()))
        if waters:
            parts.append("w=" + "/".join(waters[:6]))
        if lines:
            parts.append("l=" + ",".join(f"{name.strip()}:{home}/{away}" for name, home, away in lines[-3:]))
        return ";".join(parts)
    if category == "score_odds_changes":
        if "模块降级" in text:
            return "SC:D"
        numbers = re.findall(r"(?<!\d)-?\d+(?:\.\d+)?", text)
        return "SC:" + "/".join(numbers[:12])
    if category == "predicted_lineup":
        ratings = re.findall(r"\|\s*[^|\n]+\|\s*(-?[\d.]+)\s*\|\s*(-?[\d.]+)\s*\|", text)
        return "PL:" + ",".join(f"{rating}/{value}" for rating, value in ratings[:8]) if ratings else "PL:D"
    if category == "historical_lineup_ratings":
        formations = re.findall(r"阵型：\s*([\d-]+)", text)
        totals = re.findall(r"(?:首发|替补|全队)合计：\*\*身价\s*([\d.]+)\*\*，\*\*评分\s*([\d.]+)\*\*", text)
        parts = ["HL"]
        if formations:
            parts.append("f=" + "/".join(formations[:4]))
        if totals:
            parts.append("t=" + ",".join(f"{value}/{rating}" for value, rating in totals[:6]))
        return ";".join(parts) if len(parts) > 1 else "HL:D"
    return ""


MODULE_IDS = [
    "data_consistency_audit", "data_confidence_score", "water_market", "team_analysis",
    "league_analysis", "company_source_analysis", "correct_score", "soccerstats_htft",
    "odds_abnormal_detection", "match_risk_engine", "conflict_detection",
    "cross_model_interaction", "calibration",
]


def _evidence_audit(item: dict) -> tuple[str, dict[str, list[str]]]:
    """Return the strongest defensible module mask and module-scoped evidence refs."""
    by_category: dict[str, list[str]] = {}
    usable: set[str] = set()
    for section in item.get("sections", []):
        category = str(section.get("category") or "data")
        compact = _compact_section(category, section.get("content", ""))
        ref = section.get("source_url") or f"{category}:{str(section.get('source_sha256', ''))[:12]}"
        by_category.setdefault(category, []).append(str(ref))
        if compact and not compact.endswith(":D") and compact not in {"AH", "SC:"}:
            usable.add(category)

    def refs(*patterns: str) -> list[str]:
        values = [ref for category, found in by_category.items()
                  if any(pattern in category for pattern in patterns) for ref in found]
        return list(dict.fromkeys(values))

    def has_usable(*patterns: str) -> bool:
        return any(any(pattern in category for pattern in patterns) for category in usable)

    all_refs = list(dict.fromkeys(ref for values in by_category.values() for ref in values))
    market_refs = refs("asian", "score", "odds", "market")
    team_refs = refs("lineup", "team", "mixed_data")
    league_refs = refs("league")
    htft_refs = refs("soccerstats", "htft", "half")
    model_refs = refs("model")
    calibration_refs = refs("calibration", "probability")
    masked = bool((item.get("result_mask") or {}).get("applied")) if isinstance(item.get("result_mask"), dict) else bool(item.get("result_mask"))
    available = [
        masked and bool(item.get("identity_check")) and bool(all_refs),
        len(usable) >= 3,
        has_usable("asian", "market"),
        has_usable("lineup", "team"),
        has_usable("league"),
        has_usable("asian", "score", "odds", "market"),
        has_usable("score"),
        has_usable("soccerstats", "htft", "half"),
        has_usable("asian", "score", "odds", "market"),
        len(usable) >= 2,
        len(usable) >= 2,
        bool(model_refs),
        bool(calibration_refs),
    ]
    module_refs = {
        MODULE_IDS[0]: all_refs, MODULE_IDS[1]: all_refs, MODULE_IDS[2]: refs("asian", "market"),
        MODULE_IDS[3]: team_refs, MODULE_IDS[4]: league_refs, MODULE_IDS[5]: market_refs,
        MODULE_IDS[6]: refs("score"), MODULE_IDS[7]: htft_refs, MODULE_IDS[8]: market_refs,
        MODULE_IDS[9]: all_refs, MODULE_IDS[10]: all_refs, MODULE_IDS[11]: model_refs,
        MODULE_IDS[12]: calibration_refs,
    }
    return "".join("C" if value else "D" for value in available), module_refs


def _range_batches(task_ids: list[str]) -> list[dict]:
    return [_prediction_request("GET", f"/v1/tasks/{task_id}/analysis-batch") for task_id in task_ids]


def _ready_range_prefix(task_ids: list[str], statuses: list[dict]) -> tuple[list[str], list[str]]:
    """Return the stable ready date prefix and every task still collecting."""
    ready_states = {"AWAITING_GPT", "COMPLETED"}
    pending = [task_id for task_id, item in zip(task_ids, statuses)
               if item.get("status") not in ready_states]
    prefix = []
    for task_id, item in zip(task_ids, statuses):
        if item.get("status") not in ready_states:
            break
        prefix.append(task_id)
    return prefix, pending


def _validate_range_task_dates(request: ReplayRangeRequest, statuses: list[dict]) -> None:
    command = parse_command(request.command)
    expected_dates = [
        (command.start_date + timedelta(days=offset)).isoformat()
        for offset in range((command.end_date - command.start_date).days + 1)
    ]
    actual_dates = [
        (item.get("payload") or {}).get("date") if isinstance(item.get("payload"), dict) else None
        for item in statuses
    ]
    if actual_dates != expected_dates:
        raise HTTPException(status_code=409, detail={
            "error": "RANGE_TASK_DATE_MISMATCH",
            "expected_dates": expected_dates,
            "actual_dates": actual_dates,
        })


# Keep each model-facing action comfortably below tool/context limits while
# preserving the per-match frozen analysis and the server's 3-item persistence.
RANGE_PAGE_SIZE = 6
REPORT_PAGE_CHARS = 20000

RANGE_OUTPUT_FORMAT = (
    "Return every k exactly once in p using: "
    "k|score1,score2,score3|htft1,htft2,htft3|asian|ou|1x2|goals|confidence|module_codes. "
    "H/D/A mean home/draw/away; P means PASS. q is the evidence ceiling: copy every q=D as D; "
    "a q=C may still be D after analysis. Never copy a fixed all-C template."
)


def _compact_range_match(key: int, item: dict) -> dict:
    evidence = []
    for section in item.get("sections", []):
        category = section.get("category", "data")
        compact = _compact_section(category, section.get("content", ""))
        if compact:
            evidence.append(f"{category}:{compact}")
    result_mask = item.get("result_mask") or {}
    return {
        "k": key,
        "d": item.get("date"),
        "n": item.get("match_no"),
        "c": item.get("code"),
        "t": item.get("kickoff_at_raw"),
        "i": _excerpt(item.get("identity_check", {}), 100),
        "e": evidence,
        "q": _evidence_audit(item)[0],
        "mask": bool(result_mask.get("applied")) if isinstance(result_mask, dict) else bool(result_mask),
    }


def _range_page_instruction() -> str:
    return (
        "Load and apply the complete prediction_prompt_bundle.execution_prompt. Execute every frozen stage and all 13 "
        "modules independently for every returned match; do not shorten, skip, merge, or copy module conclusions. On "
        "later pages continue using the complete prompt loaded at cursor 0. The compact p syntax is transport only. "
        "control_state=PREDICT_AND_SUBMIT means this page is ready now: predict every returned match and call "
        "completeReplayRange with this exact cursor and page predictions; do not wait or reply first. "
        "has_more only means another page follows after submission."
    )


@app.post(
    "/replay/range/bundle",
    operation_id="getReplayRangeBundle",
    summary="一次读取整个日期范围的紧凑脱敏证据",
    response_model=ReplayRangeBundleResponse,
    openapi_extra={"x-openai-isConsequential": False},
)
def get_replay_range_bundle(request: ReplayRangeRequest, _: None = Security(require_token)) -> dict:
    """Collapse range polling and evidence transfer into one repeatable Action."""
    _range_task_ids(request)
    deadline = time.monotonic() + 24
    while True:
        try:
            statuses = [_prediction_request("GET", f"/v1/tasks/{task_id}") for task_id in request.task_ids]
        except HTTPException as exc:
            if _is_transient_gateway_error(exc):
                return _range_retry_response(request, "getReplayRangeBundle", exc.detail)
            raise
        _validate_range_task_dates(request, statuses)
        failed = [item for item in statuses
                  if item.get("status") in {"FAILED", "BLOCKED", "CANCELLED", "PARTIAL"}]
        if failed:
            raise HTTPException(status_code=409, detail={
                "error": "RANGE_REPLAY_TASK_FAILED",
                "tasks": [{"task_id": item.get("id"), "status": item.get("status"),
                           "blockers": item.get("blockers", [])} for item in failed],
            })
        ready_task_ids, pending = _ready_range_prefix(request.task_ids, statuses)
        if ready_task_ids or not pending or time.monotonic() >= deadline:
            break
        time.sleep(2)
    if pending and not ready_task_ids:
        return {
            "ready": False,
            "control_state": "WAIT_FOR_DATA",
            "pending_task_ids": pending,
            "must_continue": True,
            "next_operation": "getReplayRangeBundle",
            "instruction": (
                "Call getReplayRangeBundle again immediately with the same command, all original task_ids, and cursor. "
                "must_continue=true forbids replying to the user with progress, waiting, or pending status."
            ),
        }

    try:
        batches = _range_batches(ready_task_ids)
    except HTTPException as exc:
        if _is_transient_gateway_error(exc):
            return _range_retry_response(request, "getReplayRangeBundle", exc.detail)
        raise
    matches = []
    key = 1
    for task_id, batch in zip(ready_task_ids, batches):
        for item in batch.get("matches", []):
            matches.append(_compact_range_match(key, item))
            key += 1
    total_matches = len(matches)
    if request.cursor >= total_matches and pending:
        return {
            "ready": False,
            "control_state": "WAIT_FOR_DATA",
            "pending_task_ids": pending,
            "must_continue": True,
            "next_operation": "getReplayRangeBundle",
            "instruction": (
                "The stable ready-date prefix is fully saved. Call getReplayRangeBundle again immediately with the "
                "same command, all original task_ids, and cursor. Do not reply to the user."
            ),
        }
    if request.cursor >= total_matches:
        raise HTTPException(status_code=422, detail="RANGE_CURSOR_INVALID")
    page_matches = matches[request.cursor:request.cursor + RANGE_PAGE_SIZE]
    next_cursor = request.cursor + len(page_matches)
    has_more = next_cursor < total_matches or bool(pending)
    prompt_bundle = (next((batch.get("prompt_bundle") for batch in batches if batch.get("prompt_bundle")), None)
                     if request.cursor == 0 else None)
    return {
        "ready": True,
        "control_state": "PREDICT_AND_SUBMIT",
        "replay_mode": True,
        "task_ids": request.task_ids,
        "cursor": request.cursor,
        "next_cursor": next_cursor if has_more else None,
        "has_more": has_more,
        "total_matches": total_matches,
        "prediction_prompt_bundle": prompt_bundle,
        "must_continue": True,
        "required_module_order": MODULE_IDS,
        "matches": page_matches,
        "output_format": RANGE_OUTPUT_FORMAT,
        "next_operation": "completeReplayRange",
        "instruction": _range_page_instruction(),
    }


def _decode_ultra(line: str) -> tuple[int, list[str], str]:
    parts = [part.strip() for part in line.split("|")]
    if len(parts) != 9:
        raise HTTPException(status_code=422, detail="RANGE_PREDICTION_FORMAT_INVALID")
    try:
        key = int(parts[0])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="RANGE_PREDICTION_KEY_INVALID") from exc
    score_field = parts[1].upper()
    if score_field in {"P", "PASS"}:
        scores = []
    else:
        scores = [value.strip().replace("-", ":").replace("：", ":")
                  for value in re.split(r"[,，]", parts[1])]
        if len(scores) != 3 or any(not re.fullmatch(r"\d{1,2}:\d{1,2}", value) for value in scores):
            raise HTTPException(status_code=422, detail=f"RANGE_SCORE_INVALID:{key}")
    htft_field = parts[2].upper()
    if htft_field in {"P", "PASS"}:
        htft = []
    else:
        htft = []
        htft_letters = {"胜": "H", "平": "D", "负": "A"}
        for raw_value in re.split(r"[,，;；]", parts[2]):
            value = raw_value.strip().upper()
            for source, target in htft_letters.items():
                value = value.replace(source, target)
            value = re.sub(r"[\s/\\:：\-]", "", value)
            htft.append(value)
        if len(htft) != 3 or any(not re.fullmatch(r"[HDA]{2}", value) for value in htft):
            raise HTTPException(status_code=422, detail=f"RANGE_HTFT_INVALID:{key}")
    if not re.fullmatch(r"[CD]{13}", parts[8]):
        raise HTTPException(status_code=422, detail=f"RANGE_MODULE_CODES_INVALID:{key}")
    letter = {"H": "胜", "D": "平", "A": "负"}
    result = {"H": "主胜", "D": "平", "A": "客胜", "HD": "主不败", "AD": "客不败", "P": "PASS"}
    asian = parts[3]
    asian_text = "PASS" if asian == "P" else (("主队 " if asian.startswith("H") else "客队 ") + asian[1:])
    total = parts[4]
    total_text = "PASS" if total == "P" else (("大 " if total.startswith("O") else "小 ") + total[1:])
    goals = "PASS" if parts[6] == "P" else parts[6] + ("球" if not parts[6].endswith("球") else "")
    try:
        confidence = max(0, min(100, int(parts[7]))) / 100
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"RANGE_CONFIDENCE_INVALID:{key}") from exc
    values = [
        "精准比分 Top3：" + (" / ".join(value.replace(":", "-") for value in scores) if scores else "PASS"),
        "半全场 Top3：" + (" / ".join(f"{letter[value[0]]}/{letter[value[1]]}" for value in htft) if htft else "PASS"),
        "亚洲盘：" + asian_text,
        "大小球：" + total_text,
        "胜平负：" + result.get(parts[5], "PASS"),
        "总进球：" + goals,
        f"置信度：{confidence:.2f}",
    ]
    return key, values, parts[8]


@app.post(
    "/replay/range/complete",
    operation_id="completeReplayRange",
    summary="一次保存整个范围预测并返回完整但去重的最终回测报告",
    response_model=ReplayRangeCompleteResponse,
    openapi_extra={"x-openai-isConsequential": False},
)
def complete_replay_range(request: ReplayRangeCompleteRequest, _: None = Security(require_token)) -> dict:
    """Persist one small page, then close the range only after every page is saved."""
    _range_task_ids(request)
    try:
        statuses = [_prediction_request("GET", f"/v1/tasks/{task_id}") for task_id in request.task_ids]
    except HTTPException as exc:
        if _is_transient_gateway_error(exc):
            return _range_retry_response(request, "completeReplayRange", exc.detail)
        raise
    _validate_range_task_dates(request, statuses)
    failed = [item for item in statuses
              if item.get("status") in {"FAILED", "BLOCKED", "CANCELLED", "PARTIAL"}]
    if failed:
        raise HTTPException(status_code=409, detail={
            "error": "RANGE_REPLAY_TASK_FAILED",
            "tasks": [{"task_id": item.get("id"), "status": item.get("status"),
                       "blockers": item.get("blockers", [])} for item in failed],
        })
    ready_task_ids, pending = _ready_range_prefix(request.task_ids, statuses)
    try:
        batches = _range_batches(ready_task_ids)
    except HTTPException as exc:
        if _is_transient_gateway_error(exc):
            return _range_retry_response(request, "completeReplayRange", exc.detail)
        raise
    index = []
    for task_id, batch in zip(ready_task_ids, batches):
        for item in batch.get("matches", []):
            index.append((task_id, item))
    if request.cursor >= len(index):
        raise HTTPException(status_code=422, detail="RANGE_CURSOR_INVALID")
    page_end = min(request.cursor + RANGE_PAGE_SIZE, len(index))
    page_keys = set(range(request.cursor + 1, page_end + 1))
    supplied = {}
    for line in request.p:
        key, values, module_codes = _decode_ultra(line)
        if key in supplied:
            raise HTTPException(status_code=422, detail=f"RANGE_PREDICTION_DUPLICATE:{key}")
        supplied[key] = (values, module_codes)
    if set(supplied) != page_keys:
        raise HTTPException(status_code=422, detail="RANGE_PREDICTION_SET_INCOMPLETE")

    grouped: dict[str, list[dict]] = {task_id: [] for task_id in request.task_ids}
    for key in sorted(page_keys):
        task_id, item = index[key - 1]
        evidence_mask, module_refs = _evidence_audit(item)
        values, claimed_codes = supplied[key]
        module_codes = "".join("C" if claim == ceiling == "C" else "D"
                               for claim, ceiling in zip(claimed_codes, evidence_mask))
        forced = [MODULE_IDS[position] for position, (claim, final)
                  in enumerate(zip(claimed_codes, module_codes)) if claim != final]
        refs = [f"{module_id}::{ref}" for module_id, values_for_module in module_refs.items()
                for ref in (values_for_module or ["unavailable"])]
        grouped[task_id].append({
            "n": item["match_no"],
            "c": item["code"],
            "m": module_codes,
            "e": refs,
            "r": values,
            "w": (["证据不足模块已降级"] if "D" in module_codes else [])
                 + (["服务器纠正无证据的 COMPLETED：" + ",".join(forced)] if forced else []),
            "p": "已按完整冻结提示词独立分析十三模块；模块状态经服务器证据审计后冻结。",
        })

    for task_id, predictions in grouped.items():
        if not predictions:
            continue
        status = _prediction_request("GET", f"/v1/tasks/{task_id}")
        if status.get("status") == "COMPLETED" and status.get("prediction_commit"):
            continue
        for offset in range(0, len(predictions), 3):
            try:
                _prediction_request("POST", f"/v1/tasks/{task_id}/analysis-min", {
                    "p": predictions[offset:offset + 3],
                })
            except HTTPException as exc:
                if _is_transient_gateway_error(exc):
                    return _range_retry_response(request, "completeReplayRange", exc.detail)
                raise

    if page_end < len(index) or pending:
        if page_end < len(index):
            next_page_end = min(page_end + RANGE_PAGE_SIZE, len(index))
            next_has_more = next_page_end < len(index) or bool(pending)
            return {
                "status": "PAGE_SAVED",
                "control_state": "PREDICT_AND_SUBMIT",
                "ready": True,
                "replay_mode": True,
                "task_ids": request.task_ids,
                "pending_task_ids": pending,
                "cursor": page_end,
                "saved_keys": sorted(page_keys),
                "next_cursor": next_page_end if next_has_more else None,
                "has_more": next_has_more,
                "total_matches": len(index),
                "matches": [
                    _compact_range_match(key, item)
                    for key, (_, item) in enumerate(index[page_end:next_page_end], page_end + 1)
                ],
                "required_module_order": MODULE_IDS,
                "output_format": RANGE_OUTPUT_FORMAT,
                "must_continue": True,
                "next_operation": "completeReplayRange",
                "instruction": _range_page_instruction(),
            }
        return {
            "status": "PAGE_SAVED",
            "control_state": "LOAD_NEXT_PAGE",
            "task_ids": request.task_ids,
            "saved_keys": sorted(page_keys),
            "next_cursor": page_end,
            "has_more": True,
            "must_continue": True,
            "next_operation": "getReplayRangeBundle",
            "instruction": (
                f"Call getReplayRangeBundle immediately with cursor={page_end}, the same command and task_ids. "
                "control_state=LOAD_NEXT_PAGE means continue pagination; do not wait or reply. Continue using the "
                "complete frozen prediction prompt loaded at cursor 0."
            ),
        }

    commits = []
    for task_id in request.task_ids:
        try:
            status = _prediction_request("GET", f"/v1/tasks/{task_id}")
            if status.get("status") != "COMPLETED" or not status.get("prediction_commit"):
                status = _prediction_request("POST", f"/v1/tasks/{task_id}/finalize-compact", {})
        except HTTPException as exc:
            if _is_transient_gateway_error(exc):
                return _range_retry_response(request, "completeReplayRange", exc.detail)
            raise
        commit = status.get("prediction_commit") or {}
        if not commit.get("prediction_commit_id"):
            raise HTTPException(status_code=409, detail=f"RANGE_COMMIT_MISSING:{task_id}")
        commits.append(commit["prediction_commit_id"])

    report = get_manager().run(request.command, commits)
    get_manager().db.complete_replay_run(request.task_ids)
    task_id = report["task_id"]
    return {
        "status": "REPORT_READY",
        "control_state": "REPORT_READY",
        "task_ids": request.task_ids,
        "saved_keys": sorted(page_keys),
        "has_more": False,
        "must_continue": True,
        "next_operation": "getReplayRangeReportPage",
        "instruction": (
            "Call getReplayRangeReportPage immediately with this task_id and cursor=0. Continue until has_more=false; "
            "do not reply before all report pages are read. Then apply the complete prompt_bundle.content and output the "
            "detailed report without shortening or skipping required sections."
        ),
        "task_id": task_id,
        "snapshot_id": report["snapshot_id"],
        "date_range": report["date_range"],
        "pollution_status": report["pollution_status"],
        "generated_time": report["generated_time"],
        "summary": report["summary"],
        "prediction_commit_ids": commits,
        "prompt_bundle": _insight_prompt,
        "report_url": f"/backtest/report/{task_id}",
    }


@app.get(
    "/replay/range/report/{task_id}/page",
    operation_id="getReplayRangeReportPage",
    summary="分页读取完整范围回测报告",
    response_model=ReplayRangeReportPageResponse,
)
def get_replay_range_report_page(task_id: str, cursor: int = 0,
                                 _: None = Security(require_token)) -> dict:
    report = get_manager().report(task_id)
    if report is None:
        raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND")
    markdown = report["report_markdown"]
    if cursor < 0 or cursor >= len(markdown):
        raise HTTPException(status_code=422, detail="REPORT_CURSOR_INVALID")
    end = min(cursor + REPORT_PAGE_CHARS, len(markdown))
    if end < len(markdown):
        newline = markdown.rfind("\n", cursor, end)
        if newline > cursor:
            end = newline + 1
    has_more = end < len(markdown)
    return {
        "task_id": task_id,
        "status": "REPORT_READY",
        "cursor": cursor,
        "next_cursor": end if has_more else None,
        "has_more": has_more,
        "content": markdown[cursor:end],
        "must_continue": has_more,
        "next_operation": "getReplayRangeReportPage" if has_more else "final_response",
        "instruction": (
            f"Call getReplayRangeReportPage immediately with cursor={end}; do not reply yet."
            if has_more else
            "All report pages are loaded. Apply the complete Insight prompt already returned by completeReplayRange and "
            "output the complete detailed Chinese report now."
        ),
    }


@app.get("/replay/tasks/{task_id}/analysis-page", operation_id="getReplayAnalysisPage", summary="读取最多两场已脱敏赛前证据；源网站日期目录为日期最高优先级", response_model=ReplayAnalysisPageResponse)
def get_replay_analysis_page(task_id: str, cursor: int = 0, _: None = Security(require_token)) -> dict:
    response = _prediction_request("GET", f"/v1/tasks/{task_id}/analysis-page?cursor={cursor}")
    if "prompt_bundle" in response and response["prompt_bundle"] is not None:
        response["prediction_prompt_bundle"] = response.pop("prompt_bundle")
    response["insight_prompt_binding"] = prompt_binding(_insight_prompt)
    return response


@app.post("/replay/tasks/{task_id}/analysis-min", operation_id="saveReplayPredictionBatch", summary="不可变保存一至三场重放预测", response_model=ReplayPredictionResponse, openapi_extra={"x-openai-isConsequential": False})
def save_replay_prediction_batch(task_id: str, payload: ReplayPredictionBatchRequest, _: None = Security(require_token)) -> dict:
    return _prediction_request("POST", f"/v1/tasks/{task_id}/analysis-min", payload.model_dump())


@app.post("/replay/tasks/{task_id}/finalize", operation_id="finalizeReplayPrediction", summary="完成重放 Prediction Commit", response_model=ReplayPredictionResponse, openapi_extra={"x-openai-isConsequential": False})
def finalize_replay_prediction(task_id: str, _: None = Security(require_token)) -> dict:
    return _prediction_request("POST", f"/v1/tasks/{task_id}/finalize-compact", {})


@app.get("/replay/tasks/{task_id}/report", operation_id="getReplayPredictionReport", summary="读取重放预测报告", response_model=ReplayReportResponse)
def get_replay_prediction_report(task_id: str, _: None = Security(require_token)) -> dict:
    return _prediction_request("GET", f"/v1/tasks/{task_id}/report")


@app.get("/backtest/status/{task_id}", operation_id="getBacktestStatus", response_model=TaskStatusResponse)
def get_status(task_id: str, _: None = Security(require_token)) -> dict:
    task = get_manager().status(task_id)
    if task is not None:
        return task
    replay = _prediction_request("GET", f"/v1/tasks/{task_id}")
    replay_date = str((replay.get("payload") or {}).get("date") or "UNKNOWN")
    status = replay.get("status", "UNKNOWN")
    must_continue = status in {"CREATED", "STARTUP_CHECK", "COLLECTING", "AWAITING_GPT"}
    return {
        "task_id": replay.get("id", task_id),
        "start_date": replay_date,
        "end_date": replay_date,
        "status": status,
        "snapshot_id": (replay.get("input_ref") or {}).get("snapshot_id"),
        "created_time": str(replay.get("created", "")),
        "replay_mode": True,
        "blockers": replay.get("blockers", []),
        "must_continue": must_continue,
        "next_operation": "getReplayRangeBundle" if must_continue else None,
        "instruction": (
            "This is a Replay task, not a missing backtest report task. Continue the active range workflow with "
            "getReplayRangeBundle; do not recreate tasks and do not report TASK_NOT_FOUND."
            if must_continue else None
        ),
        "prediction_commit": replay.get("prediction_commit"),
    }


@app.get("/backtest/report/{task_id}", operation_id="getBacktestReport", response_model=ReportResponse)
def get_report(task_id: str, _: None = Security(require_token)) -> dict:
    report = get_manager().report(task_id)
    if report is None:
        raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND")
    return _prompted_report(report)
