"""Help / FAQ page routes."""

from litestar import get
from litestar.response import Template


@get("/help", sync_to_thread=False)
def help_page() -> Template:
    """Help & FAQ page."""
    return Template(template_name="help/help.html")


routes = [help_page]
