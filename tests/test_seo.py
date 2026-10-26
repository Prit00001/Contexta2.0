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


# ---- link previews, 404 page, permanent redirects, private pages ----


def test_both_public_pages_carry_a_link_preview_image(client):
    page = client.get("/about").text
    assert '<meta property="og:image" content="https://contexta.example.com/static/og.png">' in page
    assert '<meta name="twitter:card" content="summary_large_image">' in page
    img = client.get("/static/og.png")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.content[16:24] == (1200).to_bytes(4, "big") + (630).to_bytes(4, "big")  # PNG IHDR width, height


def test_unknown_address_gets_a_404_page_in_browsers_and_json_elsewhere(client):
    browser = client.get("/no-such-page", headers={"accept": "text/html,application/xhtml+xml"})
    assert browser.status_code == 404 and "This page doesn't exist" in browser.text
    assert browser.headers["x-robots-tag"] == "noindex"
    api = client.get("/no-such-page", headers={"accept": "application/json"})
    assert api.status_code == 404 and api.json() == {"detail": "Not Found"}


def test_a_deliberate_404_from_an_endpoint_stays_json_for_browsers_too(client):
    r = client.get("/cron/maintenance", headers={"accept": "text/html"})  # 404 "Not found." when no CRON_SECRET
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_trailing_slash_and_www_redirect_permanently(client):
    r = client.get("/about/?ref=x", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/about?ref=x"
    r = client.get("/about", headers={"host": "www.contexta.example.com"}, follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "https://contexta.example.com/about"
    assert client.get("/about", follow_redirects=False).status_code == 200
    assert client.post("/ask/", json={}, follow_redirects=False).status_code != 301  # never for POST


def test_workspace_shell_has_its_own_title_and_no_canonical(client, tmp_path):
    shell = tmp_path / "index.html"
    shell.write_text('<title>Contexta: home</title>\n    <link rel="canonical" href="__SITE_URL__/" />\n<h1>x</h1>')

    class Req:
        def __init__(self, path):
            self.app, self.headers = client.app, {}
            self.url = type("U", (), {"path": path, "scheme": "http", "netloc": "x"})()

    ws = render_shell(Req("/workspace/w1/ask"), shell).body.decode()
    assert "<title>Workspace · Contexta</title>" in ws and "canonical" not in ws
    home = render_shell(Req("/"), shell).body.decode()
    assert "<title>Contexta: home</title>" in home and 'href="https://contexta.example.com/"' in home
