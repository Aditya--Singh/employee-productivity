# Tab 1 — Document Assistant: Specification

Status: agreed with the product owner on 2026-10-14. Revised 2026-10-14. Built in seven phases
(section 9).

## 1. Purpose

A writing assistant for all PNB employees. It proofreads, rewrites, summarizes and drafts
documents, and answers questions about spreadsheets and data files. Everything runs on this
machine: the local Ollama model (default `gpt-oss:20b`), local LanguageTool, and local OCR.

## 2. Users and language

- **Users:** all employees, from branch staff to head office.
- **Language:** British English (`en-GB`) only. Non-English text is not checked; the UI says so.

## 3. Features

The tab has five modes, chosen from a mode selector at the top of the page.

| Mode | Input | Output |
|------|-------|--------|
| Proofread | Text or document | Inline suggestions to accept or reject |
| Rewrite | Text or document | Rewritten text in the chosen style |
| Summarize | Text, document or data file | Streamed summary |
| Draft | Instructions, optionally with an attached file | New document |
| Ask about data | XLSX, CSV or JSON | Multi-turn chat with computed answers |

### 3.1 Input

- **Paste text** into a text area.
- **Upload a file:** DOCX, PDF, TXT, PPTX, XLSX, CSV, JSON.
- Uploads go through the shared validator in `core/uploads.py`: extension allowlist, content
  signature check, 20 MB limit. The core validator must be extended for PPTX, XLSX, CSV and JSON
  signatures (this is a shared-code change in `core/`).
- **Document length:** up to about 20 pages, or roughly 10,000 words.
  - Summarize splits longer text into chunks and combines the chunk summaries.
  - Proofread and Rewrite reject text over the limit with a clear message.
- **Data files:** up to 10,000 rows. Larger files are rejected with a message.
- **Scanned PDFs:** if a PDF page has no extractable text, run local OCR (Tesseract) on it.
  Show a notice that OCR was used and accuracy may vary. Until phase 7 ships, such PDFs are
  rejected with a message asking for a text-based copy.

### 3.2 Proofread

- Combine two sources of suggestions:
  1. LanguageTool (`http://localhost:8120`, language `en-GB`) for spelling, grammar and
     punctuation. The language code is set in `core/config.py` as `LANGUAGETOOL_LANGUAGE`.
  2. The LLM for clarity and wording problems LanguageTool misses.
- Each suggestion has: start offset, end offset, category, short explanation, and one or more
  replacements. Suggestions from both sources are merged and de-duplicated by overlapping range.
- **UI:** the text is shown with issues underlined (colour by category). Clicking an issue shows
  a popover with the explanation and replacements. Users can accept, reject, or accept all.
- For pasted text, the result is the corrected text.
- For DOCX, PPTX and XLSX uploads, accepted changes are written back into the original file.
  Section 5 states exactly which formatting survives.

### 3.3 Rewrite

- Two styles:
  - **Formal / official:** standard bank correspondence.
  - **Polite / customer-friendly:** warmer tone for customers.
- The model must not add facts, amounts, dates, names or account numbers that are not in the
  source. The prompt states this and the tests check that numbers in the output appear in the
  input.
- Output streams into an editable result panel, shown side by side with the original.

### 3.4 Summarize

- Works on documents, pasted text and data files.
- Output: a short paragraph followed by key points. For data files, the summary is based on
  computed statistics (row count, columns, totals, notable values), not on raw rows.
- Streams as it is generated.

### 3.5 Draft

- Document types:
  - Customer letter (for example loan sanction, KYC reminder, account closure)
  - Internal memo or office note
  - Formal email
  - Presentation (PPTX)
  - PDF document
  - Excel sheet (XLSX)
  - Free-form
- The user fills a short form: document type, recipient, subject, key points, and optional
  tone. They can attach a data file whose contents are used as facts for the draft.
- Letters, memos, emails, PDF documents and free-form drafts stream as text into an editable
  panel before download.
- **Presentations:** the model produces a structured slide outline (title plus bullet points
  per slide). The backend builds a PPTX from it. The user can edit the outline before building.
- **Excel sheets:** the model produces a table definition (columns plus rows). The backend
  builds an XLSX. The model must not invent figures; empty cells are left blank for the user.
- Placeholders such as `[Customer Name]` are used for any detail the user did not give.

### 3.6 Ask about data

- Multi-turn chat about one uploaded XLSX, CSV or JSON file. Follow-up questions remember earlier
  questions in the same conversation.
- The conversation is kept in the browser and sent with each question. The server keeps no
  chat state.
