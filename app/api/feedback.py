"""Feedback API endpoints."""

import logging

from litestar import Controller, post
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import Feedback

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
        
        # Save feedback to database
        feedback = Feedback(
            name=data.name,
            email=str(data.email),  # EmailStr to string
            message=data.message,
            read=False,
        )
        session.add(feedback)
        await session.commit()
        
        logger.info(f"Feedback saved to database from {data.email}")
        
        return FeedbackResponse(
            success=True,
            message="Thank you for your feedback! We'll review it and get back to you if needed."
        )
