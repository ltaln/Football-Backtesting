from collections import Counter


class ImprovementAdvisor:
    """Creates review proposals only; never mutates prediction configuration."""

    def build(self, summary: dict, records: list[dict]) -> dict:
        total = summary["total_matches"]
        proposals = []
        if summary["score_accuracy"] < 0.25:
            proposals.append({"priority": "HIGH", "area": "SCORE", "finding": "比分 Top2 命中率偏低", "proposal": "复核 Correct Score 候选排序与低比分覆盖；先分联赛统计偏差，不直接调整权重。"})
        if summary["htft_accuracy"] < 0.5:
            proposals.append({"priority": "MEDIUM", "area": "HTFT", "finding": "半全场整体命中率低于 50%", "proposal": "复核半场节奏与全场转换证据，重点检查比赛流突变样本。"})
        if summary["result_accuracy"] < 0.55:
            proposals.append({"priority": "HIGH", "area": "RESULT", "finding": "赛果方向命中率低于 55%", "proposal": "按市场冲突、球队状态和联赛分组复盘；仅形成候选改进包。"})
        if summary["goal_accuracy"] < 0.5:
            proposals.append({"priority": "MEDIUM", "area": "GOAL", "finding": "总进球精确命中率低于 50%", "proposal": "同时查看区间命中率，避免仅因精确值偏差扩大模型改动。"})
        errors = Counter(item["error_type"] for item in records if item.get("error_type"))
        if errors.get("DATA_ERROR"):
            proposals.insert(0, {"priority": "HIGH", "area": "DATA", "finding": f"发现 {errors['DATA_ERROR']} 场数据错误", "proposal": "先修复数据来源、身份或时间污染，再评价模型表现。"})
        weak_high_confidence = [bucket for bucket, values in summary["confidence_performance"].items() if bucket in {"65-79", "80-100", "HIGH"} and values["matches"] and values["accuracy"] < 0.6]
        if weak_high_confidence:
            proposals.append({"priority": "HIGH", "area": "CALIBRATION", "finding": f"高置信区间表现不足：{', '.join(weak_high_confidence)}", "proposal": "复核 Calibration 的过度自信样本；禁止基于单批次自动调参。"})
        if not proposals:
            proposals.append({"priority": "LOW", "area": "MONITORING", "finding": "本批次未触发预设异常阈值", "proposal": "保持模型冻结，继续积累分联赛与置信度样本。"})
        return {
            "auto_apply": False,
            "model_change_allowed": total >= 100,
            "minimum_sample_for_model_change": 100,
            "sample_warning": None if total >= 100 else f"当前仅 {total} 场；不足 100 场，建议只记录，不修改模型。",
            "error_distribution": dict(errors),
            "proposals": proposals,
        }
