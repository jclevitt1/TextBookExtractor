# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Important Instructions

**DO NOT push to git remote** without explicit user permission. Always ask before running `git push`.

## Project Overview

Textbook Extraction v2 — an 8-worker pipeline that takes a textbook PDF from S3 and produces structured JSON content for every section. Part of the Pen (UsePen) platform for the textbook connection feature (MBA-52).

The pipeline runs locally via CLI or on AWS via Step Functions + Lambda (single Docker-based Lambda, dispatched by `worker` field in the event).

**All 8 workers are fully implemented.**

## Quick Setup

```bash
# Install dependencies (requires Python 3.12+)
pip install -r requirements.txt

# System dependency for PDF rendering (macOS)
brew install poppler

# Configure environment
cp .env.example .env
# Edit .env with ANTHROPIC_API_KEY (required) and optional AWS settings
```

**Note:** There is no virtual environment checked in. The parent `FiatLux/` project uses `.venv/` (not `venv/`).

## Commands

### Running the Pipeline

```bash
# Full pipeline (W1-W8)
python -m textbook_extraction run-pipeline -i input.json

# Stop after a specific worker (recommended for iterative testing)
python -m textbook_extraction run-pipeline -i input.json --through w4       # Stop after W4
python -m textbook_extraction run-pipeline -i input.json --through w5 -l 3  # W5 with only 3 units
python -m textbook_extraction run-pipeline -i input.json --through w5 -r 5-10  # W5 range: sections 5-10

# Save intermediate state (useful for debugging)
python -m textbook_extraction run-pipeline -i input.json -s ./debug_state/

# Resume from a specific worker using saved state (skips earlier workers)
python -m textbook_extraction run-pipeline -i ./debug/state_after_w3_toc_structure.json --resume w4
python -m textbook_extraction run-pipeline -i ./debug/state_after_w3_toc_structure.json --resume w4 -s ./debug/

# Write final state to file
python -m textbook_extraction run-pipeline -i input.json -o final_state.json
```

### Running Individual Workers

```bash
# List all registered workers
python -m textbook_extraction list-workers

# Run a single worker (for testing/debugging)
python -m textbook_extraction run-worker w1_s3_fetch -i w1_input.json
python -m textbook_extraction run-worker w2_toc_raw -i w2_input.json -o output.json
```

### AWS Deployment

```bash
# Build and deploy with SAM (requires SAM CLI + Docker)
sam build && sam deploy \
  --parameter-overrides Stage=dev AnthropicApiKey=$ANTHROPIC_API_KEY \
  --resolve-s3 --resolve-image-repos

# Start a pipeline execution via Step Functions
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-west-1:ACCOUNT:stateMachine:textbook-extraction-dev \
  --input file://input.json
```

## Architecture

### Pipeline: 8 Workers

```
W1 (S3 Fetch) → W2 (TOC Raw) → W3 (TOC Structure) → W4 (Granularity) →
W5 (Fan-out Extractor) → W6 (TOC Enrich) → W7 (Coverage) → W8 (Section Keys)
```

| Worker | LLM? | Purpose |
|--------|------|---------|
| W1 `w1_s3_fetch` | No | Download PDF from S3, return page count |
| W2 `w2_toc_raw` | Yes | Extract flat TOC entries (text-first, vision fallback) |
| W3 `w3_toc_structure` | Yes | Organize flat entries into nested hierarchy |
| W4 `w4_granularity` | No | Walk TOC tree, produce fan-out manifest of leaf extraction units |
| W5 `w5_extractor` | Yes | Extract content from one leaf section (runs N times in parallel) |
| W6 `w6_toc_enrich` | No | Merge W5 content_uri values back into toc_structured.json leaf nodes |
| W7 `w7_coverage` | Yes | Find pages not covered by W5, extract gap content |
| W8 `w8_section_keys` | Yes | Generate self-describing field metadata for all content files |

### Key Architectural Patterns

