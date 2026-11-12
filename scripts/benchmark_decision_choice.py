"""A separate primary-intent Choice study; never confused with full multi-intent parsing."""

import copy
import json
from pathlib import Path
import statistics
import sys
import time
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.decision_preflight import INTENTS


def main():
    rows = []
    output = Path(".local/verification/preflight-kev-choice.json")
    for name in ["preflight_cases.json", "preflight_unseen.json"]:
        corpus = json.loads((Path("tests/fixtures") / name).read_text())
        for case in corpus["cases"]:
            ctx = copy.deepcopy(corpus["context"])
            ctx["previous_requests"] = case.get("previous", [])
            start = time.perf_counter()
            with httpx.Client(timeout=120, trust_env=False) as client:
                r = client.post(
                    "http://127.0.0.1:8009/v1/systemone",
                    json={
                        "state": {"user_request": case["text"], "dashboard": ctx},
                        "questions": {
                            "primary": {
                                "type": "choice",
                                "instructions": "What is the main explicit action requested in user_request? Select the best matching intent. Existing dashboard context is not a new request. Quoted or negated commands must not be executed.",
                                "criteria": INTENTS,
                            }
                        },
                    },
                )
                r.raise_for_status()
                body = r.json()
            answer = body["answers"]["primary"]
            rows.append(
                {
                    "id": case["id"],
                    "text": case["text"],
                    "corpus": name,
                    "ms": (time.perf_counter() - start) * 1000,
                    "answer": answer,
                    "expected": case["kinds"],
                    "single_intent": len(case["kinds"]) == 1,
                    "correct_primary": answer["choice"] in case["kinds"],
                }
            )
            output.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            print(case["id"], answer["choice"], rows[-1]["correct_primary"], flush=True)
    for corpus in ["preflight_cases.json", "preflight_unseen.json"]:
        subset = [r for r in rows if r["corpus"] == corpus]
        single = [r for r in subset if r["single_intent"]]
        print(
            corpus,
            {
                "primary_any": sum(r["correct_primary"] for r in subset),
                "total": len(subset),
                "single_correct": sum(r["correct_primary"] for r in single),
                "single_count": len(single),
                "p50_ms": statistics.median(r["ms"] for r in subset),
            },
        )


if __name__ == "__main__":
    main()
