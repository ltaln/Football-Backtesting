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


class ReplayRangeRequest(BaseModel):
    command: str
    task_ids: list[str] = Field(min_length=1, max_length=7)


class ReplayRangeCompleteRequest(ReplayRangeRequest):
    p: list[str] = Field(
        min_length=1,
        max_length=999,
        description=(
            "One ultra-compact prediction per returned k: "
            "k|score1,score2,score3|htft1,htft2,htft3|asian|ou|1x2|goals|confidence|13 module codes. "
            "Scores use 1:0; HTFT uses H/D/A pairs; asian H-0.5/A+0.5/P; ou O2.5/U2.5/P; "
            "1x2 H/D/A/HD/AD/P; confidence 0-100; module codes contain only C or D."
        ),
    )


class ReplayRangeBundleResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    ready: bool
    replay_mode: bool | None = None
    task_ids: list[str] = Field(default_factory=list)
    pending_task_ids: list[str] = Field(default_factory=list)
    prediction_prompt_bundle: dict | None = None
    matches: list[dict] = Field(default_factory=list)
    output_format: str | None = None
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


def _compact_prompt_bundle(bundle: object) -> dict | None:
    if not isinstance(bundle, dict):
        return None
    compact = {key: bundle[key] for key in ("prompt_id", "sha256", "model_version", "version") if key in bundle}
    compact["execution_prompt"] = (
        "Use only each match's masked pre-kickoff evidence. Apply all 13 required frozen modules independently; "
        "degrade missing/conflicting modules, never invent data, and choose calibrated score, HT/FT, Asian, "
        "over/under, 1X2 and total-goal predictions. Upgrade Package 1 remains PARKED."
    )
    return compact


def _range_batches(task_ids: list[str]) -> list[dict]:
    return [_prediction_request("GET", f"/v1/tasks/{task_id}/analysis-batch") for task_id in task_ids]


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
        statuses = [_prediction_request("GET", f"/v1/tasks/{task_id}") for task_id in request.task_ids]
        failed = [item for item in statuses if item.get("status") in {"FAILED", "BLOCKED", "CANCELLED"}]
        if failed:
            raise HTTPException(status_code=409, detail={
                "error": "RANGE_REPLAY_TASK_FAILED",
                "tasks": [{"task_id": item.get("id"), "status": item.get("status"),
                           "blockers": item.get("blockers", [])} for item in failed],
            })
        pending = [item.get("id") for item in statuses
                   if item.get("status") in {"CREATED", "STARTUP_CHECK", "COLLECTING"}]
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(2)
    if pending:
        return {
            "ready": False,
            "pending_task_ids": pending,
            "next_operation": "getReplayRangeBundle",
            "instruction": "Call getReplayRangeBundle again immediately with the same command and task_ids. Do not reply.",
        }

    batches = _range_batches(request.task_ids)
    matches = []
    key = 1
    for task_id, batch in zip(request.task_ids, batches):
        for item in batch.get("matches", []):
            evidence = []
            for section in item.get("sections", []):
                category = section.get("category", "data")
                compact = _compact_section(category, section.get("content", ""))
                if compact:
                    evidence.append(f"{category}:{compact}")
            result_mask = item.get("result_mask") or {}
            matches.append({
                "k": key,
                "d": item.get("date"),
                "n": item.get("match_no"),
                "c": item.get("code"),
                "t": item.get("kickoff_at_raw"),
                "i": _excerpt(item.get("identity_check", {}), 100),
                "e": evidence,
                "mask": bool(result_mask.get("applied")) if isinstance(result_mask, dict) else bool(result_mask),
            })
            key += 1
    prompt_bundle = next((batch.get("prompt_bundle") for batch in batches if batch.get("prompt_bundle")), None)
    return {
        "ready": True,
        "replay_mode": True,
        "task_ids": request.task_ids,
        "prediction_prompt_bundle": _compact_prompt_bundle(prompt_bundle),
        "matches": matches,
        "output_format": (
            "Return every k exactly once in p using: "
            "k|1:0,1:1,2:0|HH,DH,DD|H-0.5|U2.5|H|2-3|62|CCCCCCCCCCCCC. "
            "H/D/A mean home/draw/away; P means PASS. Analyze all 13 frozen modules before choosing values."
        ),
        "next_operation": "completeReplayRange",
        "instruction": "Analyze every returned match under the bound frozen prompt, then call completeReplayRange once. Do not reply first.",
    }


