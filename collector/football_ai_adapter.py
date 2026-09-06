import json
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from collector.collector_adapter import CollectorAdapter


RESULT_MAP = {"胜": "H", "平": "D", "负": "A"}


class _ResultPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.field: str | None = None
        self.current: dict | None = None
        self.matches: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div" and tag != "span":
            return
        classes = set(dict(attrs).get("class", "").split())
        if tag == "div" and {"element", "match"} <= classes:
            self.current, self.depth = {"teams": [], "scores": [], "statuses": []}, 1
        elif self.current is not None:
            if tag == "div":
                self.depth += 1
            if "team-name" in classes:
                self.field = "team"
            elif "score" in classes:
                self.field = "score"
            elif "time" in classes:
                self.field = "time"
            elif "status" in classes:
                self.field = "status"

    def handle_endtag(self, tag: str) -> None:
        self.field = None
        if tag == "div" and self.current is not None:
            self.depth -= 1
            if self.depth == 0:
                self.matches.append(self.current)
                self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is None or self.field is None:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self.field == "team":
            self.current["teams"].append(text)
        elif self.field == "score" and re.fullmatch(r"\d+\s*[:：-]\s*\d+", text):
            self.current["scores"].append(text.replace("：", ":"))
        elif self.field == "time":
            number = re.search(r":\s*(\d+)", text)
            if number:
                self.current["match_no"] = int(number.group(1))
        elif self.field == "status":
            self.current["statuses"].append(text)


def _direction(score: str) -> str:
    home, away = (int(value) for value in score.split("-"))
    return "H" if home > away else "A" if home < away else "D"


def _score_list(value) -> list[str]:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return [f"{home}-{away}" for home, away in re.findall(r"(?<!\d)(\d+)\s*[-:]\s*(\d+)(?!\d)", text)][:2]


def _htft(value) -> list[dict]:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    if _is_pass(text):
        return []
    found = re.findall(r"(胜|平|负)\s*[/／]\s*(胜|平|负)", text)
    return [{"half_result": RESULT_MAP[half], "full_result": RESULT_MAP[full]} for half, full in found[:3]]


def _is_pass(value) -> bool:
    text = str(value).upper()
    return "PASS" in text or "跳过" in text or "不予评价" in text


def _normal_team(value: str) -> str:
    return re.sub(r"[\s·・.()（）\-_/]|足球俱乐部|俱乐部|FC$", "", value, flags=re.I).casefold()


def _result(value: str, match_name: str) -> list[str]:
    text = re.sub(r"^\s*胜平负\s*[:：]?\s*", "", str(value), flags=re.I).strip()
    if _is_pass(text):
        return []
    if "主不败" in text:
        return ["H", "D"]
    if "客不败" in text:
        return ["D", "A"]
    if text.startswith("主胜"):
        return ["H"]
    if text.startswith("客胜"):
        return ["A"]
    if re.fullmatch(r"(?:平|平局)(?:倾向|优先)?", text):
        return ["D"]
    teams = re.split(r"\s+vs\s+", match_name, maxsplit=1, flags=re.I)
    if len(teams) != 2:
        return []
    winner = re.sub(r"[（(].*$", "", text).strip()
    winner = re.sub(r"(?:取胜|胜出|胜|倾向|优先)$", "", winner).strip()
    normalized = _normal_team(winner)
    home, away = (_normal_team(team) for team in teams)
    home_hit = bool(normalized) and (normalized == home or normalized in home or home in normalized)
    away_hit = bool(normalized) and (normalized == away or normalized in away or away in normalized)
    if home_hit != away_hit:
        return ["H" if home_hit else "A"]
    return []


def _goals(value: str) -> dict:
    if _is_pass(value):
        return {"exact": None, "range": []}
    numbers = [int(v) for v in re.findall(r"\d+", str(value))]
    if not numbers:
        return {"exact": None, "range": []}
    low, high = min(numbers), max(numbers)
    return {"exact": low if low == high else None, "range": [low, high]}


