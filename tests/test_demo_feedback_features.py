"""Review flows operate on stored snapshots, not user-supplied candidate names/IDs."""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nomenclature_matcher.api import create_app
from nomenclature_matcher.business_demo import (
    editable_constraints, render_corrected_query, validate_edits,
    build_demo_item,
)
from nomenclature_matcher.demo_feedback import (
    DemoFeedbackStore, ReviewConflict, token_digest, SCHEMA,
)
from nomenclature_matcher.models import MatchResult, SearchCandidate, SelectedMatch


def test_correction_query_is_rebuilt_and_11b27p_safety_is_not_bypassed():
    original="Кран шаровый 11Б27П Ду20 Ру16"
    editable=editable_constraints(original,{})
    assert editable["product_type"]=="ball_valve"
    assert editable["dn"]==20
    assert editable["pn_min_mpa"]==1.6
    revised=render_corrected_query(original,{
        **editable,"working_medium":"воды","thread_type":"female_female","thread_size":"3/4"})
    assert "11Б27П" in revised and "Ду20" in revised and "Ру16" in revised
    assert "ВР/ВР" in revised and "резьба G3/4" in revised
    from nomenclature_matcher.product_safety import missing_required_valve_fields
    assert missing_required_valve_fields(original)==[
        "working_medium","thread_type","thread_size"]
    assert missing_required_valve_fields(revised)==[]


@pytest.mark.parametrize("before,changes,expected", [
    ("Фланец стальной Ду80 под втулку ПНД Ду90",
     {"dn":50,"stub_dn":63},"Ду50 под втулку ПНД Ду63"),
    ("Фланец стальной плоский приварной, марка стали 20, номинальное давление 1,6 МПа, номинальный диаметр 100 мм",
     {"dn":80},"Ду80"),
])
def test_flange_edit_produces_new_query_without_duplicate_old_dimensions(before,changes,expected):
    constraints=editable_constraints(before,{})
    updated={**constraints,**validate_edits(changes)}
    query=render_corrected_query(before,updated)
    assert expected in query
    assert "под втулку" in query if "втулку" in before else "плоский приварной" in query


def test_invalid_or_unbounded_edits_fail_closed():
    for item in ({"dn":0},{"dn":"999999"},{"pn_min_mpa":"nan"},
                 {"working_medium":"water; drop table"},{"unknown":"ok"},
                 {"thread_type":"magic"}):
        with pytest.raises(ValueError):
            validate_edits(item)
    with pytest.raises(ValueError):
        render_corrected_query("Фланец Ду80 под втулку ПНД Ду90",
                               {"product_type":"flange","flange_kind":"pe_stub","dn":80})


def product(ld_id,article,required_status="MATCH",selection="FOR_REVIEW"):
    return {
        "ld_id":ld_id,"name":article,"article":article,
        "selection":selection,
        "comparison":{"rows":[{"key":"dn","strict":True,"status":required_status}]},
    }


def snapshot():
    return {"status":"MATCHED","recommended":[product(11,"LD-11","MATCH","RECOMMENDED")],
            "other_candidates":[product(22,"LD-22","MATCH"),product(33,"LD-33","MISMATCH"),
                                product(44,"LD-44","UNKNOWN")]}


def test_manual_candidate_must_be_present_and_strictly_safe():
    s=snapshot()
    assert DemoFeedbackStore._candidate(s,"ACCEPTED",None,False)==11
    assert DemoFeedbackStore._candidate(s,"SELECTED_OTHER",22,False)==22
    for bad in (11,12345,33):
        with pytest.raises(ReviewConflict):
            DemoFeedbackStore._candidate(s,"SELECTED_OTHER",bad,False)
    with pytest.raises(ReviewConflict):
        DemoFeedbackStore._candidate(s,"SELECTED_OTHER",44,False)
    assert DemoFeedbackStore._candidate(s,"SELECTED_OTHER",44,True)==44


def test_tokens_are_hashed_before_persistence():
    token="review-secret-example"
    assert token_digest(token)!=token
    assert token_digest(token)==token_digest(token)
    assert "FOREIGN KEY (request_id, item_index)" in SCHEMA
    assert "demo_review_events" in SCHEMA


