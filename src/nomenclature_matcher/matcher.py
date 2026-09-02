from .models import MatchResult, SearchCandidate


class NomenclatureMatcher:
    def __init__(self, embedder, store, settings):
        self.embedder, self.store, self.settings = embedder, store, settings

    def match_one(self, query: str) -> MatchResult:
        query = " ".join(query.split())
        if not query:
            return MatchResult(query=query, status="NOT_FOUND")
        hits = self.store.search(self.embedder.embed_query(query), self.settings.match_top_k)
        candidates = [SearchCandidate(ld_id=int(hit.payload["ld_id"]), name=hit.payload.get("name", ""), article=hit.payload.get("article"), score=float(hit.score), price=hit.payload.get("price"), dn=hit.payload.get("dn"), pn=hit.payload.get("pn"), joining_type=hit.payload.get("joining_type"), url=hit.payload.get("url")) for hit in hits]
        best = candidates[0] if candidates else None
        status = "MATCHED" if best and best.score >= self.settings.match_score_threshold else "NOT_FOUND"
        return MatchResult(query=query, status=status, score=best.score if best else None, ld_product=best if status == "MATCHED" else None, candidates=candidates)

    def match_many(self, queries: list[str]) -> list[MatchResult]:
        cache = {q: self.match_one(q) for q in dict.fromkeys(queries) if q.strip()}
        return [cache.get(q, MatchResult(query=" ".join(q.split()), status="NOT_FOUND")) for q in queries]

    match = match_many