- **Compute, then explain:** the model never does the arithmetic itself.
  1. The model picks one operation from a fixed allowlist and fills its parameters as JSON.
     Allowed operations: filter, group-by with sum/count/mean/min/max, sort, top-N,
     distinct values, and describe a column.
  2. The backend validates the JSON and runs the operation with pandas. No generated code is
     ever executed.
  3. The model writes a plain-English answer from the computed result. The result table is
     shown under the answer.
- If a question cannot be answered with the allowed operations, the assistant says so.
- **Proofread text cells:** for data files, the Proofread mode checks text columns only and
  writes accepted fixes back to the file.

### 3.7 Streaming

- All model text output streams to the browser as it is generated.
- The backend uses Ollama's streaming API and sends server-sent events to the frontend.
- The shared Ollama client in `core/ollama.py` needs a streaming chat method (shared-code change).
- Users can stop a response while it is streaming.

### 3.8 Model context window

Ollama's default context window is small and it silently drops the start of any prompt that
does not fit. Every request must therefore set `num_ctx` explicitly.

- **Sizing:** 20 pages is about 10,000 words, or about 14,000 tokens. Rewrite needs the full
  input and a full-length output in the same window, plus the instructions:
  14,000 + 14,000 + 2,000 = 30,000 tokens.
- **Default:** `num_ctx = 32768`, set in `core/config.py` as `OLLAMA_NUM_CTX` and overridable
  by environment variable. `gpt-oss:20b` supports this size.
- **Enforcement:** the shared Ollama client adds `num_ctx` to the `options` of every chat
  request, streaming or not. Callers cannot leave it out.
- **Guard:** before sending, the client estimates the prompt size at 1 token per 3 characters,
  a deliberately high estimate. If the prompt plus the expected output would exceed `num_ctx`,
  it raises an error instead of letting Ollama truncate. The tab turns this into the
  "document too long" message.
- **Chunking:** Summarize chunks are sized so each chunk request also stays inside `num_ctx`.
- **Rewrite sections:** Rewrite does not send the whole document at once. It rewrites a few
  paragraphs at a time (at most 4 paragraphs or about 4,000 characters per request), and the
  model must return the same number of paragraphs. Each request is small, `num_ctx` stays at
  32768, and every rewritten paragraph maps to one source paragraph for DOCX in-place edits.
  Proofread's clarity check uses the same sections.

Required tests:
- **Unit, mocked:** every chat request, streaming and non-streaming, carries `num_ctx`.
- **Unit, mocked:** a generated 20-page document, with a unique marker sentence as its last
  line, is sent to the model in full. The test checks that the marker is in the request body
  and that the guard does not reject it.
- **Unit, mocked:** a document over the limit is rejected by the guard, not sent.
- **Integration, real Ollama:** the same 20-page document with a unique code in its last line
  is sent to `gpt-oss:20b`, and the model is asked to repeat the code. The test passes only if
  the reply contains the code and Ollama's reported `prompt_eval_count` is at least the
  document's estimated token count. This test is marked `ollama` and skipped by default; run it
  with `uv run pytest -m ollama`. It must pass before phase 3 is committed.

## 4. Output and downloads

Every result has a **Copy** button. Downloads depend on the source:

| Source | Download options |
|--------|------------------|
| Pasted text or new draft | TXT, DOCX, PDF |
| Uploaded TXT | TXT, DOCX, PDF |
| Uploaded DOCX | DOCX edited in place, plus TXT and PDF |
| Uploaded PPTX | PPTX edited in place |
| Uploaded XLSX | XLSX edited in place |
| Uploaded CSV or JSON | Same format |
| Uploaded PDF | User picks a rebuilt PDF or a DOCX |
| Presentation draft | PPTX |
| Excel draft | XLSX |

- **New documents** (drafts and pasted text) get a simple text PNB header: "Punjab National
  Bank" in maroon (#A6192E) with a gold (#F2A900) rule. No official logo is used.
- Before a download that will lose formatting (section 5), the UI shows a short warning naming
  what will be lost.

## 5. What keeps formatting and what may not

"Edited in place" means the original file is opened, only the changed text is replaced, and
the file is saved again. Everything not listed as changed is left byte-for-byte as the library
reads and writes it.

### 5.1 DOCX

| Kept | May be lost or not edited |
|------|---------------------------|
| Paragraphs and runs that were not edited | Mixed formatting inside an edited span. If a fix spans several runs, for example a word that is half bold, the replacement takes the formatting of the first run. |
| Formatting of a fix that sits inside one run | Character formatting inside a rewritten paragraph. Rewrite replaces whole paragraphs, so the paragraph style is kept but bold, italic or coloured words inside it become the formatting of the first run. |
| Paragraph styles, numbering, indentation | Hyperlinks inside an edited span. Suggestions inside hyperlinks are shown but cannot be applied. |
| Headers, footers, letterhead, images, page setup | Text in text boxes, footnotes, endnotes, comments and fields such as a table of contents. This text is not checked or edited in v1. |
| Tables and their styles | Tracked changes. Files with tracked changes are rejected with a message asking the user to accept or reject them first. |

