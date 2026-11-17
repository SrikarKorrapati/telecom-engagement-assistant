"""Read-only exploration of the local, synthetic telecom demonstration dataset."""
from __future__ import annotations

import atexit
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from app.service import EngagementService, ServiceError
from ui.labels import customer_name, ist_time

DATA_DIR = Path(__file__).resolve().parents[1] / "datasets" / "telecom_5000"
SEGMENTS = ("consistent", "loyalty_candidate", "declining_activity", "dormant", "payment_reminder")


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
            "customer_id": customer["id"], "name": customer_name(customer["name"]),
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


def show_customer(customer: dict[str, Any], expected: dict[str, Any], evaluation: dict[str, Any]) -> None:
    context = customer.get("context") or {}
    st.subheader(customer_name(customer["name"]))
    st.caption(customer.get("email", ""))
    st.write(f"**Plan:** {context.get('plan_code') or 'Unknown'}")
    tenure = context.get("tenure_months")
    st.write(f"**Customer since:** {tenure} months" if tenure is not None else "**Customer since:** Unknown")
    st.write(f"**Stored strategy:** {label(expected.get('strategy_objective'))}")
    days = active_days(context.get("usage") or {}, "current")
    st.write(f"**Recent activity:** {days:g} active days" if days is not None else "**Recent activity:** Unknown")
    invoices = context.get("invoices")
    amounts = [money(item.get("outstanding")) for item in invoices] if isinstance(invoices, list) else None
    balance = sum(amounts) if amounts is not None and all(amount is not None for amount in amounts) else None
    st.write(f"**Outstanding balance:** ₹{balance:,.2f}" if balance is not None else "**Outstanding balance:** Unknown")


@st.cache_resource(show_spinner=False)
def get_service() -> EngagementService:
    """Read the same live evaluations as the reviewer."""
    service = EngagementService()
    atexit.register(service.close)
    return service


def render_overview() -> None:
    st.header("Overview")
    try:
        dashboard = get_service().dashboard()
    except ServiceError as exc:
        st.warning(f"The dashboard is unavailable. {exc}")
        st.caption("The customer list remains available below.")
        if st.button("Retry dashboard"):
            st.rerun()
        return
    if not isinstance(dashboard, dict):
        st.warning("The dashboard response is unavailable. Refresh to try again.")
        return
    counts = dashboard.get("counts") or {}
    metrics = [
        ("Customers", "customers"), ("Awaiting review", "awaiting_review"),
        ("Approved", "approved"), ("Evaluations", "runs"),
        ("Historical holds", "held"), ("Mock deliveries", "delivered"),
    ]
    for start in (0, 3):
        for column, (title, field) in zip(st.columns(3), metrics[start:start + 3]):
            column.metric(title, counts.get(field, 0))
    st.caption("Saved evaluations, including earlier policy decisions · Mock delivery. The synthetic dataset deliberately includes refusal cases.")

    segments_column, strategies_column = st.columns(2)
    with segments_column:
        st.subheader("Customer segments")
        st.caption("Latest evaluation · Segments may overlap")
        segment_rows = dashboard.get("segments") or []
        if segment_rows:
            st.bar_chart(
                [{"Segment": label(item.get("segment")), "Customers": item.get("count", 0)} for item in segment_rows],
                x="Segment", y="Customers", color="#167C6B", height=280,
            )
        else:
            st.info("No segment results yet.")
    with strategies_column:
        st.subheader("Selected strategies")
        st.caption("Latest evaluation per customer")
        strategy_rows = dashboard.get("strategies") or []
        if strategy_rows:
            st.bar_chart(
                [{"Strategy": label(item.get("objective")), "Customers": item.get("count", 0)} for item in strategy_rows],
                x="Strategy", y="Customers", color="#557AA8", height=280,
            )
        else:
            st.info("No strategies yet.")
    st.subheader("Recent activity")
    activity = dashboard.get("recent_activity") or []
    if activity:
        st.dataframe(
            [{"Customer": customer_name(item.get("customer_name")), "Activity": label(item.get("event_type")),
              "Time": ist_time(item.get("created_at")), "Evaluation": item.get("run_id") or "—"}
             for item in activity],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No activity yet. Create an evaluation in the reviewer to see activity here.")
    if st.button("Refresh overview"):
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Customers", page_icon="◈", layout="wide")
    style_path = Path(__file__).with_name("styles.css")
    st.markdown(f"<style>{style_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
    st.title("Customers")
    render_overview()
    st.divider()
    st.header("Browse customers")
    st.caption("Demo data · Stored strategies are sample labels, not live decisions.")
    try:
        _, frame, customers, labels, evaluations = load_data()
    except (OSError, ValueError, KeyError, TypeError):
        st.error("The customer data could not be loaded. Check the local dataset files.")
        st.stop()
    search = st.text_input("Search customers", placeholder="Name, email or customer ID")
    selected = filtered_rows(frame, "All", "All", "All", search)
    st.caption(f"{len(selected):,} customers")
    if selected.empty:
        st.info("No customers match. Try another name or clear the search.")
        return
    columns = {"name": "Customer", "plan": "Plan", "expected_strategy": "Stored strategy"}
    table = selected[list(columns)].rename(columns=columns).copy()
    table["Stored strategy"] = table["Stored strategy"].map(label)
    st.dataframe(table, hide_index=True, use_container_width=True, height=260)
    customer_id = st.selectbox("View customer", selected["customer_id"].tolist(),
                               format_func=lambda identifier: customer_name(customers[identifier]["name"]))
    show_customer(customers[customer_id], labels[customer_id], evaluations[customer_id])


if __name__ == "__main__":
    main()
