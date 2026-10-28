"""Build the public legal pages and PDFs from the supplied policy text.

Run with a Python environment containing reportlab. Generated assets are checked
in, so production does not need reportlab or a document build step.
"""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import shutil

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
POLICIES = json.loads((ROOT / "legal/policies.json").read_text(encoding="utf-8"))
PDF_NAMES = {
    "terms-of-service": "NorthStar-Locate-Terms-of-Service.pdf",
    "privacy-policy": "NorthStar-Locate-Privacy-Policy.pdf",
    "refund-and-cancellation-policy": "NorthStar-Locate-Refund-and-Cancellation-Policy.pdf",
}
EMAIL = "rochelledevot@gmail.com"


def linked(text: str) -> str:
    return escape(text).replace(EMAIL, f'<a href="mailto:{EMAIL}">{EMAIL}</a>')


def translate(key: str, default: str) -> str:
    return f'<span data-i18n="{key}">{escape(default)}</span>'


def page(title: str, body: str) -> str:
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Read NorthStar Locate's terms, privacy policy, and refund and cancellation policy.">
  <title>{escape(title)} | NorthStar Locate</title>
  <link rel="stylesheet" href="/legal.css?v=20261029-1">
  <script src="/locales.js?v=20261029-legal"></script>
  <script src="/i18n.js?v=20261029-1"></script>
</head>
<body>
  <a class="skip-link" href="#main">{translate('legal.skip', 'Skip to content')}</a>
  <header class="site-header shell">
    <a class="brand" href="/" data-language-link><span class="brand-mark" aria-hidden="true"></span>Northstar Locate</a>
    <nav class="header-links" aria-label="Legal navigation">
      <a href="/" data-language-link>{translate('legal.home', 'Back to website')}</a>
      <a href="/legal.html" data-language-link>Legal</a>
      <select data-language-select data-i18n-aria-label="nav.language" aria-label="Choose language"><option value="en">English</option></select>
    </nav>
  </header>
  {body}
  <footer class="shell footer">
    <span>© 2026 Northstar Locate · Devren Consu Inc</span>
    <a href="mailto:{EMAIL}">{EMAIL}</a>
  </footer>
