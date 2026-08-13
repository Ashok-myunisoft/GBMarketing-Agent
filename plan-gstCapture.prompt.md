## Plan to improve GST capture reliably

### Goal
Capture more valid GST numbers before falling back to Google by making website and directory source logic stronger.

---

## 1. Improve website source discovery

- Expand the same-site page crawl beyond current hints
  - Add keywords: `gstin`, `gst-number`, `registration`, `compliance`, `certificate`, `certification`, `tax`, `invoice`, `profile`, `about-us`
- Match both link text and `href` path
- Keep the visit limit safe but allow more useful pages if GST isn’t found
- Prefer exact company pages and legal/registration documents

Files:
- `backend/services/gst_turnover_enrichment/website_source.py`

---

## 2. Improve GST extraction on website pages

- Search both rendered page text and raw HTML
- Look at table cells, metadata, and PDF link context
- Keep strict checksum validation from `gst_extraction.py`
- If GST is not found in body text, still scan raw HTML/attributes

Files:
- `backend/services/gst_turnover_enrichment/website_source.py`
- `backend/services/gst_turnover_enrichment/gst_extraction.py`

---

## 3. Improve PDF scanning

- Scan more than 3 PDFs when necessary, prioritizing documents with GST/legal hints
- Treat PDF labels and URLs containing `gst`, `certificate`, `registration`, `annual report` as higher priority
- Ensure PDF extraction doesn’t fail silently on malformed PDFs

Files:
- `backend/services/gst_turnover_enrichment/pdf_utils.py`
- `backend/services/gst_turnover_enrichment/website_source.py`

---

## 4. Strengthen directory search logic

- Add more trusted directory domains if needed
- Build better Google site-restricted queries with exact company names and common suffix variants
- Only accept pages whose URL/snippet includes GST-related terms
- Continue using directory sources only if website sources fail

Files:
- `backend/services/gst_turnover_enrichment/directory_source.py`

---

## 5. Improve scoring and tracing

- Keep strong validation with GST checksum
- Deduplicate repeated candidates from same source
- Add logs for why each page was accepted or skipped

Files:
- `backend/services/gst_turnover_enrichment/service.py`
- `backend/services/gst_turnover_enrichment/confidence.py`

---

## 6. Add regression tests

- Test GST extraction from HTML text in varied formats
- Test website link selection for new keywords and URLs
- Test directory source result processing with mocked search results

Files:
- `backend/tests/test_gst_turnover_enrichment.py` (new)

---

## Verification

1. Run `python -m pytest backend/tests`
2. Confirm website source returns GST while the old generic path fails
3. Confirm staging sample company pages now yield GST reliably

---

## Recommendation

Start with `website_source.py` first: this gives the biggest reliability gain. Then improve `directory_source.py` and add regression tests.