"""Claims and verdicts: hold the agent's words against its receipts.

Claim extraction is deliberately narrow. Pinocchio only speaks about
claims it can check: test results, builds, checks/lint, file writes,
git operations, deploys, and (only when nothing checkable was said) a
generic "done/fixed" claim. A claim is:

- verified:     the most recent matching receipt succeeded
- contradicted: the most recent matching receipt failed
- unverified:   no matching receipt exists in this session

Everything is deterministic: no model calls, no heuristics beyond the
patterns below, and the evidence cited is always a real recorded
receipt with its timestamp.
"""

from __future__ import annotations

import re
import time

# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

# Each pattern yields (category, matched text, optional file path).
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("tests", re.compile(
        r"((?:all\s+)?(?:the\s+)?tests?\s+(?:now\s+)?(?:pass|passing|passed|are\s+green|green)|"
        r"test\s+suite[^\n.]{0,40}?(?:pass|passing|passed|green|success)|"
        r"(?:all\s+)?\d+\s+(?:tests?\s+)?(?:pass|passing|passed)|"
        r"no\s+(?:test\s+)?failures)", re.IGNORECASE)),
    ("build", re.compile(
        r"(build\s+(?:now\s+)?(?:succeeds|succeeded|success|successful|passes|passed|complete|completed)|"
        r"(?:compiles|compiled|compilation)[^\n.]{0,30}?(?:success|successfully|clean|without\s+errors)|"
        r"built\s+successfully)", re.IGNORECASE)),
    ("checks", re.compile(
        r"((?:all\s+)?checks\s+(?:pass|passing|passed)|"
        r"lint(?:ing)?\s+(?:is\s+)?(?:clean|passes|passed)|"
        r"no\s+(?:lint\s+)?errors)", re.IGNORECASE)),
    ("git", re.compile(
        r"((?:changes\s+)?(?:are\s+|have\s+been\s+|has\s+been\s+)?(?:committed|pushed|merged)|"
        r"pushed\s+(?:to|the)\b[^\n.]{0,30}|"
        r"merged\s+(?:in)?to\s+\w+)", re.IGNORECASE)),
    ("deploy", re.compile(
        r"((?:it\s+)?(?:is\s+)?(?:now\s+)?(?:deployed|published|released|shipped)|"
        r"(?:deploy|deployment)\s+(?:is\s+)?(?:complete|completed|successful|done|live)|"
        r"(?:is|it\s+is|now)\s+live\b)", re.IGNORECASE)),
    ("file", re.compile(
        r"(?:created|updated|wrote|written|edited|modified|patched|added|saved)"
        r"\s+(?:the\s+)?(?:file\s+)?`?([\w][\w./\-]*\.[A-Za-z0-9]{1,10})`?",
        re.IGNORECASE)),
    ("changes", re.compile(
        r"(changes\s+(?:are\s+)?(?:applied|saved|in\s+place|written\s+to\s+disk))",
        re.IGNORECASE)),
]

_GENERIC_RE = re.compile(
    r"\b(done|fixed|complete|completed|resolved|works|working\s+now|all\s+set|"
    r"successfully|sorted|ready\s+to\s+go)\b",
    re.IGNORECASE,
)

# Which receipt kinds can settle each claim category.
_POOLS: dict[str, list[str]] = {
    "tests": ["test"],
    "build": ["build"],
    "checks": ["test", "lint", "build"],
    "git": ["git"],
    "deploy": ["deploy"],
    "file": ["file"],
    "changes": ["file", "command"],
}

_POOL_LABELS = {
    "tests": "test run",
    "build": "build",
    "checks": "check",
    "git": "git operation",
    "deploy": "deploy",
    "file": "file write",
    "changes": "file write",
}


