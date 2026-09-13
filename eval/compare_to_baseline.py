"""Regression gate — the heart of "preparing for when things change".

Runs the golden datasets against the agent at the per-criterion thresholds committed in
`baseline.json` (the "score floor"). If any criterion drops below its floor, the agent has
regressed: the script prints a diff table and exits non-zero — so you can stop the change before
committing. (Wire this same command into CI — e.g. GitHub Actions — to block the merge on a regression.)

This is the agent equivalent of a snapshot test. When you legitimately improve the agent, RAISE
the numbers in baseline.json to ratchet the floor up — that's how the suite gets stricter over
time and the agent can never silently slide back.

Usage:
    cd eval
    python compare_to_baseline.py             # fast deterministic datasets only
    python compare_to_baseline.py --judge     # also run the LLM-judge datasets (needs GOOGLE_API_KEY)

Why thresholds rather than raw score deltas? `AgentEvaluator.evaluate` enforces thresholds and
raises on failure — a robust, version-stable signal. To inspect raw per-criterion numbers, run
`adk eval <agent> <dataset> --print_detailed_results`.
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import warnings

# Silence third-party noise from the ADK eval stack (mirrors pytest.ini's filterwarnings, which
# only applies under pytest — this script runs as plain `python`). These are [EXPERIMENTAL] notices
# and deprecation churn from google.adk / mcp / genai, none from our code.
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=r".*\[EXPERIMENTAL\].*", category=UserWarning)

# Make the eval target importable when run as a script (mirrors conftest.py).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# Load deploy/.env (the Makefile doesn't), mainly for the judge's Vertex AI config. Env wins over file.
import _envload  # noqa: E402
_envload.load_dotenv()
# The agent reaches the MCP Search server over HTTP — make sure it's up (docker compose up mcp qdrant).
os.environ.setdefault("MCP_SERVER_URL", "http://localhost:3000/mcp")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

from google.adk.evaluation.agent_evaluator import AgentEvaluator  # noqa: E402
import trajectory_metrics  # noqa: E402

# Register custom metrics so the gate's datasets (e.g. delegated_and_retrieved) resolve.
trajectory_metrics.register()


async def _run_dataset(entry: dict) -> tuple[str, bool, str]:
    """Evaluate one dataset at its baseline criteria. Returns (path, passed, detail)."""
    dataset_path = os.path.join(_HERE, entry["path"])
    # Isolate the run: copy the .test.json into a temp dir with a baseline-derived test_config.json
    # so we score against the COMMITTED floor, not whatever config sits next to the fixture.
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(dataset_path, tmp)
        config = {"criteria": entry["criteria"]}
        # Carry custom_metrics through so datasets that use a custom metric (e.g. the deterministic
        # trajectory gate) score against the committed definition, not the prebuilt criteria.
        if entry.get("custom_metrics"):
            config["custom_metrics"] = entry["custom_metrics"]
        with open(os.path.join(tmp, "test_config.json"), "w") as f:
            json.dump(config, f)
        target = os.path.join(tmp, os.path.basename(dataset_path))
        # num_runs defaults to 1 to keep Cohere trial-key usage down (every run fans out ~3 calls
        # per case). A dataset can override it via "num_runs" in baseline.json if it needs averaging.
        # (Bonus: num_runs=1 also dodges an ADK bug where a failed inference under load makes it call
        # len(None) on the missing inferences and crash the whole gate.)
        num_runs = entry.get("num_runs", 1)
        try:
            await AgentEvaluator.evaluate(
                agent_module="research_assistant",
                eval_dataset_file_path_or_dir=target,
                num_runs=num_runs,
            )
            return entry["path"], True, "all criteria at/above baseline"
        except AssertionError as exc:
            return entry["path"], False, str(exc).strip().splitlines()[-1] if str(exc).strip() else "below baseline"
        except Exception as exc:  # noqa: BLE001 — infra hiccup (e.g. Cohere trial limit), not a regression
            return entry["path"], False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:120]} (infra, not a score regression)"


async def main(include_judge: bool) -> int:
    with open(os.path.join(_HERE, "baseline.json")) as f:
        baseline = json.load(f)

    entries = list(baseline.get("datasets", []))
    if include_judge:
        if not os.environ.get("GOOGLE_API_KEY"):
            print("⚠️  --judge requested but GOOGLE_API_KEY is unset; skipping judge datasets.")
        else:
            entries += baseline.get("judge_datasets", [])

    print(f"Running regression gate over {len(entries)} dataset(s)...\n")
    results = [await _run_dataset(e) for e in entries]

    width = max((len(p) for p, _, _ in results), default=10)
    print(f"\n{'DATASET':<{width}}  RESULT   DETAIL")
    print("-" * (width + 40))
    regressed = False
    for path, passed, detail in results:
        flag = "✅ PASS" if passed else "🚫 FAIL"
        regressed = regressed or not passed
        print(f"{path:<{width}}  {flag}  {detail}")

    if regressed:
        print("\n🚫 REGRESSION DETECTED — at least one criterion fell below baseline. Blocking merge.")
        return 1
    print("\n✅ No regression — all criteria at or above baseline.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Block merges that regress the agent below baseline.")
    parser.add_argument("--judge", action="store_true", help="also run LLM-judge datasets (needs GOOGLE_API_KEY)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.judge)))
