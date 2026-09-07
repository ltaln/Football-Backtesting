import os
import json
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
    task_id: str
    start_date: str
    end_date: str
    status: str
    snapshot_id: str | None
    created_time: str


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


@app.get("/health", operation_id="healthCheck", response_model=HealthResponse)
def health() -> HealthResponse:
    return {"status": "ok", "service": "HH520 Insight AI", "version": "Backtest V1.3 Fresh Run",
            "prompt_binding": prompt_binding(_insight_prompt)}


def _prompted_report(report: dict) -> dict:
    return {**report, "prompt_bundle": _insight_prompt,
            "runtime_instruction": (
                "Apply prompt_bundle.content to interpret this server-generated report and produce the final Chinese response. "
                "Keep all metrics unchanged and treat recommendations as non-automatic candidates only."
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
    try:
        with urlopen(Request(base + path, data=data, headers=headers, method=method), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            detail = payload.get("error") or payload.get("detail") or "REPLAY_GATEWAY_ERROR"
        except Exception:
            detail = "REPLAY_GATEWAY_ERROR"
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="REPLAY_GATEWAY_UNAVAILABLE") from exc


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
    if day_count == 1:
        return _decorate_replay_create(_prediction_request("POST", "/v1/tasks", request.model_dump()))

    tasks: list[dict] = []
    errors: list[dict] = []
    for offset in range(day_count):
        replay_date = (command.start_date + timedelta(days=offset)).isoformat()
        child_request_id = _child_request_id(request.request_id, replay_date)
        child_command = f"回测 {replay_date} 全部比赛"
        try:
            child = _decorate_replay_create(_prediction_request("POST", "/v1/tasks", {
                "request_id": child_request_id,
                "command": child_command,
            }))
            tasks.append({
                "date": replay_date,
                "request_id": child_request_id,
                "command": child_command,
                **child,
            })
        except HTTPException as exc:
            errors.append({
                "date": replay_date,
                "request_id": child_request_id,
                "command": child_command,
                "http_status": exc.status_code,
                "error": str(exc.detail),
            })

    created_count = len(tasks)
    failed_count = len(errors)
    execution_status = "CREATED" if failed_count == 0 else ("PARTIAL" if created_count else "FAILED")
    return {
        "created": created_count == day_count,
        "must_continue": created_count == day_count,
        "next_operation": "getReplayTask" if created_count == day_count else None,
        "instruction": (
            "If errors is non-empty, stop and report each exact date, HTTP status, and error code. Otherwise process "
            "every returned task in ascending date order through getReplayTask, getReplayAnalysisPage, "
            "saveReplayPredictionBatch, and finalizeReplayPrediction. Then call evaluateReplayBacktest once with "
            "the original range command and all new prediction commit IDs in date order. Never reuse an older task or commit."
        ),
        "insight_prompt_binding": prompt_binding(_insight_prompt),
        "execution_status": execution_status,
        "range_run_id": f"range-{sha256(f'{request.request_id}:{request.command}'.encode('utf-8')).hexdigest()[:20]}",
        "parent_request_id": request.request_id,
        "start_date": command.start_date.isoformat(),
        "end_date": command.end_date.isoformat(),
        "day_count": day_count,
        "created_count": created_count,
        "failed_count": failed_count,
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
    if task is None:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND")
    return task


@app.get("/backtest/report/{task_id}", operation_id="getBacktestReport", response_model=ReportResponse)
def get_report(task_id: str, _: None = Security(require_token)) -> dict:
    report = get_manager().report(task_id)
    if report is None:
        raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND")
    return _prompted_report(report)
