# HH520 Insight AI V1.0 MVP · Codex Implementation Specification

## 1. 项目

- 名称：HH520 Insight AI
- 仓库：Football-Backtesting
- 版本：Backtest V1.0 MVP
- 目的：独立完成历史比赛回测、预测验证、误差分析与回测报告。

## 2. 系统位置与隔离

HH520 System 下，Football AI V2.1-Test 负责 Prediction；HH520 Insight AI V1.0 MVP 负责 Backtesting。

Insight AI 只可读取 Historical Snapshot、Prediction Archive、Match Result。禁止修改 Football AI 代码、预测参数、预测权重与预测模型配置。

> Backtest validates prediction. Backtest does not change prediction.

## 3. MVP 必须实现

- Core：Command Parser、Task Manager、Config Manager
- Data：Snapshot Manager、Snapshot Checker、Collector Adapter
- Cleaning：Time Pollution Filter
- Evaluation：Score、HTFT、Result、Goal、Evaluation Engine
- Analysis：Error Analyzer
- Output：Report Engine
- Infrastructure：SQLite、FastAPI、Logging、Basic Tests

V1 禁止实现：DNA Engine、Tail Risk、Candidate Model、Shadow Test、自动训练、自动参数优化、预测模型升级、前端 UI。只预留后续接入边界，不进入核心路径。

## 4. 命令与窗口

支持：`回测 2026-08-01 全部比赛` 与 `回测 2026-08-01至2026-08-07`。输出 `type=BACKTEST`、`start_date`、`end_date`。

`MAX_BACKTEST_DAYS = 3`。超过 3 天返回 `BACKTEST_WINDOW_LIMIT_EXCEEDED`，禁止自动拆分。

## 5. 任务流程

`CREATED → CHECKING_DATA → COLLECTING → SANITIZING → SNAPSHOT_READY → RUNNING → EVALUATING → REPORT_READY`；异常为 `FAILED`。

## 6. Snapshot 与采集

Snapshot 必须含 `snapshot_id`、`date_range`、`source`、`version`、`pollution_status`、`created_time`。所有回测必须基于冻结 Snapshot。

Collector 仅定义 `collect_history(start_date, end_date)`，返回 Historical Snapshot 候选。

## 7. 时间污染

- REMOVE：`final_score`、`final_result`、`post_match_odds`、`post_match_news`
- DOWNGRADE：`post_match_status`、`after_match_confirmed_information`
- KEEP：`prematch_odds`、`handicap`、`team_form`、`injuries_before_match`

## 8. 评价与误差

- Score：输入 `score_top2` 和 `final_score`；输出 `exact_hit`、`near_hit`、`direction_hit`
- HTFT：输出 `half_hit`、`transition_hit`、`overall_hit`
- Result：输出 `hit`、`confidence_bucket`
- Goal：输出 `exact`、`range`
- Error：`DATA_ERROR`、`MARKET_ERROR`、`TEAM_STATE_ERROR`、`GAME_FLOW_ERROR`、`RANDOM_EVENT`

## 9. 报告

Match Report：Match、Prediction、Actual Result、Evaluation、Error Type。

Summary Report：Total Matches、Score Accuracy、HTFT Accuracy、Result Accuracy、Goal Accuracy、Confidence Performance。

## 10. SQLite

- `backtest_tasks`：task_id、start_date、end_date、status、snapshot_id、created_time
- `snapshots`：snapshot_id、version、source、pollution_status、created_time
- `evaluations`：match_id、prediction_json、actual_json、evaluation_json、error_type

## 11. API

- `POST /backtest/run`，输入 `{"command":"回测 2026-08-01 全部比赛"}`
- `GET /backtest/status/{task_id}`
- `GET /backtest/report/{task_id}`

## 12. 验收

必须测试：单日成功、7 日成功、8 日拒绝、污染字段不会进入评价输入。

完成标准：一条 `回测 日期` 命令自动完成创建任务、获取数据、清洗、冻结 Snapshot、读取预测、评价和生成报告。

开发规则：简单、模块化、不过度设计、无无关代码、不生成前端、不修改预测系统。
