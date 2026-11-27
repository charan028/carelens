"""Data model (blueprint section 6).

Identity, prescription, schedule and event facts live in separate tables so a
reference drug record can never be mistaken for a person's prescription, and a
self-report can never be mistaken for verified ingestion or payment.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, UTCDateTime


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DoseStatus:
    SCHEDULED = "scheduled"
    REMINDED = "reminded"
    SELF_REPORTED_TAKEN = "self_reported_taken"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"
    OVERDUE = "overdue"
    DISCONTINUED = "discontinued"
    ALL = (SCHEDULED, REMINDED, SELF_REPORTED_TAKEN, SKIPPED, UNKNOWN, OVERDUE, DISCONTINUED)
    AWAITING = (SCHEDULED, REMINDED, OVERDUE)


class BillStatus:
    UPCOMING = "upcoming"
    DUE = "due"
    OVERDUE = "overdue"
    SELF_REPORTED_PAID = "self_reported_paid"
    VERIFIED_PAID = "verified_paid"
    CANCELLED = "cancelled"
    ALL = (UPCOMING, DUE, OVERDUE, SELF_REPORTED_PAID, VERIFIED_PAID, CANCELLED)
    OPEN = (UPCOMING, DUE, OVERDUE)


class PrescriptionStatus:
    PENDING = "pending_verification"
    ACTIVE = "active"
    DISCONTINUED = "discontinued"


class Role:
    SENIOR = "senior"
    CAREGIVER = "caregiver"
    VERIFIER = "verifier"


SCOPES = ("medications", "bills", "appointments")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")
    locale: Mapped[str] = mapped_column(String(16), default="en-US")
    accessibility: Mapped[dict] = mapped_column(JSON, default=dict)
    # Caregiver escalation is opt-in and sized by the older adult.
    escalation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_after_reminders: Mapped[int] = mapped_column(Integer, default=2)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class CaregiverGrant(Base):
    __tablename__ = "caregiver_grants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("grant"))
    senior_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    caregiver_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class MedicationCatalog(Base):
    """Reference identity only. Never a person's prescription."""

    __tablename__ = "medication_catalog"
    rxcui: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    generic_name: Mapped[str] = mapped_column(String(120))
    brand_names: Mapped[list] = mapped_column(JSON, default=list)
    strength: Mapped[str] = mapped_column(String(60))
    dose_form: Mapped[str] = mapped_column(String(60))
    provenance: Mapped[str] = mapped_column(String(200))


class Prescription(Base):
    __tablename__ = "prescriptions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rx"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    rxcui: Mapped[str] = mapped_column(ForeignKey("medication_catalog.rxcui"))
    verbatim_directions: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(200))
    # Times written on the source record, as "HH:MM" in the user's timezone.
    record_times: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default=PrescriptionStatus.PENDING)
    verified_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    catalog: Mapped[MedicationCatalog] = relationship(lazy="joined")
    schedules: Mapped[list["DoseSchedule"]] = relationship(back_populates="prescription")


class DoseSchedule(Base):
    __tablename__ = "dose_schedules"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("sched"))
    prescription_id: Mapped[str] = mapped_column(ForeignKey("prescriptions.id"), index=True)
    local_time: Mapped[str] = mapped_column(String(5))  # "HH:MM"
    timezone: Mapped[str] = mapped_column(String(64))
    days_of_week: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # None = daily
    effective_from: Mapped[date] = mapped_column(Date)
    effective_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    prescription: Mapped[Prescription] = relationship(back_populates="schedules", lazy="joined")


class DoseEvent(Base):
    __tablename__ = "dose_events"
    __table_args__ = (UniqueConstraint("schedule_id", "scheduled_at", name="uq_dose_once"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("dose"))
    schedule_id: Mapped[str] = mapped_column(ForeignKey("dose_schedules.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    reminder_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    response_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default=DoseStatus.SCHEDULED)
    # "system" = set by the reminder engine, "self_report" = said by the user.
    origin: Mapped[str] = mapped_column(String(20), default="system")

    schedule: Mapped[DoseSchedule] = relationship(lazy="joined")


class DrugContentCache(Base):
    __tablename__ = "drug_content_cache"
    __table_args__ = (UniqueConstraint("rxcui", "source", name="uq_cache_source"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ref"))
    rxcui: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(80), default="")
    origin: Mapped[str] = mapped_column(String(20), default="live")  # live | bundled
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Bill(Base):
    __tablename__ = "bills"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("bill"))
    series_id: Mapped[str] = mapped_column(String(40), index=True, default=lambda: new_id("series"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    payee_label: Mapped[str] = mapped_column(String(120))
    amount_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    due_date: Mapped[date] = mapped_column(Date)
    anchor_day: Mapped[int] = mapped_column(Integer)
    recurrence: Mapped[str] = mapped_column(String(20), default="none")
    reminder_lead_days: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(30), default=BillStatus.UPCOMING)
    # Private bills stay hidden from caregivers even when "bills" is shared.
    private: Mapped[bool] = mapped_column(Boolean, default=False)


class BillEvent(Base):
    __tablename__ = "bill_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("bev"))
    bill_id: Mapped[str] = mapped_column(ForeignKey("bills.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(60))
    actor_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    detail: Mapped[str] = mapped_column(String(300), default="")


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("appt"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(40), default="doctor")
    location: Mapped[str] = mapped_column(String(160), default="")
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    reminder_lead_minutes: Mapped[int] = mapped_column(Integer, default=120)


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("note"))
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(40))
    target_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    subject_user_id: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(String(40), default="")
    resource_id: Mapped[str] = mapped_column(String(40), default="")
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    delivered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class AuditEvent(Base):
    """Who did what to which record. Never holds clinical free text."""

    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("aud"))
    actor_id: Mapped[str] = mapped_column(String(40), index=True)
    subject_user_id: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(60), default="")
    outcome: Mapped[str] = mapped_column(String(20), default="ok")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
