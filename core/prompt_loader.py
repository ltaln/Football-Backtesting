"""Load and identify the trusted HH520 Insight AI runtime prompt."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "prompt.json"


def load_insight_prompt(root: Path = ROOT) -> dict:
    root = Path(root).resolve()
    config_path = (root / "config" / "prompt.json").resolve()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        prompt_path = (root / config["path"]).resolve()
        if not prompt_path.is_relative_to(root) or not prompt_path.is_file():
            raise ValueError("INSIGHT_PROMPT_MISSING")
        content = prompt_path.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError("INSIGHT_PROMPT_EMPTY")
        prompt_id = config["prompt_id"]
        if prompt_id != "HH520-INSIGHT-AI-V1.0":
            raise ValueError("INSIGHT_PROMPT_ID_INVALID")
        prompt_bytes = prompt_path.read_bytes().replace(b"\r\n", b"\n")
        actual_hash = hashlib.sha256(prompt_bytes).hexdigest()
        if config["sha256"] != actual_hash:
            raise ValueError("INSIGHT_PROMPT_HASH_MISMATCH")
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise ValueError("INSIGHT_PROMPT_CONFIGURATION_INVALID") from exc
    instruction = (
        "Use content as the governing policy for interpreting this backtest and presenting the final mobile response. "
        "Do not change server-calculated metrics, invent missing evidence, or apply recommendations automatically."
    )
    return {
        "prompt_id": prompt_id,
        "path": prompt_path.relative_to(root).as_posix(),
        "sha256": actual_hash,
        "content": content,
        "instruction": instruction,
    }


def prompt_binding(bundle: dict) -> dict:
    return {key: bundle[key] for key in ("prompt_id", "path", "sha256")}
