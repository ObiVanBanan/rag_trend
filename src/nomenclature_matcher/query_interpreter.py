from __future__ import annotations

import json
import re
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

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

    @staticmethod
    def _explicit_thread_type(query: str) -> str | None:
        text = query.lower().replace("ё", "е")
        female = r"(?:вр|вн\.?|внутр\.?|внутренняя)"
        male = r"(?:нр|нар\.?|наруж\.?|наружная)"
        sep = r"\s*[/\-–—]\s*"
        if re.search(rf"{female}{sep}{female}", text):
            return "female_female"
        if re.search(rf"{male}{sep}{male}", text):
            return "male_male"
        if re.search(rf"(?:{female}{sep}{male}|{male}{sep}{female})", text):
            return "male_female"
        return None

    @staticmethod
    def _explicit_control(query: str) -> str | None:
        text = query.lower().replace("ё", "е")
        if "под электропривод" in text:
            return "electric_ready"
        if "электропривод" in text:
            return "electric"
        if "пневмопривод" in text or "пневматическ" in text:
            return "pneumatic"
        if "редуктор" in text:
            return "gearbox"
        if any(token in text for token in ("ручн", "рукоят", "рычаг", "бабочк", "маховик")):
            return "manual"
        return None

    def _sanitize_payload(self, query: str, payload: dict) -> dict:
        constraints = payload.get("constraints")
        if not isinstance(constraints, dict):
            return payload

        # DeepSeek occasionally returns null for an unknown product family even though
        # QueryConstraints historically required a string. Keep the runtime robust:
        # `other` means "no known supported family" and is harmless for rejected queries.
        if constraints.get("product_type") is None:
            constraints["product_type"] = "other"

        # valve_type describes variants of ball valves only in our candidate normalizer.
        # Applying `standard` to filters, butterfly valves, flanges, etc. makes every
        # otherwise-valid candidate fail the deterministic hard filter.
        if constraints.get("product_type") != "ball_valve":
            constraints["valve_type"] = None

        # Control is a hard constraint only when it is explicit in the source query.
        # Do not let the LLM silently turn an ordinary product into `manual`.
        constraints["control"] = self._explicit_control(query)

        # Explicit ВР/НР notation is deterministic and should override an occasional
        # LLM orientation slip such as "вн.-вн." -> male_male.
        explicit_thread = self._explicit_thread_type(query)
        if explicit_thread is not None:
            constraints["thread_type"] = explicit_thread

        return payload

    def _request(self, query: str, *, retry: bool = False) -> str:
        suffix = ""
        if retry:
            suffix = (
                "\n\nПредыдущий ответ не прошёл JSON/schema validation. "
                "Верни заново только один строго валидный JSON-объект по указанной схеме."
            )
        response = self.client.chat.completions.create(
            model=self.settings.deepseek_model,
            temperature=0,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"QUERY:\n{query}{suffix}"},
            ],
        )
        return response.choices[0].message.content or "{}"

    def interpret(self, query: str) -> QueryInterpretation:
        last_error: Exception | None = None
        for attempt in range(2):
            content = self._request(query, retry=attempt > 0)
            try:
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise TypeError("Query interpreter response must be a JSON object")
                payload = self._sanitize_payload(query, payload)
                result = QueryInterpretation.model_validate(payload)
                if result.searchable and not result.normalized_query:
                    result.normalized_query = " ".join(query.split())
                return result
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                last_error = exc

        assert last_error is not None
        raise last_error
