from scripts.demo_recheck import grade, summarize


def test_unverified_gold_excluded_not_assumed_negative():
    row = grade(
        {"id":"one","query":"Кран DN50","expected_status":None,"label_status":"UNREVIEWED"},
        {"status":"NOT_FOUND","recommended":[]},
    )
    assert row["verdict"] == "EXCLUDED_UNVERIFIED"


def test_metrics_separate_wrong_not_found_and_false_match():
    data = [
        grade({"id":"1","query":"one","label_status":"VERIFIED",
               "expected_status":"MATCHED","acceptable_ld_ids":[5]},
              {"status":"MATCHED","recommended":[{"ld_id":5}],
               "metrics":{"latency_ms":125}}),
        grade({"id":"2","query":"two","label_status":"VERIFIED",
               "expected_status":"MATCHED","acceptable_ld_ids":[7]},
              {"status":"NOT_FOUND","recommended":[],"metrics":{"latency_ms":100}}),
        grade({"id":"3","query":"three","label_status":"VERIFIED",
               "expected_status":"NOT_FOUND"},
              {"status":"MATCHED","recommended":[{"ld_id":9}],
               "metrics":{"latency_ms":155}}),
    ]
    result = summarize(data)
    assert result["exact_top1_accuracy"] == .3333
    assert result["positive_top1_accuracy"] == .5
    assert result["wrong_not_found_rate"] == .5
    assert result["false_match_rate"] == 1.0
    assert result["graded_cases"] == 3
