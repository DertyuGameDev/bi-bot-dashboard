"""Identical intent contract/corpus for read-only local and real model classification."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.config import Settings
from shared.model import CodexAdapter
from shared.preflight import local_route, model_route


def score(case, route):
    actions = route["actions"]
    kinds = sorted(a["kind"] for a in actions)
    intent = kinds == sorted(case["kinds"])
    ids = sorted({i for a in actions for i in a["chart_ids"]})
    target = ids == sorted(case["ids"]) if "ids" in case else True
    params = {p["name"]: p["value"] for a in actions for p in a["parameters"]}

    def canonical(v):
        return str(v).lower().replace("ё", "е").replace(" ", "")

    parameter = all(canonical(params.get(k, "")) == canonical(v) for k, v in case.get("params", {}).items())
    status = route["status"] == case.get("status", "resolved")
    return {
        "intent": intent,
        "target": target,
        "parameters": parameter,
        "status": status,
        "full": intent and target and parameter and status,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["local", "model", "decision", "both"], default="both")
    parser.add_argument("--output", default=".local/verification/preflight-comparison.json")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--corpus", default="tests/fixtures/preflight_cases.json")
    args = parser.parse_args()
    corpus = json.loads(Path(args.corpus).read_text())
    adapter = CodexAdapter(Settings())
    results = []

    def evaluate(case, mode):
        ctx = copy.deepcopy(corpus["context"])
        ctx["previous_requests"] = case.get("previous", [])
        start = time.perf_counter()
        try:
            if mode == "decision":
                from shared.decision_preflight import decision_route

                route, metadata = decision_route(case["text"], ctx, timeout=120)
            else:
                metadata = {}
                route = (
                    local_route(case["text"], ctx)
                    if mode == "local"
                    else model_route(case["text"], ctx, adapter)[0]
                )
            value = route.model_dump()
            return {
                "id": case["id"],
                "text": case["text"],
                "split": case.get("split", "development"),
                "mode": mode,
                "ms": (time.perf_counter() - start) * 1000,
                "result": value,
                "telemetry": metadata,
                "score": score(case, value),
            }
        except Exception as exc:
            return {
                "id": case["id"],
                "mode": mode,
                "ms": (time.perf_counter() - start) * 1000,
                "error": str(exc)[:500],
                "score": dict.fromkeys(["intent", "target", "parameters", "status", "full"], False),
            }

    modes = ["local", "model"] if args.mode == "both" else [args.mode]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(evaluate, case, mode) for mode in modes for case in corpus["cases"]]
        for future in as_completed(jobs):
            result = future.result()
            results.append(result)
            output.write_text(
                json.dumps(
                    {
                        "model": adapter.settings.model,
                        "reasoning": adapter.settings.reasoning,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "results": results,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            print(
                result["mode"],
                result["id"],
                "PASS" if result["score"]["full"] else "FAIL",
                round(result["ms"], 1),
                flush=True,
            )
    for mode in modes:
        rows = [r for r in results if r["mode"] == mode]
        times = sorted(r["ms"] for r in rows)
        print(
            json.dumps(
                {
                    "mode": mode,
                    "count": len(rows),
                    "full": sum(r["score"]["full"] for r in rows),
                    "p50_ms": statistics.median(times),
                    "p95_ms": times[int(0.95 * (len(times) - 1))],
                }
            )
        )


if __name__ == "__main__":
    main()
