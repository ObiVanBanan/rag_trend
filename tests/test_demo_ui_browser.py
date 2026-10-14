"""Real Chromium smoke for the customer review UI (mock API, no LLM/Qdrant).

Validates the actual broken workflow: see top 10, choose another LD product,
verify primary table updates, and reopen full answer snapshot after reload.
"""
import json
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "src" / "nomenclature_matcher"
URL = "https://ld-smoke.test"
ARTIFACT = Path(__file__).resolve().parents[1] / "test-artifacts"


def candidate(ld_id: int, article: str, *, recommended: bool = False):
    return {
        "ld_id":ld_id,
        "name":f"Фланец стальной ПНД LD DN80 {article}",
        "article":article,
        "dn":80, "pn":"1.6", "joining_type":"flanged",
        "url":f"https://example.org/product/{ld_id}",
        "technical":{
            "dn":80, "pn_mpa":1.6,"product_type":"flange",
            "flange_kind":"pe_stub","stub_dn":90,
            "flange_bore_mm":108,"body_material":"steel",
            "joining_type":"flanged",
        },
        "reason":"Проверены DN и внутренний диаметр.",
        "selection":"RECOMMENDED" if recommended else "FOR_REVIEW",
        "comparison":{"rows":[
            {"key":"dn","label":"Диаметр","required":"DN80",
             "catalog":"DN80","status":"MATCH","strict":True},
            {"key":"stub_dn","label":"ПНД","required":"Ø90 мм",
             "catalog":"Ø90 мм","status":"MATCH","strict":True}
        ]},
    }


def mock_result():
    product="Фланец стальной ду80мм. под втулку ПНД ду90мм."
    best=candidate(101,"ФПНД-080.16-ст20-1080000",recommended=True)
    alternative=candidate(102,"ФПНД-080.16-ст20-1080001")
    return {
        "request_id":"req-reference-chrome-smoke",
        "review_token":"S"*44,
        "results":[{
            "input":product,"status":"MATCHED",
            "message":"Рекомендация основана на технической проверке.",
            "normalized_query":product,
            "editable_constraints":{"product_type":"flange",
                                    "flange_kind":"pe_stub","dn":80,"stub_dn":90,
                                    "body_material":"steel"},
            "parameters":[{"key":"dn","label":"DN","value":"DN80"}],
            "recommended":[best], "other_candidates":[alternative],
            "top_candidates":[best,alternative],
        },{
            "input":"Кран шаровый 11Б27П Ду20 Ру16",
            "status":"INSUFFICIENT_PARAMETERS",
            "message":"Недостаточно характеристик",
            "missing_fields":["working_medium","thread_type","thread_size"],
            "editable_constraints":{"product_type":"ball_valve","dn":20,"pn_min_mpa":1.6},
            "parameters":[],"recommended":[],"other_candidates":[],"top_candidates":[],
        }]
    }


def test_reference_ui_in_real_browser():
    payload=mock_result()
    decisions=[]
    with sync_playwright() as browser_host:
        browser=browser_host.chromium.launch(headless=True)
        context=browser.new_context(viewport={"width":1440,"height":960})
        page=context.new_page()

        def handle_match(route):
            route.fulfill(status=200,content_type="application/json",body=json.dumps(payload))

        def handle_decision(route):
            submitted=route.request.post_data_json
            decisions.append(submitted)
            route.fulfill(status=200,content_type="application/json",body=json.dumps({
                "decision":submitted["action"],
                "revision":submitted["revision"]+1,
                "selected_ld_id":submitted.get("selected_ld_id"),
            }))

        page.route("**/v1/demo/match",handle_match)
        page.route("**/v1/demo/decision",handle_decision)
        page.route(URL+"/demo",lambda route:route.fulfill(
            status=200,content_type="text/html; charset=utf-8",
            body=(ROOT/"business_demo.html").read_text(encoding="utf-8")))
        page.goto(URL+"/demo")
        page.locator("#query").fill("\n".join(x["input"] for x in payload["results"]))
        page.locator("#go").click()
        page.locator(".result-row").first.wait_for()
        assert page.locator(".result-row").count()==2
        # The first row opens by default, with actual alternatives not nested
        # behind another <details> control.
        assert page.locator(".result-row").first.get_attribute("class").find("expanded")>=0
        assert page.locator(".ld-candidate-table tbody tr").count()==2
        assert page.locator(".ld-select-product").count()==2
        page.locator(".ld-select-product").nth(1).click()
        page.get_by_text("Выбран другой вариант").first.wait_for()
        assert decisions[-1]["action"]=="SELECTED_OTHER"
        assert decisions[-1]["selected_ld_id"]==102
        assert "ФПНД-080.16-ст20-1080001" in page.locator(".result-row").first.locator(".article-code").inner_text()

        # Query and all responses, including the human decision, survived
        # a real browser reload — without retaining a review capability.
        page.reload()
        page.locator("#history-open").click()
        assert page.locator(".history-item").count()==1
        assert "2 позиций" in page.locator(".history-item").first.inner_text()
        page.locator(".history-item").first.click()
        assert page.locator(".result-row").count()==2
        assert "ФПНД-080.16-ст20-1080001" in page.locator(".result-row").first.locator(".article-code").inner_text()
        page.locator(".result-row").first.locator(".open-detail").click()
        assert page.locator(".ld-candidate-table tbody tr").count()==2
        assert page.locator(".ld-select-product").nth(1).is_disabled()
        history=page.evaluate("JSON.parse(localStorage.getItem('ld-tender-demo-snapshots-v3'))")
        assert history and len(history[0]["results"])==2
        assert history[0]["states"][0]["decision"]=="SELECTED_OTHER"
        assert "review_token" not in json.dumps(history)
        assert "ФПНД-080.16-ст20-1080001" in json.dumps(history,ensure_ascii=False)
        assert "metrics" not in json.dumps(history)
        ARTIFACT.mkdir(exist_ok=True)
        page.screenshot(path=str(ARTIFACT/"demo-reference-review.png"),full_page=True)
        browser.close()


