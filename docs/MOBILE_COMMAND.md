# 手机单命令接入

已建立的私人 GPT：`https://chatgpt.com/g/g-6a9cedc3f4e88191851540b88c642507-hh520-insight-ai-hui-ce`

## 目标

用户在手机 ChatGPT/Codex 中只输入：

```text
回测 2026-08-01 全部比赛
```

私人 GPT 自动完成“逐日重新采集 → 脱敏 → 重新预测 → 评价 → 改进建议”，用户不参与中间步骤。

每条新的用户命令都生成新任务并完整重跑，即使日期相同、已有历史结果或前一任务失败/未完成，也不得复用、跳过或拦截。仅同一次网络请求重试复用 request_id。

系统不设置每日或每周固定时间。用户何时发送命令、回测哪一天或哪段日期，完全由用户决定。

## 部署

1. 将本项目部署到可被手机访问的 HTTPS 主机。
2. 配置私人预测网关令牌，并以只读方式挂载 `replay_predictions`。
3. 当前 HH520 服务器使用 `docker compose -f compose.server.yaml up -d --build`。
4. 确认 `https://你的域名/health` 返回 `status=ok`。当前生产地址为 `https://insight.156.225.23.17.sslip.io`。

## ChatGPT/Codex 动作

在自定义动作中导入：

```text
https://你的域名/openapi.json
```

认证方式选择 Bearer，值为部署时的 `HH520_API_TOKEN`。私人 GPT 对每个日期调用 `createReplayTask`，等待采集完成后读取脱敏页、按冻结模型提交预测，最后把本次 Commit 交给 `evaluateReplayBacktest`。日期范围最多 7 天，并按日期顺序一日一个 Commit。

`createReplayTask` 和首个分析页会返回 `prediction_prompt_bundle`；历史重放预测必须先应用其中的 `execution_prompt`。最终 `evaluateReplayBacktest`/`getBacktestReport` 响应会返回 `prompt_bundle`；手机 GPT 必须使用其中的 HH520 Insight AI 提示词解释评价结果并生成最终中文答复。每个阶段都核对提示词 ID 与 SHA-256；提示词缺失或绑定变化时停止执行，不得静默回退。

## 运行边界

手机端只是命令入口。回测服务复用冻结的 HH520 V2.1-Test 方法，但不自动修改参数、权重、Prompt 或模型配置。
