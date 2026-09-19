# Provider capability verification

Checked 2026-09-19. Public documentation establishes request/response contracts;
a successful catalogue call establishes catalogue access, not invoice quality.

- [fal GOT-OCR2 API](https://fal.ai/models/fal-ai/got-ocr/v2/api): accepts
  `input_image_urls`, `do_format`, `multi_page`; returns `outputs`, a list of
  strings. The adapter renders each page at 200 DPI and submits it separately
  using a data URI. It keeps every response and represents each returned string
  as one `other` block. No coordinates, confidence or tables are invented.
  [Direct synchronous endpoint](https://fal.ai/docs/documentation/model-apis/inference/synchronous)
  is `https://fal.run/fal-ai/got-ocr/v2`. FAL_KEY is not available locally;
  invoice execution is untested.
- [TypeSafe Jev HTTP API](https://docs.typesafe.ai/api): `/v1/systemone`,
  `state`, `model`, and named Choice questions with `criteria`; response has
  `answers`, `model`, and `usage`. Authenticated GET `/v1/models` returned 200
  and aliases `jev-latest` and `jev-preview`; saved in `jev-catalogue.json`.
  Live calls also established a maximum of 255 choices per question (HTTP 400
  on oversized requests). The bounded-group adapter preserves all source
  candidates and adds a final Choice over group winners.
  JEV_API_KEY from the existing local environment works. The returned alias
  is recorded honestly; an immutable weight revision is not available in that
  catalogue. Live provisional development results are stored separately.
- [DeepSeek JSON output](https://api-docs.deepseek.com/guides/json_mode/):
  `response_format: {"type":"json_object"}` plus an explicit JSON instruction.
  The direct adapter validates the resulting object locally. DEEPSEEK_API_KEY
  is unavailable; no direct invoice run is claimed.
- [Helmcode models](https://dev.helmcode.com/docs/models): public catalogue
  advertises `deepseek-v4-flash` and an OpenAI-compatible text endpoint.
  GET `https://api.helmcode.com/v1/models` without credentials returned 401.
  HELMCODE_API_KEY is unavailable. Account model availability is **unverified**;
  the Helmcode adapter refuses execution until the authenticated catalogue is
  saved and contains the requested DeepSeek ID. It does not assume JSON-mode
  support from generic compatibility claims.
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) remains an OSS candidate,
  not an implemented or evaluated adapter here. Native structured outputs from
  other parsers can be imported after explicit canonical conversion. Mark only
  actually supported structure capabilities; retain complete native output.

No credential values are in these records. Costs remain unknown unless supplied
with defensible billing/usage evidence. Monthly subscription access does not imply
zero cost per invoice. No marketing quality claims are benchmark results.
