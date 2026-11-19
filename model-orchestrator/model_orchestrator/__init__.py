"""Model orchestrator: an OpenAI-compatible router in front of self-hosted model servers.

Nothing in this package holds state at import time. ``api.create_app`` builds the
application and every piece of runtime state hangs off one ``state.RouterState``
object that is created in the application lifespan.
"""