class FootballAIArchiveCollector(CollectorAdapter):
    """Read-only adapter for immutable Football AI Prediction Commit files."""

    def __init__(self, prediction_dir: Path, result_url: str = "https://www.hh520.com/?date={date}"):
        self.prediction_dir = Path(prediction_dir)
        self.result_url = result_url

    def _results(self, target: date) -> dict[int, dict]:
        url = self.result_url.format(date=target.strftime("%Y%m%d"))
        request = Request(url, headers={"User-Agent": "HH520-Insight-AI/1.0"})
        with urlopen(request, timeout=45) as response:
            html = response.read().decode("utf-8", errors="replace")
        parser = _ResultPageParser()
        parser.feed(html)
        output = {}
        for match in parser.matches:
            if match.get("match_no") is None or len(match["teams"]) < 2 or len(match["scores"]) < 2 or "完场" not in match["statuses"]:
                continue
            full = match["scores"][0].replace(":", "-")
            half = match["scores"][1].replace(":", "-")
            output[match["match_no"]] = {
                "match": f"{match['teams'][0]} vs {match['teams'][1]}",
                "home_team": match["teams"][0],
                "away_team": match["teams"][1],
                "actual": {"final_score": full, "half_result": _direction(half), "final_result": _direction(full), "total_goals": sum(int(v) for v in full.split("-"))},
            }
        return output

    def _archive(self, target: date, commit_id: str | None) -> tuple[Path, dict] | None:
        if not commit_id or not re.fullmatch(r"HH520-BTR-\d{8}-[a-f0-9]{16}", commit_id):
            raise ValueError("REPLAY_PREDICTION_COMMIT_REQUIRED")
        directory = self.prediction_dir / target.isoformat()
        path = directory / f"{commit_id}.json"
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (payload.get("date") != target.isoformat()
                or payload.get("purpose") != "BACKTEST_REPLAY"
                or payload.get("result_mask") != "TARGET_RESULT_AND_POST_KICKOFF_DATA_REMOVED_V1"):
            raise ValueError("INVALID_REPLAY_PREDICTION_COMMIT")
        return path, payload

    def collect_history(self, start_date: date, end_date: date, prediction_commit_ids: list[str] | None = None) -> dict:
        commit_ids = prediction_commit_ids or []
        expected_days = (end_date - start_date).days + 1
        if len(commit_ids) != expected_days:
            raise ValueError("ONE_REPLAY_COMMIT_PER_DATE_REQUIRED")
        matches, missing_dates, commits, exclusions = [], [], [], []
        current = start_date
        position = 0
        while current <= end_date:
            archived = self._archive(current, commit_ids[position])
            position += 1
            if archived is None:
                missing_dates.append(current.isoformat())
                current += timedelta(days=1)
                continue
            path, payload = archived
            commits.append(path.stem)
            results = self._results(current)
            for item in payload.get("matches", []):
                number = int(item.get("match_no", 0))
                code = str(item.get("code", ""))
                source_date = current.strftime("%Y%m%d")
                if not re.fullmatch(rf"{source_date}\d{{3}}", code):
                    exclusions.append({"date": current.isoformat(), "match_no": number, "code": code, "reason": "SOURCE_DIRECTORY_DATE_MISMATCH"})
                    continue
                actual = results.get(number)
                if not actual:
                    exclusions.append({"date": current.isoformat(), "match_no": number, "code": code, "reason": "FINISHED_RESULT_NOT_FOUND"})
                    continue
                source = item.get("results", {})
                scores = _score_list(source.get("correct_score_top3", ""))
                warnings = [str(value) for value in item.get("warnings", [])]
                degraded_modules = [module.get("module_id") for module in item.get("modules", []) if module.get("status") == "DEGRADED"]
                warning_text = " ".join(warnings)
                data_error = bool(degraded_modules) or any(word in warning_text for word in ("日期", "身份", "错配", "不一致", "数据", "阵容"))
                market_error = any(word in warning_text for word in ("盘口", "水位", "赔率", "市场"))
                prediction = {
                    "score_top2": scores,
                    "htft": _htft(source.get("htft_top3", "")),
                    "result": {"result": _result(source.get("one_x_two", ""), actual["match"]), "confidence": source.get("confidence", "unknown")},
                    "goal": _goals(source.get("total_goals", "")),
                }
                matches.append({
                    "match_id": code,
                    "match_date": current.isoformat(), "match": actual["match"],
                    "prediction_input": {
                        "prediction_commit": path.stem,
                        "model_version": payload.get("model_version"),
                        "prompt_version": payload.get("prompt_version"),
                        "source_catalog_date": current.isoformat(),
                        "source_catalog_url": self.result_url.format(date=source_date),
                        "date_validation_rule": "SOURCE_WEBSITE_DIRECTORY_PRIORITY_V1",
                        "source_warnings": warnings,
                        "degraded_modules": degraded_modules,
                    },
                    "error_signals": {"data_error": data_error, "market_error": market_error},
                    "prediction": prediction, "actual": actual["actual"],
                })
            current += timedelta(days=1)
        if missing_dates:
            raise ValueError(f"PREDICTION_ARCHIVE_NOT_FOUND:{','.join(missing_dates)}")
        if not matches:
            raise ValueError("FINISHED_MATCH_RESULTS_NOT_FOUND")
        return {
            "source": "fresh_masked_replay_prediction",
            "version": "1.2",
            "matches": matches,
            "prediction_commits": commits,
            "excluded_matches": exclusions,
        }