### 5.2 PPTX

| Kept | May be lost or not edited |
|------|---------------------------|
| Slide layouts, masters, themes, images, shapes | Mixed formatting inside an edited span, as for DOCX. |
| Text frames and table cells that were not edited | Fit of the text. Longer text may overflow its box; autofit is not recalculated. |
| Formatting of a fix inside one run | Text in charts, SmartArt and grouped shapes. Not checked or edited in v1. |
| Speaker notes | Speaker notes are not checked or edited in v1. |

### 5.3 XLSX

| Kept | May be lost or not edited |
|------|---------------------------|
| Cell styles, number formats, column widths, merged cells | **Charts, images and shapes.** The XLSX library drops these when it saves. If the workbook contains any, the UI warns before download and offers a CSV of the edited sheet instead. |
| Formulas | Pivot tables, slicers and macros. Macro-enabled files (`.xlsm`) are not accepted. |
| Sheets and cells that were not edited | Mixed formatting inside one cell. An edited cell takes a single style. |
| Data validation, conditional formatting | Formula cells are never edited, even if their result is text. |

### 5.4 Other formats

- **CSV:** delimiter and quoting style are detected and reused. Output is UTF-8; a byte-order
  mark is kept if the input had one.
- **JSON:** values and key order are kept. Indentation is normalised to 2 spaces.
- **TXT:** line endings are kept.
- **PDF:** layout is never kept. The rebuilt PDF keeps headings and paragraphs in a clean
  layout; the DOCX option lets users adjust and print. Tables, columns, images and the original
  letterhead are lost.

## 6. Session data and file retention

There is no server-side history in v1. It returns once bank single sign-on exists.

- **In the browser:** documents, results and data-chat conversations live only in the user's
  browser session (React state, mirrored to `sessionStorage` so a page refresh does not lose
  work). Closing the browser tab discards them.
- **On the server:** uploaded files and generated downloads are stored temporarily so they can
  be edited and downloaded. Each file has a random, unguessable id.
  - Storage: a temporary folder under a data directory outside version control (configurable,
    default `backend/data/uploads/`, git-ignored).
  - **Retention:** files are deleted automatically 4 hours after upload. A cleanup task runs on
    startup and every 15 minutes. Requests for an expired file return a clear "file expired,
    please upload again" message.
  - The user can delete their files immediately with a "Clear" button.
  - No database. The server keeps no record of who uploaded what.

## 7. Interface

- Follows the app shell: white background, maroon and gold accents, generous white space.
- Layout: mode selector at the top; input area (paste box or drop zone) on the left or top;
  result panel on the right or below.
- A small note says that work is kept only in this browser tab and uploaded files are deleted
  from the server after 4 hours.
- Clear states for empty, loading, streaming, error, file expired, and "LLM or LanguageTool
  unavailable".
- Error messages are plain English and never show stack traces.

## 8. Backend API

All routes are under `/api/document-assistant`. Exact shapes are fixed during implementation.

| Method and path | Purpose | Phase |
|-----------------|---------|-------|
| `POST /extract` | Upload a file, return extracted text or data preview and a file id | 2 |
| `POST /summarize` | Stream a summary | 2 |
| `DELETE /files/{id}` | Delete an uploaded or generated file now | 2 |
| `POST /rewrite` | Stream rewritten text | 3 |
| `POST /proofread` | Return merged suggestions for text or a file id | 3 |
| `POST /proofread/apply` | Apply accepted suggestions, return updated text or file id | 3 |
| `GET /download/{id}` | Download a result in a chosen format | 3 |
| `POST /data/ask` | One chat turn about a data file; the client sends prior turns | 4 |
| `POST /draft` | Stream a text draft, or return a slide outline or table definition | 6 |
| `POST /draft/build` | Build a PPTX or XLSX from an outline or table | 6 |

## 9. Build phases

Each phase is built, tested with `uv run pytest`, and committed before the next one starts.
A phase is not done until its tests pass and the app still starts.

