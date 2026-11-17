"""View-first configuration dashboard; all writes require explicit form saves."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json

import streamlit as st

from app.content import RESPECTFUL_TONES
from app.service import ServiceError


OBJECTIVES = {"offer_or_reward": "Offer or reward", "billing_reminder": "Billing reminder",
              "appreciation_without_offer": "Appreciation"}
PATTERNS = {"prohibited_language": "Prohibited language", "unsupported_claims": "Unsupported claims",
            "unsupported_dates": "Unverified dates", "sensitive_data": "Sensitive information"}


def offer_status(offer: dict, now: datetime | None = None) -> str:
    if not offer.get("active"):
        return "Inactive"
    try:
        expiry = datetime.fromisoformat(offer["details"]["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            return "Invalid expiry"
        return "Available" if expiry > (now or datetime.now(timezone.utc)) else "Expired"
    except (KeyError, TypeError, ValueError):
        return "Invalid expiry"


def _key(section, snapshot, suffix):
    content_hash = snapshot["content_hash" if section == "content" else "policy_hash"]
    return f"policy-admin-{section}-{content_hash}-{st.session_state.get('policy-admin-epoch', 0)}-{suffix}"


def _save(operation, message):
    try:
        operation()
    except ServiceError as error:
        st.error(str(error))
    else:
        st.session_state["policy-admin-notice"] = message
        st.rerun()


def _segments(service, snapshot):
    policy, rules = snapshot["policy"], snapshot["policy"]["segments"]
    st.caption("Segments can overlap. Missing evidence stays unknown.")
    rows = [
        {"Segment": "Consistent", "IF": "Latest completed bill paid on time."},
        {"Segment": "Loyalty", "IF": f"Active subscription + tenure ≥ {rules['loyalty_tenure_months']} months."},
        {"Segment": "Declining activity", "IF": f"Usage is lower than in the previous {rules['usage_window_days']}-day period, but is not zero."},
        {"Segment": "Dormant", "IF": f"Active subscription + zero active days in a complete {rules['usage_window_days']}-day window."},
        {"Segment": "Overdue bill", "IF": "Verified outstanding balance > 0 and due date has passed."},
    ]
    st.markdown("| Segment | IF |\n| --- | --- |\n" + "\n".join(
        f"| {row['Segment']} | {row['IF']} |" for row in rows))
    with st.expander("Strategy priority"):
        st.markdown("1. **Hold:** missing data, a dispute, or contact is blocked.\n2. **Billing:** an unpaid bill is overdue and has no previous reminder.\n3. **Offer:** loyal, declining, or dormant customer + an eligible offer.\n4. **Appreciation:** latest bill paid on time + active, non-declining usage + no earlier action.\n5. **No outreach:** none of the above applies.")
        st.caption("Consent, disputes, contact frequency, and duplicate controls still apply.")
    with st.expander("Edit rules"):
        with st.form(_key("policy", snapshot, "segments-form")):
            changed = deepcopy(policy)
            left, right = st.columns(2)
            with left:
                for field, label, maximum in (("loyalty_tenure_months", "Loyalty tenure · months", 1200), ("usage_window_days", "Usage window · days", 1200)):
                    changed["segments"][field] = st.number_input(label, min_value=1, max_value=maximum, value=rules[field])
            with right:
                changed["outreach"]["frequency_days"] = st.number_input("Time between contacts · days", min_value=1, max_value=10000, value=policy["outreach"]["frequency_days"])
                changed["max_source_age_hours"] = st.number_input("Maximum evidence age · hours", min_value=1, max_value=8760, value=policy["max_source_age_hours"])
                st.caption(f"Timezone: {policy['billing_timezone']} · Channel: email")
            submitted = st.form_submit_button("Save rules", type="primary")
        if submitted:
            _save(lambda: service.save_configuration(section="policy", document=changed, expected_hash=snapshot["policy_hash"]), "Rules saved. Re-evaluate customers to use them.")


def _offers(service, snapshot):
    offers = sorted(snapshot["offers"], key=lambda item: (
        {"Available": 0, "Expired": 1, "Inactive": 2}.get(offer_status(item), 3),
        item["code"], -item["version"], item["id"],
    ))
    if not offers:
        st.info("No offers configured.")
        return
    options = {item["id"]: item for item in offers}
    selected = st.selectbox("Offer version", list(options), format_func=lambda key: f"{options[key]['code']} · v{options[key]['version']} · {offer_status(options[key])}")
    offer = options[selected]
    st.subheader(offer["details"]["value_text"])
    left, right = st.columns(2)
    with left:
        st.markdown("**Eligible plans**")
        st.write(", ".join(offer["details"]["eligible_plans"]))
    with right:
        st.markdown("**Expires · UTC**")
        expiry = datetime.fromisoformat(offer["details"]["expires_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        st.write(expiry.strftime("%d %b %Y, %H:%M"))
    st.write(offer["details"]["terms"])
    st.caption(f"{offer_status(offer)} · Once every {offer['details']['redemption_days']} days · Customer eligibility checked separately")
    with st.expander("All offer versions"):
        st.dataframe([
            {"Offer": item["details"]["title"], "Version": item["version"], "Status": offer_status(item)}
            for item in offers
        ], hide_index=True, use_container_width=True)
    with st.expander("Edit offer"):
        st.caption("Save creates a new version and retires earlier active versions of this offer code.")
        form_key = f"policy-admin-offer-{selected}-{snapshot['catalog_hash']}-{st.session_state.get('policy-admin-epoch', 0)}"
        with st.form(form_key):
            details = deepcopy(offer["details"])
            left, right = st.columns(2)
            with left:
                details["title"] = st.text_input("Offer title", value=details["title"], max_chars=160)
                details["value_text"] = st.text_input("Benefit", value=details["value_text"], max_chars=300)
                plans = st.text_area("Eligible plans · one per line", value="\n".join(details["eligible_plans"]), height=100)
                details["eligible_plans"] = list(dict.fromkeys(line.strip() for line in plans.splitlines() if line.strip()))
            with right:
                expiry = datetime.fromisoformat(details["expires_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
                day = st.date_input("Expiry date · UTC", value=expiry.date())
                time = st.time_input("Expiry time · UTC", value=expiry.time().replace(tzinfo=None))
                details["expires_at"] = datetime.combine(day, time, tzinfo=timezone.utc).isoformat()
                details["redemption_days"] = st.number_input("Redemption interval · days", min_value=1, max_value=10000, value=details["redemption_days"])
                active = st.checkbox("Enable new version", value=offer["active"])
            details["terms"] = st.text_area("Terms", value=details["terms"], height=100, max_chars=1500)
            submitted = st.form_submit_button("Save new offer version", type="primary")
        if submitted:
            _save(lambda: service.save_offer(offer_id=selected, details=details, active=active, expected_hash=snapshot["catalog_hash"]), "New offer version saved.")


def _reminders(service, snapshot):
    st.subheader("One courteous reminder")
    st.write("Send one reminder when a bill has an unpaid balance and its due date has passed.")
    st.write("If that bill already has a recorded reminder, stop. There is no escalation.")
    st.caption("Valid evidence, billing permission and contact limits still apply.")


def _content(service, snapshot):
    content = snapshot["content"]
    objective = st.selectbox("Message purpose", list(OBJECTIVES), format_func=OBJECTIVES.get)
    stage = None
    if objective == "billing_reminder":
        stage = "1"
    template = content["templates"][objective]
    wording = template["stages"][stage] if stage else template
    st.subheader(wording["subject"])
    st.write(wording["opening"])
    st.caption("[Verified customer, offer, or invoice facts]")
    st.write(template["action"])
    st.write(template["closing"])
    with st.expander("Edit message"):
        with st.form(_key("content", snapshot, f"message-{objective}-{stage}")):
            changed = deepcopy(content)
            changed_template = changed["templates"][objective]
            changed_wording = changed_template["stages"][stage] if stage else changed_template
            changed_wording["subject"] = st.text_input("Subject", value=wording["subject"], max_chars=160)
            changed_wording["opening"] = st.text_area("Template opening", value=wording["opening"], max_chars=240, height=80)
            changed_template["action"] = st.text_area("Required statement / call to action", value=template["action"], max_chars=1500, height=100)
            changed_template["closing"] = st.text_area("Template closing", value=template["closing"], max_chars=240, height=80)
            if stage:
                st.caption("Billing reminders always use a courteous tone.")
            else:
                tones = sorted(RESPECTFUL_TONES)
                changed["tones"][objective] = st.selectbox("Tone", tones, index=tones.index(content["tones"][objective]))
            left, right = st.columns(2)
            with left:
                changed["greeting"] = st.text_input("Greeting · all messages", value=content["greeting"], max_chars=160, help="Keep {customer_name} exactly once.")
            with right:
                changed["signature"] = st.text_input("Signature · all messages", value=content["signature"], max_chars=160)
            st.caption("OpenAI writes its own opening and closing. The subject and required statement stay fixed.")
            submitted = st.form_submit_button("Save message", type="primary")
        if submitted:
            _save(lambda: service.save_configuration(section="content", document=changed, expected_hash=snapshot["content_hash"]), "Message content saved.")
    with st.expander("Blocked language · advanced"):
        st.caption("One regular expression per line. Invalid patterns cannot be saved.")
        with st.form(_key("content", snapshot, "patterns-form")):
            changed = deepcopy(content)
            for group, label in PATTERNS.items():
                value = st.text_area(label, value="\n".join(content["validation_patterns"][group]), height=100)
                changed["validation_patterns"][group] = [line.strip() for line in value.splitlines() if line.strip()]
            submitted = st.form_submit_button("Save language rules", type="primary")
        if submitted:
            _save(lambda: service.save_configuration(section="content", document=changed, expected_hash=snapshot["content_hash"]), "Language rules saved.")
    with st.expander("AI prompts · read only"):
        st.caption(f"Generator: {content['generation']['prompt_version']} · Critic: {content['critique']['prompt_version']} · Maximum attempts: {content['critique']['max_generation_attempts']}")
        st.markdown("**Generator**")
        st.write(content["generation"]["system_prompt"])
        st.markdown("**Critic**")
        st.write(content["critique"]["system_prompt"])


def render_policy_dashboard(service):
    st.header("Policy & content")
    notice = st.session_state.pop("policy-admin-notice", None)
    if notice:
        st.success(notice)
    try:
        snapshot = service.policy_configuration()
    except ServiceError as error:
        st.error(str(error))
        return
    policy, content, offers = snapshot["policy"], snapshot["content"], snapshot["offers"]
    st.caption("One current policy · Save changes in place")
    for column, label, value in zip(st.columns(3),
                                   ("Segments", "Available offers", "Reminders per bill"),
                                   (5, sum(offer_status(item) == "Available" for item in offers), 1)):
        column.metric(label, value)
    st.divider()
    tabs = st.tabs(["Segments", "Offers", "Reminders", "Messages"])
    for tab, render in zip(tabs, (_segments, _offers, _reminders, _content)):
        with tab:
            render(service, snapshot)
    st.divider()
    st.caption("After saving changes, create a new customer evaluation.")
    left, right = st.columns(2)
    with left:
        if st.button("Discard edits", key="policy-admin-refresh"):
            st.session_state["policy-admin-epoch"] = st.session_state.get("policy-admin-epoch", 0) + 1
            st.rerun()
    with right:
        st.download_button("Download configuration", data=json.dumps(snapshot, indent=2), file_name="telecom-policy-configuration.json", mime="application/json", key="policy-admin-export")