1. **Worker Registration**: Workers self-register via `@register_worker` decorator in `workers/__init__.py`. New workers must:
   - Subclass `BaseWorker` and set `worker_name` class variable
   - Decorate with `@register_worker`
   - Import in `__main__.py:_import_workers()` and `handlers/worker_handler.py`

2. **State Accumulation**: Workers chain via accumulated state dict. Each worker's output is stored under its short key (`w1`, `w2`, etc.). The `build_worker_event()` function in `pipeline.py` extracts the right parameters for each worker from accumulated state — this mirrors the Step Functions ASL `Parameters` blocks exactly.

3. **JSON Repair**: Workers that call Claude (W2, W3, W5, W7, W8) all use `json_repair.extract_json()` for robust parsing:
   - Try direct JSON parse
   - Try extracting from markdown code block
   - Try finding first `{` to last `}`
   - Validation + correction retries (typically 2 attempts)

4. **Text-First Vision Fallback** (W2 only): W2 tries `pdfplumber` text extraction first. If text passes heuristics (alphanumeric ratio > 0.3, has numbers, most pages non-empty), it sends text + one validation image to Claude. Otherwise falls back to full vision mode (all TOC pages as images).

5. **Per-Worker LLM Provider**: Each LLM worker can use a different provider (Anthropic Claude or Kimi) and model via env var overrides:
   ```bash
   # Global defaults
   TEXTBOOK_PROVIDER=anthropic
   TEXTBOOK_MODEL=claude-sonnet-4-5-20250929

   # Per-worker overrides (fall back to global if empty)
   W2_PROVIDER=anthropic    # TOC extraction — needs Claude for accuracy
   W5_PROVIDER=kimi         # Content fan-out — Kimi to save cost
   W5_MODEL=kimi-k2.5
   ```
   Workers call `get_client(self.settings, self.worker_name)` which resolves the override or falls back to global.

### Key Modules

- `prompts.py` — All Claude prompts centralized. **Prompt quality directly determines extraction accuracy.**
- `utils/claude.py` — `ClaudeClient` wrapper with exponential backoff retry (529, overloaded, rate limit, 5xx) and cumulative token tracking. `get_client()` factory returns Claude or Kimi client based on per-worker/global provider.
- `utils/kimi.py` — `KimiClient` drop-in replacement for `ClaudeClient`, uses OpenAI SDK pointed at Moonshot's API.
- `utils/pdf.py` — PDF→base64 PNG rendering via `pdf2image`/`poppler`, text extraction via `pdfplumber`. Images resized to max 1568px (Claude API limit).
- `utils/s3.py` — S3 read/write with lazy client init. `ensure_local_pdf()` caches downloads in `/tmp` by S3 key.
- `utils/json_repair.py` — 3-tier JSON extraction from Claude responses (see "JSON Repair" pattern above).
- `models/pipeline.py` — Pydantic models: `PipelineInput`, `ExtractionUnit`, `CoverageGap`.
- `config.py` — `Settings` via pydantic-settings, reads from env vars / `.env` file. Supports per-worker provider/model overrides.
- `pipeline.py` — Local pipeline runner with `--through`, `--resume`, `--limit`, `--range` controls. W5 fan-out runs sequentially locally (Map state with MaxConcurrency=10 on AWS).

### AWS Infrastructure

- Single Docker-based Lambda (includes poppler) dispatched by `worker` field
- Step Functions state machine (`statemachine/textbook_extraction.asl.json`) orchestrates the pipeline
- W5 uses Map state with `MaxConcurrency: 10`
- SAM template in `template.yaml`, region: us-west-1
- Lambda: 15 min timeout, 3GB memory

## Pipeline Input Format

```json
{
  "textbook_s3_uri": "s3://bucket/path/to/textbook.pdf",
  "page_1_offset": 14,
  "toc_start_page": 3,
  "toc_end_page": 7,
  "output_s3_prefix": "s3://output-bucket/extraction-run-id/"
}
```

