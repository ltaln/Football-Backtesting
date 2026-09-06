import re


def confidence_bucket(confidence: float) -> str:
    if isinstance(confidence, str):
        labels = {"低": "LOW", "中": "MEDIUM", "高": "HIGH"}
        if confidence in labels:
            return labels[confidence]
        found = re.search(r"\d+(?:\.\d+)?", confidence)
        if not found:
            return "UNKNOWN"
        confidence = float(found.group())
    percent = confidence * 100 if confidence <= 1 else confidence
    if percent < 50:
        return "0-49"
    if percent < 65:
        return "50-64"
    if percent < 80:
        return "65-79"
    return "80-100"


def evaluate_result(prediction: dict, actual: dict) -> dict:
    predicted = prediction.get("result")
    accepted = predicted if isinstance(predicted, list) else [predicted]
    return {
        "hit": actual.get("final_result") in accepted,
        "confidence_bucket": confidence_bucket(prediction.get("confidence", 0)),
    }
