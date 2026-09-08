from collections import Counter


class ImprovementAdvisor:
    """Creates review proposals only; never mutates prediction configuration."""

    def build(self, summary: dict, records: list[dict]) -> dict:
        total = summary.get("valid_matches", summary["total_matches"])
        samples = summary.get("metric_samples", {})
        proposals = []
        if samples.get("score", total) and summary["score_accuracy"] < 0.25:
            proposals.append({"priority": "HIGH", "area": "SCORE", "finding": "比分 Top2 命中率偏低", "proposal": "复核 Correct Score 候选排序与低比分覆盖；先分联赛统计偏差，不直接调整权重。"})
        if samples.get("htft", total) and summary["htft_accuracy"] < 0.5:
            proposals.append({"priority": "MEDIUM", "area": "HTFT", "finding": "半全场整体命中率低于 50%", "proposal": "复核半场节奏与全场转换证据，重点检查比赛流突变样本。"})
        if samples.get("result", total) and summary["result_accuracy"] < 0.55:
            proposals.append({"priority": "HIGH", "area": "RESULT", "finding": "赛果方向命中率低于 55%", "proposal": "按市场冲突、球队状态和联赛分组复盘；仅形成候选改进包。"})
        if samples.get("goal_range", total) and summary["goal_accuracy"] < 0.5:
            proposals.append({"priority": "MEDIUM", "area": "GOAL", "finding": "总进球区间命中率低于 50%", "proposal": "按区间复核大小球边界，不因缺少精确值扩大模型改动。"})
        errors = Counter(item["error_type"] for item in records if item.get("error_type"))
        if errors.get("A_DATA_ERROR") or errors.get("E_INFORMATION_INSUFFICIENT") or errors.get("NOT_EVALUABLE"):
            count = errors.get("A_DATA_ERROR", 0) + errors.get("E_INFORMATION_INSUFFICIENT", 0) + errors.get("NOT_EVALUABLE", 0)
            proposals.insert(0, {"priority": "HIGH", "area": "DATA", "finding": f"发现 {count} 场数据错误或不可评价记录", "proposal": "先修复数据来源、身份或时间污染，再评价模型表现。"})
        weak_high_confidence = [bucket for bucket, values in summary["confidence_performance"].items() if bucket in {"65-79", "80-100", "HIGH"} and values["matches"] and values["accuracy"] < 0.6]
        if weak_high_confidence:
            proposals.append({"priority": "HIGH", "area": "CALIBRATION", "finding": f"高置信区间表现不足：{', '.join(weak_high_confidence)}", "proposal": "复核 Calibration 的过度自信样本；禁止基于单批次自动调参。"})
        if not proposals:
            proposals.append({"priority": "LOW", "area": "MONITORING", "finding": "本批次未触发预设异常阈值", "proposal": "保持模型冻结，继续积累分联赛与置信度样本。"})
        for proposal in proposals:
            area = proposal["area"]
            proposal.update({
                "hypothesis": f"{proposal['finding']}可能与 {area} 环节的输入覆盖、信号解释或校准有关。",
                "basis": f"本次冻结回测的 {area} 指标/根因统计触发预设审计阈值；这只是候选解释，不是既定因果。",
                "expected_impact": "若假设成立，应提高目标指标的时间外稳定性，而不是只提高本批次成绩。",
                "risk": "可能产生过拟合、联赛迁移失效或挤压其他指标；稳定版本不得直接修改。",
                "validation_method": "冻结当前版为对照，仅在开发候选中单变量改动；使用不少于100场、按时间切分的独立样本复测并分联赛/置信度报告。",
                "acceptance": "两段独立时期方向一致，目标指标改善，且其他核心指标下降不超过3个百分点。",
                "rejection": "样本不足、改善只出现在回测集、两段方向不一致，或任一核心指标下降超过3个百分点即否决。",
                "rollback": "候选失败即丢弃，继续使用当前冻结稳定版及其提示词、参数和权重。",
            })
        return {
            "auto_apply": False,
            "model_change_allowed": total >= 100,
            "minimum_sample_for_model_change": 100,
            "sample_warning": None if total >= 100 else f"当前仅 {total} 场；不足 100 场，建议只记录，不修改模型。",
            "error_distribution": dict(errors),
            "proposals": proposals,
        }
