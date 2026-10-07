"""One CloudWatch EMF line per AI call: tokens, audio seconds and an estimated
cost in dollars.

EMF rather than PutMetricData so there is no extra API call and no extra IAM
permission: CloudWatch reads the metrics out of the log line itself. The line
carries counts only, never transcripts.
"""

from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger("workoutlog")

NAMESPACE = "WorkoutLog"

# Dollars, from the brief's section 16 (checked Sep 2026). Environment
# variables so a price change does not need a deploy of new code.
def _price(name: str, fallback: float) -> float:
    try:
        return float(os.environ.get(name, fallback))
    except ValueError:
        return fallback


def estimate_cost(input_tokens: int, output_tokens: int, audio_seconds: float) -> float:
    text = (input_tokens / 1_000_000) * _price("PRICE_INPUT_PER_M", 0.10) \
        + (output_tokens / 1_000_000) * _price("PRICE_OUTPUT_PER_M", 0.50)
    audio = (audio_seconds / 60) * _price("PRICE_TRANSCRIBE_PER_MIN", 0.0045)
    return round(text + audio, 6)


def record(route: str, input_tokens: int = 0, output_tokens: int = 0,
           audio_seconds: float = 0.0, model_calls: int = 0) -> float:
    cost = estimate_cost(input_tokens, output_tokens, audio_seconds)
    log.info(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [["Route"]],
                "Metrics": [
                    {"Name": "InputTokens", "Unit": "Count"},
                    {"Name": "OutputTokens", "Unit": "Count"},
                    {"Name": "AudioSeconds", "Unit": "Seconds"},
                    {"Name": "ModelCalls", "Unit": "Count"},
                    {"Name": "EstimatedCostUsd", "Unit": "None"},
                ],
            }],
        },
        "Route": route,
        "InputTokens": input_tokens,
        "OutputTokens": output_tokens,
        "AudioSeconds": round(audio_seconds, 2),
        "ModelCalls": model_calls,
        "EstimatedCostUsd": cost,
    }))
    return cost
