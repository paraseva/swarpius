from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

InterruptAction = Literal["queue", "interrupt_and_replace", "interrupt_only"]


class InterruptArbiterOutputSchema(BaseModel):

    action: InterruptAction = Field(..., description="Interrupt decision")
    reason: str = Field(..., description="Concise reason for this decision")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Decision confidence from 0 to 1")
