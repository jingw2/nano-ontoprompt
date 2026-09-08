"""Agent audit API (P3B-STATEAUDIT) — route ownership wrapper.

The audit list/detail routes live in `agent_application_state.py` (same
packet); this module exposes them under the packet's audit router name for
registration clarity.  Read-only only; no audit write route exists.
"""
from app.routers.agent_application_state import router

__all__ = ["router"]
