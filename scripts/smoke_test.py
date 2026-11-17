"""Exercise the direct service against the configured synthetic demo database.

The default uses offline templates. --openai enables paid generator/critic calls.
All delivery outcomes are local mock records; no email is sent.
"""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.service import EngagementService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openai", action="store_true", help="Enable the paid generator–critic workflow")
    parser.add_argument("--output", type=Path, help="Report path; defaults to .local/service[_openai]_smoke_report.json")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.openai and not settings.openai_configured:
        parser.error("--openai requires an API key and model in the server configuration")
    if not args.openai:
        settings = replace(settings, openai_api_key="", openai_model="")

    results = []
    with EngagementService(settings) as service:
        config = service.config()
        assert config["delivery_mode"] == "mock", "Refusing a real delivery transport."
        customers = service.list_customers()
        for scenario, objective in (("loyalty_offer", "offer_or_reward"),
                                    ("billing_first", "billing_reminder"),
                                    ("appreciation", "appreciation_without_offer")):
            run = None
            for customer in [c for c in customers if c["scenario"] == scenario][:10]:
                detail = service.get_customer(customer["id"])
                assert detail["context"].get("synthetic") is True
                candidate = service.create_run(customer["id"])
                if candidate["state"] == "evaluated" and candidate["strategy"]["objective"] == objective:
                    run = candidate
                    break
            if run is None:
                raise RuntimeError("No eligible fresh synthetic customer for " + scenario)
            provider = "openai" if args.openai and scenario == "loyalty_offer" else "template"
            run = service.draft(run["id"], provider=provider)
            revision = run["revisions"][-1]
            assert revision["validation"]["passed"], revision["validation"]
            service.review(revision["id"], action="approve", review_source="automated",
                           feedback="Automated smoke test on fictional data; mock transport only.")
            run = service.deliver(revision["id"])
            assert run["state"] == "delivered"
            assert len(service.deliver(revision["id"])["deliveries"]) == 1
            results.append({"scenario": scenario, "objective": run["strategy"]["objective"],
                "run_id": run["id"], "state": run["state"],
                "provider": revision["provider"], "model": revision["model_version"],
                "validation_passed": True, "idempotent_retry": True})
    report = {"model": config["openai_model"], "reasoning": config.get("openai_reasoning_effort"),
              "customer_count": len(customers), "transport": "mock", "interface": "direct_python", "results": results}
    output = args.output or ROOT / ".local" / ("service_openai_smoke_report.json" if args.openai else "service_smoke_report.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
