import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from analysis.improvement_advisor import ImprovementAdvisor
from core.prompt_loader import load_insight_prompt, prompt_binding

MODULE_NAMES = {
    "data_consistency_audit": "数据一致性审计", "data_confidence_score": "数据置信度",
    "water_market": "市场水位", "team_analysis": "球队与阵容", "league_analysis": "联赛环境",
    "company_source_analysis": "公司与来源", "correct_score": "精准比分",
    "soccerstats_htft": "半全场数据", "odds_abnormal_detection": "赔率异常",
    "match_risk_engine": "比赛风险", "conflict_detection": "冲突检测",
    "cross_model_interaction": "跨模型交叉", "calibration": "概率校准",
}


class ReportEngine:
    def __init__(self, report_dir: Path):
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.advisor = ImprovementAdvisor()

    @staticmethod
    def _eligible(records: list[dict], section: str, eligibility: str = "evaluable") -> list[dict]:
        return [item for item in records if item["evaluation"][section].get(eligibility)]

    @classmethod
    def _rate(cls, records: list[dict], section: str, field: str, eligibility: str = "evaluable") -> float:
        eligible = cls._eligible(records, section, eligibility)
        return round(sum(bool(item["evaluation"][section][field]) for item in eligible) / len(eligible), 4) if eligible else 0.0

    @staticmethod
    def _wilson(hits: int, total: int) -> list[float] | None:
        if not total:
            return None
        z, proportion = 1.96, hits / total
        denominator = 1 + z * z / total
        centre = (proportion + z * z / (2 * total)) / denominator
        margin = z * ((proportion * (1 - proportion) / total + z * z / (4 * total * total)) ** 0.5) / denominator
        return [round(max(0, centre - margin), 4), round(min(1, centre + margin), 4)]

    @staticmethod
    def _module_audit(records: list[dict]) -> list[dict]:
        audit = []
        for module_id, name in MODULE_NAMES.items():
            rows = [module for record in records for module in record.get("modules", []) if module.get("module_id") == module_id]
            completed = sum(module.get("status") == "COMPLETED" for module in rows)
            degraded = sum(module.get("status") == "DEGRADED" for module in rows)
            evidence = sum(any(ref != "unavailable" for ref in module.get("evidence_refs", [])) for module in rows)
            conclusion = "数据不足模块" if degraded or evidence < len(records) else "待验证模块"
            audit.append({"module_id": module_id, "name": name, "samples": len(rows), "completed": completed,
                          "degraded": degraded, "evidence_available": evidence, "conclusion": conclusion,
                          "note": "未保存模块独立方向/反事实，不能宣称有效、无增量或误导。"})
        return audit

    def build(self, task: dict, snapshot: dict, records: list[dict]) -> dict:
        confidence: dict[str, dict[str, int | float]] = defaultdict(lambda: {"matches": 0, "hits": 0})
        for item in self._eligible(records, "result"):
            bucket = item["evaluation"]["result"]["confidence_bucket"]
            confidence[bucket]["matches"] += 1
            confidence[bucket]["hits"] += int(item["evaluation"]["result"]["hit"])
        for values in confidence.values():
            values["accuracy"] = round(values["hits"] / values["matches"], 4) if values["matches"] else 0.0
        metric_samples = {"score": len(self._eligible(records, "score")), "htft": len(self._eligible(records, "htft")),
                          "result": len(self._eligible(records, "result")),
                          "goal_exact": len(self._eligible(records, "goal", "exact_evaluable")),
                          "goal_range": len(self._eligible(records, "goal", "range_evaluable"))}
        valid = sum(any(item["evaluation"][section].get("evaluable") for section in ("score", "htft", "result", "goal")) for item in records)
        summary = {"total_matches": len(records), "valid_matches": valid, "not_evaluable_matches": len(records) - valid,
                   "source_excluded_matches": len(snapshot.get("excluded_matches", [])), "metric_samples": metric_samples,
                   "score_accuracy": self._rate(records, "score", "exact_hit"),
                   "htft_accuracy": self._rate(records, "htft", "overall_hit"),
                   "htft_top1_accuracy": self._rate(records, "htft", "top1_overall_hit"),
                   "result_accuracy": self._rate(records, "result", "hit"),
                   "goal_accuracy": self._rate(records, "goal", "range", "range_evaluable"),
                   "goal_range_accuracy": self._rate(records, "goal", "range", "range_evaluable"),
                   "goal_exact_accuracy": self._rate(records, "goal", "exact", "exact_evaluable"),
                   "confidence_performance": dict(confidence)}
        result_rows = self._eligible(records, "result")
        summary["result_accuracy_95pct_interval"] = self._wilson(
            sum(bool(item["evaluation"]["result"]["hit"]) for item in result_rows), len(result_rows))
        report = {"task_id": task["task_id"], "status": "REPORT_READY", "evaluation_version": "1.3-audited",
                  "snapshot_id": snapshot["snapshot_id"], "date_range": snapshot["date_range"],
                  "pollution_status": snapshot["pollution_status"], "generated_time": datetime.now(timezone.utc).isoformat(),
                  "insight_prompt_binding": prompt_binding(load_insight_prompt()),
                  "prediction_commit_ids": task.get("prediction_commit_ids", []),
                  "prediction_prompt_versions": sorted({x.get("prediction_input", {}).get("prompt_version") for x in records if x.get("prediction_input", {}).get("prompt_version")}),
                  "model_versions": sorted({x.get("prediction_input", {}).get("model_version") for x in records if x.get("prediction_input", {}).get("model_version")}),
                  "summary": summary, "module_audit": self._module_audit(records), "matches": records}
        report["improvement_plan"] = self.advisor.build(summary, records)
        report["report_markdown"] = self._markdown(report)
        (self.report_dir / f"{task['task_id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    @staticmethod
    def _markdown(report: dict) -> str:
        summary = report["summary"]
        lines = ["# HH520 Insight AI 回测报告", "", "## 【回测身份】", "",
                 f"- 被测模型：{', '.join(report['model_versions']) or '未记录'}",
                 f"- 预测提示词：{', '.join(report['prediction_prompt_versions']) or '未记录'}",
                 f"- 回测提示词：{report['insight_prompt_binding']['prompt_id']}（{report['insight_prompt_binding']['sha256']}）",
                 f"- 评价规则：{report['evaluation_version']}；模型保持冻结。", "", "## 【回测范围】", "",
                 f"- 任务：{report['task_id']}；日期：{report['date_range']}。",
                 "- 冻结规则：仅使用赛前脱敏快照；Prediction Commit 冻结后才读取赛果。",
                 f"- Prediction Commit：{', '.join(report['prediction_commit_ids']) or '未记录'}。", "",
                 "## 【数据与时间审计】", "",
                 f"- 总样本 {summary['total_matches']}；有效 {summary['valid_matches']}；不可评价 {summary['not_evaluable_matches']}；源结果缺失 {summary['source_excluded_matches']}。",
                 f"- 污染检查：{report['pollution_status']}。模块缺证据时强制降级，不允许无证据标记完成。", "",
                 "## 【预测结果对比】", ""]
        for item in report["matches"]:
            predicted = item.get("prediction", {}).get("result", {}).get("result", [])
            actual = item.get("actual_result", {}).get("final_result", "?")
            lines.append(f"- {item.get('match_id') or item['match']}：预测 {predicted or 'PASS'}；实际 {actual}；根因 {item.get('error_type') or '命中/无错误'}。")
        interval = summary.get("result_accuracy_95pct_interval")
        interval_text = f"；95% 区间 {interval[0]:.1%}–{interval[1]:.1%}" if interval else ""
        lines += ["", "## 【指标统计】", "", f"- 比分 Top3：{summary['score_accuracy']:.1%}（n={summary['metric_samples']['score']}）。",
                  f"- 半全场 Top3：{summary['htft_accuracy']:.1%}（n={summary['metric_samples']['htft']}）。",
                  f"- 赛果：{summary['result_accuracy']:.1%}（n={summary['metric_samples']['result']}{interval_text}）。",
                  f"- 总进球区间：{summary['goal_range_accuracy']:.1%}（n={summary['metric_samples']['goal_range']}）。",
                  "- 未提供价格/收益数据，不声称 ROI 优势。", "", "## 【模块表现】", ""]
        for module in report["module_audit"]:
            lines.append(f"- {module['name']}：{module['conclusion']}；完成 {module['completed']}，降级 {module['degraded']}，有证据 {module['evidence_available']}/{module['samples']}。{module['note']}")
        errors = report["improvement_plan"].get("error_distribution", {})
        lines += ["", "## 【错误分析】", "", "- A–E 分布：" + ("；".join(f"{key}={value}" for key, value in sorted(errors.items())) or "无未命中样本") + "。",
                  "- D 类仅在有红牌、极端天气或重大临场事件证据时使用；未知原因不再归为随机事件。", "",
                  "## 【校准与稳定性】", "", f"- 置信度分桶：{json.dumps(summary['confidence_performance'], ensure_ascii=False)}。",
                  "- 当前未获得足够的联赛/市场分组与样本外模块方向数据，相关稳定性结论保留。", "", "## 【改进候选】（改进方案）", ""]
        warning = report["improvement_plan"].get("sample_warning")
        if warning:
            lines += [f"> {warning}", ""]
        for index, proposal in enumerate(report["improvement_plan"]["proposals"], 1):
            lines += [f"{index}. **{proposal['area']} · {proposal['priority']}**：{proposal['finding']}",
                      f"   - 建议：{proposal['proposal']}", f"   - 假设：{proposal['hypothesis']}",
                      f"   - 依据：{proposal['basis']}", f"   - 预期影响：{proposal['expected_impact']}",
                      f"   - 风险：{proposal['risk']}", f"   - 验证方法：{proposal['validation_method']}",
                      f"   - 通过标准：{proposal['acceptance']}", f"   - 否决标准：{proposal['rejection']}",
                      f"   - 回滚：{proposal['rollback']}"]
        lines += ["", "## 【版本建议】", "", "- 保持稳定版；候选建议须在独立样本验证，不自动升级。", "",
                  "## 【审计结论】", "", "- 指标与 A–E 根因分布由冻结提交及揭盲赛果支持；模块有效性因缺少独立方向/反事实仍属待验证或数据不足。",
                  "- Upgrade Package 保持 PARKED；不自动修改模型、参数、权重或版本。"]
        return "\n".join(lines)

    def get(self, task_id: str) -> dict | None:
        path = self.report_dir / f"{task_id}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
