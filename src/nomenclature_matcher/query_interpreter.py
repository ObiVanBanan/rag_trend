from __future__ import annotations

import json
import re
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .query_constraints import QueryConstraints
from .query_signals import explicit_dn_from_query, has_product_identity


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

    @staticmethod
    def _looks_like_service_query(query: str) -> bool:
        text = " ".join(query.lower().replace("ё", "е").split())
        return bool(
            re.match(
                r"^(?:монтаж|установка|снятие|демонтаж|замена|смена|ремонт|"
                r"обследование|устройство|техническое обслуживание|то\b)",
                text,
            )
        )

    @staticmethod
    def _has_model_token(query: str) -> bool:
        # Kept as a compatibility shim for callers/tests; identity detection is shared
        # with the competitor lookup gate and includes long numeric articles.
        return has_product_identity(query)

    @staticmethod
    def _specificity_score(constraints: dict) -> int:
        values = [
            constraints.get("dn"),
            constraints.get("pn_min_mpa"),
            constraints.get("joining_type"),
            constraints.get("thread_type"),
            constraints.get("working_medium"),
            constraints.get("valve_designation"),
            constraints.get("body_material"),
            constraints.get("body_material_grade"),
            constraints.get("bore_type"),
            constraints.get("control"),
        ]
        score = sum(value not in (None, "") for value in values)
        valve_type = constraints.get("valve_type")
        if valve_type not in (None, "", "standard"):
            score += 1
        return score

    @classmethod
    def _has_specific_anchor(cls, query: str, constraints: dict) -> bool:
        return bool(
            constraints.get("dn") is not None
            or constraints.get("valve_designation")
            or has_product_identity(query)
        )

    def _sanitize_payload(self, query: str, payload: dict) -> dict:
        constraints = payload.get("constraints")
        if not isinstance(constraints, dict):
            return payload

        if constraints.get("product_type") is None:
            constraints["product_type"] = "other"

        if constraints.get("product_type") != "ball_valve":
            constraints["valve_type"] = None

        # Control is hard only when it is explicit in the tender query.
        constraints["control"] = self._explicit_control(query)

        # Explicit thread orientation is deterministic and overrides LLM slips.
        explicit_thread = self._explicit_thread_type(query)
        if explicit_thread is not None:
            constraints["thread_type"] = explicit_thread

        # Explicit DN/inch notation is deterministic and must override model/web guesses.
        explicit_dn = explicit_dn_from_query(query)
        if explicit_dn is not None:
            constraints["dn"] = explicit_dn

        service_query = self._looks_like_service_query(query)
        score = self._specificity_score(constraints)
        identity_anchor = has_product_identity(query)
        anchored = self._has_specific_anchor(query, constraints)
        scope = constraints.get("catalog_scope")
        ambiguous = bool(constraints.get("ambiguous"))
        sufficiently_specific = identity_anchor or (anchored and score >= 2)

        if service_query:
            payload["searchable"] = False
            payload["reason"] = "Строка описывает работу/услугу над оборудованием, а не конкретный товар для подбора."
        elif scope == "out_of_scope" or ambiguous:
            payload["searchable"] = False
        elif payload.get("searchable") and not sufficiently_specific:
            payload["searchable"] = False
            payload["reason"] = (
                "Недостаточно различающих характеристик для выбора конкретного LD-аналога: "
                "нужны конкретный размер/обозначение и ещё один технический признак либо точная модель/артикул."
            )
        elif (
            not payload.get("searchable")
            and scope == "in_scope"
            and sufficiently_specific
        ):
            payload["searchable"] = True
            payload["reason"] = (
                "Извлечено достаточно технических признаков для подбора LD-аналога; "
                "поиск разрешён deterministic eligibility gate."
            )

        return payload

    @staticmethod
    def _user_message(query: str, competitor_context: dict | None, suffix: str = "") -> str:
        text = f"QUERY:\n{query}"
        if competitor_context:
            text += (
                "\n\nCOMPETITOR_CONTEXT:\n"
                + json.dumps(competitor_context, ensure_ascii=False, indent=2)
            )
        return text + suffix

    def _request(
        self,
        query: str,
        *,
        competitor_context: dict | None = None,
        retry: bool = False,
    ) -> str:
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
                {
                    "role": "user",
                    "content": self._user_message(query, competitor_context, suffix),
                },
            ],
        )
        return response.choices[0].message.content or "{}"

    def interpret(
        self,
        query: str,
        competitor_context: dict | None = None,
    ) -> QueryInterpretation:
        last_error: Exception | None = None
        for attempt in range(2):
            content = self._request(
                query,
                competitor_context=competitor_context,
                retry=attempt > 0,
            )
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
