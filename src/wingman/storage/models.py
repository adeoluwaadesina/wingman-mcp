"""Pydantic models for plans and tasks."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TaskStatus = Literal["pending", "in_progress", "done", "blocked"]

PLAN_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-'.:()]{1,64}$")


def validate_plan_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("plan name must be a string")
    name = value.strip()
    if not PLAN_NAME_RE.match(name):
        raise ValueError(
            "plan name must be 1-64 chars; letters, digits, space, hyphen, "
            "underscore, apostrophe, period, colon, parentheses only"
        )
    return name


class Task(BaseModel):
    id: int
    plan_name: str
    content: str
    status: TaskStatus = "pending"
    sort_order: int
    position: int = 0  # 1-based rank within plan; set by db.get_plan
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class Plan(BaseModel):
    name: str
    created_at: datetime
    updated_at: datetime
    tasks: list[Task] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return validate_plan_name(value)

    @property
    def counts(self) -> dict[str, int]:
        c = {"pending": 0, "in_progress": 0, "done": 0, "blocked": 0}
        for t in self.tasks:
            c[t.status] = c.get(t.status, 0) + 1
        c["total"] = len(self.tasks)
        return c