class FeedbackFake:
    def __init__(self):
        self.sessions={}
        self.actions=[]
    def start(self,request_id,results):
        self.sessions[request_id]=[{"revision":0,"decision":"PENDING",
                                    "current_result":x} for x in results]
        return "v"*44
    def get(self,request_id,index,token):
        return self.sessions[request_id][index]
    def revise(self,request_id,index,token,revision,result,changes):
        self.actions.append(("PARAMETERS_CORRECTED",changes))
        self.sessions[request_id][index]={"revision":revision+1,"decision":"PENDING",
                                           "current_result":result}
        return {"revision":revision+1,"decision":"PENDING","result":result}
    def decide(self,request_id,index,token,revision,action,ld_id,ack):
        self.actions.append((action,ld_id))
        self.sessions[request_id][index]["revision"]=revision+1
        self.sessions[request_id][index]["decision"]=action
        return {"revision":revision+1,"decision":action,"selected_ld_id":ld_id}
    def stats(self,days):
        return {"period_days":days,"counts":{"processed":1,"accepted":1},
                "timeline":[],"events":{"ACCEPTED":1}}


class Runtime:
    def __init__(self):
        self.settings=SimpleNamespace(
            app_env="development",public_demo_username="demo",
            public_demo_password="", public_demo_admin_username="admin",
            public_demo_admin_password="admin-test-only-secret",
            api_max_batch_size=10,public_demo_max_batch_size=10,
            match_trace_enabled=False)
        self.demo_feedback=FeedbackFake()
        self.matcher=self
        self.queries=[]
    def close(self): pass
    def ready(self): return True
    def match_one_hybrid_with_rerank(self,query):
        self.queries.append(query)
        if "11Б27П" in query and "резьба G3/4" not in query:
            return MatchResult(query=query,status="INSUFFICIENT_PARAMETERS",
                               reason="INSUFFICIENT_PARAMETERS",
                               query_interpretation={"missing_fields":["working_medium","thread_type","thread_size"]})
        candidate=SearchCandidate(ld_id=15,name="Кран шаровой латунный DN20",
                                  article="LD-15",score=.3,dn=20,pn="1,6")
        selected=SelectedMatch(candidate_id=1,ld_id=15,name=candidate.name,
                               article=candidate.article,llm_confidence=.7,reason="from catalog")
        return MatchResult(query=query,status="MATCHED",ld_product=candidate,
                           candidates=[candidate],selected=[selected],query_interpretation={
                             "constraints":{"product_type":"ball_valve","dn":20},
                             "hard_constraints":{"dn":20},
                           })


def admin_header():
    data=base64.b64encode(b"admin:admin-test-only-secret").decode("ascii")
    return {"Authorization":"Basic "+data}


def test_demo_session_edit_action_and_admin_only_metrics():
    runtime=Runtime()
    with TestClient(create_app(runtime_factory=lambda:runtime)) as client:
        start=client.post("/v1/demo/match",json={"products":["Кран шаровый 11Б27П Ду20 Ру16"]})
        assert start.status_code==200
        batch=start.json()
        assert batch["review_token"] and batch["results"][0]["status"]=="INSUFFICIENT_PARAMETERS"
        target=dict(request_id=batch["request_id"],review_token=batch["review_token"],
                    item_index=0,revision=0)
        assert client.get("/demo/stats").status_code==401
        assert client.get("/v1/demo/stats").status_code==401
        assert client.get("/v1/demo/stats",headers=admin_header()).status_code==200
        assert client.get("/demo/stats",headers=admin_header()).status_code==200

        revised=client.post("/v1/demo/correct",json={**target,"changes":{
            "working_medium":"воды","thread_type":"female_female","thread_size":"3/4"}})
        assert revised.status_code==200,revised.text
        assert revised.json()["result"]["status"]=="MATCHED"
        assert runtime.queries[-1].count("11Б27П")==1
        assert runtime.queries[-1].count("Ду20")==1
        assert runtime.demo_feedback.actions[0][0]=="PARAMETERS_CORRECTED"

        action=client.post("/v1/demo/decision",json={
            **target,"revision":1,"action":"REJECTED"})
        assert action.status_code==200
        assert action.json()["decision"]=="REJECTED"
