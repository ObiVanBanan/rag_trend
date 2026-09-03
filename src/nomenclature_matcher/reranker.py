import json

from openai import OpenAI

from .models import RerankedCandidate, RerankResult, SearchCandidate

RERANKER_SYSTEM_PROMPT = """Ты выполняешь техническое сопоставление тендерной
номенклатуры с товарами каталога.

Тебе дан исходный запрос и список товаров-кандидатов,
уже найденных поисковой системой.

Твоя задача — выбрать наиболее подходящий товар
или несколько товаров.

Правила:

1. Не придумывай характеристики, которых нет в запросе
или описании товара.

2. Сначала учитывай тип изделия.

3. Учитывай DN, PN, тип присоединения, материал,
исполнение, назначение и другие характеристики,
если они указаны.

4. Дополнительная специализация товара не является
преимуществом, если пользователь её не запросил.

Например:
- подземное исполнение;
- ПЭ патрубки;
- продувочные свечи;
- специальный привод;
- специальное климатическое исполнение;
- нестандартное назначение.

Если запрос обычный, предпочитай обычное исполнение
специализированному при прочих равных.

5. Не выбирай товар только потому, что у него высокий
vector similarity score.

6. Vector score — только вспомогательный сигнал.

7. Если ни один кандидат достаточно не соответствует
запросу, верни NOT_FOUND.

8. Не выбирай кандидатов, которых нет во входном списке.

Ответь только JSON без markdown.
"""


class DeepSeekReranker:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.deepseek_timeout_seconds,
        )

    def rerank(self, query: str, candidates: list[SearchCandidate]) -> RerankResult:
        response = self.client.chat.completions.create(
            model=self.settings.deepseek_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": RERANKER_SYSTEM_PROMPT},
                {"role": "user", "content": self._build_prompt(query, candidates)},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return self._parse_result(content, len(candidates))

    def _build_prompt(self, query: str, candidates: list[SearchCandidate]) -> str:
        blocks = [f"QUERY:\n{query}", "", "CANDIDATES:"]
        for index, candidate in enumerate(candidates, 1):
            blocks.extend(
                [
                    "",
                    f"CANDIDATE {index}",
                    f"candidate_id: {index}",
                    f"ld_id: {candidate.ld_id}",
                    f"article: {candidate.article or ''}",
                    f"name: {candidate.name}",
                    f"vector_score: {candidate.score:.4f}",
                    "",
                    "Характеристики:",
                    candidate.search_text or candidate.name,
                ]
            )
        blocks.extend(
            [
                "",
                "Верни JSON формата:",
                '{"status":"MATCHED","selected":[{"candidate_id":1,"confidence":0.95,"reason":"..."}],"reason":"..."}',
                'или {"status":"NOT_FOUND","selected":[],"reason":"..."}',
                f"Выбери не более {self.settings.rerank_result_limit} кандидатов.",
            ]
        )
        return "\n".join(blocks)

    def _parse_result(self, content: str, candidate_count: int) -> RerankResult:
        payload = json.loads(content)
        status = payload.get("status")
        if status not in {"MATCHED", "NOT_FOUND"}:
            raise ValueError(f"Unsupported rerank status: {status!r}")
        selected = []
        seen = set()
        for item in payload.get("selected", []):
            candidate_id = item.get("candidate_id")
            if not isinstance(candidate_id, int) or not (1 <= candidate_id <= candidate_count):
                continue
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            confidence = item.get("confidence")
            if confidence is not None:
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError):
                    confidence = None
            reason = str(item.get("reason") or "")
            selected.append(RerankedCandidate(candidate_id=candidate_id, confidence=confidence, reason=reason))
            if len(selected) >= self.settings.rerank_result_limit:
                break
        reason = payload.get("reason")
        if selected:
            return RerankResult(status="MATCHED", selected=selected, reason=reason)
        return RerankResult(status="NOT_FOUND", selected=[], reason=reason)
