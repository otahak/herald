"""Feedback model for user feedback submissions."""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Feedback(Base):
    """User feedback submission."""
    
    __tablename__ = "feedback"
    
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read: Mapped[bool] = mapped_column(default=False, nullable=False)  # Track if admin has read it
    
    def __repr__(self) -> str:
        return f"<Feedback {self.email} ({self.created_at})>"
