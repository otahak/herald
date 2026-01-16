"""Feedback API endpoints."""

import logging
from datetime import datetime
from typing import Optional

from litestar import Controller, post
from litestar.exceptions import ValidationException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("Herald.feedback")


# --- Request/Response Schemas ---

class FeedbackRequest(BaseModel):
    """Request to submit feedback."""
    name: str = Field(..., min_length=1, max_length=200, description="User's name")
    email: EmailStr = Field(..., description="User's email address")
    message: str = Field(..., min_length=1, max_length=5000, description="Feedback message")


class FeedbackResponse(BaseModel):
    """Response after submitting feedback."""
    success: bool
    message: str


# --- Controller ---

class FeedbackController(Controller):
    """API endpoints for feedback submission."""
    
    path = "/api/feedback"
    tags = ["feedback"]
    
    @post("/")
    async def submit_feedback(
        self,
        data: FeedbackRequest,
        session: AsyncSession,
    ) -> FeedbackResponse:
        """Submit user feedback."""
        logger.info(f"Feedback submitted from {data.email} ({data.name})")
        
        # Log the feedback (for now, we'll just log it)
        # In the future, you could store it in a database table
        logger.info(f"Feedback details:\nName: {data.name}\nEmail: {data.email}\nMessage: {data.message}")
        
        # TODO: In the future, you might want to:
        # 1. Store feedback in a database table
        # 2. Send an email notification
        # 3. Create a ticket in an issue tracker
        
        return FeedbackResponse(
            success=True,
            message="Thank you for your feedback! We'll review it and get back to you if needed."
        )