def test_admin_statistics_charts_in_real_browser():
    with sync_playwright() as host:
        browser=host.chromium.launch(headless=True)
        page=browser.new_page(viewport={"width":1440,"height":900})
        page.route(URL+"/demo/stats",lambda route:route.fulfill(
            status=200,content_type="text/html; charset=utf-8",
            body=(ROOT/"business_demo_stats.html").read_text(encoding="utf-8")))
        data={
            "counts":{"processed":10,"auto_matched":7,"not_found":1,
                      "insufficient":2,"errors":0,"accepted":4,"rejected":1,
                      "selected_other":2,"parameter_edited":3,"pending":3},
            "timeline":[
                {"day":"2026-10-13","processed":4,"auto_matched":3,
                 "not_found":1,"insufficient":0,"errors":0},
                {"day":"2026-10-14","processed":6,"auto_matched":4,
                 "not_found":0,"insufficient":2,"errors":0}],
            "interaction_timeline":[
                {"day":"2026-10-13","accepted":1,"rejected":0,
                 "selected_other":0,"parameter_edited":1},
                {"day":"2026-10-14","accepted":3,"rejected":1,
                 "selected_other":2,"parameter_edited":2}],
            "events":{"ACCEPTED":4,"REJECTED":1,"SELECTED_OTHER":2,"PARAMETERS_CORRECTED":3,"ITEM_ADDED":2}
        }
        page.route("**/v1/demo/stats**",lambda route:route.fulfill(
            status=200,content_type="application/json",body=json.dumps(data)))
        page.goto(URL+"/demo/stats")
        page.wait_for_function("document.querySelector('#processed').textContent.trim() === '10'")
        assert page.locator(".chart-column").count()==2
        assert "conic-gradient" in page.locator("#donut").get_attribute("style")
        assert page.locator("#interaction-chart polyline").count()==4
        assert page.locator("#review-bars .review-row").count()==5
        ARTIFACT.mkdir(exist_ok=True)
        page.screenshot(path=str(ARTIFACT/"demo-statistics.png"),full_page=True)
        browser.close()



def test_connection_dropdown_edits_are_sent_for_both_stutser_variants():
    """The user's missing dropdown item must work in an actual browser."""
    import copy
    for kind in ("nipple", "welded_nipple"):
        payload=copy.deepcopy(mock_result())
        edits=[]
        with sync_playwright() as host:
            browser=host.chromium.launch(headless=True)
            page=browser.new_page(viewport={"width":1440,"height":960})
            page.route(URL+"/demo",lambda route:route.fulfill(
                status=200,content_type="text/html; charset=utf-8",
                body=(ROOT/"business_demo.html").read_text(encoding="utf-8")))
            page.route("**/v1/demo/match",lambda route:route.fulfill(
                status=200,content_type="application/json",body=json.dumps(payload)))

            def handle_correct(route):
                body=route.request.post_data_json
                edits.append(body)
                result=copy.deepcopy(payload["results"][0])
                result["editable_constraints"]["joining_type"]=kind
                route.fulfill(status=200,content_type="application/json",body=json.dumps({
                    "revision":1,"decision":"PENDING","result":result,
                }))
            page.route("**/v1/demo/correct",handle_correct)
            page.goto(URL+"/demo")
            page.locator("#query").fill(payload["results"][0]["input"])
            page.locator("#go").click()
            page.locator(".result-row").first.wait_for()
            select=page.locator("#edit-0-joining_type")
            select.select_option(kind)
            assert select.input_value()==kind
            page.locator(".ld-parameter-side").get_by_role("button",name="Применить и подобрать заново").click()
            page.wait_for_function("document.querySelector('#edit-0-joining_type')?.value === "+json.dumps(kind))
            assert edits and edits[0]["changes"]["joining_type"]==kind
            browser.close()



