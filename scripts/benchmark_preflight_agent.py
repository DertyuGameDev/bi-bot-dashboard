"""Real ReAct comparison on isolated synthetic data, never the user's session DB."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.agent import Agent
from shared.analytics import empty_state, install_dataset
from shared.config import Settings
from shared.source import Source
from shared.storage import Store
from shared.tools import Tools

CASES = [
    ("chart", "Замени первый график на столбчатый, остальные не меняй"),
    (
        "structure",
        "Расположи графики в одну колонку и добавь вычисляемый столбец delta2 по формуле fact - plan",
    ),
    ("ambiguous", "Измени график"),
]


def run(case, mode):
    with tempfile.TemporaryDirectory(prefix="my-preflight-eval-") as folder:
        settings = Settings(
            source="demo",
            model_adapter="codex",
            preflight_mode=mode,
            storage=str(Path(folder) / "state.sqlite3"),
            _env_file=None,
        )
        store = Store(settings.storage)
        state = empty_state()
        install_dataset(state, Source(settings).load("2025-01-01", "2025-12-31", "sales"))
        Tools(settings, store, "eval", state).invoke("create_dashboard", {})
        before = copy.deepcopy(state)
        start = time.perf_counter()
        try:
            result = Agent(settings, store).run("eval", state, case[1])
            if case[0] == "chart":
                passed = (
                    state["dashboard"]["charts"][0]["type"] == "bar"
                    and state["dashboard"]["charts"][1:] == before["dashboard"]["charts"][1:]
                )
            elif case[0] == "structure":
                passed = state["dashboard"]["layout"] == "one-column" and any(
                    c["name"] == "delta2" for c in state["calculations"]
                )
            else:
                passed = state["dashboard"] == before["dashboard"] and bool(result["text"])
            if mode == "agent":
                passed = (
                    passed
                    and bool(state["debug"].get("preflight", {}).get("result"))
                    and state["debug"]["preflight"].get("extra_model_calls") == 0
                )
            return {
                "id": case[0],
                "mode": mode,
                "ms": (time.perf_counter() - start) * 1000,
                "passed": passed,
                "answer": result["text"],
                "steps": state["debug"].get("agent_steps"),
                "preflight": state["debug"].get("preflight"),
            }
        except Exception as exc:
            return {
                "id": case[0],
                "mode": mode,
                "ms": (time.perf_counter() - start) * 1000,
                "passed": False,
                "error": str(exc)[:500],
            }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["off", "local", "model", "agent", "decision"],
        default=["off", "local", "model"],
    )
    parser.add_argument("--output", default=".local/verification/preflight-agent.json")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    rows = []
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, case, mode) for mode in args.modes for case in CASES]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            output.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            print(row["mode"], row["id"], row["passed"], round(row["ms"]), flush=True)


if __name__ == "__main__":
    main()
