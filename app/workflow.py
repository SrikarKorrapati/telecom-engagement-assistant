"""Transactional prototype workflow. Every delivery is a local mock record."""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json

from sqlalchemy import select

from app.models import (
    Customer, Offer, EngagementRun, MessageRevision, Review, DeliveryJob,
    AuditEvent, utcnow,
)
from app.policy import evaluate, load_policy
from app.messages import generate_message
from app.validation import validate_message
from app.rubric import validate_scores
from app.critique import CritiqueResult, critique_message, fingerprint
from app.content import content_for


class WorkflowError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def audit(session, run_id, event, details=None, actor="local-demo-reviewer"):
    session.add(AuditEvent(run_id=run_id, event_type=event, actor=actor,
                           details=details or {}))


def required(session, model, identity):
    value = session.get(model, identity)
    if value is None:
        raise WorkflowError("Record not found.", 404)
    return value


def lock_customer(session, customer_id):
    customer = session.scalar(select(Customer).where(Customer.id == customer_id)
                              .with_for_update().execution_options(populate_existing=True))
    if customer is None:
        raise WorkflowError("Customer not found.", 404)
    return customer


def lock_run(session, run_id):
    run = required(session, EngagementRun, run_id)
    # All mutations acquire customer before run, preventing cross-run send races.
    customer = lock_customer(session, run.customer_id)
    run = session.scalar(select(EngagementRun).where(EngagementRun.id == run_id)
                         .with_for_update().execution_options(populate_existing=True))
    return customer, run


def snapshot_for(session, customer, now=None):
    history = []
    if customer.context.get("synthetic") is True:
        history = deepcopy(customer.context.get("fixture_delivery_history", []))
    deliveries = session.execute(select(DeliveryJob, EngagementRun).join(
        EngagementRun, DeliveryJob.run_id == EngagementRun.id).where(
        EngagementRun.customer_id == customer.id, DeliveryJob.status == "delivered")).all()
    history += [{"created_at": (job.delivered_at or job.created_at).isoformat(),
                 "purpose": run.strategy.get("purpose"),
                 "idempotency_key": job.idempotency_key} for job, run in deliveries]
    offers = session.scalars(select(Offer).order_by(Offer.code, Offer.version)).all()
    return {"customer_id": customer.id, "customer_name": customer.name,
            "customer_email": customer.email, "customer_version": customer.version,
            "as_of": (now or utcnow()).isoformat(), "context": deepcopy(customer.context),
            "offers": [{"id": o.id, "code": o.code, "version": o.version,
                        "active": o.active, "details": deepcopy(o.details)} for o in offers],
            "recent_deliveries": history}


def create_run(session, customer_id, now=None):
    customer = lock_customer(session, customer_id)
    snapshot = snapshot_for(session, customer, now)
    try:
        policy = load_policy()
    except ValueError as error:
        audit(session, None, "configuration_error", {"customer_id": customer_id})
        raise WorkflowError("Policy/content configuration is invalid. Correct it before evaluation.", 503) from error
    if "content" in policy:
        snapshot["content_policy"] = deepcopy(policy["content"])
    result = evaluate(snapshot, policy)
    objective = result["strategy"]["objective"]
    state = "held" if objective == "hold" else "no_outreach" if objective == "no_outreach" else "evaluated"
    run = EngagementRun(customer_id=customer.id, state=state, snapshot=snapshot,
                        policy_snapshot=policy, policy_hash=digest(policy),
                        segments=result["segments"], strategy=result["strategy"],
                        blocked_reasons=result["blocked_reasons"])
    session.add(run)
    session.flush()
    audit(session, run.id, "evaluated", {"objective": objective,
          "policy_hash": run.policy_hash, "blocked_reasons": run.blocked_reasons})
    return run


def ensure_editable(run):
    if run.state in {"held", "no_outreach", "rejected", "delivered"}:
        raise WorkflowError("This run is closed. Create a new evaluation from current facts.")


def require_current_policy(session, run, action):
    """The policy name stays fixed; its full content hash identifies changes."""
    try:
        current = load_policy()
    except ValueError:
        hold(session, run, ["Current policy/content configuration is invalid."])
    if digest(current) != run.policy_hash:
        hold(session, run, [f"Policy/content changed. Create a new evaluation before {action}."])


