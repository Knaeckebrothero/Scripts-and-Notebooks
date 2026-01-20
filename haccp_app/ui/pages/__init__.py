"""Page modules for HACCP application."""
from .kitchen import render_kitchen_page
from .housekeeping import render_housekeeping_page
from .hotel import render_hotel_page
from .login import render_login_page
from .admin import render_admin_page

__all__ = [
    "render_kitchen_page",
    "render_housekeeping_page",
    "render_hotel_page",
    "render_login_page",
    "render_admin_page",
]