</body>
</html>
'''


def make_html() -> None:
    folder = PUBLIC / "legal"
    folder.mkdir(parents=True, exist_ok=True)
    cards = []
    for index, policy in enumerate(POLICIES, 1):
        slug = policy["slug"]
        title = policy["shortTitle"]
        pdf = f'/legal/{PDF_NAMES[slug]}'
        paragraphs = []
        contents = []
        for number, block in enumerate(policy["blocks"]):
            kind = block["type"]
            if kind == "heading":
                anchor = f"section-{number}"
                contents.append(f'<a href="#{anchor}">{escape(block["text"])}</a>')
                paragraphs.append(f'<h2 id="{anchor}">{escape(block["text"])}</h2>')
            elif kind == "paragraph":
                paragraphs.append(f'<p>{linked(block["text"])}</p>')
            elif kind == "bullets":
                paragraphs.append('<ul>' + ''.join(f'<li>{linked(item)}</li>' for item in block["items"]) + '</ul>')
            else:
                raise ValueError(f"Unknown block type: {kind}")
        related = ''.join(
            f'<a href="/legal/{other["slug"]}.html" data-language-link{chr(32) + "aria-current=\"page\"" if other == policy else ""}>{escape(other["shortTitle"])}</a>'
            for other in POLICIES
        )
        body = f'''<main id="main" class="shell policy-layout">
    <aside class="policy-sidebar">
      <p class="eyebrow">Legal</p>
      <nav class="policy-navigation" aria-label="Policies">{related}</nav>
      <a class="button" href="{pdf}" download>{translate('legal.download', 'Download PDF')} <span aria-hidden="true">↓</span></a>
      <p class="small">{translate('legal.english', 'Policy documents are provided in English.')}</p>
    </aside>
    <article class="policy" lang="en" data-policy="{slug}">
      <header class="document-header"><h1>{escape(policy['title'])}</h1><p class="updated">{escape(policy['updated'])}</p></header>
      <details class="contents"><summary>{translate('legal.contents', 'On this page')}</summary><nav aria-label="Table of contents">{''.join(contents)}</nav></details>
      <div class="policy-text">{''.join(paragraphs)}</div>
      <div class="document-end"><a href="/legal.html" data-language-link>← {translate('legal.all', 'All legal documents')}</a><a href="{pdf}" download>{translate('legal.download', 'Download PDF')} ↓</a></div>
    </article>
  </main>'''
        (folder / f"{slug}.html").write_text(page(title, body), encoding="utf-8", newline="\n")
        cards.append(f'''<article class="policy-card">
      <span class="card-number">0{index}</span><p class="format">PDF · ENGLISH</p>
      <h2 lang="en"><a href="/legal/{slug}.html" data-language-link>{escape(title)}</a></h2>
      <p class="card-description">{translate('legal.description.' + slug, {
          'terms-of-service': 'Service rules, consent requirements, subscriptions, and billing.',
          'privacy-policy': 'Information collection, location data, retention, and your choices.',
          'refund-and-cancellation-policy': 'Cancellation, automatic renewals, and the refund policy.',
      }[slug])}</p>
      <p class="small" lang="en">{escape(policy['updated'])}</p>
      <div class="card-actions"><a class="button" href="/legal/{slug}.html" data-language-link>{translate('legal.read', 'Read policy')} <span aria-hidden="true">→</span></a><a href="{pdf}" download>{translate('legal.download', 'Download PDF')} ↓</a></div>
    </article>''')
    body = f'''<main id="main" class="shell hub">
    <div class="hub-heading"><p class="eyebrow">NORTHSTAR LOCATE</p><h1>Legal</h1>
      <p class="intro">{translate('legal.intro', 'Our terms and policies, together in one place. Read online or download a copy.')}</p>
      <p class="small">{translate('legal.english', 'Policy documents are provided in English.')}</p>
    </div>
    <div class="policy-cards">{''.join(cards)}</div>
    <section class="contact"><div><h2>{translate('legal.questions', 'Questions about our policies?')}</h2><p>Devren Consu Inc</p></div><a href="mailto:{EMAIL}">{EMAIL} <span aria-hidden="true">↗</span></a></section>
  </main>'''
    (PUBLIC / "legal.html").write_text(page("Legal", body), encoding="utf-8", newline="\n")


def make_pdf(policy: dict, destination: Path) -> None:
    ink = colors.HexColor("#14234a")
    blue = colors.HexColor("#2f68f5")
    muted = colors.HexColor("#66728b")
    body = ParagraphStyle("Body", fontName="Helvetica", fontSize=10.1, leading=14.1,
                          textColor=ink, spaceAfter=5, alignment=TA_LEFT)
    heading = ParagraphStyle("Section", parent=body, fontName="Helvetica-Bold",
                             fontSize=10.6, leading=14, spaceBefore=7.5, spaceAfter=3,
                             keepWithNext=True)
    bullet = ParagraphStyle("Bullet", parent=body, leftIndent=12, firstLineIndent=0,
                            bulletIndent=1, spaceAfter=4)
    title = ParagraphStyle("Title", parent=body, fontName="Helvetica-Bold",
                           fontSize=21, leading=26, spaceAfter=9, keepWithNext=True)
    date = ParagraphStyle("Date", parent=body, textColor=muted, fontSize=9,
                          spaceAfter=16, keepWithNext=True)
    story = [Paragraph(escape(policy["title"]), title), Paragraph(escape(policy["updated"]), date)]
    for block in policy["blocks"]:
        if block["type"] == "heading":
            story.append(Paragraph(escape(block["text"]), heading))
        elif block["type"] == "paragraph":
            story.append(Paragraph(linked(block["text"]), body))
        else:
            story.extend(Paragraph(linked(item), bullet, bulletText="•") for item in block["items"])
    def frame(canvas, document):
        canvas.saveState()
        width, height = document.pagesize
        canvas.setFillColor(blue)
        canvas.rect(48, height - 39, 28, 3, fill=1, stroke=0)
        canvas.setStrokeColor(colors.HexColor("#d9e4ef"))
        canvas.line(48, 43, width - 48, 43)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(muted)
        canvas.drawString(48, 29, "NorthStar Locate | Devren Consu Inc")
        canvas.drawRightString(width - 48, 29, f"Page {document.page}")
        canvas.restoreState()
    doc = SimpleDocTemplate(str(destination), pagesize=(8.5 * inch, 11 * inch),
                            leftMargin=48, rightMargin=48, topMargin=55, bottomMargin=56,
                            title=policy["title"], author="Devren Consu Inc",
                            subject=policy["shortTitle"], pageCompression=1)
    doc.build(story, onFirstPage=frame, onLaterPages=frame)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-output", type=Path, help="Also copy the PDFs to this output directory")
    args = parser.parse_args()
    make_html()
    generated = []
    for policy in POLICIES:
        destination = PUBLIC / "legal" / PDF_NAMES[policy["slug"]]
        make_pdf(policy, destination)
        if args.pdf_output:
            args.pdf_output.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, args.pdf_output / destination.name)
        generated.append(str(destination.resolve()))
    print(json.dumps({"pdfs": generated, "html_documents": 4}, indent=2))


if __name__ == "__main__":
    main()
