"""Appending a forgotten item must not invoke matching for prior items."""
from __future__ import annotations

from fastapi.testclient import TestClient

from nomenclature_matcher.api import create_app
from nomenclature_matcher.demo_feedback import ReviewConflict, ReviewNotFound
from nomenclature_matcher.business_demo import AppendItemRequest
from test_demo_feedback_features import FeedbackFake, Runtime


class AppendFeedbackFake(FeedbackFake):
    def assert_can_append(self, request_id, token, expected_count, *, max_rows=100):
        if token!="v"*44 or request_id not in self.sessions:
            raise ReviewNotFound("Сессия не найдена.")
        if len(self.sessions[request_id])!=expected_count:
            raise ReviewConflict("Список изменился.")
        if expected_count>=max_rows:
            raise ReviewConflict("Лимит 100 позиций.")

    def append(self, request_id, token, expected_count, result, *, max_rows=100):
        self.assert_can_append(request_id,token,expected_count,max_rows=max_rows)
        self.sessions[request_id].append({
            "revision":0,"decision":"PENDING","current_result":result
        })
        self.actions.append(("ITEM_ADDED",result["input"]))
        return {
            "item_index":expected_count,"revision":0,"decision":"PENDING","result":result
        }


def test_add_one_only_calls_matcher_once_and_preserves_original_results_and_choices():
    runtime=Runtime()
    runtime.demo_feedback=AppendFeedbackFake()
    with TestClient(create_app(runtime_factory=lambda:runtime)) as client:
        first=client.post("/v1/demo/match",json={"products":[
            "Кран шаровый Ду20 Ру40 штуцерный",
            "Фланец стальной Ду80 Ру16"
        ]})
        assert first.status_code==200,first.text
        state=first.json()
        assert len(runtime.queries)==2
        target=dict(request_id=state["request_id"],review_token=state["review_token"])
        old_results=[row["current_result"] for row in runtime.demo_feedback.sessions[target["request_id"]]]
        # Leave a human decision on the first original row: it must survive.
        rejected=client.post("/v1/demo/decision",json={
            **target,"item_index":0,"revision":0,"action":"REJECTED"
        })
        assert rejected.status_code==200
        before=list(runtime.queries)
        response=client.post("/v1/demo/append",json={
            **target,"expected_count":2,"query":"Кран шаровый Ду15 Ру40",
            "fields":{
                "product_type":"ball_valve","dn":15,
                "pn_min_mpa":4.0,"joining_type":"nipple"
            },
        })
        assert response.status_code==200,response.text
        inserted=response.json()
        assert inserted["item_index"]==2
        assert inserted["result"]["status"]=="MATCHED"
        assert inserted["result"]["input"]=="Кран шаровый Ду15 Ру40"
        assert "штуцерное" in inserted["result"]["corrected_query"]
        assert runtime.queries[:2]==before
        assert len(runtime.queries)==3
        assert runtime.demo_feedback.sessions[target["request_id"]][0]["decision"]=="REJECTED"
        assert runtime.demo_feedback.sessions[target["request_id"]][0]["current_result"]==old_results[0]
        assert runtime.demo_feedback.sessions[target["request_id"]][1]["current_result"]==old_results[1]
        assert len(runtime.demo_feedback.sessions[target["request_id"]])==3
        assert runtime.demo_feedback.actions[-1][0]=="ITEM_ADDED"
        decision=client.post("/v1/demo/decision",json={
            **target,"item_index":2,"revision":0,"action":"REJECTED"
        })
        assert decision.status_code==200
        assert decision.json()["revision"]==1
        # Re-submit with the same expected_count: fail before paid inference.
        before=len(runtime.queries)
        duplicate=client.post("/v1/demo/append",json={
            **target,"expected_count":2,"query":"Дубликат Ду15"
        })
        assert duplicate.status_code==409
        assert len(runtime.queries)==before
        assert client.post("/v1/demo/append",json={
            **target,"review_token":"x"*44,"expected_count":3,
            "query":"Кран шаровой Ду20"
        }).status_code==404
        assert len(runtime.queries)==before


def test_raw_one_item_and_strict_valve_refusal():
    runtime=Runtime()
    runtime.demo_feedback=AppendFeedbackFake()
    with TestClient(create_app(runtime_factory=lambda:runtime)) as client:
        first=client.post("/v1/demo/match",json={"products":["Фланец Ду80"]}).json()
        response=client.post("/v1/demo/append",json={
            "request_id":first["request_id"],"review_token":first["review_token"],
            "expected_count":1,"query":"Кран шаровый 11Б27П Ду20 Ру16"
        })
        assert response.status_code==200,response.text
        assert response.json()["result"]["status"]=="INSUFFICIENT_PARAMETERS"
        assert response.json()["result"]["missing_fields"]==[
            "working_medium","thread_type","thread_size"
        ]


def test_invalid_or_oversized_append_fails_before_matcher():
    runtime=Runtime()
    runtime.demo_feedback=AppendFeedbackFake()
    with TestClient(create_app(runtime_factory=lambda:runtime)) as client:
        first=client.post("/v1/demo/match",json={"products":["Фланец Ду80"]}).json()
        target={
            "request_id":first["request_id"],"review_token":first["review_token"],
            "expected_count":1
        }
        before=len(runtime.queries)
        for patch,status in [
            ({"query":""},422),
            ({"query":"Кран Ду20\nКран Ду30"},422),
            ({"query":"Кран Ду20","fields":{"joining_type":"fictional"}},422),
            ({"query":"Кран Ду20","fields":{"dn":-1}},422),
            ({"fields":{"dn":20}},422),
            ({"query":"x"*2001},422),
        ]:
            response=client.post("/v1/demo/append",json={**target,**patch})
            assert response.status_code==status,(patch,response.text)
        assert len(runtime.queries)==before


def test_session_limit_is_separate_from_initial_batch_limit():
    runtime=Runtime()
    runtime.demo_feedback=AppendFeedbackFake()
    with TestClient(create_app(runtime_factory=lambda:runtime)) as client:
        first=client.post("/v1/demo/match",json={"products":["Фланец Ду80"]}).json()
        rid=first["request_id"]
        runtime.demo_feedback.sessions[rid]*=100
        before=len(runtime.queries)
        response=client.post("/v1/demo/append",json={
            "request_id":rid,"review_token":first["review_token"],
            "expected_count":100,"query":"Кран Ду20"
        })
        assert response.status_code==409
        assert len(runtime.queries)==before
