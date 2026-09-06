# Changelog

## 1.1.0

- 回测改为每次重新采集历史数据并重新执行 HH520 V2.1-Test 预测。
- GPT 输入强制屏蔽目标比分、赛果、完场状态和开赛后盘口版本；保留预测时点之前的历史战绩。
- 仅接受本次生成的 `HH520-BTR-*` Replay Prediction Commit，再读取真实赛果完成评价。

## V1.0 final delivery fix

- 强制私人 GPT 在回复前真实调用 `runBacktest`，禁止虚构“已提交”或后台任务。
- 回测动作支持“始终允许”，后续手机命令无需再次确认。
- 明确回测时间完全由用户命令决定，不启用固定定时计划。
- 验证缺档错误路径及 29 场真实成功路径。

## Backtest V1.0 MVP

- 实现中文单日与日期范围命令解析，固定最多 7 天。
- 实现完整任务状态流、Collector Adapter、Snapshot 冻结与污染过滤。
- 实现比分、半全场、赛果、进球评价和固定误差分类。
- 实现 SQLite、JSON 报告、FastAPI、日志和四项验收测试。
- 增加 Docker 部署与手机 ChatGPT/Codex 单命令接入说明。
- 保持 Football AI V2.1-Test 只读隔离；未实现任何 V1 禁止功能。