def test_append_a_missing_item_to_current_batch_and_history_without_rerunning_old_items():
    import copy
    payload=mock_result()
    appended=[]
    match_calls=[]
    one=copy.deepcopy(payload["results"][0])
    one["input"]="Кран шаровой штуцерный Ду20 Ру40"
    one["source_input"]=one["input"]
    one["editable_constraints"]={
        "product_type":"ball_valve","dn":20,
        "pn_min_mpa":4.0,"joining_type":"nipple"
    }
    one["recommended"]=[candidate(601,"LD-КШЦШ-20",recommended=True)]
    one["other_candidates"]=[]
    one["top_candidates"]=one["recommended"]
    with sync_playwright() as host:
        browser=host.chromium.launch(headless=True)
        page=browser.new_page(viewport={"width":1440,"height":960})
        page.route(URL+"/demo",lambda route:route.fulfill(
            status=200,content_type="text/html; charset=utf-8",
            body=(ROOT/"business_demo.html").read_text(encoding="utf-8")))

        def match(route):
            match_calls.append(route.request.post_data_json)
            route.fulfill(status=200,content_type="application/json",body=json.dumps(payload))

        def append(route):
            data=route.request.post_data_json
            appended.append(data)
            route.fulfill(status=200,content_type="application/json",body=json.dumps({
                "item_index":2,"revision":0,"decision":"PENDING","result":one
            }))

        page.route("**/v1/demo/match",match)
        page.route("**/v1/demo/append",append)
        page.goto(URL+"/demo")
        assert page.locator("#append-open").is_hidden()
        page.locator("#query").fill("\n".join(x["input"] for x in payload["results"]))
        page.locator("#go").click()
        page.locator(".result-row").first.wait_for()
        original_name=page.locator(".result-row").first.locator(".article-code").inner_text()
        assert page.locator(".result-row").count()==2
        assert len(match_calls)==1
        page.locator("#append-open").click()
        page.locator("#append-query").fill("Кран шаровой штуцерный Ду20 Ру40")
        page.locator("#append-field-product_type").select_option("ball_valve")
        page.locator("#append-field-joining_type").select_option("nipple")
        page.locator("#append-field-dn").fill("20")
        page.locator("#append-field-pn_min_mpa").fill("4")
        page.locator("#append-submit").click()
        page.locator(".result-row").nth(2).wait_for()
        assert len(match_calls)==1  # no full-batch re-run
        assert len(appended)==1
        assert appended[0]["expected_count"]==2
        assert appended[0]["fields"]["joining_type"]=="nipple"
        assert appended[0]["fields"]["product_type"]=="ball_valve"
        assert len(page.locator(".result-row").all())==3
        assert page.locator(".result-row").first.locator(".article-code").inner_text()==original_name
        assert "LD-КШЦШ-20" in page.locator(".result-row").nth(2).locator(".article-code").inner_text()
        assert page.locator("#tray-count").inner_text()=="2 / 3"
        page.reload()
        page.locator("#history-open").click()
        assert page.locator(".history-item").count()==1
        page.locator(".history-item").click()
        assert page.locator(".result-row").count()==3
        assert "LD-КШЦШ-20" in page.locator(".result-row").nth(2).locator(".article-code").inner_text()
        assert page.locator("#append-open").is_hidden()
        saved=page.evaluate("JSON.parse(localStorage.getItem('ld-tender-demo-snapshots-v3'))")
        assert saved[0]["products"][-1]=="Кран шаровой штуцерный Ду20 Ру40"
        assert len(saved[0]["results"])==3
        assert len(saved[0]["states"])==3
        assert "review_token" not in json.dumps(saved)
        assert len(match_calls)==1  # re-open history never calls match
        browser.close()
