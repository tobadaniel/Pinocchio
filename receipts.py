"""Receipts: the ground-truth record of what the agent actually did.

Pinocchio watches every tool call in a session (via the post_tool_call
hook) and stores the ones that constitute *work evidence*: commands and
their exit codes, test runs, builds, file writes, git operations,
deploys. Read-only lookups are ignored; a receipt exists only where the
agent acted on the world and the world answered.

Pure standard library, SQLite storage, thread-safe.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time

# ---------------------------------------------------------------------------
# Tool-call classification
# ---------------------------------------------------------------------------

# Tools that only look at the world. Their results are not work evidence.
_READ_ONLY_HINTS = (
    "read", "get_", "list", "search", "fetch", "view", "find", "grep",
    "glob", "describe", "show", "lookup", "inspect", "status", "check_",
)

_FILE_WRITE_TOOLS = {
    "write_file", "create_file", "edit_file", "patch_file", "apply_patch",
    "replace_in_file", "insert_line", "delete_file", "move_file",
    "rename_file", "copy_file", "make_directory", "mkdir",
}

_TEST_RE = re.compile(
    r"(pytest|python\s+-m\s+(pytest|unittest)|npm\s+(run\s+)?test|npx\s+(jest|vitest|mocha)|"
    r"yarn\s+test|pnpm\s+test|go\s+test|cargo\s+test|rspec|phpunit|"
    r"mvn\s+.*\btest\b|gradle\s+.*\btest\b|\btox\b|make\s+test|jest|vitest)",
    re.IGNORECASE,
)
_BUILD_RE = re.compile(
    r"(npm\s+run\s+build|yarn\s+build|pnpm\s+build|cargo\s+build|go\s+build|"
    r"\btsc\b|make\s+(build|all)?\s*$|make\b|mvn\s+.*\b(package|compile|install)\b|"
    r"gradle\s+.*\bbuild\b|python\s+-m\s+build|pyinstaller|webpack|vite\s+build|"
    r"next\s+build|nuxt\s+build|hugo\b|jekyll\s+build)",
    re.IGNORECASE,
)
_GIT_RE = re.compile(r"\bgit\s+(commit|push|merge|rebase|tag|cherry-pick)\b", re.IGNORECASE)
_DEPLOY_RE = re.compile(
    r"(deploy|netlify\s+deploy|vercel\s*(--prod|deploy)?|kubectl\s+apply|"
    r"docker\s+push|gh\s+release|rsync\s+.*:|scp\s+.*:|fly\s+deploy|"
    r"railway\s+up|render\s+deploy|publish)",
    re.IGNORECASE,
)
_LINT_RE = re.compile(
    r"(eslint|flake8|ruff|mypy|pylint|prettier\s+.*--check|golangci-lint|"
    r"cargo\s+clippy|shellcheck|stylelint)",
    re.IGNORECASE,
)


def _parse_result(result) -> tuple[str, int | None, str]:
    """Return (status, exit_code, detail) from a raw tool result.

    Status is "success" or "failure". Hermes tool results arrive as JSON
    strings (terminal: {"output": ..., "exit_code": N}), as dicts, or as
    plain text. Anything ambiguous counts as success only when nothing in
    it signals an error; an explicit error always wins.
    """
    data = result
    if isinstance(result, str):
        text = result.strip()
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None
        if not isinstance(data, (dict, list)):
            low = text.lower()
            if low.startswith(("error", "traceback", "fatal:", "failed")):
                return "failure", None, text[:200]
            return "success", None, text[:200]
    if isinstance(data, dict):
        exit_code = data.get("exit_code")
        if exit_code is None:
            exit_code = data.get("returncode")
        if isinstance(exit_code, (int, float)):
            code = int(exit_code)
            detail = str(data.get("output") or data.get("message") or "")[:200]
            return ("success" if code == 0 else "failure"), code, detail
        err = data.get("error")
        if err:
            return "failure", None, str(err)[:200]
        if data.get("success") is False or data.get("ok") is False:
            return "failure", None, str(data.get("message") or "")[:200]
        detail = str(data.get("output") or data.get("message") or "")[:200]
        return "success", None, detail
    return "success", None, str(data)[:200]


def classify_receipt(tool_name: str, args, result) -> dict | None:
    """Turn one tool call into a receipt dict, or None if it is not evidence.

    Receipt kinds: test, build, lint, git, deploy, file, command.
    """
    name = (tool_name or "").strip()
    if not name:
        return None
    low = name.lower()
    args = args if isinstance(args, dict) else {}
    status, exit_code, detail = _parse_result(result)

    if low in _FILE_WRITE_TOOLS or low.endswith(("_write", "_edit", "_patch")):
        target = (
            args.get("path") or args.get("file") or args.get("filename")
            or args.get("target") or ""
        )
        return {
            "tool_name": name, "kind": "file", "target": str(target),
            "status": status, "exit_code": exit_code,
            "detail": detail or f"{name} {target}".strip(),
        }

    if low == "terminal" or "terminal" in low or "shell" in low or "bash" in low:
        command = str(args.get("command") or args.get("cmd") or "")
        if not command:
            return None
        kind = "command"
        if _TEST_RE.search(command):
            kind = "test"
        elif _BUILD_RE.search(command):
            kind = "build"
        elif _GIT_RE.search(command):
            kind = "git"
        elif _DEPLOY_RE.search(command):
            kind = "deploy"
        elif _LINT_RE.search(command):
            kind = "lint"
        return {
            "tool_name": name, "kind": kind, "target": command[:160],
            "status": status, "exit_code": exit_code,
            "detail": detail or command[:200],
        }

    if any(hint in low for hint in _READ_ONLY_HINTS):
        return None

    # Any other acting tool: record it as generic work evidence.
    return {
        "tool_name": name, "kind": "command", "target": name,
        "status": status, "exit_code": exit_code, "detail": detail,
    }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ReceiptStore:
    """Thread-safe SQLite store for receipts and verdicts."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()

    @classmethod
    def from_path(cls, path) -> "ReceiptStore":
        from pathlib import Path

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        return cls(conn)

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    ts REAL NOT NULL,
                    tool_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    detail TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_receipts_session "
                "ON receipts(session_id, ts)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS verdicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    ts REAL NOT NULL,
                    claim TEXT NOT NULL,
                    category TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_verdicts_session "
                "ON verdicts(session_id, ts)"
            )
            self._conn.commit()

    # -- receipts -----------------------------------------------------------

    def add_receipt(self, session_id: str, receipt: dict, ts: float | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO receipts (session_id, ts, tool_name, kind, target,"
                " status, exit_code, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id or "default",
                    ts if ts is not None else time.time(),
                    receipt["tool_name"], receipt["kind"], receipt.get("target", ""),
                    receipt["status"], receipt.get("exit_code"),
                    receipt.get("detail", ""),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def receipts(self, session_id: str, kinds=None, limit: int = 100) -> list[dict]:
        query = "SELECT * FROM receipts WHERE session_id = ?"
        params: list = [session_id or "default"]
        if kinds:
            query += f" AND kind IN ({', '.join('?' for _ in kinds)})"
            params.extend(kinds)
        query += " ORDER BY ts ASC, id ASC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def last_receipt(self, session_id: str, kinds) -> dict | None:
        rows = self.receipts(session_id, kinds=kinds, limit=100000)
        return rows[-1] if rows else None

    def latest_session(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM receipts ORDER BY ts DESC, id DESC LIMIT 1"
            ).fetchone()
            if row:
                return row["session_id"]
            row = self._conn.execute(
                "SELECT session_id FROM verdicts ORDER BY ts DESC, id DESC LIMIT 1"
            ).fetchone()
        return row["session_id"] if row else None

    # -- verdicts -----------------------------------------------------------

    def add_verdict(self, session_id: str, verdict: dict, ts: float | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO verdicts (session_id, ts, claim, category, verdict,"
                " evidence) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id or "default",
                    ts if ts is not None else time.time(),
                    verdict["claim"], verdict["category"], verdict["verdict"],
                    verdict.get("evidence", ""),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def verdicts(self, session_id: str, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM verdicts WHERE session_id = ?"
                " ORDER BY ts ASC, id ASC LIMIT ?",
                (session_id or "default", int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def nose_count(self) -> int:
        """Total contradicted claims, all sessions. The nose only grows."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM verdicts WHERE verdict = 'contradicted'"
            ).fetchone()
        return int(row["n"]) if row else 0

    def session_summary(self, session_id: str) -> dict:
        recs = self.receipts(session_id, limit=100000)
        vers = self.verdicts(session_id, limit=100000)
        by_kind: dict[str, int] = {}
        failures = 0
        for r in recs:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
            if r["status"] == "failure":
                failures += 1
        by_verdict: dict[str, int] = {}
        for v in vers:
            by_verdict[v["verdict"]] = by_verdict.get(v["verdict"], 0) + 1
        return {
            "session_id": session_id or "default",
            "receipts": len(recs),
            "receipts_by_kind": by_kind,
            "failed_actions": failures,
            "claims_checked": len(vers),
            "verdicts_by_kind": by_verdict,
            "nose_total": self.nose_count(),
        }
