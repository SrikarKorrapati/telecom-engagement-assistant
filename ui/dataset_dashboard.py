"""Read-only exploration of the local, synthetic telecom demonstration dataset."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).resolve().parents[1] / "datasets" / "telecom_5000"
SEGMENTS = ("consistent", "loyalty_candidate", "declining_activity", "dormant", "payment_reminder")
GREEN = "#167C6B"


def label(value: Any) -> str:
    return "Unknown" if value is None else str(value).replace("_", " ").capitalize()


def money(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return float(result) if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def active_days(usage: dict[str, Any], window: str) -> float | None:
    value = usage.get(f"{window}_active_days")
    days = usage.get(f"{window}_days")
    if usage.get(f"{window}_complete") is not True or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and isinstance(days, (int, float)) and 0 <= value <= days:
        return float(value)
    return None


def invoice_totals(invoices: Any, as_of_date: Any) -> tuple[float | None, float | None]:
    if not isinstance(invoices, list):
        return None, None
    balance = overdue = 0.0
    for invoice in invoices:
        amount = money(invoice.get("outstanding"))
        try:
            due = date.fromisoformat(invoice.get("due_date", ""))
        except (ValueError, TypeError):
            return None, None
        if amount is None:
            return None, None
        balance += amount
        if due < as_of_date:
            overdue += amount
    return balance, overdue


@st.cache_data(show_spinner="Loading the local synthetic dataset…")
def load_data() -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    def read_lines(name: str) -> list[dict[str, Any]]:
        with (DATA_DIR / name).open(encoding="utf-8") as source:
            return [json.loads(line) for line in source if line.strip()]

    manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    customers = read_lines("customers.jsonl")
    expected = read_lines("expected_labels.jsonl")
    contexts = read_lines("evaluation_contexts.jsonl")
    customer_by_id = {item["id"]: item for item in customers}
    label_by_id = {item["customer_id"]: item for item in expected}
    context_by_id = {item["customer_id"]: item for item in contexts}
    if len(customer_by_id) != len(customers) or len(label_by_id) != len(expected):
        raise ValueError("The dataset contains duplicate customer or expected-label identifiers.")
    if set(customer_by_id) != set(label_by_id) or set(customer_by_id) != set(context_by_id):
        raise ValueError("Customer, expected-label and evaluation-context identifiers do not match.")
    as_of = pd.Timestamp(manifest["as_of"])
    local_date = as_of.tz_convert(ZoneInfo(manifest.get("billing_timezone", "Asia/Kolkata"))).date()
    rows = []
    for customer in customers:
        context = customer.get("context") or {}
        usage = context.get("usage") or {}
        expected_label = label_by_id[customer["id"]]
        balance, overdue = invoice_totals(context.get("invoices"), local_date)
        row = {
            "customer_id": customer["id"], "name": customer["name"],
            "email": customer.get("email", ""), "scenario": customer["scenario"],
            "plan": context.get("plan_code") or "Unknown", "active": context.get("active"),
            "tenure_months": context.get("tenure_months"),
            "expected_strategy": expected_label.get("strategy_objective", "unknown"),
            "expected_reason": expected_label.get("reason_code", "unknown"),
            "current_active_days": active_days(usage, "current"),
            "prior_active_days": active_days(usage, "prior"),
            "outstanding_inr": balance, "overdue_inr": overdue,
        }
        row.update({segment: expected_label.get("segments", {}).get(segment, "insufficient_evidence") for segment in SEGMENTS})
        rows.append(row)
    return manifest, pd.DataFrame(rows), customer_by_id, label_by_id, context_by_id


def filtered_rows(frame: pd.DataFrame, scenario: str, strategy: str, plan: str, search: str = "") -> pd.DataFrame:
    result = frame
    for column, value in (("scenario", scenario), ("expected_strategy", strategy), ("plan", plan)):
        if value != "All":
            result = result[result[column] == value]
    if search.strip():
        term = search.strip()
        match = result[["name", "email", "customer_id"]].apply(lambda series: series.str.contains(term, case=False, regex=False)).any(axis=1)
        result = result[match]
    return result


def count_chart(frame: pd.DataFrame, column: str, title: str, *, color: str = GREEN) -> None:
    counts = frame[column].value_counts(dropna=False).rename_axis("Category").reset_index(name="Customers")
    counts["Category"] = counts["Category"].map(label)
    chart = alt.Chart(counts).mark_bar(cornerRadiusEnd=3, color=color).encode(
        x=alt.X("Customers:Q", title="Customers", axis=alt.Axis(format="d")),
        y=alt.Y("Category:N", title=None, sort="-x"),
        tooltip=["Category:N", "Customers:Q"],
    ).properties(height=max(150, len(counts) * 36), title=title)
    st.altair_chart(chart, use_container_width=True)


def show_customer(customer: dict[str, Any], expected: dict[str, Any], evaluation: dict[str, Any]) -> None:
    context = customer.get("context") or {}
    st.subheader(customer["name"])
    st.caption(f"{customer.get('email', '')} · {context.get('account_id', '')} · {context.get('subscription_id', '')}")
    left, right = st.columns(2)
    left.metric("Expected strategy", label(expected.get("strategy_objective")))
    right.metric("Plan", context.get("plan_code", "Unknown"))
    st.write("Expected reason: " + label(expected.get("reason_code")))
    st.caption(f"Expected policy outcome at {expected.get('as_of', 'unknown time')}. This is a stored fixture label, not a live prediction.")
    st.dataframe(
        pd.DataFrame([{"Segment": label(segment), "Expected result": label(expected.get("segments", {}).get(segment))} for segment in SEGMENTS]),
        hide_index=True, use_container_width=True,
    )
    invoices_tab, usage_tab, feedback_tab, context_tab = st.tabs(["Invoices", "Usage", "Feedback", "Context"])
    with invoices_tab:
        invoices = context.get("invoices")
        if invoices is None:
            st.warning("Invoice evidence is missing. Balance and payment behaviour are unknown.")
        elif not invoices:
            st.info("The source explicitly records no invoices.")
        else:
            st.dataframe(pd.DataFrame(invoices), hide_index=True, use_container_width=True)
            st.caption("Money is stored as exact decimal strings in INR. Overdue comparisons use the manifest reference date in Asia/Kolkata.")
    with usage_tab:
        usage = context.get("usage") or {}
        st.dataframe(pd.DataFrame([
            {"Window": name.capitalize(), "Active days": active_days(usage, name),
             "Observed completely": usage.get(f"{name}_complete"), "Window days": usage.get(f"{name}_days"),
             "From": usage.get(f"{name}_start"), "To": usage.get(f"{name}_end")}
            for name in ("prior", "current")
        ]), hide_index=True, use_container_width=True)
        if active_days(usage, "prior") is None or active_days(usage, "current") is None:
            st.warning("A missing or incomplete usage window is unknown, not zero activity.")
        st.caption("Known outage: " + label(usage.get("known_outage")))
    with feedback_tab:
        st.caption("Synthetic text only. This dataset provides no sentiment labels.")
        for item in context.get("feedback") or []:
            st.write(f"{label(item.get('channel'))} · {item.get('occurred_at', '')}")
            st.text(item.get("text", ""))
    with context_tab:
        st.json({"source_as_of": context.get("source_as_of"), "active": context.get("active"),
                 "tenure_months": context.get("tenure_months"), "permissions": context.get("permissions"),
                 "open_dispute": context.get("open_dispute"), "reminder_history": context.get("reminder_history"),
                 "redemptions": context.get("redemptions"), "recent_deliveries": evaluation.get("recent_deliveries")}, expanded=True)


def main() -> None:
    st.set_page_config(page_title="Telecom dataset explorer", page_icon="◈", layout="wide")
    style_path = Path(__file__).with_name("styles.css")
    st.markdown(f"<style>{style_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
    st.title("Telecom dataset explorer")
    st.caption("Read-only · Local files · No API key required")
    try:
        manifest, frame, customers, labels, evaluations = load_data()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error("The local dataset could not be loaded: " + str(exc))
        st.caption(str(DATA_DIR))
        st.stop()
    st.info("Synthetic, scenario-balanced demonstration data. These counts do not represent real customer prevalence.")
    st.caption(f"Stored expected labels: {manifest.get('policy_version', 'prototype policy')} · Reference time: {manifest.get('as_of', 'unknown')} · Billing timezone: {manifest.get('billing_timezone', 'unknown')}")
    with st.sidebar:
        st.header("Filter customers")
        scenario = st.selectbox("Scenario", ["All"] + sorted(frame["scenario"].unique()), format_func=label)
        strategy = st.selectbox("Expected strategy", ["All"] + sorted(frame["expected_strategy"].unique()), format_func=label)
        plan = st.selectbox("Plan", ["All"] + sorted(frame["plan"].unique()))
        search = st.text_input("Search name, email or customer ID")
        st.caption("All filters apply together to every chart and customer detail.")
        if st.button("Reload local files"):
            load_data.clear()
            st.rerun()
    selected = filtered_rows(frame, scenario, strategy, plan, search)
    total, scenarios, held, unknown = st.columns(4)
    total.metric("Customers in view", f"{len(selected):,}", help=f"{len(frame):,} customers in the complete dataset")
    scenarios.metric("Scenarios in view", selected["scenario"].nunique())
    held.metric("Expected hold", int((selected["expected_strategy"] == "hold").sum()))
    unknown.metric("Unknown segment evidence", int(selected[list(SEGMENTS)].eq("insufficient_evidence").any(axis=1).sum()), help="Customers with at least one expected segment result marked insufficient evidence")
    if selected.empty:
        st.info("No customers match these filters. Choose All or clear the search to broaden the view.")
        return

    overview, usage_billing, browse = st.tabs(["Overview", "Usage and billing", "Customer explorer"])
    with overview:
        st.subheader("Expected policy outcomes")
        st.caption("Stored prototype policy labels at the manifest reference time; no evaluation is run here.")
        strategy_column, segment_column = st.columns(2)
        with strategy_column:
            count_chart(selected, "expected_strategy", "Expected strategy")
        with segment_column:
            segment_counts = pd.DataFrame([
                {"Segment": label(segment), "Result": label(status), "Customers": int((selected[segment] == status).sum())}
                for segment in SEGMENTS for status in ("match", "no_match", "insufficient_evidence")
            ])
            chart = alt.Chart(segment_counts).mark_bar().encode(
                x=alt.X("Customers:Q", axis=alt.Axis(format="d")), y=alt.Y("Segment:N", title=None),
                color=alt.Color("Result:N", scale=alt.Scale(domain=["Match", "No match", "Insufficient evidence"], range=[GREEN, "#C9D5CE", "#D9AA5D"]), legend=alt.Legend(orient="bottom")),
                tooltip=["Segment:N", "Result:N", "Customers:Q"],
            ).properties(height=250, title="Independent segment results")
            st.altair_chart(chart, use_container_width=True)
            st.caption("Segments overlap: one customer can match several. Each row evaluates the same filtered customers.")
        plan_column, scenario_column = st.columns(2)
        with plan_column:
            count_chart(selected, "plan", "Plan mix", color="#557AA8")
        with scenario_column:
            count_chart(selected, "scenario", "Synthetic scenario mix", color="#93A99D")

    with usage_billing:
        st.subheader("Usage evidence")
        first, second, third = st.columns(3)
        current = selected["current_active_days"].dropna()
        first.metric("Mean current active days", f"{current.mean():.1f}" if len(current) else "Unknown")
        second.metric("Complete current windows", f"{len(current):,}")
        third.metric("Unknown current usage", f"{selected['current_active_days'].isna().sum():,}")
        st.caption("Means and charts exclude missing or incomplete windows. Known zero activity remains zero; usage is measured in active days, not data volume.")
        usage = selected.melt(id_vars=["customer_id"], value_vars=["prior_active_days", "current_active_days"], var_name="Window", value_name="Active days").dropna(subset=["Active days"])
        if not usage.empty:
            usage["Window"] = usage["Window"].map({"prior_active_days": "Prior window", "current_active_days": "Current window"})
            usage = usage.groupby(["Window", "Active days"], as_index=False).agg(Customers=("customer_id", "count"))
            chart = alt.Chart(usage).mark_bar(opacity=.65).encode(
                x=alt.X("Active days:Q", bin=alt.Bin(step=5), title="Active days in complete usage windows"),
                y=alt.Y("sum(Customers):Q", title="Customer windows"),
                color=alt.Color("Window:N", scale=alt.Scale(range=[GREEN, "#557AA8"])),
                xOffset="Window:N", tooltip=["Window:N", alt.Tooltip("sum(Customers):Q", title="Customer windows")],
            ).properties(height=280)
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("No complete usage evidence is available in this selection.")
        st.subheader("Billing evidence · INR")
        balances = selected["outstanding_inr"].dropna()
        overdue = selected["overdue_inr"].dropna()
        first, second, third, fourth = st.columns(4)
        first.metric("Known outstanding", f"₹{balances.sum():,.2f}" if len(balances) else "Unknown")
        second.metric("Known overdue balance", f"₹{overdue.sum():,.2f}" if len(overdue) else "Unknown")
        third.metric("Customers with balance", f"{int((balances > 0).sum()):,}")
        fourth.metric("Unknown invoice evidence", f"{selected['outstanding_inr'].isna().sum():,}")
        st.caption(f"Sums cover {len(balances):,} customers with known invoice evidence. Missing invoices are excluded, never treated as zero. Overdue is based on the manifest reference date.")
        if len(balances):
            billed = pd.DataFrame({"Balance status": ["Positive outstanding", "Known zero outstanding", "Unknown invoice evidence"], "Customers": [int((balances > 0).sum()), int((balances == 0).sum()), int(selected["outstanding_inr"].isna().sum())]})
            st.altair_chart(alt.Chart(billed).mark_bar(color=GREEN).encode(x="Customers:Q", y=alt.Y("Balance status:N", title=None), tooltip=["Balance status:N", "Customers:Q"]).properties(height=150), use_container_width=True)

    with browse:
        st.subheader("Browse customers")
        columns = ["name", "scenario", "plan", "expected_strategy", "expected_reason", "tenure_months", "current_active_days", "outstanding_inr", "customer_id"]
        st.dataframe(selected[columns].rename(columns={column: label(column) for column in columns}), hide_index=True, use_container_width=True, height=320)
        st.caption("Blank usage or invoice values mean unknown. The selection below opens a customer's original evidence.")
        customer_id = st.selectbox("Inspect customer", selected["customer_id"].tolist(), format_func=lambda identifier: f"{customers[identifier]['name']} · {label(customers[identifier]['scenario'])}")
        show_customer(customers[customer_id], labels[customer_id], evaluations[customer_id])
    st.caption("Local source: datasets/telecom_5000 · Expected labels are fixture expectations and are never application classification inputs.")


if __name__ == "__main__":
    main()
