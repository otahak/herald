"""Feedback page routes."""

from litestar import get
from litestar.response import Template


@get("/feedback", sync_to_thread=False)
def feedback_page() -> Template:
    """Feedback form page."""
    return Template(template_name="feedback/feedback.html")


routes = [feedback_page]