def _decode_ultra(line: str) -> tuple[int, list[str], str]:
    parts = [part.strip() for part in line.split("|")]
    if len(parts) != 9:
        raise HTTPException(status_code=422, detail="RANGE_PREDICTION_FORMAT_INVALID")
    try:
        key = int(parts[0])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="RANGE_PREDICTION_KEY_INVALID") from exc
    scores = parts[1].split(",")
    if len(scores) != 3 or any(not re.fullmatch(r"\d{1,2}:\d{1,2}", value) for value in scores):
        raise HTTPException(status_code=422, detail=f"RANGE_SCORE_INVALID:{key}")
    htft = parts[2].split(",")
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
        "精准比分 Top3：" + " / ".join(value.replace(":", "-") for value in scores),
        "半全场 Top3：" + " / ".join(f"{letter[value[0]]}/{letter[value[1]]}" for value in htft),
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
    summary="一次保存整个范围预测并直接返回最终回测报告",
    response_model=ReportResponse,
    openapi_extra={"x-openai-isConsequential": False},
)
def complete_replay_range(request: ReplayRangeCompleteRequest, _: None = Security(require_token)) -> dict:
    """Keep the model-generated request below Actions' size limit, then close the range server-side."""
    _range_task_ids(request)
    batches = _range_batches(request.task_ids)
    index = []
    for task_id, batch in zip(request.task_ids, batches):
        for item in batch.get("matches", []):
            index.append((task_id, item))
    supplied = {}
    for line in request.p:
        key, values, module_codes = _decode_ultra(line)
        if key in supplied:
            raise HTTPException(status_code=422, detail=f"RANGE_PREDICTION_DUPLICATE:{key}")
        supplied[key] = (values, module_codes)
    required = set(range(1, len(index) + 1))
    if set(supplied) != required:
        raise HTTPException(status_code=422, detail="RANGE_PREDICTION_SET_INCOMPLETE")

    grouped: dict[str, list[dict]] = {task_id: [] for task_id in request.task_ids}
    for key, (task_id, item) in enumerate(index, 1):
        refs = [section.get("source_url") or f"{section.get('category')}:{section.get('source_sha256', '')[:12]}"
                for section in item.get("sections", [])]
        refs = [value for value in refs if value] or [item.get("package_sha256", "masked-evidence")]
        values, module_codes = supplied[key]
        grouped[task_id].append({
            "n": item["match_no"],
            "c": item["code"],
            "m": module_codes,
            "e": refs,
            "r": values,
            "w": ["部分模块因证据不足降级"] if "D" in module_codes else [],
            "p": "依据冻结提示词，对该场脱敏赛前证据完成十三模块分析后生成。",
        })

    commits = []
    for task_id in request.task_ids:
        status = _prediction_request("GET", f"/v1/tasks/{task_id}")
        if status.get("status") == "COMPLETED" and status.get("prediction_commit"):
            commits.append(status["prediction_commit"]["prediction_commit_id"])
            continue
        response = None
        for offset in range(0, len(grouped[task_id]), 3):
            response = _prediction_request("POST", f"/v1/tasks/{task_id}/analysis-min", {
                "p": grouped[task_id][offset:offset + 3],
            })
        commit = (response or {}).get("prediction_commit") or {}
        if not commit.get("prediction_commit_id"):
            raise HTTPException(status_code=409, detail=f"RANGE_COMMIT_MISSING:{task_id}")
        commits.append(commit["prediction_commit_id"])

    return _prompted_report(get_manager().run(request.command, commits))


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
