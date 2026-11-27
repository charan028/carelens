"""Request bodies. Responses are plain dicts built by the read models."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class DemoLogin(BaseModel):
    user_id: str = Field(max_length=40)


class SelfReport(BaseModel):
    status: Literal["taken", "skipped", "unsure"]


class VerifyPrescription(BaseModel):
    prescription_id: str = Field(max_length=40)
    # Optional correction of the times on the record, "HH:MM" 24-hour.
    times: Optional[list[str]] = Field(default=None, max_length=6)


class BillCreate(BaseModel):
    payee_label: str = Field(min_length=1, max_length=120)
    due_date: date
    amount_cents: Optional[int] = Field(default=None, ge=0, le=10_000_000)
    recurrence: Literal["none", "weekly", "monthly", "yearly"] = "none"
    reminder_lead_days: int = Field(default=2, ge=0, le=30)
    private: bool = False


class BillStatusUpdate(BaseModel):
    status: Literal[
        "self_reported_paid", "verified_paid", "cancelled", "upcoming"
    ]
    source: Literal[
        "self_report", "biller_confirmation", "bank_statement"
    ] = "self_report"
    note: str = Field(default="", max_length=300)


class ConsentChange(BaseModel):
    action: Literal["grant", "revoke"]
    caregiver_id: str = Field(max_length=40)
    scopes: list[Literal["medications", "bills", "appointments"]] = Field(default_factory=list)
    alerts_enabled: bool = False


class EscalationSettings(BaseModel):
    escalation_enabled: bool
    escalation_after_reminders: int = Field(ge=1, le=3)


class AssistantMessage(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    channel: Literal["phone", "glasses"] = "phone"
    context: dict = Field(default_factory=dict)


class ClockChange(BaseModel):
    advance_minutes: Optional[int] = Field(default=None, ge=1, le=60 * 24 * 14)
    set_local_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    reset: bool = False
