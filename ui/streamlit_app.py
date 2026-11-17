"""Streamlit reviewer calling the in-process application service."""

from __future__ import annotations

import json
import atexit
from pathlib import Path
from typing import Any, Callable

import streamlit as st

from app.service import EngagementService, ServiceError
from ui.labels import customer_name, ist_time
from ui.access import require_access


RUBRIC = {
    "relevance": "Relevance", "clarity": "Clarity", "personalization": "Personalization",
    "call_to_action": "Call to action", "tone": "Tone",
}
TERMINAL_STATES = {"held", "no_outreach", "rejected", "delivered"}
STATE_LABELS = {
    "evaluated": "Ready to draft",
    "held": "On hold",
    "no_outreach": "No outreach selected",
    "drafted": "Awaiting review",
    "validation_failed": "Validation failed",
    "approved": "Approved for final checks",
    "rejected": "Rejected",
    "delivered": "Mock delivery recorded",
}


def state_label(run: dict[str, Any]) -> str:
    if (run.get("strategy") or {}).get("reason_code") in {"outreach_completed", "duplicate_outreach"}:
        return "Previously delivered"
    return STATE_LABELS.get(run.get("state"), human(run.get("state")))


@st.cache_resource(show_spinner=False)
def get_service() -> EngagementService:
    """Reuse the connection pool; each service operation creates its own session."""
    service = EngagementService()
    atexit.register(service.close)
    return service


def human(value: Any) -> str:
    if value is None or value == "":
        return "Unknown"
    return str(value).replace("_", " ").capitalize()


def flash(level: str, message: str) -> None:
    st.session_state["flash"] = (level, message)


def show_flash() -> None:
    pending = st.session_state.pop("flash", None)
    if pending:
        level, message = pending
        getattr(st, level if level in {"success", "error", "warning", "info"} else "info")(message)


def mutate(operation: Callable[[], dict[str, Any]], message: str, *, opens_run: bool = True) -> None:
    """Called only from an explicit button/form event, never while displaying data."""
    try:
        result = operation()
    except ServiceError as exc:
        flash("error", str(exc))
    else:
        if opens_run and isinstance(result, dict) and result.get("id"):
            st.session_state["active_run_id"] = result["id"]
        if message == "Evaluation ready." and result.get("state") == "delivered":
            message = "Opened the completed mock delivery. No new message was sent."
        flash("success", message)
    # Fetch fresh state even on a rejected mutation: a final gate may have held it.
    st.rerun()


