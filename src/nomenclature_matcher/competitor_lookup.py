from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_STOPWORDS = {
    "кран", "краны", "шаровой", "шаровый", "затвор", "дисковый", "поворотный",
    "клапан", "фланец", "фланцы", "фильтр", "задвижка", "ду", "dn", "dy", "du",
    "ру", "pn", "вр", "нр", "вн", "нар", "резьбовой", "резьбовая", "фланцевый",
    "фланцевое", "межфланцевый", "под", "сварку", "стальной", "стальная",
    "латунный", "нержавеющий", "нержавеющая",
}

_RELEVANT_PROPERTY_KEY = re.compile(
    r"(?:артик|код\s+производ|модел|серия|бренд|производител|"
    r"\bду\b|\bdn\b|условн.*проход|диаметр|\bру\b|\bpn\b|давлен|"
    r"присоедин|подключ|резьб|материал|уплотн|проход|исполн|"
    r"управл|привод|рабоч.*сред|тип\s+шаров)",
    re.IGNORECASE,
)

_INCH_TO_DN = {
    "1/2": 15,
    "3/4": 20,
    "1": 25,
    "1 1/4": 32,
    "1 1/2": 40,
    "2": 50,
    "2 1/2": 65,
    "3": 80,
    "4": 100,
}


@dataclass(frozen=True)
class LookupTerm:
    value: str
    kind: str
    weight: int


@dataclass
class CompetitorCandidate:
    product_id: str
    family: str | None
    name: str | None
    article: str | None
    vendor_article: str | None
    manufacturer_code: str | None
    brand: str | None
    model: str | None
    series: str | None
    dn_text: str | None
    pn_text: str | None
    joining_type: str | None
    thread_type: str | None
    connection_size: str | None
    body_material: str | None
    seal_material: str | None
    control: str | None
    working_medium: str | None
    url: str | None
    score: float
    confidence: float
    matched_terms: list[str] = field(default_factory=list)
    match_basis: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)

    def prompt_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("score", None)
        return payload


@dataclass
class CompetitorLookupResult:
    attempted: bool
    accepted: bool
    reason: str
    query: str
    identity_terms: list[str] = field(default_factory=list)
    candidates: list[CompetitorCandidate] = field(default_factory=list)

    def debug_payload(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "accepted": self.accepted,
            "reason": self.reason,
            "identity_terms": self.identity_terms,
            "candidates": [asdict(candidate) for candidate in self.candidates],
        }

    def prompt_context(self) -> dict[str, Any] | None:
        if not self.accepted or not self.candidates:
            return None
        top = self.candidates[0]
        return {
            "source": "local_santech_competitor_catalog",
            "identity_confidence": top.confidence,
            "candidate": top.prompt_payload(),
            "instruction": (
                "Use this record only as technical context for the source competitor product. "
                "Explicit facts in the original query have priority."
            ),
        }


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", _normalize(value))


def _strip_technical_notation(query: str) -> str:
    text = _normalize(query)
    text = re.sub(r"(?:ду|dn|dy|du|ру|pn)\s*[-:]?\s*\d+(?:[.,]\d+)?", " ", text)
    text = re.sub(r"\b[мm]\s*\d+\s*[xх]\s*\d+(?:[.,]\d+)?\b", " ", text)
    return " ".join(text.split())


def _identity_terms(query: str) -> list[LookupTerm]:
    cleaned = _strip_technical_notation(query)
    raw_tokens = re.findall(r"[a-zа-я0-9][a-zа-я0-9._/-]*", cleaned)
    terms: list[LookupTerm] = []

    def add(value: str, kind: str, weight: int) -> None:
        normalized = _normalize(value).strip("._/-")
        if not normalized or normalized in _STOPWORDS:
            return
        if any(term.value == normalized for term in terms):
            return
        terms.append(LookupTerm(normalized, kind, weight))

    modelish_found = False
    for token in raw_tokens:
        normalized = token.strip("._/-")
        if not normalized or normalized in _STOPWORDS:
            continue
        has_alpha = bool(re.search(r"[a-zа-я]", normalized))
        has_digit = bool(re.search(r"\d", normalized))
        has_separator = any(ch in normalized for ch in ".-/")
        compact = _compact(normalized)
        if has_alpha and has_digit and len(compact) >= 4:
            add(normalized, "model", 32)
            modelish_found = True
        elif has_digit and has_separator and len(compact) >= 4:
            add(normalized, "model", 28)
            modelish_found = True
        elif normalized.isdigit() and len(normalized) >= 5:
            add(normalized, "article", 38)
            modelish_found = True

    # Brand + pure numeric model, e.g. IVR 60 / IVR 956. The pair is atomic for identity:
    # a random article "956" from another brand must never be treated as IVR 956.
    for match in re.finditer(r"\b([a-z]{2,16})\s+([0-9]{2,4})\b", cleaned):
        brand, number = match.groups()
        if brand not in _STOPWORDS:
            add(brand, "brand", 20)
            add(number, "model_number", 26)
            modelish_found = True

    if modelish_found:
        for token in raw_tokens:
            normalized = token.strip("._/-")
            if (
                normalized not in _STOPWORDS
                and normalized.isalpha()
                and normalized.isascii()
                and 3 <= len(normalized) <= 20
            ):
                add(normalized, "brand", 14)

    return terms