- `page_1_offset`: number of PDF pages before printed page 1 (front matter)
- `toc_start_page` / `toc_end_page`: 0-indexed PDF page indices of the TOC

## Recommended Test Workflow

```bash
# 1. Run W1-W3 first with Claude (--through w3 -s ./debug/)
python -m textbook_extraction run-pipeline -i input.json --through w3 -s ./debug/

# 2. Inspect toc_structured.json — verify all chapters present
aws s3 cp s3://output-bucket/extraction-run-id/toc_structured.json ./debug/

# 3. Resume from W4, inspect extraction_manifest.json
python -m textbook_extraction run-pipeline -i ./debug/state_after_w3_toc_structure.json --resume w4 -s ./debug/

# 4. Test W5 on 2-3 sections, verify content quality
python -m textbook_extraction run-pipeline -i ./debug/state_after_w4_granularity.json --resume w5 --range 0-2

# 5. Full run from W4 onward (when confident)
python -m textbook_extraction run-pipeline -i ./debug/state_after_w3_toc_structure.json --resume w4 -s ./debug/
```

**Why this workflow?**
- W2/W3 are cheap (only 2 LLM calls total for TOC extraction)
- W4 is free (programmatic tree walk)
- W5 is expensive (N LLM calls, one per section) — test on small sample first
- Resume feature lets you iterate quickly without re-running earlier workers

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key |
| `KIMI_API_KEY` | No | — | Kimi API key (if using Kimi provider) |
| `TEXTBOOK_PROVIDER` | No | `anthropic` | Global default: `anthropic` or `kimi` |
| `TEXTBOOK_MODEL` | No | `claude-opus-4-6` | Global default model name |
| `W{N}_PROVIDER` | No | — | Per-worker provider override (N = 2,3,5,7,8) |
| `W{N}_MODEL` | No | — | Per-worker model override (N = 2,3,5,7,8) |
| `AWS_REGION` | No | `us-west-1` | AWS region |
| `TEXTBOOK_OUTPUT_BUCKET` | No | `textbook-extraction-output` | Default output S3 bucket |
| `TEXTBOOK_IMAGE_DPI` | No | `200` | PDF rendering DPI |

## Key Lessons (Debugging Notes)

- **Kimi truncates large TOC extraction** — always use Claude for W2/W3. Kimi is viable for W5 fan-out.
- **W2 page=0 poisons W3** — if W2 outputs `"page": 0` for entries, W3 cannot compute page ranges. The prompt now instructs omitting `"page"` entirely when no number is printed.
- **W3 "Continued" merging** — TOCs spanning multiple pages may produce "Chapter X — Continued" entries. W3 prompt now handles merging these.
- **W5 content structure** — prompt explicitly forbids page-level grouping; content must be organized by logical boundaries (definitions, examples, exercises).

## Troubleshooting

### `poppler-utils` not found
```bash
brew install poppler  # macOS
apt-get install poppler-utils  # Ubuntu/Debian
```

### `ANTHROPIC_API_KEY` not set
Create `.env` file in project root:
```bash
ANTHROPIC_API_KEY=sk-ant-xxx
```

### S3 permissions issues
Ensure your AWS credentials have `s3:GetObject` on the input bucket and `s3:PutObject` on the output bucket.

### JSON parsing failures
If a worker repeatedly fails to parse Claude's JSON response:
1. Check the prompt in `prompts.py` — make sure JSON schema is clear
2. Try a different model (Opus is more reliable than Sonnet for complex JSON)
3. Check `json_repair.py` — the 3-tier strategy should handle most cases

### Worker not found
If `list-workers` doesn't show your new worker:
1. Make sure it's decorated with `@register_worker`
2. Import it in `__main__.py:_import_workers()` and `handlers/worker_handler.py`

## See Also

- `README.md` — Full usage guide with detailed examples for each worker
- `workers_schema.md` — Complete I/O contracts for all 8 workers with JSON schemas
- `statemachine/textbook_extraction.asl.json` — Step Functions state machine definition
