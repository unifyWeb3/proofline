"""Vercel entrypoint for the stateless Proofline frontend/API."""

from frontend.server import wsgi_app as app

__all__ = ["app"]
