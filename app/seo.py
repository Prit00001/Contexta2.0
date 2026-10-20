"""What search engines and AI answer engines read: robots.txt, sitemap.xml, llms.txt, a server-rendered
/about page (with FAQ structured data), and the app shell with absolute canonical / Open Graph URLs.

The React app renders in the browser, and most AI crawlers (GPTBot, ClaudeBot, PerplexityBot) do not
run JavaScript - so everything a crawler should understand about Contexta is plain HTML here.
Absolute URLs come from APP_SITE_URL; until that is set, from the request's (forwarded) host."""

from __future__ import annotations

import html
import json
from functools import lru_cache
from pathlib import Path
from string import Template

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from data.documents import DOCUMENTS

router = APIRouter(include_in_schema=False)

# Bump when the public pages' content changes (sitemap <lastmod>, "Updated" on /about).
CONTENT_UPDATED = "2026-10-20"
NAME = "Contexta"
TAGLINE = "cited answers to machine learning and NLP research questions"
DESCRIPTION = (
    "Contexta answers machine learning and NLP research questions from a curated library of 25 landmark "
    "papers, live arXiv results or a PDF you upload. Every citation is checked against the passages it "
    "came from, and sentences without a source are marked."
)
_LOCAL = ("localhost", "127.0.0.1", "0.0.0.0")


def site_url(request: Request) -> str:
    """The public origin, e.g. https://contexta.example.com (no trailing slash)."""
    configured = request.app.state.settings.site_url.rstrip("/")
    if configured and not any(h in configured for h in _LOCAL):
        return configured
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip() or "https"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host.split(',')[0].strip()}"


def library_titles() -> list[str]:
    return [d["title"] for d in DOCUMENTS]


def price_text(request: Request) -> str:
    label = request.app.state.settings.billing_price_label
    return f"then {label}" if label else "then a monthly subscription per member"


def faq(request: Request) -> list[tuple[str, str]]:
    """Questions people (and answer engines) ask about Contexta, with self-contained answers."""
    n = len(DOCUMENTS)
    return [
        (
            "What is Contexta?",
            f"Contexta is a research assistant for machine learning and NLP. You ask a question and it answers "
            f"only from sources it retrieved - a library of {n} landmark papers, live arXiv results, or a PDF you "
            "upload - with a numbered citation on each claim.",
        ),
        (
            "How does Contexta avoid made-up citations?",
            "After the answer is written, every [n] marker is checked against the sources that were actually "
            "retrieved. Markers pointing at nothing are removed and reported, and sentences with no citation are "
            "underlined so you can see what is unsupported. If nothing relevant is found, Contexta says so "
            "instead of answering.",
        ),
        (
            "Is Contexta free?",
            "Paper search is free and needs no account. Team workspaces for your own documents are free for one "
            f"hour of active use, {price_text(request)}.",
        ),
        (
            "Which papers are in the library?",
            "Landmark ML and NLP papers including " + ", ".join(library_titles()[:8]) + f" - {n} in total, listed "
            "on the About page. Questions beyond the library use live arXiv search.",
        ),
        (
            "Can I ask questions about my own documents?",
            "Yes. Upload a PDF on the main page for a one-off question, or create a workspace to upload PDF, DOCX, "
            "TXT and Markdown files that your team can question together, with roles and API keys.",
        ),
        (
            "What happens to my documents and questions?",
            "Workspace documents are visible only to members of that workspace. Saved conversations are visible "
            "only to their author and deleted 30 days after the last message. To write an answer, the relevant "
            "passages and your question are sent to the language model provider.",
        ),
    ]


def _json_ld(data: dict) -> str:
    # Inside <script>, "</" would end the element early.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def app_json_ld(base: str) -> str:
    return _json_ld(
        {
            "@context": "https://schema.org",
            "@type": "WebApplication",
            "name": NAME,
            "url": f"{base}/",
            "description": DESCRIPTION,
            "applicationCategory": "EducationalApplication",
            "operatingSystem": "Any (web browser)",
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "INR", "description": "Paper search is free"},
        }
    )


# ---- crawler files ----


@router.api_route("/robots.txt", methods=["GET", "HEAD"])
async def robots(request: Request):
    base = site_url(request)
    return PlainTextResponse(
        "# Public pages are open to search engines and AI answer engines alike.\n"
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /workspace\n"
        "Disallow: /workspaces/\n"
        "Disallow: /billing/\n"
        "Disallow: /auth/\n"
        "Disallow: /metrics\n"
        "Disallow: /*?q=\n"  # shared question links run a live answer: don't let crawlers spend quota
        f"\nSitemap: {base}/sitemap.xml\n"
    )


@router.api_route("/sitemap.xml", methods=["GET", "HEAD"])
async def sitemap(request: Request):
    base = html.escape(site_url(request))
    urls = "".join(
        f"<url><loc>{base}{path}</loc><lastmod>{CONTENT_UPDATED}</lastmod></url>" for path in ("/", "/about")
    )
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n',
        media_type="application/xml",
    )


@router.api_route("/llms.txt", methods=["GET", "HEAD"])
async def llms_txt(request: Request):
    """The llmstxt.org convention: a short Markdown map of the site for language models."""
    base = site_url(request)
    papers = "\n".join(f"- {t}" for t in library_titles())
    qa = "\n\n".join(f"### {q}\n{a}" for q, a in faq(request))
    return PlainTextResponse(
        f"# {NAME}\n\n> {DESCRIPTION}\n\n"
        "## Pages\n"
        f"- [Ask a question]({base}/): the search page; `{base}/?q=your+question` opens it with a question\n"
        f"- [About, pricing and FAQ]({base}/about)\n"
        f"- [API reference]({base}/docs): `POST /ask` returns an answer, its sources and a citation check\n\n"
        f"## Questions and answers\n\n{qa}\n\n"
        f"## Papers in the library\n{papers}\n",
        media_type="text/markdown; charset=utf-8",
    )


# ---- /about ----

_ABOUT = Template((Path(__file__).parent / "templates" / "about.html").read_text(encoding="utf-8"))


@router.api_route("/about", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def about(request: Request):
    base = site_url(request)
    questions = faq(request)
    faq_ld = _json_ld(
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in questions
            ],
        }
    )
    esc = html.escape
    return HTMLResponse(
        _ABOUT.substitute(
            base=esc(base),
            description=esc(DESCRIPTION),
            tagline=esc(TAGLINE),
            updated=CONTENT_UPDATED,
            n_papers=len(DOCUMENTS),
            price=esc(price_text(request)),
            papers="".join(f"<li>{esc(t)}</li>" for t in library_titles()),
            faq="".join(f"<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in questions),
            app_ld=app_json_ld(base),
            faq_ld=faq_ld,
        )
    )


# ---- the React app shell ----


@lru_cache(maxsize=4)
def _shell(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def render_shell(request: Request, index_html: Path) -> HTMLResponse:
    """The built index.html with absolute URLs filled in. Workspace pages are private: noindex."""
    base = site_url(request)
    page = _shell(str(index_html)).replace("__SITE_URL__", html.escape(base))
    page = page.replace("__APP_LD__", app_json_ld(base))
    headers = {"X-Robots-Tag": "noindex"} if request.url.path.startswith("/workspace") else {}
    return HTMLResponse(page, headers=headers)
