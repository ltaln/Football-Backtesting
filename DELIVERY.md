# HH520 Insight AI Backtest V1.0 交付记录

## 生产部署

- 服务：`https://insight.156.225.23.17.sslip.io`
- OpenAPI：`https://insight.156.225.23.17.sslip.io/openapi.json`
- 健康检查：`https://insight.156.225.23.17.sslip.io/health`
- 服务器目录：`/opt/hh520-insight`
- 容器：`hh520-insight`
- 预测档案：只读挂载 `hh520_hh520_state`
- 独立持久化：`deploy_insight_state`

## 已验收

- 本地规范测试：4/4 通过。
- 真实回测：`回测 2026-09-05 全部比赛`。
- 结果：`REPORT_READY`，29 场，`pollution_status=CLEAN`。
- 报告包含四项准确率、置信度表现、逐场评价、误差分类、中文摘要和改进方案。
- `improvement_plan.auto_apply=false`；不会自动修改预测模型。

## 手机命令

- 私人 GPT：`https://chatgpt.com/g/g-6a9cedc3f4e88191851540b88c642507-hh520-insight-ai-hui-ce`
- 可见范围：只有我。
- 动作认证：Bearer，已保存。
- 手机端真实验收：29 场报告、四项指标、改进方案、任务编号及 `CLEAN` 状态均已直接返回。
- 执行策略：不设自动计划，回测时间和日期范围由用户每次下达命令决定。
- 动作策略：已设置“始终允许”；GPT 必须取得接口真实响应后才能回复，不再虚构后台提交状态。
- 最终成功验收任务：`BT-d82dd5abb493`，HTTP 200，29 场，`CLEAN`。

```text
回测 2026-09-05 全部比赛
回测 2026-09-01至2026-09-07
```

范围最多 3 天。每个日期都先重新采集、脱敏并重新预测，再以本次不可变 Replay Prediction Commit 评价；没有本次 Commit 或没有完场赛果时明确返回错误。
