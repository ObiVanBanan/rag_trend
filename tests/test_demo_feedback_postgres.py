"""Real PostgreSQL contract for persisted human review (runs in GitHub Actions)."""
import os
from uuid import uuid4

import pytest
from psycopg_pool import ConnectionPool

from nomenclature_matcher.demo_feedback import (
    DemoFeedbackStore, ReviewConflict, ReviewNotFound, initialize_review_schema,
)


@pytest.mark.skipif(not os.getenv("TEST_DEMO_PG_DSN"), reason="Requires test PostgreSQL")
def test_real_database_review_events_and_stats_are_durable():
    dsn=os.environ["TEST_DEMO_PG_DSN"]
    pool=ConnectionPool(conninfo=dsn,min_size=1,max_size=2,kwargs={"autocommit":True},
                        open=True)
    request_id="review-test-"+uuid4().hex
    result={
      "input":"Фланец стальной Ду80 под втулку ПНД Ду90",
      "status":"MATCHED",
      "editable_constraints":{"product_type":"flange","dn":80,"stub_dn":90,"flange_kind":"pe_stub"},
      "recommended":[{
        "ld_id":11,"name":"LD flange","article":"LD-11",
        "comparison":{"rows":[{"key":"dn","strict":True,"status":"MATCH"}]}
      }],
      "other_candidates":[{
        "ld_id":22,"name":"Other LD flange","article":"LD-22",
        "comparison":{"rows":[{"key":"dn","strict":True,"status":"MATCH"}]}
      }]
    }
    try:
        with pool.connection() as conn:
            initialize_review_schema(conn)
            initialize_review_schema(conn)
        store=DemoFeedbackStore(pool)
        token=store.start(request_id,[result])
        assert token and token != request_id
        read=store.get(request_id,0,token)
        assert read["revision"]==0
        assert read["decision"]=="PENDING"
        with pytest.raises(ReviewNotFound):
            store.get(request_id,0,"invalid-token")

        picked=store.decide(request_id,0,token,0,"SELECTED_OTHER",22)
        assert picked=={"revision":1,"decision":"SELECTED_OTHER","selected_ld_id":22}
        with pytest.raises(ReviewConflict):
            store.decide(request_id,0,token,0,"REJECTED")

        corrected={**result,"status":"NOT_FOUND","recommended":[]}
        changed=store.revise(request_id,0,token,1,corrected,{"dn":50})
        assert changed["revision"]==2 and changed["decision"]=="PENDING"
        assert store.get(request_id,0,token)["edited"] is True
        rejected=store.decide(request_id,0,token,2,"REJECTED")
        assert rejected["decision"]=="REJECTED" and rejected["revision"]==3

        # Append one item after the original decision and correction. The
        # original result and its event history must remain intact.
        before=store.get(request_id,0,token)
        store.assert_can_append(request_id,token,1)
        new_item={**result,"input":"Кран шаровой штуцерный Ду20 Ру40"}
        appended=store.append(request_id,token,1,new_item)
        assert appended["item_index"]==1
        assert appended["result"]["input"]==new_item["input"]
        assert store.get(request_id,0,token)==before
        assert store.get(request_id,1,token)["current_result"]==new_item
        with pytest.raises(ReviewConflict):
            store.append(request_id,token,1,new_item)
        with pytest.raises(ReviewNotFound):
            store.assert_can_append(request_id,"invalid-token",2)
        accepted=store.decide(request_id,1,token,0,"ACCEPTED")
        assert accepted["decision"]=="ACCEPTED"
        counters=store.stats(1)["counts"]
        assert counters["rejected"]>=1
        assert counters["parameter_edited"]>=1
        assert counters["selected_other"]>=0
        with pool.connection() as conn:
            stored=conn.execute(
                "SELECT token_hash FROM demo_review_sessions WHERE request_id=%s",
                (request_id,)).fetchone()
            assert stored and stored[0] != token
            events=conn.execute(
                "SELECT event_type FROM demo_review_events WHERE request_id=%s ORDER BY id",
                (request_id,)).fetchall()
            assert [e[0] for e in events]==[
                "SELECTED_OTHER","PARAMETERS_CORRECTED","REJECTED",
                "ITEM_ADDED","ACCEPTED"]
    finally:
        with pool.connection() as conn:
            conn.execute("DELETE FROM demo_review_sessions WHERE request_id=%s",(request_id,))
        pool.close()