| Phase | Scope | New libraries |
|-------|-------|---------------|
| 1 | **`core/` changes.** Upload validator learns PPTX, XLSX, CSV and JSON. Ollama client gets streaming chat and always sends `num_ctx` (section 3.8), with the size guard. | None |
| 2 | **Upload and Summarize.** Text extraction for TXT, DOCX, PDF (text-based), PPTX, XLSX, CSV, JSON. Temporary file store with 4-hour cleanup. Streaming Summarize, including chunking and data-file summaries. Tab UI with paste box, drop zone and mode selector. | python-docx, python-pptx, openpyxl, pandas, pypdf |
| 3 | **Rewrite and Proofread.** Both rewrite styles. LanguageTool plus LLM suggestions with inline accept and reject. Download in place for DOCX and XLSX, and TXT and DOCX output for text. The real-Ollama context test (section 3.8) must pass. | None |
| 4 | **Data chat** for XLSX, CSV and JSON. Operation allowlist, pandas execution, multi-turn chat kept in the browser. Proofread text cells. | None |
| 5 | **PPTX edits and PDF output.** Proofread and Rewrite applied in place to PPTX. Rebuilt-PDF output and the PDF-or-DOCX choice for edited PDFs. PDF download for text and DOCX results. | reportlab |
| 6 | **Draft mode.** All document types, the PNB header for new documents, PPTX and XLSX builders. | None |
| 7 | **OCR** for scanned PDFs. | pypdfium2, pytesseract, plus the Tesseract system package |

## 10. New dependencies and licences

All chosen libraries have permissive licences. Each still needs approval when its phase starts,
per CLAUDE.md.

| Library | Licence | Used for | Phase |
|---------|---------|----------|-------|
| `python-docx` | MIT | Read and write DOCX | 2 |
| `python-pptx` | MIT | Read and write PPTX | 2 |
| `openpyxl` | MIT | Read and write XLSX | 2 |
| `pandas` | BSD-3-Clause | Data operations and data summaries | 2 |
| `pypdf` | BSD-3-Clause | Extract text from PDFs | 2 |
| `reportlab` (open-source edition) | BSD | Build PDFs | 5 |
| `pypdfium2` | BSD-3-Clause or Apache-2.0 | Render PDF pages to images for OCR | 7 |
| `pytesseract` | Apache-2.0 | Call Tesseract from Python | 7 |
| Tesseract OCR (system package) | Apache-2.0 | OCR engine, installed with `apt install tesseract-ocr tesseract-ocr-eng` | 7 |

Main packages pulled in by the above, all permissive: `lxml` (BSD-3-Clause), `Pillow` (MIT-CMU),
`XlsxWriter` (BSD-2-Clause), `et-xmlfile` (MIT), `numpy` (BSD-3-Clause), `python-dateutil`
(Apache-2.0 or BSD-3-Clause), `tzdata` (Apache-2.0). Licences are re-checked from the installed
package metadata when each library is added.

Considered and rejected:

| Library | Licence | Reason |
|---------|---------|--------|
| `PyMuPDF` | AGPL-3.0 or commercial | AGPL |
| `fpdf2` | LGPL-3.0 | Not permissive; `reportlab` covers the need |
| `pdfplumber` | MIT | Not needed; `pypdf` is enough for text extraction |

LanguageTool itself is LGPL-2.1, but it runs as a separate Docker service and is not linked into
the app.

## 11. Code layout

- Backend: `backend/app/tabs/document_assistant/` with a router and separate modules for
  extraction, proofreading, rewriting, summarizing, drafting, data operations, document writers,
  OCR, and the temporary file store.
- Frontend: `frontend/src/tabs/document_assistant/`.
- Shared-code changes in `backend/app/core/` (phase 1): streaming chat, `num_ctx` and the size
  guard in the Ollama client, and new file types in the upload validator.

## 12. Testing

- Unit tests for every backend module. Ollama and LanguageTool are mocked; no network in tests.
- The real-Ollama context test in section 3.8 is the only test that calls a live service. It
  is opt-in with `-m ollama`.
- Round-trip tests: apply edits to sample DOCX, PPTX and XLSX files and check that the items
  listed as "Kept" in section 5 are unchanged.
- Data operation tests: every allowed operation, plus rejection of unknown operations and bad
  column names.
- Limit tests: over-length documents, over-size data files, empty files, scanned PDFs.
- File store tests: files older than 4 hours are deleted, newer ones are kept, expired ids
  return the "file expired" error, and "Clear" deletes immediately.
- Test fixtures are generated in the tests or placed in `samples/`. Never use `private/`.

## 13. Out of scope for v1

- Server-side history. Returns with bank single sign-on.
- Login and per-user access control.
- Languages other than English.
- Keeping the exact layout of edited PDFs.
- Running model-generated code.
- Calling any external service.

## 14. Known risks

- **Model quality:** `gpt-oss:20b` may be weaker at strict formatting (JSON outlines, operation
  choice). All structured output is validated and retried once before showing an error.
- **Speed and memory:** a 32,768-token window uses more memory and is slower than Ollama's
  default. Streaming and progress messages keep the user informed.
- **Lost work:** closing the browser tab, or waiting more than 4 hours, loses the session. The
  UI states this clearly.
- **Shared server files:** for up to 4 hours, anyone who knows a file's random id could
  download it. Ids are long and random, and the app only listens on this machine.
