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


def _htft(value) -> dict:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    found = re.search(r"(胜|平|负)\s*[/／]\s*(胜|平|负)", text)
    return {"half_result": RESULT_MAP[found.group(1)], "full_result": RESULT_MAP[found.group(2)]} if found else {}


def _result(value: str) -> list[str]:
    text = str(value).upper().replace("主胜", "1").replace("平局", "X").replace("客胜", "2")
    outcomes = []
    if "1" in text or "胜" in text:
        outcomes.append("H")
    if "X" in text or "平" in text:
        outcomes.append("D")
    if "2" in text or "负" in text:
        outcomes.append("A")
    return outcomes


def _goals(value: str) -> dict:
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
        matches, missing_dates, commits = [], [], []
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
                actual = results.get(number)
                if not actual:
                    continue
                source = item.get("results", {})
                scores = _score_list(source.get("correct_score_top3", ""))
                prediction = {
                    "score_top2": scores,
                    "htft": _htft(source.get("htft_top3", "")),
                    "result": {"result": _result(source.get("one_x_two", "")), "confidence": source.get("confidence", "unknown")},
                    "goal": _goals(source.get("total_goals", "")),
                }
                matches.append({
                    "match_id": item.get("code", f"{current.strftime('%Y%m%d')}{number:03d}"),
                    "match_date": current.isoformat(), "match": actual["match"],
                    "prediction_input": {"prediction_commit": path.stem, "model_version": payload.get("model_version"), "prompt_version": payload.get("prompt_version")},
                    "prediction": prediction, "actual": actual["actual"],
                })
            current += timedelta(days=1)
        if missing_dates:
            raise ValueError(f"PREDICTION_ARCHIVE_NOT_FOUND:{','.join(missing_dates)}")
        if not matches:
            raise ValueError("FINISHED_MATCH_RESULTS_NOT_FOUND")
        return {"source": "fresh_masked_replay_prediction", "version": "1.1", "matches": matches, "prediction_commits": commits}
