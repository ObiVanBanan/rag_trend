from __future__ import annotations


RESEARCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question", "findings", "recommended_direction"],
    "properties": {
        "question": {"type": "string"},
        "findings": {"type": "string"},
        "recommended_direction": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
}

PLANNER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "experiment_name", "hypothesis", "expected_effect", "implementation_steps", "evaluation_focus"],
    "properties": {
        "action": {"type": "string", "enum": ["IMPLEMENT", "DONE"]},
        "experiment_name": {"type": "string"},
        "hypothesis": {"type": "string"},
        "expected_effect": {"type": "string"},
        "implementation_steps": {"type": "array", "items": {"type": "string"}},
        "evaluation_focus": {"type": "string"},
        "files": {"type": "array", "items": {"type": "string"}},
    },
}

WORKER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "changed_files"],
    "properties": {
        "status": {"type": "string", "enum": ["complete", "blocked"]},
        "summary": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "blocker": {"type": "string"},
    },
}

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "summary", "lesson", "next_direction"],
    "properties": {
        "decision": {"type": "string", "enum": ["ACCEPT", "REJECT"]},
        "summary": {"type": "string"},
        "lesson": {"type": "string"},
        "next_direction": {"type": "string"},
    },
}
