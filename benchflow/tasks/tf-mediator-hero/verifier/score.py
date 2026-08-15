"""Run each structural criterion and emit a fractional BenchFlow reward."""

import importlib.util
import json
import os
from pathlib import Path

VERIFIER_DIR = Path(os.environ.get("BENCHFLOW_VERIFIER_DIR", "/verifier"))
REWARD_TEXT = Path(os.environ.get("BENCHFLOW_REWARD_TEXT", "/logs/verifier/reward.txt"))
REWARD_JSON = Path(os.environ.get("BENCHFLOW_REWARD_JSON", "/logs/verifier/reward.json"))

spec = importlib.util.spec_from_file_location("criteria", VERIFIER_DIR / "test_output.py")
criteria = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(criteria)

checks = sorted((name, value) for name, value in vars(criteria).items() if name.startswith("test_") and callable(value))
details = []
for name, check in checks:
    try:
        check()
        details.append({"criterion": name, "passed": True, "message": ""})
    except Exception as exc:  # verifier must report all criteria
        details.append({"criterion": name, "passed": False, "message": str(exc)})

passed = sum(item["passed"] for item in details)
reward = passed / len(details) if details else 0.0
payload = {"reward": reward, "passed": passed, "total": len(details), "criteria": details}

REWARD_TEXT.parent.mkdir(parents=True, exist_ok=True)
REWARD_JSON.parent.mkdir(parents=True, exist_ok=True)
REWARD_TEXT.write_text(f"{reward:.6f}\n", encoding="utf-8")
REWARD_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
