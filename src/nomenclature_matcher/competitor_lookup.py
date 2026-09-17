from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_STOPWORDS = {
    "кран",
    "краны",
    "шаровой",
    "шаровый",
    "затвор",
    "дисковый",
    "поворотный",
    "клапан",
    "фланец",
    "фланцы",
    "фильтр",
    "задвижка",
    "ду",
    "dn",
    "dy",
    "du",
    "ру",
    "pn",
    "вр",
    "нр",
    "вн",
    "нар",
    "резьбовой",
    "резьбовая",
    "фланцевый",
    "фланцевое",
    "межфланцевый",
    "под",
    "сварку",
    "стальной",
    "стальная",
    "латунный",
    "нержавеющий",
    "нержавеющая",
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
            add(normalized, "model", 30)
            modelish_found = True
        elif has_digit and has_separator and len(compact) >= 4:
            add(normalized, "model", 26)
            modelish_found = True
        elif normalized.isdigit() and len(normalized) >= 5:
            add(normalized, "article", 34)
            modelish_found = True

    # Common catalog notation is a pure brand/series token followed by a numeric model,
    # e.g. "IVR 60" or "IVR 956". Keep the pair together; the number alone is too broad.
    for match in re.finditer(r"\b([a-z]{2,16})\s+([0-9]{2,4})\b", cleaned):
        brand, number = match.groups()
        if brand not in _STOPWORDS:
            add(brand, "brand", 16)
            add(number, "model_number", 22)
            modelish_found = True

    if modelish_found:
        # A nearby Latin brand is useful for disambiguating families such as VALTEC VT.214.
        for token in raw_tokens:
            normalized = token.strip("._/-")
            if (
                normalized not in _STOPWORDS
                and normalized.isalpha()
                and normalized.isascii()
                and 3 <= len(normalized) <= 20
            ):
                add(normalized, "brand", 12)

    return terms


def _explicit_dn_signals(query: str) -> set[int]:
    text = _normalize(query)
    values = {
        int(match.group(1))
        for match in re.finditer(r"(?:ду|dn|dy|du)\s*[-:]?\s*(\d{1,4})", text)
    }
    inch_pattern = re.compile(
        r"(?<!\d)(2\s+1/2|1\s+1/2|1\s+1/4|1/2|3/4|1|2|3|4)\s*[\"″]"
    )
    for match in inch_pattern.finditer(text):
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
        if not _RELEVANT_PROPERTY_KEY.search(str(key)):
            continue
        if isinstance(value, (dict, list)):
            continue
        text = " ".join(str(value or "").split())
        if not text:
            continue
        result[str(key)] = text[:180]
        if len(result) >= limit:
            break
    return result


class LocalCompetitorLookup:
    """Deterministic lookup over the compact local competitor Parquet.

    The lookup does not infer LD constraints itself. It only resolves a likely source
    competitor product and passes its catalog facts to the query interpreter.
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
            name
            for name in (
                "name",
                "article",
                "vendor_article",
                "manufacturer_code",
                "brand",
                "model",
                "series",
                "product_name",
                "product_type",
                "product_variant",
                "dn_text",
                "pn_text",
                "joining_type",
                "thread_type",
                "connection_size",
                "properties_json",
            )
            if name in self._columns
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

    def _row_dicts(self, query: str, terms: list[LookupTerm]) -> list[dict[str, Any]]:
        con = self._ensure_connection()
        select_columns = [
            name
            for name in (
                "product_id",
                "family",
                "name",
                "article",
                "vendor_article",
                "manufacturer_code",
                "brand",
                "model",
                "series",
                "dn_text",
                "pn_text",
                "joining_type",
                "thread_type",
                "connection_size",
                "body_material",
                "seal_material",
                "control",
                "working_medium",
                "url",
                "properties_json",
            )
            if name in self._columns
        ]
        score_parts: list[str] = []
        params: list[Any] = []
        where_parts: list[str] = []
        for term in terms:
            pattern = f"%{_normalize(term.value)}%"
            score_parts.append(f"CASE WHEN _search_blob LIKE ? THEN {term.weight} ELSE 0 END")
            params.append(pattern)
            where_parts.append("_search_blob LIKE ?")
            params.append(pattern)
        if not where_parts:
            return []
        # Parameters are interleaved above, but SQL expects score params first and WHERE params second.
        score_params = [f"%{_normalize(term.value)}%" for term in terms]
        where_params = [f"%{_normalize(term.value)}%" for term in terms]
        sql = (
            f"SELECT {', '.join(select_columns)}, "
            f"({' + '.join(score_parts)}) AS _pre_score, _search_blob "
            "FROM competitor_catalog "
            f"WHERE {' OR '.join(where_parts)} "
            "ORDER BY _pre_score DESC "
            f"LIMIT {self.prelimit}"
        )
        cursor = con.execute(sql, score_params + where_params)
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
        compact_blob = _compact(blob)
        identifiers = {
            key: _compact(row.get(key))
            for key in ("article", "vendor_article", "manufacturer_code", "model")
            if row.get(key)
        }
        matched_terms: list[str] = []
        basis: list[str] = []
        raw_score = 0.0
        exact_identifier = False
        model_prefix = False
        model_terms = [term for term in terms if term.kind in {"model", "article", "model_number"}]

        for term in terms:
            normalized = _normalize(term.value)
            compact = _compact(term.value)
            hit = normalized in blob or (compact and compact in compact_blob)
            if hit:
                matched_terms.append(term.value)
                raw_score += float(term.weight)
            if compact and any(compact == value for value in identifiers.values()):
                exact_identifier = True
                raw_score += 120.0
                basis.append(f"exact_identifier:{term.value}")
            model_value = identifiers.get("model")
            if compact and model_value and len(compact) >= 3:
                if model_value.startswith(compact) or compact.startswith(model_value):
                    model_prefix = True
                    raw_score += 70.0
                    basis.append(f"model_prefix:{term.value}")

        if terms and len(matched_terms) == len(terms):
            raw_score += 20.0
            basis.append("all_identity_terms")

        dn_hit = False
        candidate_dn_numbers = {
            int(value)
            for value in re.findall(r"\d{1,4}", str(row.get("dn_text") or ""))
            if int(value) <= 2000
        }
        if dn_signals and candidate_dn_numbers.intersection(dn_signals):
            dn_hit = True
            raw_score += 30.0
            basis.append("explicit_dn")

        inch_hit = False
        size_blob = _normalize(
            " ".join(
                str(row.get(key) or "")
                for key in ("connection_size", "name", "properties_json")
            )
        )
        for inch in inch_signals:
            compact_inch = inch.replace(" ", "")
            if re.search(rf"(?<!\d){re.escape(compact_inch)}\s*[\"″]", size_blob.replace(" ", "")):
                inch_hit = True
                raw_score += 18.0
                basis.append(f"explicit_inch:{inch}")
                break

        matched_model_terms = [term for term in model_terms if term.value in matched_terms]
        if exact_identifier:
            confidence = 0.99
        elif model_prefix and matched_model_terms:
            confidence = 0.95 if (dn_hit or inch_hit) else 0.91
        elif model_terms and len(matched_model_terms) == len(model_terms):
            if len(terms) >= 2 and len(matched_terms) == len(terms):
                confidence = 0.92 if (dn_hit or inch_hit) else 0.88
            else:
                confidence = 0.87 if (dn_hit or inch_hit) else 0.82
        elif len(matched_terms) >= 2:
            confidence = 0.80
        else:
            confidence = 0.60

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

    def lookup(self, query: str) -> CompetitorLookupResult:
        terms = _identity_terms(query)
        if not terms:
            return CompetitorLookupResult(
                attempted=False,
                accepted=False,
                reason="no_external_identity_signal",
                query=query,
            )

        availability_error = self._availability_error()
        if availability_error:
            return CompetitorLookupResult(
                attempted=True,
                accepted=False,
                reason=availability_error,
                query=query,
                identity_terms=[term.value for term in terms],
            )

        rows = self._row_dicts(query, terms)
        if not rows:
            return CompetitorLookupResult(
                attempted=True,
                accepted=False,
                reason="no_local_catalog_candidate",
                query=query,
                identity_terms=[term.value for term in terms],
            )

        dn_signals = _explicit_dn_signals(query)
        inch_signals = _explicit_inch_signals(query)
        candidates = [
            self._score_candidate(row, terms, dn_signals, inch_signals)
            for row in rows
        ]
        candidates.sort(key=lambda candidate: (-candidate.score, -candidate.confidence, candidate.product_id))
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
            attempted=True,
            accepted=accepted,
            reason=reason,
            query=query,
            identity_terms=[term.value for term in terms],
            candidates=candidates,
        )
