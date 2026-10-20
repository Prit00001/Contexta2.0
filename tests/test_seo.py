"""What crawlers and AI answer engines read: robots.txt, sitemap.xml, llms.txt, /about and the app shell."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.seo import render_shell


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("APP_SITE_URL", "https://contexta.example.com")
    monkeypatch.setenv("BILLING_PRICE_LABEL", "₹499 per seat / month")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _ld(page: str) -> list[dict]:
    return [json.loads(m) for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)]


def test_robots_keeps_private_and_costly_pages_out_and_points_at_the_sitemap(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    for rule in ("Disallow: /workspace", "Disallow: /workspaces/", "Disallow: /billing/", "Disallow: /*?q="):
        assert rule in r.text
    assert "Sitemap: https://contexta.example.com/sitemap.xml" in r.text


def test_sitemap_lists_the_public_pages(client):
    r = client.get("/sitemap.xml")
    assert r.headers["content-type"].startswith("application/xml")
    locs = re.findall(r"<loc>(.*?)</loc>", r.text)
    assert locs == ["https://contexta.example.com/", "https://contexta.example.com/about"]


def test_llms_txt_summarises_the_site_for_language_models(client):
    r = client.get("/llms.txt")
    assert r.text.startswith("# Contexta\n\n> ")
    assert "https://contexta.example.com/about" in r.text
    assert "Attention Is All You Need" in r.text and "### Is Contexta free?" in r.text


def test_about_is_server_rendered_with_faq_structured_data_and_the_price(client):
    r = client.get("/about")
    assert r.status_code == 200
    page = r.text
    assert '<link rel="canonical" href="https://contexta.example.com/about">' in page
    assert "₹499 per seat / month" in page
    assert "<li>LoRA: Low-Rank Adaptation of Large Language Models</li>" in page
    types = {d["@type"]: d for d in _ld(page)}
    assert set(types) == {"WebApplication", "FAQPage"}
    questions = [q["name"] for q in types["FAQPage"]["mainEntity"]]
    assert "Is Contexta free?" in questions
    assert all(q["acceptedAnswer"]["text"] for q in types["FAQPage"]["mainEntity"])


def test_site_url_falls_back_to_the_forwarded_host_until_configured(monkeypatch):
    monkeypatch.delenv("APP_SITE_URL", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            r = c.get(
                "/robots.txt", headers={"x-forwarded-proto": "https", "x-forwarded-host": "contexta.onrender.com"}
            )
        assert "Sitemap: https://contexta.onrender.com/sitemap.xml" in r.text
    finally:
        get_settings.cache_clear()


def test_app_shell_gets_absolute_urls_and_workspace_pages_are_noindex(client, tmp_path):
    shell = tmp_path / "index.html"
    shell.write_text(
        '<link rel="canonical" href="__SITE_URL__/"><script type="application/ld+json">__APP_LD__</script>'
    )

    class Req:  # the parts of a Request render_shell reads
        def __init__(self, path):
            self.app, self.headers = client.app, {}
            self.url = type("U", (), {"path": path, "scheme": "http", "netloc": "x"})()

    home = render_shell(Req("/"), shell)
    body = home.body.decode()
    assert 'href="https://contexta.example.com/"' in body
    assert _ld(body)[0]["@type"] == "WebApplication"
    assert "x-robots-tag" not in home.headers
    assert render_shell(Req("/workspace/w1/ask"), shell).headers["x-robots-tag"] == "noindex"


def test_json_ld_cannot_close_its_script_element():
    from app.seo import _json_ld

    assert "</script>" not in _json_ld({"text": "</script><script>alert(1)</script>"})


def test_public_pages_answer_head_requests(client):
    for path in ("/robots.txt", "/sitemap.xml", "/llms.txt", "/about"):
        assert client.head(path).status_code == 200, path
