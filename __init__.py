"""Pinocchio: the honesty layer for Hermes Agent.

Agents announce "done, all tests pass" when the tests do not pass.
Pinocchio keeps the receipts. A post_tool_call hook records work
evidence (commands, exit codes, test runs, file writes, git, deploys)
for every session. When the agent makes a closing claim, a
transform_llm_output hook cross-examines it against those receipts and
stamps the message: verified, unverified, or contradicted. On coding
turns a pre_verify hook bounces a contradicted claim back once, so the
agent re-runs the failing step or corrects the record before finishing.

No second model, no prompts judging prose, no network: just the
agent's own words, held against its own receipts, deterministically.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

try:  # Hermes loads the plugin as a package; tests may load it flat.
    from . import schemas, tools
except ImportError:  # pragma: no cover - flat import fallback
    import schemas
    import tools

logger = logging.getLogger(__name__)

_USAGE = (
    "Pinocchio commands:\n"
    "  /pinocchio           truth table for the most recent session\n"
    "  /pinocchio receipts  the recorded tool evidence behind the verdicts\n"
    "  /pinocchio nose      how many claims have been contradicted, all time"
)

_ICONS = {"verified": "✓", "contradicted": "✗", "unverified": "?"}


def _fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _report_text(session_id: str) -> str:
    store = tools.get_store()
    summary = store.session_summary(session_id)
    verdicts = store.verdicts(session_id, limit=20)
    lines = [
        f"Pinocchio truth table · session {session_id}",
        f"receipts: {summary['receipts']} "
        f"({summary['failed_actions']} failed actions), "
        f"claims checked: {summary['claims_checked']}, "
        f"nose: ×{summary['nose_total']} all time",
    ]
    if not verdicts:
        lines.append("No claims checked yet in this session.")
    for v in verdicts:
        lines.append(
            f"{_ICONS.get(v['verdict'], '?')} [{_fmt_ts(v['ts'])}] "
            f"“{v['claim']}” → {v['verdict']} ({v['evidence']})"
        )
    return "\n".join(lines)


def _receipts_text(session_id: str) -> str:
    store = tools.get_store()
    recs = store.receipts(session_id, limit=100000)[-20:]
    if not recs:
        return "No receipts recorded yet in this session."
    lines = [f"Pinocchio receipts · session {session_id}"]
    for r in recs:
        mark = "✓" if r["status"] == "success" else "✗"
        code = f" (exit {r['exit_code']})" if r["exit_code"] is not None else ""
        lines.append(
            f"{mark} [{_fmt_ts(r['ts'])}] {r['kind']}: "
            f"{r['target'] or r['tool_name']}{code}"
        )
    return "\n".join(lines)


def _handle_pinocchio_command(raw_args: str, **kwargs) -> str:
    """Slash command: /pinocchio [receipts|nose]."""
    try:
        store = tools.get_store()
        action = (raw_args or "").strip().lower()
        session_id = store.latest_session() or "default"
        if action in ("", "report"):
            return _report_text(session_id)
        if action == "receipts":
            return _receipts_text(session_id)
        if action == "nose":
            nose = store.nose_count()
            if nose == 0:
                return "🤥 nose: ×0. Every claim so far has checked out."
            return (
                f"🤥 nose: ×{nose}. That is how many closing claims the "
                f"receipts have contradicted, all time."
            )
        if action == "help":
            return _USAGE
        return _USAGE
    except Exception as exc:
        return f"Pinocchio command failed: {exc}"


def register(ctx):
    """Wire schemas to handlers, register the hooks, command, and skill."""
    try:
        tools._SETTINGS["bounce_on_contradiction"] = bool(
            ctx.get_config("bounce_on_contradiction", default=True)
        )
        tools._SETTINGS["show_unverified"] = bool(
            ctx.get_config("show_unverified", default=True)
        )
    except Exception:
        logger.debug("Pinocchio: could not read plugin settings", exc_info=True)

    ctx.register_tool(
        name="pinocchio_report", toolset="pinocchio",
        schema=schemas.PINOCCHIO_REPORT, handler=tools.pinocchio_report,
    )
    ctx.register_tool(
        name="pinocchio_receipts", toolset="pinocchio",
        schema=schemas.PINOCCHIO_RECEIPTS, handler=tools.pinocchio_receipts,
    )

    ctx.register_hook("post_tool_call", tools.on_post_tool_call)
    ctx.register_hook("transform_llm_output", tools.on_transform_llm_output)
    ctx.register_hook("pre_verify", tools.on_pre_verify)

    ctx.register_command(
        "pinocchio",
        handler=_handle_pinocchio_command,
        description="Honesty check: claims vs receipts truth table, receipts, nose count",
        args_hint="[receipts|nose]",
    )

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)
