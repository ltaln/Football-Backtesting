# HH520 Insight AI · Backtest V1.0 MVP

独立足球预测回测系统。现行规则为：每次任务重新采集指定历史日期的数据，先屏蔽目标比赛的比分、赛果和开赛后数据，再按冻结的 HH520 V2.1-Test 流程重新预测；预测完成后才读取真实赛果、评价误差并生成改进建议。不会自动修改模型参数、权重或配置。

当前评价版本为 `1.2`：PASS/无法解析项目不进入对应指标分母；半全场按 Top3 覆盖评价；总进球同时保留精确与区间口径；赛果仅接受严格主胜/平/客胜或经主客队身份核对的获胜球队。

> Backtest validates prediction. Backtest does not change prediction.

## 一条命令使用

启动服务：

```bash
python -m pip install -r requirements.txt
python main.py
```

手机 ChatGPT/Codex 接入本服务的 FastAPI OpenAPI 地址 `/openapi.json` 后，用户只需输入：

```text
回测 2026-08-01 全部比赛
```

系统经 `POST /backtest/run` 自动完成创建任务、获取数据、清洗、冻结 Snapshot、读取预测、评价、误差分析和改进方案，并直接返回 `REPORT_READY` 报告。无需逐步操作。日期范围命令为：

```text
回测 2026-08-01至2026-08-07
```

最多 7 个自然日；超过限制返回 `BACKTEST_WINDOW_LIMIT_EXCEEDED`，不会自动拆分。

回测不设固定时间，也不自动定时执行；开始日期、结束日期和执行时机完全由用户在手机 GPT 命令中决定。

每一条新的预测或回测命令都必须创建新任务，从重新采集开始完整执行；不得因相同日期、历史记录、旧任务失败或旧任务仍在运行而复用、跳过或拒绝。只有同一次网络请求重试可以复用相同 `request_id`，用于防止传输重试意外重复创建。

正式手机接入与 Docker 部署见 [`docs/MOBILE_COMMAND.md`](docs/MOBILE_COMMAND.md)。

## 固定流程

```text
CREATED → CHECKING_DATA → COLLECTING → SANITIZING → SNAPSHOT_READY
→ RUNNING → EVALUATING → REPORT_READY
```

异常状态为 `FAILED`。任务、Snapshot 和评价写入 SQLite；冻结快照与 JSON 报告分别写入 `data/snapshots/`、`data/reports/`。

## 输入档案

V1 Collector Adapter 默认读取 `data/archive/*.json`，远程历史采集器只需实现：

```python
collect_history(start_date, end_date) -> HistoricalSnapshot candidate
```

每场记录结构：

```json
{
  "match_id": "...",
  "match_date": "2026-08-01",
  "match": "主队 vs 客队",
  "prediction_input": {},
  "prediction": {
    "score_top2": ["1-0", "1-1"],
    "htft": {"half_result": "D", "full_result": "H"},
    "result": {"result": "H", "confidence": 0.68},
    "goal": {"exact": 1, "range": [1, 2]}
  },
  "actual": {
    "final_score": "1-0",
    "half_result": "D",
    "final_result": "H",
    "total_goals": 1
  }
}
```

仓库含一条明确标记的合成示例档案，仅用于开箱验证，不代表真实预测。

生产环境将 `HH520_REPLAY_PREDICTION_DIR` 指向只读的历史重放 Prediction Commit 目录。每个日期必须提交本次新生成、已标记脱敏策略的 Commit；旧实时预测档案不再作为现行回测输入。真实赛果只在预测 Commit 完成后独立读取。

## 时间污染规则

- 删除：`final_score`、`final_result`、`post_match_odds`、`post_match_news`
- 降级并记录：`post_match_status`、`after_match_confirmed_information`
- 保留：`prematch_odds`、`handicap`、`team_form`、`injuries_before_match`

清洗仅作用于 `prediction_input`。真实赛果留在独立的 `actual` 区域，直到评价阶段才使用。

日期校验第 1 层（最高优先级）为源网站日期目录，例如 `https://www.hh520.com/?date=20260716`。该目录下即使比赛在次日凌晨开赛，仍归属于 `2026-07-16`，不得仅按自然日时间判为日期不符。第 2、3 层规则待用户确认后补充。

## 评价口径

- Score：`exact_hit`（Top2 覆盖）、`near_hit`（与任一 Top2 只差一个进球）、`direction_hit`（Top1 胜平负方向）
- HTFT：`half_hit`、`transition_hit`、`top1_overall_hit`、Top3 `overall_hit`
- Result：`hit`、`confidence_bucket`（0–49、50–64、65–79、80–100）
- Goal：`exact`、`range`；主报告使用有实际输出的区间命中率
- Error：仅在存在未命中时分类为 `DATA_ERROR`、`MARKET_ERROR`、`TEAM_STATE_ERROR`、`GAME_FLOW_ERROR` 或 `RANDOM_EVENT`

总结报告包含原始场次、有效场次、各指标独立样本数、源结果缺失数、比分/半全场/赛果/进球准确率与置信度分桶表现。PASS 和不可评价结果不会伪造命中或进入对应指标分母。V1 不计算投注 ROI。

每份报告同时包含中文 `report_markdown` 和结构化 `improvement_plan`。改进方案根据实际错误分布和指标生成，只提出复核方向；少于 100 场时明确禁止修改模型，任何情况下都不会自动应用参数变化。

## API

- `POST /backtest/run`：提交 `{"command":"回测 2026-08-01 全部比赛"}`，同步返回最终报告
- `GET /backtest/status/{task_id}`：查询任务
- `GET /backtest/report/{task_id}`：读取报告
- `/docs`：FastAPI 交互文档
- `/openapi.json`：手机 ChatGPT/Codex 动作接入描述
- `/health`：部署健康检查

## Docker 部署

```bash
cp .env.example .env
cd deploy
docker compose up -d --build
```

默认服务端口为 `8000`，`data/` 与 `logs/` 作为持久化目录挂载。

现有 HH520 服务器部署（复用预测档案卷但强制只读）：

```bash
cd deploy
docker compose -f compose.server.yaml up -d --build
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试严格覆盖规范四项：单日成功、7 日成功、8 日拒绝、污染字段隔离。

## V1 边界

未实现 DNA Engine、Tail Risk、Candidate Model、Shadow Test、自动训练、自动参数优化、预测模型升级和前端 UI。它们不得进入 V1 核心路径。完整工程规范见 `docs/CODEX_IMPLEMENTATION_SPEC_HH520_INSIGHT_AI_V1.0.md`。
