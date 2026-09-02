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


@dataclass
class MatchResult:
    query: str
    status: Literal["MATCHED", "NOT_FOUND"]
    score: float | None = None
    ld_product: SearchCandidate | None = None
    candidates: list[SearchCandidate] = field(default_factory=list)