def _explicit_dn_signals(query: str) -> set[int]:
    text = _normalize(query)
    values = {
        int(match.group(1))
        for match in re.finditer(r"(?:ду|dn|dy|du)\s*[-:]?\s*(\d{1,4})", text)
    }
    for match in re.finditer(
        r"(?<!\d)(2\s+1/2|1\s+1/2|1\s+1/4|1/2|3/4|1|2|3|4)\s*[\"″]",
        text,
    ):
        key = " ".join(match.group(1).split())
        if key in _INCH_TO_DN:
            values.add(_INCH_TO_DN[key])
    return values


def _explicit_inch_signals(query: str) -> list[str]:
    text = _normalize(query)
    return [
        " ".join(match.group(1).split())
        for match in re.finditer(
            r"(?<!\d)(2\s+1/2|1\s+1/2|1\s+1/4|1/2|3/4|1|2|3|4)\s*[\"″]",
            text,
        )
    ]


def _relevant_properties(raw: Any, limit: int = 28) -> dict[str, str]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        if not _RELEVANT_PROPERTY_KEY.search(str(key)) or isinstance(value, (dict, list)):
            continue
        text = " ".join(str(value or "").split())
        if text:
            result[str(key)] = text[:180]
        if len(result) >= limit:
            break
    return result


def _term_hits(term: LookupTerm, blob: str) -> bool:
    value = _normalize(term.value)
    if term.kind == "brand":
        return bool(re.search(rf"(?<![a-zа-я0-9]){re.escape(value)}(?![a-zа-я0-9])", blob))
    if term.kind == "model_number":
        return bool(re.search(rf"(?<!\d){re.escape(value)}(?!\d)", blob))
    compact_value = _compact(value)
    return value in blob or (compact_value and compact_value in _compact(blob))


def _logical_identity(candidate: CompetitorCandidate) -> str:
    # Prefer manufacturer-facing identifiers. This collapses the same product scraped
    # from several distributors while keeping genuinely different size variants apart.
    for value in (candidate.manufacturer_code, candidate.vendor_article, candidate.article):
        compact = _compact(value)
        if len(compact) >= 4 and re.search(r"[a-zа-я]", compact) and re.search(r"\d", compact):
            return f"id:{compact}"
    model = _compact(candidate.model)
    if model:
        return f"model:{model}:dn:{_compact(candidate.dn_text)}"
    return f"product:{candidate.product_id}"


def _candidate_completeness(candidate: CompetitorCandidate) -> int:
    fields = (
        candidate.vendor_article,
        candidate.manufacturer_code,
        candidate.brand,
        candidate.model,
        candidate.series,
        candidate.dn_text,
        candidate.pn_text,
        candidate.joining_type,
        candidate.thread_type,
        candidate.body_material,
        candidate.control,
        candidate.working_medium,
    )
    return sum(value not in (None, "", "Не указано") for value in fields) + len(candidate.properties)


