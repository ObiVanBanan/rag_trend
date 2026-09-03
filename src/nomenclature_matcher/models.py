from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class LDProduct:
    id: int
    name: str
    article: str | None = None
    price: Any = None
    dn: Any = None
    pn: Any = None
    joining_type: str | None = None
    url: str | None = None
    properties: Any = field(default_factory=dict)


@dataclass
class SearchCandidate:
    ld_id: int
    name: str
    article: str | None
    score: float
    price: Any = None
    dn: Any = None
    pn: Any = None
    joining_type: str | None = None
    url: str | None = None
    properties: list[dict[str, Any]] | None = None
    search_text: str | None = None


@dataclass
class RerankedCandidate:
    candidate_id: int
    confidence: float | None
    reason: str


@dataclass
class SelectedMatch:
    candidate_id: int
    article: str | None
    name: str
    vector_score: float
    llm_confidence: float | None
    reason: str
    ld_id: int
    dn: Any = None
    pn: Any = None
    joining_type: str | None = None
    url: str | None = None


@dataclass
class MatchResult:
    query: str
    status: Literal["MATCHED", "NOT_FOUND", "RERANK_FAILED"]
    score: float | None = None
    ld_product: SearchCandidate | None = None
    candidates: list[SearchCandidate] = field(default_factory=list)
    selected: list[SelectedMatch] = field(default_factory=list)
    reason: str | None = None


@dataclass
class RerankResult:
    status: Literal["MATCHED", "NOT_FOUND"]
    selected: list[RerankedCandidate]
    reason: str | None = None
