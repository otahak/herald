from litestar import get
from litestar.response import Template

# Serve your home page
@get("/users", sync_to_thread=False)
def users() -> Template:
    return Template(
        template_name="users.html",
    )

routes = [users]