def records(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def reasons(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [str(value)] if value else []


def show_json(value: Any) -> None:
    if isinstance(value, (dict, list)):
        st.json(value, expanded=False)
    else:
        st.text(str(value) if value is not None else "No evidence recorded.")


def render_decision(run: dict[str, Any]) -> None:
    strategy = run.get("strategy") or {}
    if strategy.get("objective") != "hold":
        st.subheader(human(strategy.get("objective")))
        st.write(strategy.get("reason") or "No strategy selected.")
    details = [human(strategy.get("tone")) + " tone"] if strategy.get("tone") else []
    if strategy.get("reminder_stage"):
        details.append(f"Reminder stage {strategy['reminder_stage']}")
    if details:
        st.caption(" · ".join(details))
    segments = records(run.get("segments"))
    matched = [item for item in segments if item.get("status") == "match"]
    if matched:
        st.write("**Segments:** " + " · ".join(human(item.get("segment")) for item in matched))
    elif not segments:
        st.info("No segment results yet.")
    with st.expander("Classification reasons and evidence", expanded=True):
        st.dataframe(
            [{"Segment": human(item.get("segment")), "Result": human(item.get("status")),
              "Reason": item.get("reason", "")} for item in segments],
            use_container_width=True, hide_index=True,
        )
        show_json({item.get("segment", "unknown"): item.get("evidence") for item in segments})
        st.caption(f"Invoice: {strategy.get('invoice_id') or '—'} · Offer: {strategy.get('offer_id') or '—'}")


def render_validation(revision: dict[str, Any], *, critique_required: bool = False) -> bool:
    validation = revision.get("validation") or {}
    critique_required = critique_required or validation.get("critique_required") is True or revision.get("provider") == "openai"
    critique = validation.get("critique") or {}
    passed = validation.get("passed") is True and (not critique_required or critique.get("status") == "passed")
    if passed:
        st.success("Checks passed · Ready for review")
    else:
        st.warning("Changes needed · Approval is blocked")
    checks = records(validation.get("checks"))
    for check in checks:
        if check.get("passed") is not True:
            st.write(check.get("detail") or human(check.get("code")))
    if critique_required and critique.get("status") != "passed":
        st.write(critique.get("summary") or "AI review is required before approval.")
        for finding in records(critique.get("findings")):
            if finding.get("severity") == "blocking":
                st.write(f"{finding.get('explanation', '')} {finding.get('suggested_fix', '')}".strip())
    with st.expander("Validation details"):
        if checks:
            st.dataframe(
                [{"Check": human(check.get("code")),
                  "Result": "Passed" if check.get("passed") is True else "Blocked",
                  "Detail": check.get("detail", "")} for check in checks],
                use_container_width=True, hide_index=True,
            )
        if critique_required:
            st.caption(f"AI review: {human(critique.get('status', 'pending'))} · {critique.get('model_version', 'Not recorded')}")
            if critique.get("summary"):
                st.text(critique["summary"])
            findings = records(critique.get("findings"))
            if findings:
                st.dataframe(
                    [{"Severity": human(item.get("severity")), "Finding": item.get("explanation", ""),
                      "Suggested fix": item.get("suggested_fix", "")} for item in findings],
                    use_container_width=True, hide_index=True,
                )
        else:
            st.caption("Template checks only · No AI review")
        if validation.get("generation_attempt"):
            st.caption(f"Generator attempt {validation['generation_attempt']} of {validation.get('generation_attempt_limit', 1)}")
    return passed


def render_message(run: dict[str, Any], config: dict[str, Any]) -> None:
    run_id = run["id"]
    state = run.get("state", "")
    critique_enabled = config.get("critique_enabled", config.get("openai_configured")) is True
    revisions = sorted(records(run.get("revisions")), key=lambda item: int(item.get("revision_no", 0)))
    latest_id = run.get("latest_revision_id")
    if not revisions:
        if state in TERMINAL_STATES:
            if (run.get("strategy") or {}).get("reason_code") in {"outreach_completed", "duplicate_outreach"}:
                st.info("Previous outreach is recorded. Choose another eligible customer to prepare a new demo message.")
            elif state == "no_outreach":
                st.info("No message is needed for this evaluation. Choose another eligible customer to continue.")
            else:
                st.info("Drafting is blocked. Resolve the reason or choose another eligible customer.")
            return
        configured = config.get("openai_configured") is True
        if not configured:
            st.info("Configure the server's OpenAI API key and model to generate an AI draft.")
        if st.button("Generate email", type="primary", disabled=not configured, key=f"draft-{run_id}"):
            with st.spinner("Writing your email…"):
                mutate(lambda: get_service().draft(run_id, provider="openai"), "AI draft ready.")
        return

    by_id = {item["id"]: item for item in revisions}
    if latest_id not in by_id:
        st.error("Latest revision unavailable. Refresh to continue.")
        return
    with st.expander("Revision history"):
        revision_id = st.selectbox(
            "Message revision", list(by_id), index=list(by_id).index(latest_id),
            format_func=lambda value: f"Revision {by_id[value].get('revision_no')}" + (" (latest)" if value == latest_id else " (history)"),
            key=f"revision-{run_id}-{latest_id}",
        )
    revision = by_id[revision_id]
    latest = revision_id == latest_id
    st.text_input("Email subject", value=revision.get("subject", ""), disabled=True, key=f"subject-preview-{revision_id}")
    st.text_area("Email body", value=revision.get("body", ""), height=240, disabled=True, key=f"body-preview-{revision_id}")
    passed = render_validation(revision, critique_required=critique_enabled) if state != "delivered" else False
    with st.expander("Revision identity"):
        st.text(f"Revision ID: {revision_id}\nContent hash: {revision.get('content_hash', 'Not recorded')}")

    relevant_reviews = [item for item in records(run.get("reviews")) if item.get("message_id") == revision_id]
    if relevant_reviews:
        with st.expander("Review history for this revision"):
            for source, title in (("human", "Human reviews"), ("automated", "Automated reviews"), ("unknown", "Reviews with no recorded source")):
                group = [item for item in relevant_reviews if (item.get("review_source") if item.get("review_source") in {"human", "automated"} else "unknown") == source]
                if not group:
                    continue
                st.write(title)
                st.dataframe(
                    [{"Action": human(item.get("action")), "Reviewer": item.get("actor", ""),
                      "Feedback": item.get("feedback", ""), "Time": ist_time(item.get("created_at")),
                      **{label: f"{item['scores'][key]} / 5" if key in (item.get("scores") or {}) else "Not scored" for key, label in RUBRIC.items()}}
                     for item in group],
                    use_container_width=True, hide_index=True,
                )

    if not latest:
        st.info("Historical revision. Select the latest to take action.")
        return
    if state == "delivered":
        delivered = any(
            item.get("message_id") == revision_id and item.get("status") == "delivered"
            for item in records(run.get("deliveries"))
        )
        if delivered:
            st.success("Mock delivery recorded.")
            st.download_button(
                "Download approved mock output",
                data=f"Subject: {revision.get('subject', '')}\n\n{revision.get('body', '')}",
                file_name=f"approved-mock-revision-{revision.get('revision_no')}.txt",
                mime="text/plain",
                key=f"download-{revision_id}",
            )
        else:
            st.warning("Delivery record unavailable. Check the audit trail.")
        return

    if state in TERMINAL_STATES:
        st.info("This evaluation is closed. Evaluate again to continue.")
        return

    validation = revision.get("validation") or {}
    critique_status = (validation.get("critique") or {}).get("status", "pending")
    needs_critique = critique_enabled or validation.get("critique_required") or revision.get("provider") == "openai"
    if needs_critique and critique_status in {"pending", "error", "stale", "skipped"}:
        rules_passed = all(check.get("passed") is True for check in records(validation.get("checks")) if check.get("code") != "llm_critique")
        if st.button("Run AI critique", disabled=not critique_enabled or not rules_passed, key=f"critique-{revision_id}"):
            with st.spinner("Reviewing draft…"):
                mutate(lambda: get_service().critique(revision_id), "AI review complete.")
    if state == "validation_failed" and critique_enabled:
        if st.button("Regenerate email", key=f"regenerate-{revision_id}"):
            with st.spinner("Revising draft…"):
                mutate(lambda: get_service().draft(run_id, provider="openai"), "New draft ready for review.")

    with st.expander("Edit and revalidate"):
        st.caption("Edits require a new approval.")
        with st.form(f"edit-form-{revision_id}"):
            subject = st.text_input("Updated subject", value=revision.get("subject", ""))
            body = st.text_area("Updated body", value=revision.get("body", ""), height=240)
            feedback = st.text_input("Edit note (optional)")
            save = st.form_submit_button("Save new revision")
        if save:
            mutate(lambda: get_service().edit(revision_id, subject=subject, body=body, feedback=feedback), "New revision saved.")

    st.subheader("Review")
    score_usefulness = st.checkbox("Score message usefulness", key=f"score-usefulness-{revision_id}")
    with st.form(f"review-form-{revision_id}"):
        review_feedback = st.text_area("Reviewer feedback (optional)", height=80)
        scores = {}
        if score_usefulness:
            st.caption("Human rubric: 1 = Unusable; 2 = Major revision needed; 3 = Acceptable with edits; 4 = Good; 5 = Excellent. Score the message you reviewed.")
            for column, (criterion, title) in zip(st.columns(5), RUBRIC.items()):
                scores[criterion] = column.selectbox(title, [1, 2, 3, 4, 5], index=2, key=f"score-{revision_id}-{criterion}")
        approve_column, reject_column = st.columns(2)
        approve = approve_column.form_submit_button("Approve exact revision", disabled=not passed or state == "approved", type="primary")
        reject = reject_column.form_submit_button("Reject")
    if approve:
        mutate(lambda: get_service().review(revision_id, action="approve", feedback=review_feedback, scores=scores, review_source="human"), "Approved. Ready for final checks.")
    if reject:
        mutate(lambda: get_service().review(revision_id, action="reject", feedback=review_feedback, scores=scores, review_source="human"), "Rejected.")

    if st.button("Run final checks and mock deliver", disabled=state != "approved" or not passed, key=f"deliver-{revision_id}", type="primary"):
        mutate(lambda: get_service().deliver(revision_id), "Final checks completed.")


def render_audit(run: dict[str, Any]) -> None:
    st.subheader("Mock delivery records")
    deliveries = records(run.get("deliveries"))
    if deliveries:
        st.dataframe(
            [{"Status": human(item.get("status")), "Message revision": item.get("message_id", ""),
              "Created": ist_time(item.get("created_at")), "Delivered": ist_time(item.get("delivered_at"), "Not delivered")}
             for item in deliveries], use_container_width=True, hide_index=True,
        )
        with st.expander("Delivery details"):
            show_json(deliveries)
    else:
        st.caption("No deliveries yet.")
    st.subheader("Audit trail")
    events = records(run.get("audit"))
    if not events:
        st.info("No activity yet.")
        return
    st.dataframe(
        [{"Time": ist_time(event.get("created_at")), "Event": human(event.get("event_type")), "Actor": event.get("actor", "")}
         for event in events], use_container_width=True, hide_index=True,
    )
    for index, event in enumerate(events):
        with st.expander(f"{index + 1}. {human(event.get('event_type'))}"):
            show_json(event.get("details"))


def parse_profile_context(value: str) -> dict[str, Any]:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"{constant} is not valid JSON. Use null for unknown evidence.")
    parsed = json.loads(value, parse_constant=reject_constant)
    if not isinstance(parsed, dict):
        raise ValueError("Customer context must be a JSON object.")
    return parsed


def render_add_profile() -> None:
    st.header("Add profile")
    st.caption("Synthetic data only")
    example = st.session_state.get("profile_example")
    if example is None:
        try:
            example = get_service().profile_example()
        except ServiceError as exc:
            st.error(str(exc))
            return
    if not isinstance(example, dict) or not isinstance(example.get("context"), dict):
        st.error("The complete synthetic profile example is unavailable.")
        return
    st.session_state["profile_example"] = example
    st.download_button("Download complete synthetic example", json.dumps(example, indent=2), file_name="synthetic-profile-example.json", mime="application/json")
    with st.form("add-profile-form"):
        first, second = st.columns(2)
        name = first.text_input("Fictional customer name", value=customer_name(example.get("name")) if example.get("name") else "New Customer")
        email = second.text_input("Fictional email", value=example.get("email") or "synthetic.profile@example.com")
        scenario = st.text_input("Scenario label (optional)", value=example.get("scenario") or "custom_synthetic")
        context_json = st.text_area("Customer context (JSON)", value=json.dumps(example["context"], indent=2), height=420)
        st.caption("Use null for unknown values.")
        save = st.form_submit_button("Save synthetic profile", type="primary")
    if save:
        try:
            context = parse_profile_context(context_json)
            profile = get_service().add_customer({"name": name.strip(), "email": email.strip(), "context": context, "scenario": scenario.strip() or "custom_synthetic"})
        except (ValueError, ServiceError) as exc:
            st.error(str(exc))
            return
        if not isinstance(profile, dict) or not profile.get("id"):
            st.warning("The save request completed without a profile identifier. Refresh the customer list before retrying.")
            return
        st.session_state["created_profile"] = {"id": profile["id"], "name": profile.get("name", name)}
        flash("success", "Profile saved.")
        st.rerun()
    created = st.session_state.get("created_profile")
    if created:
        st.success(f"Saved profile: {created['name']}")
        if st.button("Evaluate this profile", type="primary"):
            try:
                run = get_service().create_run(created["id"])
            except ServiceError as exc:
                st.error(str(exc))
                return
            if not isinstance(run, dict) or not run.get("id"):
                st.warning("The evaluation response is unavailable. Check saved evaluations before retrying.")
                return
            st.session_state["pending_navigation"] = {"customer_id": created["id"], "run_id": run["id"]}
            st.rerun()


def main(prepare=None) -> None:
    st.set_page_config(page_title="Telecom engagement review", page_icon="✉", layout="wide")
    require_access(st)
    if prepare is not None:
        prepare()
    style_path = Path(__file__).with_name("styles.css")
    if style_path.exists():
        # Only developer-authored CSS is rendered as HTML. Customer content never is.
        st.markdown(f"<style>{style_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

    show_flash()
    query = st.experimental_get_query_params()
    page_value = query.get("page", "") if isinstance(query, dict) else ""
    requested_page = page_value[0] if isinstance(page_value, list) and page_value else page_value
    if requested_page and st.session_state.get("last_url_page") != requested_page:
        if requested_page == "policies":
            st.session_state["workspace_page"] = "Policies"
        st.session_state["last_url_page"] = requested_page
    navigation = st.session_state.pop("pending_navigation", None)
    if navigation:
        st.session_state["workspace_page"] = "Review customer"
        st.session_state["customer_picker"] = navigation["customer_id"]
        st.session_state["previous_customer_id"] = navigation["customer_id"]
        st.session_state["active_run_id"] = navigation["run_id"]
        st.session_state["customer_scope"] = navigation.get("scope", "All customers")
    if st.session_state.get("workspace_page") == "Overview":
        st.session_state["workspace_page"] = "Review customer"
    with st.sidebar:
        st.title("Telecom")
        st.caption("Synthetic data · Mock delivery")
        page = st.radio("Workspace", ["Review customer", "Add profile", "Policies"], label_visibility="collapsed", key="workspace_page")
    if page == "Policies":
        from ui.policy_dashboard import render_policy_dashboard
        try:
            render_policy_dashboard(get_service())
        except ServiceError as exc:
            st.error(str(exc))
        return
    if page == "Add profile":
        render_add_profile()
        return
    try:
        config = get_service().config()
        customers = records(get_service().list_customers())
    except ServiceError as exc:
        st.error(str(exc))
        if st.button("Retry connection"):
            st.rerun()
        st.stop()
    if not isinstance(config, dict) or config.get("delivery_mode") != "mock":
        st.error("Mock delivery configuration is required.")
        st.stop()
    if not customers:
        st.info("No customers yet. Add a profile to begin.")
        return

    by_id = {customer["id"]: customer for customer in customers}
    with st.sidebar:
        scope = st.radio("Customer selection", ["Ready for demo", "All customers"], key="customer_scope")
        try:
            examples = records(get_service().demo_customers())
        except ServiceError as exc:
            examples = []
            st.warning(str(exc))
        demo_labels = {item["id"]: item.get("demo_case", "") for item in examples}
        options = [item["id"] for item in examples if item["id"] in by_id] if scope == "Ready for demo" else list(by_id)
        if scope == "Ready for demo":
            st.caption("Eligible now with no recorded outreach. All customers includes refusal cases and completed deliveries.")
            if not options:
                st.info("No uncontacted demo examples are currently eligible. Showing all customers.")
                options = list(by_id)
            current = st.session_state.get("customer_picker")
            if current in by_id and st.session_state.get("active_run_id") and current not in options:
                options.insert(0, current)
        if st.session_state.get("customer_picker") not in options:
            st.session_state["customer_picker"] = options[0]
        customer_id = st.selectbox("Customer", options,
            format_func=lambda key: customer_name(by_id[key].get("name", key)) + (" · " + demo_labels[key] if key in demo_labels else ""), key="customer_picker")
        customer = by_id[customer_id]
        st.caption(human(customer.get("scenario")))
        if st.session_state.get("previous_customer_id") != customer_id:
            st.session_state["previous_customer_id"] = customer_id
            st.session_state["active_run_id"] = None
        try:
            run_summaries = records(get_service().list_runs(customer_id))
        except ServiceError as exc:
            st.error(str(exc))
            return
        active_id = st.session_state.get("active_run_id")
        available_runs = {item["id"]: item for item in run_summaries if item.get("customer_id") == customer_id}
        options = [""] + list(available_runs)
        selected = st.selectbox(
            "Saved evaluations", options,
            index=options.index(active_id) if active_id in options else 0,
            format_func=lambda key: "Select an evaluation" if not key else f"{state_label(available_runs[key])} / {ist_time(available_runs[key].get('created_at'))}",
            key=f"history-{customer_id}-{active_id or 'none'}",
        )
        if selected != (active_id or ""):
            st.session_state["active_run_id"] = selected or None
            st.rerun()
        if st.button("Evaluate customer", type="primary", use_container_width=True):
            mutate(lambda: get_service().create_run(customer_id), "Evaluation ready.")
        if st.button("Refresh", use_container_width=True):
            st.rerun()
        with st.expander("Synthetic test events"):
            event = st.selectbox("Event", ["payment", "opt_out", "dispute"], format_func=lambda value: {"payment": "Record payment", "opt_out": "Withdraw contact permission", "dispute": "Open a billing dispute"}[value])
            if st.button("Apply synthetic event"):
                mutate(lambda: get_service().simulate(customer_id, event=event), "Customer updated. Evaluate again to use the new facts.", opens_run=False)
        with st.expander("Configuration"):
            st.caption(str(config.get("policy_label", "Example policy")))
            if config.get("openai_configured") is True:
                st.caption(f"{config.get('openai_model') or 'AI model'} · {config.get('openai_reasoning_effort') or 'default'} reasoning")

    try:
        customer = get_service().get_customer(customer_id)
    except ServiceError as exc:
        st.error(str(exc))
        return
    if not isinstance(customer, dict) or customer.get("id") != customer_id:
        st.error("The selected customer's details are unavailable. Refresh to try again.")
        return
    st.header(customer_name(customer.get("name")))
    st.caption(customer.get("email", ""))
    context = customer.get("context") or {}
    st.caption(f"{context.get('plan_code') or 'Unknown plan'} · " +
               (f"{context['tenure_months']} months" if context.get("tenure_months") is not None else "Unknown tenure"))
    active_id = st.session_state.get("active_run_id")
    if not active_id:
        st.info("Select Evaluate customer to begin.")
        with st.expander("Customer details"):
            show_json(context)
        return
    try:
        run = get_service().get_run(active_id)
    except ServiceError as exc:
        st.error(str(exc))
        return
    if not isinstance(run, dict) or run.get("customer_id") != customer_id:
        st.error("Choose an evaluation for this customer.")
        return
    state = run.get("state", "")
    label = state_label(run)
    completed = (run.get("strategy") or {}).get("reason_code") in {"outreach_completed", "duplicate_outreach"}
    blockers = list(dict.fromkeys(reasons(run.get("blocked_reasons")) +
                                 reasons((run.get("strategy") or {}).get("blocked_reasons"))))
    if completed:
        st.info("Previously delivered · Repeat outreach prevented")
    elif state in {"held", "validation_failed", "rejected"}:
        st.warning(label + (" · " + " ".join(blockers) if blockers else ""))
    elif state == "delivered":
        st.success(label)
    else:
        st.info(label)
    if blockers and not completed and state not in {"held", "validation_failed", "rejected"}:
        st.warning(" ".join(blockers))
    next_time = (run.get("strategy") or {}).get("next_eligible_at")
    if state == "delivered":
        reopened = [item for item in records(run.get("audit")) if item.get("event_type") == "completed_outreach_reopened"]
        if reopened:
            next_time = (reopened[-1].get("details") or {}).get("next_eligible_at")
        st.caption("Completed mock output and its original approval are preserved. No new delivery is created by evaluating again.")
    if next_time:
        st.caption("Next timing check: " + ist_time(next_time) + ". Fresh evidence, consent and approval are still required.")
    if state in TERMINAL_STATES and examples:
        next_example = next((item for item in examples if item["id"] != customer_id), None)
        if next_example and st.button("Choose another eligible customer"):
            st.session_state["pending_navigation"] = {"customer_id": next_example["id"], "run_id": None, "scope": "Ready for demo"}
            st.rerun()
    snapshot_version = (run.get("snapshot") or {}).get("customer_version")
    if state != "delivered" and snapshot_version is not None and snapshot_version != customer.get("version"):
        st.warning("Customer details changed. Evaluate again before continuing.")

    decision, message, audit = st.tabs(["Decision", "Draft & review", "Activity"])
    with decision:
        render_decision(run)
    with message:
        render_message(run, config)
    with audit:
        render_audit(run)


if __name__ == "__main__":
    main()