def validation_for(run, revision, settings=None, snapshot=None):
    """Recheck rules and the saved exact-text critique without another model call."""
    snapshot = run.snapshot if snapshot is None else snapshot
    previous = revision.validation or {}
    result = validate_message(revision.subject, revision.body, snapshot, run.strategy)
    required = bool(previous.get("critique_required") or revision.provider == "openai"
                    or (settings and settings.openai_configured))
    result["critique_required"] = required
    for key in ("generation_attempt", "generation_attempt_limit"):
        if key in previous:
            result[key] = previous[key]
    if not required:
        result["critique"] = {"status": "not_required", "summary": "Offline template demo: deterministic checks only; no AI critique."}
        return result
    critique = deepcopy(previous.get("critique") or {"status": "pending", "summary": "AI critique is required for this revision."})
    passed = False
    if critique.get("status") == "passed":
        try:
            assessment = CritiqueResult.model_validate({key: critique.get(key) for key in ("verdict", "summary", "findings")})
            passed = (assessment.verdict == "pass" and critique.get("provider") == "openai"
                      and critique.get("input_hash") == fingerprint(revision.subject, revision.body, snapshot, run.strategy)
                      and critique.get("content_hash") == revision.content_hash
                      and revision.content_hash == digest({"subject": revision.subject, "body": revision.body})
                      and critique.get("policy_hash") == run.policy_hash)
        except (ValueError, TypeError, KeyError):
            passed = False
        if not passed:
            critique["status"] = "stale"
            critique["summary"] = "The saved critique does not cover this exact message and policy. Run critique again."
    result["critique"] = critique
    result["checks"].append({"code": "llm_critique", "passed": passed,
                             "detail": critique.get("summary", "AI critique must pass before approval.")})
    result["passed"] = result["passed"] and passed
    return result


def assess_revision(session, run, revision, settings, actor="local-demo-reviewer"):
    rules = validate_message(revision.subject, revision.body, run.snapshot, run.strategy)
    required = bool((revision.validation or {}).get("critique_required") or revision.provider == "openai"
                    or (settings and settings.openai_configured))
    if required:
        if rules["passed"]:
            try:
                critique = critique_message(revision.subject, revision.body, run.snapshot, run.strategy, settings)
            except ValueError as error:
                critique = {"status": "error", "summary": str(error)}
        else:
            critique = {"status": "skipped", "summary": "Correct the rule-check failures before AI critique."}
        critique.update(content_hash=revision.content_hash, policy_hash=run.policy_hash)
        revision.validation = {**(revision.validation or {}), "critique_required": True, "critique": critique}
    revision.validation = validation_for(run, revision, settings)
    run.state = "drafted" if revision.validation["passed"] else "validation_failed"
    if required:
        audit(session, run.id, "critique_completed", {"revision_id": revision.id,
              "content_hash": revision.content_hash, "critique": revision.validation["critique"]}, actor)


def add_revision(session, run, data, settings=None, *, require_critique=False, attempt=None, attempt_limit=None):
    revisions = session.scalars(select(MessageRevision).where(MessageRevision.run_id == run.id)).all()
    revision = MessageRevision(run_id=run.id, revision_no=len(revisions) + 1,
        subject=data["subject"], body=data["body"],
        content_hash=digest({"subject": data["subject"], "body": data["body"]}),
        validation=validate_message(data["subject"], data["body"], run.snapshot, run.strategy),
        provider=data["provider"], model_version=data.get("model_version", "none"),
        prompt_version=data.get("prompt_version", "none"),
        template_version=data.get("template_version", "unspecified"))
    session.add(revision)
    session.flush()
    run.latest_revision_id = revision.id
    revision.validation = {**revision.validation, "critique_required": require_critique,
                           **({"generation_attempt": attempt, "generation_attempt_limit": attempt_limit} if attempt else {})}
    audit(session, run.id, "revision_created", {"revision_id": revision.id,
          "content_hash": revision.content_hash, "validation": revision.validation,
          "provider": revision.provider, "model": revision.model_version})
    assess_revision(session, run, revision, settings)
    return revision


def repair_feedback(revision):
    validation = revision.validation or {}
    critique = validation.get("critique") or {}
    return {"critique_summary": critique.get("summary", ""),
            "findings": [{key: item.get(key, "") for key in ("category", "explanation", "suggested_fix")}
                         for item in critique.get("findings", [])],
            "rule_failures": [check["detail"] for check in validation.get("checks", [])
                              if not check["passed"] and check["code"] != "llm_critique"]}


