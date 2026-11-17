"""Opt-in live evaluation of six synthetic samples; never approves or delivers messages."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.messages import generate_message
from app.policy import evaluate, load_policy
from app.validation import validate_message


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Make six paid OpenAI requests; no automatic retries.")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return
    settings = Settings.from_env()
    if not settings.openai_configured:
        raise SystemExit("Configure the server-side OpenAI key and model first.")
    path = ROOT / "docs/evaluation/openai_samples.json"
    if path.exists():
        raise SystemExit("A live report already exists. Preserve or move it before deliberately rerunning.")
    data = ROOT / "datasets/telecom_5000"
    customers = {v["id"]: v for v in map(json.loads, (data / "customers.jsonl").read_text().splitlines())}
    contexts = {v["customer_id"]: v for v in map(json.loads, (data / "evaluation_contexts.jsonl").read_text().splitlines())}
    samples = json.loads((ROOT / "docs/evaluation/usefulness_samples.json").read_text())
    assert len(samples) == 6
    policy = load_policy()
    report = {"model": settings.openai_model, "reasoning": settings.openai_reasoning_effort,
              "generated_at": datetime.now(timezone.utc).isoformat(), "attempted": 0,
              "passed": 0, "source": "live OpenAI, no approval or delivery", "results": []}
    for sample in samples:
        customer = customers[sample["customer_id"]]
        context = contexts[customer["id"]]
        snapshot = {"customer_id": customer["id"], "customer_name": customer["name"],
                    "customer_email": customer["email"], "customer_version": customer["version"],
                    "as_of": context["as_of"], "context": customer["context"],
                    "offers": sample["offers"], "recent_deliveries": context["recent_deliveries"],
                    "content_policy": deepcopy(policy["content"])}
        strategy = evaluate(snapshot, policy)["strategy"]
        result = {k: deepcopy(v) for k, v in sample.items() if k not in {"draft", "assessment", "source"}}
        result.update(strategy=strategy, source="live OpenAI", assessment=None)
        report["attempted"] += 1
        try:
            draft = generate_message(snapshot, strategy, provider="openai", settings=settings)
            findings = validate_message(draft["subject"], draft["body"], snapshot, strategy)
            result.update(draft=draft, validation=findings, status="passed" if findings["passed"] else "failed")
            content = json.dumps({"subject": draft["subject"], "body": draft["body"]}, sort_keys=True)
            result["content_hash"] = hashlib.sha256(content.encode()).hexdigest()
            report["passed"] += int(findings["passed"])
        except ValueError as error:
            result.update(status="failed", error=str(error))
        report["results"].append(result)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps({"scenario": result["scenario"], "status": result["status"]}), flush=True)
    print(json.dumps({"attempted": report["attempted"], "passed": report["passed"]}))


if __name__ == "__main__":
    main()
