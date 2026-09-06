import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from analysis.improvement_advisor import ImprovementAdvisor


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

    def build(self, task: dict, snapshot: dict, records: list[dict]) -> dict:
        confidence: dict[str, dict[str, int | float]] = defaultdict(lambda: {"matches": 0, "hits": 0})
        for item in self._eligible(records, "result"):
            bucket = item["evaluation"]["result"]["confidence_bucket"]
            confidence[bucket]["matches"] += 1
            confidence[bucket]["hits"] += int(item["evaluation"]["result"]["hit"])
        for values in confidence.values():
            values["accuracy"] = round(values["hits"] / values["matches"], 4) if values["matches"] else 0.0
        metric_samples = {
            "score": len(self._eligible(records, "score")),
            "htft": len(self._eligible(records, "htft")),
            "result": len(self._eligible(records, "result")),
            "goal_exact": len(self._eligible(records, "goal", "exact_evaluable")),
            "goal_range": len(self._eligible(records, "goal", "range_evaluable")),
        }
        valid_matches = sum(any(item["evaluation"][section].get("evaluable") for section in ("score", "htft", "result", "goal")) for item in records)
        summary = {
            "total_matches": len(records),
            "valid_matches": valid_matches,
            "not_evaluable_matches": len(records) - valid_matches,
            "source_excluded_matches": len(snapshot.get("excluded_matches", [])),
            "metric_samples": metric_samples,
            "score_accuracy": self._rate(records, "score", "exact_hit"),
            "htft_accuracy": self._rate(records, "htft", "overall_hit"),
            "htft_top1_accuracy": self._rate(records, "htft", "top1_overall_hit"),
            "result_accuracy": self._rate(records, "result", "hit"),
            "goal_accuracy": self._rate(records, "goal", "range", "range_evaluable"),
            "goal_range_accuracy": self._rate(records, "goal", "range", "range_evaluable"),
            "goal_exact_accuracy": self._rate(records, "goal", "exact", "exact_evaluable"),
            "confidence_performance": dict(confidence),
        }
        improvement_plan = self.advisor.build(summary, records)
        report = {
            "task_id": task["task_id"],
            "status": "REPORT_READY",
            "evaluation_version": "1.2",
            "snapshot_id": snapshot["snapshot_id"],
            "date_range": snapshot["date_range"],
            "pollution_status": snapshot["pollution_status"],
            "generated_time": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "improvement_plan": improvement_plan,
            "matches": records,
        }
        report["report_markdown"] = self._markdown(report)
        (self.report_dir / f"{task['task_id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    @staticmethod
    def _markdown(report: dict) -> str:
        summary = report["summary"]
        lines = [
            "# HH520 Insight AI 回测报告",
            "",
            f"- 任务：{report['task_id']}",
            f"- 日期：{report['date_range']}",
            f"- 匹配到赛果记录：{summary['total_matches']}",
            f"- 至少一项可评价：{summary['valid_matches']}（完全不可评价：{summary['not_evaluable_matches']}；源结果缺失：{summary['source_excluded_matches']}）",
            f"- 比分准确率：{summary['score_accuracy']:.1%}",
            f"- 半全场 Top3 覆盖率：{summary['htft_accuracy']:.1%}",
            f"- 赛果准确率：{summary['result_accuracy']:.1%}",
            f"- 总进球区间准确率：{summary['goal_range_accuracy']:.1%}",
            "",
            "## 改进方案（仅建议，不自动修改模型）",
            "",
        ]
        warning = report["improvement_plan"].get("sample_warning")
        if warning:
            lines.append(f"> {warning}")
            lines.append("")
        for index, proposal in enumerate(report["improvement_plan"]["proposals"], 1):
            lines.extend([f"{index}. **{proposal['area']} · {proposal['priority']}**：{proposal['finding']}", f"   - {proposal['proposal']}"])
        lines.extend(["", "模型状态：保持冻结；Upgrade Package 不自动启用。"])
        return "\n".join(lines)

    def get(self, task_id: str) -> dict | None:
        path = self.report_dir / f"{task_id}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
