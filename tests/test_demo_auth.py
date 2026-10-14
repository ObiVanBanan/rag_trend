import base64
from types import SimpleNamespace

from fastapi.testclient import TestClient
from nomenclature_matcher.api import create_app
from nomenclature_matcher.models import MatchResult
from nomenclature_matcher.public_auth import RequestLimiter, valid_basic_auth


class Runtime:
    settings = SimpleNamespace(
        app_env="production", public_demo_username="demo",
        public_demo_password="example-test-password", public_demo_rpm=3,
        public_demo_max_batch_size=1, api_max_batch_size=100,
        match_trace_enabled=False,
    )
    def __init__(self): self.matcher = self
    def ready(self): return True
    def close(self): pass
    def match_one_hybrid_with_rerank(self, query):
        return MatchResult(query=query, status="NOT_FOUND")


def auth_headers():
    token = base64.b64encode(b"demo:example-test-password").decode()
    return {"Authorization": "Basic "+token}


def test_auth_on_demo_and_all_paid_endpoints():
    with TestClient(create_app(runtime_factory=Runtime)) as client:
        assert client.get("/health/ready").status_code == 200
        for path in ("/demo", "/docs", "/metrics"):
            assert client.get(path).status_code == 401
        assert client.post("/v1/match",json={"products":["a"]}).status_code == 401
        assert client.get("/demo",headers=auth_headers()).status_code == 200
        assert client.post("/v1/demo/match",json={"products":["a","b"]},headers=auth_headers()).status_code == 413
        for i in range(2):
            assert client.post("/v1/demo/match",json={"products":[str(i)]},headers=auth_headers()).status_code == 200
        assert client.post("/v1/demo/match",json={"products":["c"]},headers=auth_headers()).status_code == 429


def test_production_without_password_fails_closed():
    runtime = Runtime()
    runtime.settings = SimpleNamespace(app_env="production",public_demo_password="")
    with TestClient(create_app(runtime_factory=lambda: runtime)) as client:
        assert client.get("/demo").status_code == 503
        assert client.get("/health/ready").status_code == 200


def test_basic_and_limiter():
    assert not valid_basic_auth(None,username="demo",password="x")
    assert not valid_basic_auth("Basic !",username="demo",password="x")
    assert not valid_basic_auth("Bearer xyz",username="demo",password="x")
    good = "Basic "+base64.b64encode(b"demo:x").decode()
    assert valid_basic_auth(good,username="demo",password="x")
    limiter=RequestLimiter()
    assert limiter.allow(1)
    assert not limiter.allow(1)
