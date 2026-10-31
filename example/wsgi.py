"""WSGI entry point for the example project (no websockets)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "example.settings")
application = get_wsgi_application()
