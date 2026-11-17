"""In-process application service shared by Streamlit, scripts and tests.

Each operation opens its own database session. The service owns validation,
transactions and safe errors; the UI never calls workflow/database functions.
"""

from collections import Counter
from contextlib import contextmanager
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import workflow
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.models import Customer, EngagementRun, MessageRevision, Review, DeliveryJob, AuditEvent, utcnow
from app.policy import evaluate, load_policy
from app.profiles import ProfileInput, example_profile
from app.rubric import CRITERIA, SCALE, validate_scores, summarize_reviews


class ServiceError(RuntimeError):
    """An actionable application error containing no credentials or SQL details."""

    def __init__(self, message: str, code: str = "conflict"):
        super().__init__(message)
        self.code = code


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityInput(Input):
    identity: UUID


class DraftInput(Input):
    provider: Literal["template", "openai"] = "template"


class EditInput(Input):
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=1, max_length=10000)
    feedback: str = Field(default="", max_length=4000)


class ReviewInput(Input):
    action: Literal["approve", "reject"]
    feedback: str = Field(default="", max_length=4000)
    scores: dict = Field(default_factory=dict)
    review_source: Literal["human", "automated", "unspecified"] = "unspecified"

    @field_validator("scores")
    @classmethod
    def complete_scores(cls, value):
        return validate_scores(value)


class SimulationInput(Input):
    event: Literal["payment", "opt_out", "dispute"]


def validated(model, values):
    try:
        return model.model_validate(values)
    except ValidationError as error:
        messages = []
        for finding in error.errors(include_input=False, include_url=False):
            field = ".".join(str(part) for part in finding["loc"])
            messages.append((f"{field}: " if field else "") + finding["msg"])
        raise ServiceError("; ".join(messages), "invalid_input") from None


def identity(value):
    return str(validated(IdentityInput, {"identity": value}).identity)


_JSON = TypeAdapter(Any)


def row(value):
    # Return detached JSON-compatible data, including ISO timestamps, to all clients.
    return _JSON.dump_python({column.key: getattr(value, column.key)
                              for column in value.__table__.columns}, mode="json")


def run_detail(session, run):
    result = row(run)
    result["customer_name"] = run.snapshot["customer_name"]
    revisions = session.scalars(select(MessageRevision).where(MessageRevision.run_id == run.id)
                                .order_by(MessageRevision.revision_no)).all()
    result["revisions"] = [row(revision) for revision in revisions]
    result["reviews"] = [row(review) for review in session.scalars(select(Review).where(
        Review.message_id.in_([revision.id for revision in revisions])).order_by(Review.created_at))]
    result["deliveries"] = [row(job) for job in session.scalars(select(DeliveryJob).where(
        DeliveryJob.run_id == run.id).order_by(DeliveryJob.created_at))]
    result["audit"] = [row(event) for event in session.scalars(select(AuditEvent).where(
        AuditEvent.run_id == run.id).order_by(AuditEvent.created_at))]
    return result