def draft(session, run_id, provider, settings):
    _, run = lock_run(session, run_id)
    ensure_editable(run)
    try:
        configured = load_policy()
    except ValueError:
        hold(session, run, ["Current policy/content configuration is invalid."])
    if digest(configured) != run.policy_hash:
        hold(session, run, ["Policy/content changed. Create a new evaluation before generating a draft."])
    previous = session.get(MessageRevision, run.latest_revision_id) if run.latest_revision_id else None
    required = bool(settings.openai_configured or provider == "openai"
                    or (previous and (previous.validation or {}).get("critique_required")))
    feedback = repair_feedback(previous) if previous and not previous.validation.get("passed") else None
    limit = content_for(run.snapshot)["critique"]["max_generation_attempts"] if provider == "openai" else 1
    for attempt in range(1, limit + 1):
        try:
            data = generate_message(run.snapshot, run.strategy, provider=provider, settings=settings,
                                    feedback=feedback, allow_invalid=provider == "openai")
        except ValueError as error:
            audit(session, run.id, "generation_failed", {"provider": provider, "attempt": attempt, "reason": str(error)})
            raise WorkflowError(str(error), 422) from error
        revision = add_revision(session, run, data, settings, require_critique=required,
                                attempt=attempt, attempt_limit=limit)
        audit(session, run.id, "generation_completed", {"provider": provider,
              "model": data.get("model_version"), "attempt": attempt,
              "reasoning_effort": settings.openai_reasoning_effort if provider == "openai" else None,
              "revision_id": revision.id})
        if revision.validation["passed"] or revision.validation["critique"]["status"] == "error":
            break
        feedback = repair_feedback(revision)
        if revision.validation["critique"]["status"] == "changes_requested":
            feedback["previous_courtesy_copy"] = data["courtesy_copy"]
    audit(session, run.id, "generation_loop_completed", {"attempts": attempt, "limit": limit,
          "passed": revision.validation["passed"], "revision_id": revision.id,
          "limit_reached": attempt == limit and not revision.validation["passed"]})
    return run


def latest_revision(session, revision_id):
    revision = required(session, MessageRevision, revision_id)
    customer, run = lock_run(session, revision.run_id)
    session.refresh(revision)
    if run.latest_revision_id != revision.id:
        audit(session, run.id, "stale_revision_rejected", {"revision_id": revision.id})
        raise WorkflowError("This revision is outdated. Review the latest revision.")
    return customer, run, revision


def edit(session, revision_id, subject, body, feedback, actor, settings=None):
    _, run, old = latest_revision(session, revision_id)
    ensure_editable(run)
    require_current_policy(session, run, "editing")
    new = add_revision(session, run, {"subject": subject, "body": body, "provider": "human_edit",
        "model_version": old.model_version, "prompt_version": old.prompt_version,
        "template_version": old.template_version}, settings,
        require_critique=bool(old.provider == "openai" or (old.validation or {}).get("critique_required")))
    session.add(Review(message_id=old.id, action="edit", feedback=feedback,
                       actor=actor, content_hash=old.content_hash))
    audit(session, run.id, "edited_approval_invalidated", {"old_revision_id": old.id,
          "new_revision_id": new.id}, actor)
    return run


def critique(session, revision_id, settings, actor):
    _, run, revision = latest_revision(session, revision_id)
    ensure_editable(run)
    try:
        configured = load_policy()
    except ValueError:
        hold(session, run, ["Current policy/content configuration is invalid."])
    if digest(configured) != run.policy_hash:
        hold(session, run, ["Policy/content changed. Create a new evaluation before critique."])
    revision.validation = {**(revision.validation or {}), "critique_required": True}
    assess_revision(session, run, revision, settings, actor)
    audit(session, run.id, "critique_retried_approval_invalidated", {"revision_id": revision.id}, actor)
    return run


def review(session, revision_id, action, feedback, actor, scores=None, review_source="unspecified", settings=None):
    _, run, revision = latest_revision(session, revision_id)
    ensure_editable(run)
    if action not in {"approve", "reject"}:
        raise WorkflowError("Invalid review action.", 422)
    try:
        scores = validate_scores({} if scores is None else scores)
        if review_source not in {"human", "automated", "unspecified"}:
            raise ValueError("Invalid review source.")
    except ValueError as error:
        raise WorkflowError(str(error), 422) from error
    if action == "approve":
        require_current_policy(session, run, "approval")
    revision.validation = validation_for(run, revision, settings)
    if action == "approve" and not revision.validation["passed"]:
        run.state = "validation_failed"
        audit(session, run.id, "approval_blocked", revision.validation, actor)
        raise WorkflowError("Validation failed. Correct the draft before approval.")
    session.add(Review(message_id=revision.id, action=action, feedback=feedback,
                       actor=actor, content_hash=revision.content_hash, scores=scores,
                       review_source=review_source))
    run.state = "approved" if action == "approve" else "rejected"
    audit(session, run.id, action, {"revision_id": revision.id,
          "content_hash": revision.content_hash, "scores": scores,
          "review_source": review_source, "rubric_version": "usefulness-v1"}, actor)
    return run


