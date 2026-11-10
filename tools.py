"""Pinocchio handlers: hooks that watch, judge, stamp, and bounce.

Storage, in order of preference:

1. Hermes' own plugin storage (``plugins.plugin_storage.plugin_db``), so
   receipts live in the active profile's plugin data directory.
2. A local SQLite file, for running the plugin outside Hermes (unit
   tests, standalone demos). ``HERMES_PINOCCHIO_HOME`` pins the
   directory; otherwise ``~/.pinocchio`` is used.

Every handler is fail-open: Pinocchio observes and annotates, it never
breaks the conversation it is checking. Handlers return JSON strings
(tools) or the hook contracts' expected values, and never raise.
"""

from __future__ import annotations

import json
import logging
import os

try:  # Hermes loads the plugin as a package; tests may load it flat.
    from . import claims as claims_mod
    from . import receipts as receipts_mod
except ImportError:  # pragma: no cover - flat import fallback
    import claims as claims_mod
    import receipts as receipts_mod

logger = logging.getLogger(__name__)

_SETTINGS = {"bounce_on_contradiction": True, "show_unverified": True}

_store: receipts_mod.ReceiptStore | None = None


def _local_db_path() -> str:
    override = os.environ.get("HERMES_PINOCCHIO_HOME", "").strip()
    if override:
        base = override
    else:
        base = os.path.join(os.path.expanduser("~"), ".pinocchio")
    return os.path.join(base, "pinocchio.db")


def get_store() -> receipts_mod.ReceiptStore:
    global _store
    if _store is not None:
        return _store
    try:
        from plugins.plugin_storage import plugin_db

        conn = plugin_db("pinocchio")
        _store = receipts_mod.ReceiptStore(conn)
    except Exception:
        logger.debug("Pinocchio: plugin_db unavailable, using local store",
                     exc_info=True)
        _store = receipts_mod.ReceiptStore.from_path(_local_db_path())
    return _store


def reset_store() -> None:
    """Test helper: drop the cached store so the next get_store() reopens."""
    global _store
    _store = None


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def on_post_tool_call(tool_name=None, args=None, result=None,
                      session_id=None, **kwargs) -> None:
    """post_tool_call hook: record work evidence as receipts. Observer."""
    try:
        receipt = receipts_mod.classify_receipt(tool_name or "", args, result)
        if receipt is not None:
            get_store().add_receipt(session_id or "default", receipt)
    except Exception:
        logger.debug("Pinocchio: post_tool_call hook failed", exc_info=True)
    return None


def judge_text(text: str, session_id: str, record: bool) -> list[dict]:
    store = get_store()
    verdicts = claims_mod.judge_claims(text or "", store, session_id or "default")
    if record:
        for v in verdicts:
            store.add_verdict(session_id or "default", v)
    return verdicts


def on_transform_llm_output(response_text=None, session_id=None, **kwargs):
    """transform_llm_output hook: stamp checkable claims before delivery.

    Returns the response with a pinocchio verdict strip appended, or
    None to leave the text untouched (no claims, or nothing to show).
    """
    try:
        if not response_text or "pinocchio ·" in response_text:
            return None
        verdicts = judge_text(response_text, session_id or "default",
                              record=True)
        if not verdicts:
            return None
        store = get_store()
        stamp = claims_mod.format_stamp(
            verdicts, store.nose_count(),
            show_unverified=bool(_SETTINGS.get("show_unverified", True)),
        )
        if not stamp:
            return None
        return response_text.rstrip() + "\n\n—\n" + stamp
    except Exception:
        logger.debug("Pinocchio: transform hook failed", exc_info=True)
        return None


def on_pre_verify(final_response=None, session_id=None, attempt=0, **kwargs):
    """pre_verify hook: bounce a contradicted closing claim back once.

    Returns {"action": "continue", "message": ...} when the agent's
    closing claim is contradicted by its own receipts, else None. The
    verdict is not recorded here; the transform hook records the final
    text's verdicts, so a bounced and corrected claim is counted once,
    as its final version.
    """
    try:
        if not _SETTINGS.get("bounce_on_contradiction", True):
            return None
        if not final_response:
            return None
        verdicts = judge_text(final_response, session_id or "default",
                              record=False)
        message = claims_mod.bounce_message(verdicts)
        if message:
            return {"action": "continue", "message": message}
        return None
    except Exception:
        logger.debug("Pinocchio: pre_verify hook failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _resolve_session(args: dict) -> str:
    store = get_store()
    wanted = (args or {}).get("session_id")
    if wanted:
        return str(wanted)
    return store.latest_session() or "default"


def pinocchio_report(args, **kwargs) -> str:
    """Session truth table: claims made, verdicts given, receipts held."""
    try:
        store = get_store()
        session_id = _resolve_session(args or {})
        summary = store.session_summary(session_id)
        verdicts = store.verdicts(session_id, limit=50)
        return json.dumps({
            "ok": True,
            "summary": summary,
            "verdicts": [
                {
                    "claim": v["claim"], "category": v["category"],
                    "verdict": v["verdict"], "evidence": v["evidence"],
                    "ts": v["ts"],
                } for v in verdicts
            ],
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


def pinocchio_receipts(args, **kwargs) -> str:
    """List the receipts (recorded tool evidence) for a session."""
    try:
        store = get_store()
        session_id = _resolve_session(args or {})
        limit = int((args or {}).get("limit", 30) or 30)
        recs = store.receipts(session_id, limit=100000)[-limit:]
        return json.dumps({
            "ok": True,
            "session_id": session_id,
            "receipts": [
                {
                    "tool": r["tool_name"], "kind": r["kind"],
                    "target": r["target"], "status": r["status"],
                    "exit_code": r["exit_code"], "ts": r["ts"],
                } for r in recs
            ],
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