class EngagementService:
    """Thread-safe service: share the engine pool, never a mutable ORM session."""

    def __init__(self, settings=None, engine=None):
        self.settings = settings or Settings.from_env()
        self._owns_engine = engine is None
        try:
            self.engine = engine if engine is not None else make_engine(self.settings.database_url)
        except (SQLAlchemyError, ValueError, ImportError):
            raise ServiceError("Database configuration is invalid. Check DATABASE_URL and the installed driver.",
                               "unavailable") from None
        self.sessions = make_session_factory(self.engine)

    def close(self):
        if self._owns_engine:
            self.engine.dispose()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def _session(self):
        try:
            with self.sessions() as session:
                yield session
        except IntegrityError:
            raise ServiceError("A conflicting action was recorded. Refresh before retrying.") from None
        except SQLAlchemyError:
            raise ServiceError("Database operation failed. Check the database connection and initialization.",
                               "unavailable") from None

    def _change(self, operation, *, customer_result=False):
        from app.policy_admin import _lock

        # A dashboard save cannot change policy midway through a workflow action.
        with _lock(shared=True), self._session() as session:
            try:
                result = operation(session)
                session.flush()
                response = row(result) if customer_result else run_detail(session, result)
                session.commit()
                return response
            except workflow.WorkflowError as error:
                # Failed checks and holds have durable audit events, even on failure.
                session.commit()
                code = {404: "not_found", 422: "invalid_input", 503: "unavailable"}.get(error.status, "conflict")
                raise ServiceError(str(error), code) from None

    def health(self):
        with self._session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "database": self.engine.dialect.name, "delivery_mode": "mock"}

    def config(self):
        try:
            label = load_policy()["label"]
        except ValueError:
            raise ServiceError("Policy/content configuration is invalid.", "unavailable") from None
        return {"delivery_mode": "mock", "openai_configured": self.settings.openai_configured,
                "critique_enabled": self.settings.openai_configured,
                "openai_reasoning_effort": self.settings.openai_reasoning_effort,
                "openai_model": self.settings.openai_model, "policy_label": label}

    def profile_example(self):
        return example_profile()

    def _policy_operation(self, operation, **values):
        from app import policy_admin

        try:
            return getattr(policy_admin, operation)(self.sessions, **values)
        except policy_admin.PolicyAdminError as error:
            raise ServiceError(str(error), error.code) from None
        except IntegrityError:
            raise ServiceError("Configuration changed while saving. Refresh before retrying.") from None
        except (SQLAlchemyError, OSError):
            raise ServiceError("Configuration could not be loaded or saved. Check the database and local file access.",
                               "unavailable") from None

    def policy_configuration(self):
        """Current policy, content and database catalogue for the local dashboard."""
        return self._policy_operation("configuration")

    def save_configuration(self, *, section, document, expected_hash):
        return self._policy_operation("save_configuration", actor=self.settings.actor,
                                      section=section, document=document, expected_hash=expected_hash)

    def save_offer(self, *, offer_id, details, active, expected_hash):
        return self._policy_operation("save_offer", actor=self.settings.actor, offer_id=offer_id,
                                      details=details, active=active, expected_hash=expected_hash)

    def add_customer(self, profile):
        body = validated(ProfileInput, profile)

        def save(session):
            customer = Customer(**body.model_dump(mode="json"), version=1)
            session.add(customer)
            session.flush()
            workflow.audit(session, None, "synthetic_profile_created", {"customer_id": customer.id}, self.settings.actor)
            return customer

        return self._change(save, customer_result=True)

    def rubric(self):
        return {"version": "usefulness-v1", "criteria": CRITERIA, "scale": SCALE}

    def evaluation_summary(self):
        with self._session() as session:
            return summarize_reviews(session.scalars(select(Review)).all())

    def list_customers(self):
        with self._session() as session:
            return [dict(result._mapping) for result in session.execute(select(Customer.id, Customer.name,
                Customer.email, Customer.scenario, Customer.version).order_by(Customer.name))]

    def demo_customers(self):
        """Read-only, fresh examples; scenario labels never decide eligibility."""
        try:
            policy = load_policy()
        except ValueError:
            raise ServiceError("Policy/content configuration is invalid.", "unavailable") from None
        with self._session() as session:
            contacted = set(session.scalars(select(EngagementRun.customer_id).join(
                DeliveryJob, DeliveryJob.run_id == EngagementRun.id).where(DeliveryJob.status == "delivered")))
            examples, now = {}, utcnow()
            for customer in session.scalars(select(Customer).order_by(Customer.name)):
                context = customer.context
                if (customer.id in contacted or context.get("fixture_delivery_history")
                        or context.get("reminder_history")):
                    continue
                result = evaluate(workflow.snapshot_for(session, customer, now), policy)
                objective = result["strategy"]["objective"]
                matches = {item["segment"] for item in result["segments"] if item["status"] == "match"}
                if objective == "billing_reminder":
                    category = "Overdue bill"
                elif objective == "appreciation_without_offer":
                    category = "Consistent customer"
                elif objective == "offer_or_reward":
                    category = ("Dormant customer" if "dormant" in matches else
                                "Declining activity" if "declining_activity" in matches else "Loyalty offer")
                else:
                    continue
                if category not in examples:
                    examples[category] = {"id": customer.id, "name": customer.name,
                        "scenario": customer.scenario, "demo_case": category}
                if len(examples) == 5:
                    break
            return list(examples.values())

    def get_customer(self, customer_id):
        customer_id = identity(customer_id)
        with self._session() as session:
            found = session.get(Customer, customer_id)
            if found is None:
                raise ServiceError("Customer not found.", "not_found")
            return row(found)

    def create_run(self, customer_id):
        customer_id = identity(customer_id)
        return self._change(lambda session: workflow.create_run(session, customer_id))

    def list_runs(self, customer_id=None):
        customer_id = identity(customer_id) if customer_id is not None else None
        with self._session() as session:
            query = select(EngagementRun)
            if customer_id is not None:
                query = query.where(EngagementRun.customer_id == customer_id)
            return [{"id": run.id, "customer_id": run.customer_id,
                     "customer_name": run.snapshot["customer_name"], "state": run.state,
                     "created_at": run.created_at.isoformat(), "strategy": _JSON.dump_python(run.strategy, mode="json")}
                    for run in session.scalars(query.order_by(EngagementRun.created_at.desc()).limit(200))]

    def get_run(self, run_id):
        run_id = identity(run_id)
        with self._session() as session:
            found = session.get(EngagementRun, run_id)
            if found is None:
                raise ServiceError("Run not found.", "not_found")
            return run_detail(session, found)

    def draft(self, run_id, **values):
        run_id, body = identity(run_id), validated(DraftInput, values)
        return self._change(lambda session: workflow.draft(session, run_id, body.provider, self.settings))

    def edit(self, revision_id, **values):
        revision_id, body = identity(revision_id), validated(EditInput, values)
        return self._change(lambda session: workflow.edit(session, revision_id, body.subject, body.body,
            body.feedback, self.settings.actor, self.settings))

    def critique(self, revision_id):
        revision_id = identity(revision_id)
        return self._change(lambda session: workflow.critique(session, revision_id, self.settings, self.settings.actor))

    def review(self, revision_id, **values):
        revision_id, body = identity(revision_id), validated(ReviewInput, values)
        return self._change(lambda session: workflow.review(session, revision_id, body.action,
            body.feedback, self.settings.actor, body.scores, body.review_source, self.settings))

    def deliver(self, revision_id):
        revision_id = identity(revision_id)
        return self._change(lambda session: workflow.deliver(session, revision_id,
            self.settings.actor, settings=self.settings))

    def simulate(self, customer_id, **values):
        customer_id, body = identity(customer_id), validated(SimulationInput, values)
        return self._change(lambda session: workflow.simulate(session, customer_id, body.event,
            self.settings.actor), customer_result=True)

    def dashboard(self):
        with self._session() as session:
            counts = dict(session.execute(select(EngagementRun.state, func.count()).group_by(
                EngagementRun.state)).all())
            latest = {}
            for run in session.execute(select(EngagementRun.customer_id, EngagementRun.state,
                EngagementRun.segments, EngagementRun.strategy).order_by(EngagementRun.created_at)):
                latest[run.customer_id] = run
            segments = Counter(item["segment"] for run in latest.values()
                               for item in run.segments if item["status"] == "match")
            strategies = Counter(run.strategy["objective"] for run in latest.values())
            recent = session.execute(select(AuditEvent, Customer.name).join(
                EngagementRun, AuditEvent.run_id == EngagementRun.id).join(
                Customer, EngagementRun.customer_id == Customer.id).order_by(
                AuditEvent.created_at.desc()).limit(20)).all()
            return {"counts": {"customers": session.scalar(select(func.count()).select_from(Customer)),
                    "runs": sum(counts.values()), "awaiting_review": counts.get("drafted", 0),
                    "approved": counts.get("approved", 0), "held": counts.get("held", 0),
                    "delivered": counts.get("delivered", 0)},
                    "segments": [{"segment": key, "count": value} for key, value in sorted(segments.items())],
                    "strategies": [{"objective": key, "count": value} for key, value in sorted(strategies.items())],
                    "recent_activity": [{"run_id": event.run_id, "customer_name": name,
                        "event_type": event.event_type, "created_at": event.created_at.isoformat()}
                        for event, name in recent]}