def hold(session, run, reasons):
    run.state = "held"
    run.blocked_reasons = reasons
    audit(session, run.id, "delivery_blocked", {"reasons": reasons})
    raise WorkflowError("Delivery blocked: " + "; ".join(reasons))


def deliver(session, revision_id, actor, now=None, settings=None):
    customer, run, revision = latest_revision(session, revision_id)
    existing = session.scalar(select(DeliveryJob).where(DeliveryJob.message_id == revision.id,
                                                       DeliveryJob.status == "delivered"))
    if existing and run.state == "delivered":
        return run  # Idempotent retry of the exact previously delivered revision.
    if run.state != "approved":
        raise WorkflowError("Approve the latest validated revision before mock delivery.")
    approval = session.scalar(select(Review).where(Review.message_id == revision.id)
                              .order_by(Review.created_at.desc(), Review.id.desc()))
    if not approval or approval.action != "approve" or approval.content_hash != revision.content_hash:
        hold(session, run, ["Exact-revision approval is missing."])
    if digest({"subject": revision.subject, "body": revision.body}) != revision.content_hash:
        hold(session, run, ["Message changed after approval."])
    current = snapshot_for(session, customer, now)
    try:
        policy = load_policy()
    except ValueError:
        hold(session, run, ["Current policy/content configuration is invalid."])
    fresh = evaluate(current, policy)
    if fresh["strategy"]["objective"] in {"hold", "no_outreach"}:
        hold(session, run, fresh["blocked_reasons"] or [fresh["strategy"]["reason"]])
    if digest(policy) != run.policy_hash:
        hold(session, run, ["Policy changed. A new evaluation and approval are required."])
    if "content" in policy:
        current["content_policy"] = deepcopy(policy["content"])
    for key in ("customer_version", "customer_name", "customer_email", "context", "offers"):
        if current[key] != run.snapshot[key]:
            hold(session, run, ["Customer evidence or offer catalogue changed. Evaluate and approve again."])
    for key in ("objective", "purpose", "tone", "invoice_id", "offer_id", "reminder_stage", "idempotency_key"):
        if fresh["strategy"].get(key) != run.strategy.get(key):
            hold(session, run, ["The permitted strategy changed. Evaluate and approve again."])
    validation = validation_for(run, revision, settings, snapshot=current)
    if not validation["passed"]:
        hold(session, run, ["Final message validation failed."])
    key = run.strategy["idempotency_key"]
    if session.scalar(select(DeliveryJob.id).where(DeliveryJob.idempotency_key == key)):
        hold(session, run, ["This logical outreach has already been recorded."])
    sent_at = now or utcnow()
    job = DeliveryJob(run_id=run.id, message_id=revision.id, approval_review_id=approval.id,
        idempotency_key=key, status="delivered", delivered_at=sent_at,
        result={"mode": "mock", "purpose": run.strategy["purpose"], "recipient": customer.email,
                "content_hash": revision.content_hash, "transport": "local_record_only"})
    session.add(job)
    session.flush()
    if run.strategy["objective"] == "billing_reminder":
        context = deepcopy(customer.context)
        context.setdefault("reminder_history", []).append({"invoice_id": run.strategy["invoice_id"],
             "stage": run.strategy["reminder_stage"], "sent_at": sent_at.isoformat()})
        customer.context = context
    customer.version += 1
    customer.updated_at = sent_at
    run.state = "delivered"
    audit(session, run.id, "mock_delivered", {"delivery_id": job.id, "revision_id": revision.id,
          "idempotency_key": key}, actor)
    return run


def simulate(session, customer_id, event, actor):
    customer = lock_customer(session, customer_id)
    if customer.context.get("synthetic") is not True:
        raise WorkflowError("Simulation is available only for explicitly synthetic records.", 422)
    context = deepcopy(customer.context)
    if event == "payment":
        for invoice in context.get("invoices") or []:
            if Decimal(invoice["outstanding"]) > 0:
                invoice.update(outstanding="0.00", paid_full_at=utcnow().isoformat(), status="paid")
    elif event == "opt_out":
        context["permissions"] = {"billing": "denied", "marketing": "denied"}
    elif event == "dispute":
        context["open_dispute"] = True
    else:
        raise WorkflowError("Unknown simulation.", 422)
    # A simulated event does not refresh unrelated historical evidence.
    customer.context = context
    customer.version += 1
    customer.updated_at = utcnow()
    audit(session, None, "synthetic_" + event, {"customer_id": customer.id,
          "customer_version": customer.version}, actor)
    return customer
