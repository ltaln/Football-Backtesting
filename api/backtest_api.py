import os
import json
import secrets
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from core.command_parser import CommandError
from core.task_manager import TaskManager

public_url = os.getenv("HH520_PUBLIC_URL", "").strip().rstrip("/")
app = FastAPI(
    title="HH520 Insight AI",
    description="重新采集历史数据、屏蔽目标赛果与赛后信息、按 HH520 V2.1-Test 重新预测，再生成回测与改进建议；不会自动修改预测模型。",
    version="Backtest V1.1 Replay",
    servers=[{"url": public_url}] if public_url else None,
)
security = HTTPBearer(auto_error=False)
_manager: TaskManager | None = None


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


class ReplayCreateResponse(BaseModel):
    task_id: str
    status_url: str
    report_url: str
    created: bool
    must_continue: bool
    next_operation: str
    instruction: str


class ReplayStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    payload: dict
    status: str
    blockers: list[str]
    must_continue: bool
    next_operation: str
    instruction: str


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


class ReplayPredictionItem(BaseModel):
    n: int
    c: str
    m: str
    e: list[str]
    r: list[str]
    w: list[str]
    p: str


class ReplayPredictionBatchRequest(BaseModel):
    p: list[ReplayPredictionItem]


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


@app.get("/health", operation_id="healthCheck", response_model=HealthResponse)
def health() -> HealthResponse:
    return {"status": "ok", "service": "HH520 Insight AI", "version": "Backtest V1.1 Replay"}


@app.post(
    "/backtest/run",
    operation_id="runBacktest",
    summary="用一条中文命令运行完整回测",
    response_model=ReportResponse,
    openapi_extra={"x-openai-isConsequential": False},
)
def run_backtest(request: RunRequest, _: None = Security(require_token)) -> dict:
    try:
        return get_manager().run(request.command, request.prediction_commit_ids)
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
            detail = json.loads(exc.read().decode("utf-8")).get("error", "REPLAY_GATEWAY_ERROR")
        except Exception:
            detail = "REPLAY_GATEWAY_ERROR"
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="REPLAY_GATEWAY_UNAVAILABLE") from exc


@app.post("/replay/tasks", operation_id="createReplayTask", summary="启动单日历史重放：重新采集并等待 GPT 预测", response_model=ReplayCreateResponse, openapi_extra={"x-openai-isConsequential": False})
def create_replay_task(request: ReplayTaskRequest, _: None = Security(require_token)) -> dict:
    if not request.command.strip().startswith("回测 "):
        raise HTTPException(status_code=422, detail="REPLAY_COMMAND_REQUIRED")
    return _prediction_request("POST", "/v1/tasks", request.model_dump())


@app.get("/replay/tasks/{task_id}", operation_id="getReplayTask", summary="轮询重放采集状态", response_model=ReplayStatusResponse)
def get_replay_task(task_id: str, _: None = Security(require_token)) -> dict:
    return _prediction_request("GET", f"/v1/tasks/{task_id}")


@app.get("/replay/tasks/{task_id}/analysis-page", operation_id="getReplayAnalysisPage", summary="读取最多两场已脱敏的赛前证据", response_model=ReplayAnalysisPageResponse)
def get_replay_analysis_page(task_id: str, cursor: int = 0, _: None = Security(require_token)) -> dict:
    return _prediction_request("GET", f"/v1/tasks/{task_id}/analysis-page?cursor={cursor}")


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
    return report
