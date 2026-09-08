import json
from collections import Counter

from report.report_engine import ReportEngine


class GPTFactReportEngine(ReportEngine):
    """Expose deterministic backtest facts; reserve final analysis and advice for GPT."""

    def build(self, task: dict, snapshot: dict, records: list[dict]) -> dict:
        report = super().build(task, snapshot, records)
        total = report["summary"].get("valid_matches", report["summary"].get("total_matches", 0))
        errors = Counter(item.get("error_type") for item in records if item.get("error_type"))
        report["improvement_plan"] = {
            "auto_apply": False,
            "analysis_owner": "GPT",
            "server_recommendations_disabled": True,
            "model_change_allowed": total >= 100,
            "minimum_sample_for_model_change": 100,
            "sample_warning": None if total >= 100 else f"当前仅 {total} 场；不足 100 场，GPT 只能形成候选建议，不得修改稳定模型。",
            "error_distribution": dict(errors),
            "proposals": [{
                "priority": "PROCESS",
                "area": "GPT_FINAL_ANALYSIS",
                "finding": "服务器仅提供可审计回测事实，不负责最终模型分析、总结或改进建议。",
                "proposal": "由 GPT 使用绑定的 HH520 Insight AI 提示词独立完成最终分析、总结和候选改进建议。",
                "hypothesis": "最终认知层必须基于完整逐场事实由 GPT 推理，而不是复述服务器预生成结论。",
                "basis": "回测指标、逐场评价、模块状态、数据警告与 A-E 机器标签均已结构化保留。",
                "expected_impact": "最终输出能够结合跨场模式、模块关系、校准与适用边界形成独立判断。",
                "risk": "GPT 结论仍可能受小样本与相关性误判影响，必须保持证据约束和统计克制。",
                "validation_method": "所有 GPT 候选建议必须给出可测试、时间外验证、可比较和可回滚方案。",
                "acceptance": "只有独立样本验证支持的候选改动才允许进入开发版本验证。",
                "rejection": "证据不足、样本过小、仅回测集改善或其他核心指标明显下降时必须否决。",
                "rollback": "任何候选失败都继续使用当前冻结稳定版，不自动修改模型。",
            }],
        }
        report["report_markdown"] = self._markdown(report)
        (self.report_dir / f"{task['task_id']}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    @staticmethod
    def _markdown(report: dict) -> str:
        summary = report["summary"]
        lines = [
            "# HH520 Insight AI · GPT 最终分析事实包",
            "",
            "> 本内容只包含服务器确定的回测事实，不包含服务器生成的模型结论、最终总结或模型改进建议。",
            "> 最终分析、跨场模式识别、模块贡献判断、根因解释、总结和候选建议必须由 GPT 应用绑定的 HH520 Insight AI 提示词独立完成。",
            "",
            "## 【回测身份事实】",
            "",
            f"- 任务：{report['task_id']}；日期：{report['date_range']}。",
            f"- 被测模型：{', '.join(report.get('model_versions', [])) or '未记录'}。",
            f"- 预测提示词：{', '.join(report.get('prediction_prompt_versions', [])) or '未记录'}。",
            f"- 回测提示词：{report['insight_prompt_binding']['prompt_id']}（{report['insight_prompt_binding']['sha256']}）。",
            f"- 评价规则：{report.get('evaluation_version', '未记录')}。",
            f"- 污染状态：{report['pollution_status']}。",
            f"- Prediction Commit：{', '.join(report.get('prediction_commit_ids', [])) or '未记录'}。",
            "",
            "## 【机器统计事实】",
            "",
            "```json",
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2),
            "```",
            "",
            "## 【模块状态事实】",
            "",
        ]
        for module in report.get("module_audit", []):
            facts = {
                "module_id": module.get("module_id"),
                "name": module.get("name"),
                "samples": module.get("samples"),
                "completed": module.get("completed"),
                "degraded": module.get("degraded"),
                "evidence_available": module.get("evidence_available"),
            }
            lines.append("- " + json.dumps(facts, ensure_ascii=False, sort_keys=True))

        lines += ["", "## 【逐场完整事实】", ""]
        for index, item in enumerate(report.get("matches", []), 1):
            fact = {
                "match": item.get("match"),
                "match_id": item.get("match_id"),
                "prediction": item.get("prediction", {}),
                "actual_result": item.get("actual_result", {}),
                "evaluation": item.get("evaluation", {}),
                "machine_error_type": item.get("error_type"),
                "modules": item.get("modules", []),
                "source_warnings": item.get("source_warnings", []),
                "prediction_input": item.get("prediction_input", {}),
            }
            lines += [
                f"### {index}. {item.get('match_id') or item.get('match') or 'UNKNOWN'}",
                "",
                "```json",
                json.dumps(fact, ensure_ascii=False, sort_keys=True, indent=2),
                "```",
                "",
            ]

        lines += [
            "## 【GPT 最终分析与改进方案要求】",
            "",
            "- 将以上内容视为事实输入，而不是服务器结论。",
            "- 必须独立完成逐场预测对比、为什么正确/错误、A-E 根因复核、13 模块贡献、校准与稳定性分析。",
            "- 机器 error_type 只是基础标签；GPT 可在证据支持下保留多个候选解释，不得机械复述。",
            "- 必须区分数据问题、模型判断、权重/校准、异常比赛与信息不足，禁止把未知原因简单归为运气。",
            "- 所有改进建议必须由 GPT 生成，并包含假设、依据、预期影响、风险、验证方法、通过标准、否决标准和回滚方案。",
            "- 不得自动修改稳定模型、参数、权重、Prompt 或版本。",
        ]
        return "\n".join(lines)
