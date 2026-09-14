from __future__ import annotations

import json
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, field_validator

from .query_constraints import QueryConstraints


DEFAULT_QUERY_INTERPRETER_PROMPT = (
    Path(__file__).resolve().parent / "prompts" / "query_interpreter_system.md"
)


class QueryInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    searchable: bool
    normalized_query: str
    reason: str = ""
    constraints: QueryConstraints

    @field_validator("normalized_query")
    @classmethod
    def _normalize_query(cls, value: str) -> str:
        return " ".join(str(value or "").split())


class DeepSeekQueryInterpreter:
    def __init__(self, settings, client=None, prompt_path: str | Path | None = None):
        self.settings = settings
        configured = getattr(settings, "query_interpreter_system_prompt_path", None)
        self.prompt_path = Path(prompt_path or configured or DEFAULT_QUERY_INTERPRETER_PROMPT)
        if not self.prompt_path.is_absolute():
            cwd_path = Path.cwd() / self.prompt_path
            package_path = Path(__file__).resolve().parent / self.prompt_path
            if cwd_path.exists():
                self.prompt_path = cwd_path
            elif package_path.exists():
                self.prompt_path = package_path
            else:
                self.prompt_path = DEFAULT_QUERY_INTERPRETER_PROMPT
        self.system_prompt = self.prompt_path.read_text(encoding="utf-8").strip()
        self.client = client or OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.deepseek_timeout_seconds,
        )

    def interpret(self, query: str) -> QueryInterpretation:
        response = self.client.chat.completions.create(
            model=self.settings.deepseek_model,
            temperature=0,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"QUERY:\n{query}"},
            ],
        )
        content = response.choices[0].message.content or "{}"
        result = QueryInterpretation.model_validate(json.loads(content))
        if result.searchable and not result.normalized_query:
            result.normalized_query = " ".join(query.split())
        return result