class LocalCompetitorLookup:
    """Resolve a source competitor item from a local structured Parquet catalog.

    The lookup only supplies source-product facts. It never chooses an LD product and
    never manufactures hard constraints itself; DeepSeek remains the query interpreter.
    """

    def __init__(self, settings, path: str | Path | None = None):
        self.settings = settings
        configured = path or getattr(
            settings,
            "competitor_catalog_path",
            "data/competitors/santech_ld_scope.parquet",
        )
        configured_path = Path(configured)
        if configured_path.is_absolute():
            self.path = configured_path
        else:
            cwd_path = Path.cwd() / configured_path
            repo_path = Path(__file__).resolve().parents[2] / configured_path
            self.path = cwd_path if cwd_path.exists() else repo_path
        self.limit = max(1, int(getattr(settings, "competitor_lookup_limit", 3)))
        self.prelimit = max(self.limit, int(getattr(settings, "competitor_lookup_prelimit", 200)))
        self.min_confidence = float(getattr(settings, "competitor_lookup_min_confidence", 0.84))
        self.min_score_margin = float(getattr(settings, "competitor_lookup_min_score_margin", 8.0))
        self._con = None
        self._columns: set[str] = set()

    def _availability_error(self) -> str | None:
        if not self.path.exists():
            return f"catalog_missing:{self.path}"
        try:
            with self.path.open("rb") as fh:
                head = fh.read(96)
        except OSError as exc:
            return f"catalog_unreadable:{exc}"
        if head.startswith(b"version https://git-lfs.github.com/spec/v1"):
            return "catalog_is_lfs_pointer: run `git lfs pull`"
        return None

    def _ensure_connection(self):
        if self._con is not None:
            return self._con
        import duckdb

        con = duckdb.connect(database=":memory:")
        parquet_path = str(self.path.resolve()).replace("'", "''")
        con.execute(f"CREATE VIEW competitor_catalog_raw AS SELECT * FROM read_parquet('{parquet_path}')")
        self._columns = {
            row[0] for row in con.execute("DESCRIBE SELECT * FROM competitor_catalog_raw").fetchall()
        }
        searchable = [
            name for name in (
                "name", "article", "vendor_article", "manufacturer_code", "brand", "model",
                "series", "product_name", "product_type", "product_variant", "dn_text", "pn_text",
                "joining_type", "thread_type", "connection_size", "properties_json",
            ) if name in self._columns
        ]
        concat = ", ".join(f"coalesce({name}, '')" for name in searchable)
        con.execute(
            "CREATE VIEW competitor_catalog AS "
            f"SELECT *, lower(concat_ws(' ', {concat})) AS _search_blob "
            "FROM competitor_catalog_raw"
        )
        self._con = con
        return con

    @staticmethod
    def should_lookup(query: str) -> bool:
        return bool(_identity_terms(query))

    def _row_dicts(self, terms: list[LookupTerm]) -> list[dict[str, Any]]:
        con = self._ensure_connection()
        select_columns = [
            name for name in (
                "product_id", "family", "name", "article", "vendor_article", "manufacturer_code",
                "brand", "model", "series", "dn_text", "pn_text", "joining_type", "thread_type",
                "connection_size", "body_material", "seal_material", "control", "working_medium",
                "url", "properties_json",
            ) if name in self._columns
        ]
        if not terms:
            return []
        score_parts = [f"CASE WHEN _search_blob LIKE ? THEN {term.weight} ELSE 0 END" for term in terms]
        where_parts = ["_search_blob LIKE ?" for _ in terms]
        params = [f"%{_normalize(term.value)}%" for term in terms]
        sql = (
            f"SELECT {', '.join(select_columns)}, ({' + '.join(score_parts)}) AS _pre_score, _search_blob "
            "FROM competitor_catalog "
            f"WHERE {' OR '.join(where_parts)} "
            "ORDER BY _pre_score DESC "
            f"LIMIT {self.prelimit}"
        )
        cursor = con.execute(sql, params + params)
        names = [description[0] for description in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def _score_candidate(
        self,
        row: dict[str, Any],
        terms: list[LookupTerm],
        dn_signals: set[int],
        inch_signals: list[str],
    ) -> CompetitorCandidate:
        blob = _normalize(row.get("_search_blob"))
        strong_identifiers = {
            key: _compact(row.get(key))
            for key in ("article", "vendor_article", "manufacturer_code")
            if row.get(key)
        }
        matched_terms = [term.value for term in terms if _term_hits(term, blob)]
        basis: list[str] = []
        raw_score = sum(term.weight for term in terms if term.value in matched_terms)
        exact_identifier = False

        for term in terms:
            compact = _compact(term.value)
            if compact and term.kind in {"article", "model"} and any(
                compact == value for value in strong_identifiers.values()
            ):
                exact_identifier = True
                raw_score += 120.0
                basis.append(f"exact_identifier:{term.value}")

        if len(matched_terms) == len(terms):
            raw_score += 24.0
            basis.append("all_identity_terms")

        candidate_dn_numbers = {
            int(value)
            for value in re.findall(r"\d{1,4}", str(row.get("dn_text") or ""))
            if int(value) <= 2000
        }
        dn_hit = bool(dn_signals and candidate_dn_numbers.intersection(dn_signals))
        if dn_hit:
            raw_score += 36.0
            basis.append("explicit_dn")

        size_blob = _normalize(
            " ".join(str(row.get(key) or "") for key in ("connection_size", "name", "properties_json"))
        ).replace("'", '"')
        inch_hit = False
        for inch in inch_signals:
            compact_inch = inch.replace(" ", "")
            if re.search(rf"(?<!\d){re.escape(compact_inch)}\s*[\"″]", size_blob.replace(" ", "")):
                inch_hit = True
                raw_score += 22.0
                basis.append(f"explicit_inch:{inch}")
                break

        # Identity is only high-confidence if every identity token is present. This is
        # critical for brand+numeric models (IVR 956 must contain both IVR and 956).
        all_identity = len(matched_terms) == len(terms)
        if exact_identifier and all_identity:
            confidence = 0.99
        elif all_identity and (dn_hit or inch_hit):
            confidence = 0.94
        elif all_identity and len(terms) >= 2:
            confidence = 0.89
        elif all_identity:
            confidence = 0.84
        else:
            confidence = 0.55

        return CompetitorCandidate(
            product_id=str(row.get("product_id") or ""),
            family=row.get("family"),
            name=row.get("name"),
            article=row.get("article"),
            vendor_article=row.get("vendor_article"),
            manufacturer_code=row.get("manufacturer_code"),
            brand=row.get("brand"),
            model=row.get("model"),
            series=row.get("series"),
            dn_text=row.get("dn_text"),
            pn_text=row.get("pn_text"),
            joining_type=row.get("joining_type"),
            thread_type=row.get("thread_type"),
            connection_size=row.get("connection_size"),
            body_material=row.get("body_material"),
            seal_material=row.get("seal_material"),
            control=row.get("control"),
            working_medium=row.get("working_medium"),
            url=row.get("url"),
            score=round(raw_score, 3),
            confidence=confidence,
            matched_terms=matched_terms,
            match_basis=basis,
            properties=_relevant_properties(row.get("properties_json")),
        )

    @staticmethod
    def _deduplicate(candidates: list[CompetitorCandidate]) -> list[CompetitorCandidate]:
        best: dict[str, CompetitorCandidate] = {}
        for candidate in candidates:
            key = _logical_identity(candidate)
            current = best.get(key)
            if current is None or (candidate.score, _candidate_completeness(candidate)) > (
                current.score,
                _candidate_completeness(current),
            ):
                best[key] = candidate
        return list(best.values())

    def lookup(self, query: str) -> CompetitorLookupResult:
        terms = _identity_terms(query)
        if not terms:
            return CompetitorLookupResult(False, False, "no_external_identity_signal", query)

        availability_error = self._availability_error()
        if availability_error:
            return CompetitorLookupResult(
                True, False, availability_error, query, [term.value for term in terms]
            )

        rows = self._row_dicts(terms)
        if not rows:
            return CompetitorLookupResult(
                True, False, "no_local_catalog_candidate", query, [term.value for term in terms]
            )

        dn_signals = _explicit_dn_signals(query)
        inch_signals = _explicit_inch_signals(query)
        scored = [self._score_candidate(row, terms, dn_signals, inch_signals) for row in rows]

        # If the query carries multiple identity tokens, partial matches are noise.
        # This removes false positives such as a non-IVR product whose article is merely "956".
        if len(terms) >= 2:
            scored = [candidate for candidate in scored if len(candidate.matched_terms) == len(terms)]
        if not scored:
            return CompetitorLookupResult(
                True,
                False,
                "no_candidate_matches_all_identity_terms",
                query,
                [term.value for term in terms],
            )

        candidates = self._deduplicate(scored)
        candidates.sort(
            key=lambda candidate: (
                -candidate.score,
                -candidate.confidence,
                -_candidate_completeness(candidate),
                candidate.product_id,
            )
        )
        candidates = candidates[: self.limit]
        top = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        margin = top.score - second.score if second is not None else top.score
        exact = any(item.startswith("exact_identifier:") for item in top.match_basis)
        accepted = top.confidence >= self.min_confidence and (
            exact or second is None or margin >= self.min_score_margin
        )
        reason = (
            f"accepted_local_identity confidence={top.confidence:.2f} margin={margin:.1f}"
            if accepted
            else f"ambiguous_local_identity confidence={top.confidence:.2f} margin={margin:.1f}"
        )
        return CompetitorLookupResult(
            True,
            accepted,
            reason,
            query,
            [term.value for term in terms],
            candidates,
        )