def extract_claims(text: str) -> list[dict]:
    """Find checkable claims in the agent's closing message.

    Returns a list of {"category", "claim", "path"} dicts, de-duplicated
    by category (plus one entry per distinct file path). A generic
    completion claim is included (category "generic") only when no
    checkable claim was found.
    """
    if not text:
        return []
    claims: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for category, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            path = ""
            if category == "file":
                path = (match.group(1) or "").strip()
                key = (category, path.lower())
            else:
                key = (category, "")
            if key in seen:
                continue
            seen.add(key)
            snippet = match.group(0).strip()
            claims.append({
                "category": category,
                "claim": snippet[:120],
                "path": path,
            })
    if not claims and _GENERIC_RE.search(text):
        match = _GENERIC_RE.search(text)
        claims.append({
            "category": "generic",
            "claim": match.group(0).strip()[:120],
            "path": "",
        })
    return claims


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def _fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _evidence_for(receipt: dict, label: str) -> str:
    when = _fmt_ts(receipt["ts"])
    target = receipt.get("target") or ""
    if receipt["status"] == "failure":
        code = receipt.get("exit_code")
        code_txt = f" (exit {code})" if code is not None else ""
        return f"the last {label} failed{code_txt} at {when}: {target}".strip()
    return f"the last {label} succeeded at {when}: {target}".strip()


def judge_claim(claim: dict, store, session_id: str) -> dict:
    """Return {"claim", "category", "verdict", "evidence"} for one claim."""
    category = claim["category"]
    base = {"claim": claim["claim"], "category": category}

    if category == "generic":
        recs = store.receipts(session_id, limit=100000)
        work = [r for r in recs if r["kind"] != "read"]
        if work and work[-1]["status"] == "failure":
            last = work[-1]
            return {
                **base, "verdict": "contradicted",
                "evidence": (
                    f"the session's last action failed at {_fmt_ts(last['ts'])}: "
                    f"{last.get('target') or last['tool_name']}"
                ),
            }
        return {
            **base, "verdict": "unverified",
            "evidence": "a general claim pinocchio cannot settle from receipts",
        }

    kinds = _POOLS[category]
    label = _POOL_LABELS[category]
    pool = store.receipts(session_id, kinds=kinds, limit=100000)

    if category == "file" and claim.get("path"):
        wanted = claim["path"].replace("\\", "/").lower()
        matched = [
            r for r in pool
            if (r.get("target") or "").replace("\\", "/").lower().endswith(wanted)
            or wanted.endswith((r.get("target") or "").replace("\\", "/").lower())
        ]
        if not matched:
            return {
                **base, "verdict": "unverified",
                "evidence": f"no write to {claim['path']} was recorded this session",
            }
        pool = matched

    if not pool:
        return {
            **base, "verdict": "unverified",
            "evidence": f"no {label} was recorded this session",
        }
    last = pool[-1]
    verdict = "verified" if last["status"] == "success" else "contradicted"
    return {**base, "verdict": verdict, "evidence": _evidence_for(last, label)}


def judge_claims(text: str, store, session_id: str) -> list[dict]:
    """Extract claims from text and judge each against the receipts."""
    return [judge_claim(c, store, session_id) for c in extract_claims(text)]


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

_ICONS = {"verified": "✓ verified", "contradicted": "✗ contradicted",
          "unverified": "? unverified"}


def format_stamp(verdicts: list[dict], nose_total: int,
                 show_unverified: bool = True) -> str:
    """The stamp appended to the agent's message by transform_llm_output."""
    shown = [
        v for v in verdicts
        if show_unverified or v["verdict"] != "unverified"
    ]
    if not shown:
        return ""
    lines = []
    for v in shown:
        lines.append(
            f"🤥 pinocchio · “{v['claim']}” → {_ICONS[v['verdict']]} "
            f"({v['evidence']})"
        )
    lies = sum(1 for v in verdicts if v["verdict"] == "contradicted")
    if lies:
        lines.append(f"🤥 the nose grows: ×{nose_total} all time")
    return "\n".join(lines)


def bounce_message(verdicts: list[dict]) -> str | None:
    """pre_verify steering text, or None when nothing is contradicted."""
    bad = [v for v in verdicts if v["verdict"] == "contradicted"]
    if not bad:
        return None
    lines = [
        "Pinocchio check: your closing message makes a claim that this "
        "session's receipts contradict:"
    ]
    for v in bad:
        lines.append(f"- “{v['claim']}” — {v['evidence']}.")
    lines.append(
        "Re-run the failing step and confirm it passes, or correct the "
        "claim, before you finish."
    )
    return "\n".join(lines)
