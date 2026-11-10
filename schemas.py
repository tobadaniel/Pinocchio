"""Tool schemas for Pinocchio (Hermes register_tool format)."""

PINOCCHIO_REPORT = {
    "name": "pinocchio_report",
    "description": (
        "Show the Pinocchio truth table for a session: every closing claim "
        "the agent made, the verdict it received (verified, unverified, or "
        "contradicted), the receipt evidence behind each verdict, and the "
        "all-time nose count of contradicted claims. Omit session_id to "
        "report on the most recent session."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session to report on. Defaults to the most recent session with activity.",
            },
        },
        "required": [],
    },
}

PINOCCHIO_RECEIPTS = {
    "name": "pinocchio_receipts",
    "description": (
        "List the receipts Pinocchio recorded for a session: the tool calls "
        "that count as work evidence (test runs, builds, file writes, git "
        "operations, deploys, other commands) with their outcomes. Use this "
        "to see what evidence exists before making a claim about the work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session to list receipts for. Defaults to the most recent session with activity.",
            },
            "limit": {
                "type": "integer",
                "description": "How many of the most recent receipts to return (default 30).",
            },
        },
        "required": [],
    },
}
