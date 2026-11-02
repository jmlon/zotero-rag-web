"""The application shell: health, headers, and serving the React app."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_settings
from zotero_rag_web.app import ROBOTS_TXT, create_app


@pytest.fixture
def built_web(tmp_path: Path) -> Path:
    """A stand-in for `web/dist`: an index.html, a hashed asset and a top-level file."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root>SPA</div>")
    (dist / "assets" / "index-abc123.js").write_text("console.log('app')")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside the build")
    return dist


def client(**settings: object) -> TestClient:
    return TestClient(create_app(make_settings(log_format="console", **settings)))


@pytest.fixture
def app_with_database(database_url: str, built_web: Path) -> Iterator[TestClient]:
    with client(database_url=database_url, static_dir=built_web) as test_client:
        yield test_client


def test_healthz_is_green_with_a_database(app_with_database: TestClient) -> None:
    response = app_with_database.get("/healthz")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_healthz_reports_an_unreachable_database_without_crashing() -> None:
    """The app starts anyway, so the platform shows a failing check, not a crash loop."""
    with client(database_url="postgresql://nobody:x@127.0.0.1:1/none") as test_client:
        response = test_client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"


def test_healthz_without_a_database_configured() -> None:
    with client() as test_client:
        response = test_client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["database"] == "not configured"


def test_every_response_says_noindex_and_carries_a_request_id(built_web: Path) -> None:
    with client(static_dir=built_web) as test_client:
        response = test_client.get("/")
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
        assert len(response.headers["X-Request-ID"]) == 32
        echoed = test_client.get("/", headers={"X-Request-ID": "from-the-proxy"})
        assert echoed.headers["X-Request-ID"] == "from-the-proxy"


def test_robots_txt_disallows_everything() -> None:
    with client() as test_client:
        assert test_client.get("/robots.txt").text == ROBOTS_TXT


def test_the_spa_serves_its_files_and_index_for_deep_links(built_web: Path) -> None:
    with client(static_dir=built_web) as test_client:
        assert "SPA" in test_client.get("/").text
        assert "SPA" in test_client.get("/c/some-conversation").text
        assert test_client.get("/assets/index-abc123.js").text == "console.log('app')"
        assert test_client.get("/favicon.svg").text == "<svg/>"


def test_the_spa_never_serves_files_outside_the_build(built_web: Path) -> None:
    with client(static_dir=built_web) as test_client:
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
            assert "outside the build" not in test_client.get(path).text, path


def test_an_unknown_api_path_is_a_json_404_not_the_spa(built_web: Path) -> None:
    with client(static_dir=built_web) as test_client:
        response = test_client.get("/api/nothing-here")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found"}}


def test_without_a_build_the_root_explains_how_to_build(tmp_path: Path) -> None:
    with client(static_dir=tmp_path / "missing") as test_client:
        assert "npm run build" in test_client.get("/").text


def test_the_api_documents_itself_under_api() -> None:
    with client() as test_client:
        assert test_client.get("/api/openapi.json").status_code == 200


def test_each_request_is_logged_once_with_its_request_id(
    built_web: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One JSON access line per request, carrying the id an error report would quote."""
    with TestClient(create_app(make_settings(static_dir=built_web))) as test_client:
        test_client.get("/c/x", headers={"X-Request-ID": "trace-me"})
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]
    (access,) = [line for line in lines if line.get("logger") == "zotero_rag_web.access"]
    assert access["event"] == "GET /c/x 200"
    assert access["request_id"] == "trace-me"
    assert access["duration_ms"] >= 0


def test_uvicorns_own_access_line_is_silenced() -> None:
    """It would duplicate ours without the request id; uvicorn logs whenever its
    logger reaches any handler, so it must not propagate to the root's."""
    import logging

    create_app(make_settings())
    assert not logging.getLogger("uvicorn.access").hasHandlers()
