# Textbook Extraction v2

Structured data extraction from textbook PDFs using an 8-worker pipeline. Each worker is independently testable via CLI and orchestrated by AWS Step Functions in production.

## Setup

```bash
cd Textbook_Extraction_v2

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# System dependency (macOS)
brew install poppler

# Configure
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY and AWS settings
```

Requires:
- Python 3.12+
- `poppler` for PDF rendering
- AWS credentials configured (`aws configure`)
- A textbook PDF uploaded to S3

## Pipeline Overview

```
W1: S3 Fetch          (I/O)           → validates PDF, gets page count
W2: TOC Raw Extract   (LLM)          → flat list of TOC entries
W3: TOC Structure     (LLM)          → nested hierarchy with self-describing keys
W4: Granularity       (programmatic)  → fan-out manifest of extraction units
W5: Granular Extract  (LLM fan-out)  → content.json per leaf section
W6: TOC Enrich        (programmatic)  → merge W5 results back into TOC
W7: Coverage Cleanup  (prog + LLM)   → detects/extracts uncovered pages
W8: Section Keys      (LLM batch)    → self-describing metadata per section
```

## Pipeline Input

Create a file `input.json`:

```json
{
  "textbook_s3_uri": "s3://your-bucket/textbooks/algebra1.pdf",
  "page_1_offset": 14,
  "toc_start_page": 3,
  "toc_end_page": 7,
  "output_s3_prefix": "s3://your-output-bucket/extractions/algebra1/"
}
```

| Field | Description |
|-------|-------------|
| `textbook_s3_uri` | S3 URI of the textbook PDF |
| `page_1_offset` | Number of PDF pages before printed page 1 (e.g., if printed page 1 is PDF page 15, offset = 14) |
| `toc_start_page` | 0-indexed PDF page where the Table of Contents begins |
| `toc_end_page` | 0-indexed PDF page where the Table of Contents ends |
| `output_s3_prefix` | S3 prefix where all outputs will be written |

## Running the Pipeline

### Full pipeline

```bash
python -m textbook_extraction run-pipeline -i input.json
```

### Stop after a specific worker

Use `--through` to run only up to a given worker. This is the recommended approach — run cheaply through W4, inspect the manifest, then decide how much to extract.

```bash
# Run W1-W4 only (no LLM cost after W3, W4 is programmatic)
python -m textbook_extraction run-pipeline -i input.json --through w4

# Run through W5 with a limited fan-out
python -m textbook_extraction run-pipeline -i input.json --through w5 --limit 3
```

### Control W5 fan-out

W5 is the most expensive step — it calls Claude once per leaf section. Use `--limit` or `--range` to control how many sections to extract.

```bash
# First 3 sections only
python -m textbook_extraction run-pipeline -i input.json --through w5 --limit 3

# Specific index range (e.g., sections 10-15)
python -m textbook_extraction run-pipeline -i input.json --through w5 --range 10-15

# Single section by index
python -m textbook_extraction run-pipeline -i input.json --through w5 --range 42
```

### Save intermediate state

Use `--state-dir` to dump the accumulated pipeline state after each worker. Useful for debugging.

```bash
python -m textbook_extraction run-pipeline -i input.json --through w4 -s ./debug/
# Creates: debug/state_after_00_input.json, debug/state_after_w1_s3_fetch.json, etc.
```

### Write final state to file

```bash
python -m textbook_extraction run-pipeline -i input.json --through w4 -o final_state.json
```

## Testing Workers Individually

Every worker can be run standalone with `run-worker`. You provide the worker's input as a JSON file.

### List available workers

```bash
python -m textbook_extraction list-workers
```

### W1: S3 Fetch

Validates the PDF exists on S3 and returns metadata.

```bash
# Create w1_input.json:
cat > w1_input.json << 'EOF'
{
  "textbook_s3_uri": "s3://your-bucket/textbooks/algebra1.pdf",
  "output_s3_prefix": "s3://your-output-bucket/extractions/algebra1/"
}
EOF

python -m textbook_extraction run-worker w1_s3_fetch -i w1_input.json
```

**Expected output:**
```json
{
  "local_path": "/tmp/your-bucket/textbooks/algebra1.pdf",
  "page_count": 412,
  "file_size_bytes": 5989184
}
```

### W2: TOC Raw Extract

Reads TOC pages and produces flat entry list. Tries text extraction first, falls back to vision.

```bash
cat > w2_input.json << 'EOF'
{
  "textbook_s3_uri": "s3://your-bucket/textbooks/algebra1.pdf",
  "toc_start_page": 3,
  "toc_end_page": 7,
  "output_s3_prefix": "s3://your-output-bucket/extractions/algebra1/"
}
EOF

python -m textbook_extraction run-worker w2_toc_raw -i w2_input.json
```

**Expected output:**
```json
{
  "toc_raw_uri": "s3://your-output-bucket/extractions/algebra1/toc_raw.json",
  "entry_count": 85
}
```

**What to check in `toc_raw.json`:**
- Every TOC entry is captured (compare against the actual PDF)
- `level` values make sense (1 for chapters, 2 for sections, 3 for topics)
- `page` numbers match what's printed in the TOC
- No duplicate or missing entries

### W3: TOC Structure

Organizes flat entries into nested hierarchy. Requires W2 output on S3.

```bash
cat > w3_input.json << 'EOF'
{
  "toc_raw_uri": "s3://your-output-bucket/extractions/algebra1/toc_raw.json",
  "output_s3_prefix": "s3://your-output-bucket/extractions/algebra1/"
}
EOF

python -m textbook_extraction run-worker w3_toc_structure -i w3_input.json
```

**Expected output:**
```json
{
  "toc_structured_uri": "s3://your-output-bucket/extractions/algebra1/toc_structured.json",
  "depth": 3,
  "level_names": ["chapter", "section", "topic"],
  "top_level_count": 12
}
```

**What to check in `toc_structured.json`:**
- Keys are self-describing (`"chapter 1"`, `"section 3"`, etc.)
- Every node has `title` and `page_range`
- `page_range` values are sensible (start < end, no overlaps)
- `level_names` match the textbook's own terminology
- Nesting depth matches the actual TOC structure

### W4: Granularity Extraction

Programmatic tree walk — no LLM cost. Requires W3 output on S3.

```bash
cat > w4_input.json << 'EOF'
{
  "toc_structured_uri": "s3://your-output-bucket/extractions/algebra1/toc_structured.json",
  "page_1_offset": 14,
  "page_count": 412,
  "output_s3_prefix": "s3://your-output-bucket/extractions/algebra1/"
}
EOF

python -m textbook_extraction run-worker w4_granularity -i w4_input.json -o manifest.json
```

**Expected output:**
```json
{
  "extraction_manifest_uri": "s3://...",
  "extraction_units": [
    {
      "title": "The Basics of Sets",
      "printed_page_range": [2, 4],
      "pdf_page_range": [16, 18],
      "output_path": "chapter 1/section 1/topic 1/",
      "path": [...]
    }
  ]
}
```

**What to check in `extraction_manifest.json`:**
- `leaf_count` matches expected number of sections
- `pdf_page_range` values are correct (printed + offset)
- `output_path` mirrors the TOC hierarchy
- No gaps between consecutive units' page ranges within a chapter
- Page ranges don't exceed `page_count`

### W5: Granular Extractor

Extracts content from one section. This is what the fan-out calls — test it with a single unit.

```bash
# Pick one extraction unit from W4's manifest and save it:
cat > w5_input.json << 'EOF'
{
  "textbook_s3_uri": "s3://your-bucket/textbooks/algebra1.pdf",
  "output_s3_prefix": "s3://your-output-bucket/extractions/algebra1/",
  "unit": {
    "title": "The Basics of Sets",
    "printed_page_range": [2, 4],
    "pdf_page_range": [16, 18],
    "output_path": "chapter 1/section 1/topic 1/",
    "path": [
      {"level": "chapter", "key": "chapter 1", "title": "Working with Real Numbers"},
      {"level": "section", "key": "section 1", "title": "Sets and Expressions"},
      {"level": "topic", "key": "topic 1", "title": "The Basics of Sets"}
    ]
  }
}
EOF

python -m textbook_extraction run-worker w5_extractor -i w5_input.json
```

**Expected output:**
```json
{
  "content_uri": "s3://.../chapter 1/section 1/topic 1/content.json",
  "title": "The Basics of Sets",
  "output_path": "chapter 1/section 1/topic 1/"
}
```

**What to check in `content.json`:**
- `title` is present and accurate
- Content matches what's on those pages in the PDF
- Math is in LaTeX notation (`$x^2$`, `$\frac{a}{b}$`)
- `examples` and `exercises` are present when the textbook has them
- Nothing is summarized or abbreviated — full content extraction

### W6: TOC Enrich

Merges W5 extraction results back into the TOC structure. Requires W3 and all W5 outputs.

```bash
cat > w6_input.json << 'EOF'
{
  "toc_structured_uri": "s3://your-output-bucket/extractions/algebra1/toc_structured.json",
  "w5_results": [...],
  "output_s3_prefix": "s3://your-output-bucket/extractions/algebra1/"
}
EOF

python -m textbook_extraction run-worker w6_toc_enrich -i w6_input.json
```

**Expected output:**
```json
{
  "toc_enriched_uri": "s3://.../toc_structured.json",
  "enriched_count": 85
}
```

**What to check in updated `toc_structured.json`:**
- Every leaf node now has a `content_uri` field pointing to its `content.json`
- All W5 extraction results are linked into the TOC hierarchy

### W7: Coverage Cleanup

Detects uncovered pages and extracts gap content. Requires W3, W4, and all W5 outputs.

```bash
cat > w7_input.json << 'EOF'
{
  "textbook_s3_uri": "s3://your-bucket/textbooks/algebra1.pdf",
  "toc_structured_uri": "s3://your-output-bucket/extractions/algebra1/toc_structured.json",
  "extraction_manifest_uri": "s3://your-output-bucket/extractions/algebra1/extraction_manifest.json",
  "page_1_offset": 14,
  "page_count": 412,
  "output_s3_prefix": "s3://your-output-bucket/extractions/algebra1/"
}
EOF

python -m textbook_extraction run-worker w7_coverage -i w7_input.json
```

**Expected output:**
```json
{
  "coverage_report_uri": "s3://.../coverage_report.json",
  "total_gaps": 3,
  "gap_results": [
    {"chapter_key": "chapter 1", "label": "additional section 1", "content_uri": "s3://...", "title": "Chapter Introduction"}
  ]
}
```

**What to check in `coverage_report.json`:**
- Every chapter is listed with `fully_covered` status
- Gap ranges make sense (chapter intros, review sections, etc.)
- `covered_pages` + gap pages = full chapter range

**What to check in gap `content.json` files:**
- Claude assigned a reasonable title
- Content matches the actual PDF pages
- Written to `chapter N/additional section N/content.json`

### W8: Section Keys Generator

Generates self-describing metadata for all content files. Requires all W5 + W7 outputs.

```bash
cat > w8_input.json << 'EOF'
{
  "output_s3_prefix": "s3://your-output-bucket/extractions/algebra1/"
}
EOF

python -m textbook_extraction run-worker w8_section_keys -i w8_input.json
```

**Expected output:**
```json
{
  "sections_processed": 130,
  "normalizations": [
    {"from": "practice_problems", "to": "exercises", "reason": "Standardized across sections"}
  ]
}
```

**What to check in updated `content.json` files:**
- `section_keys` is present in every content file
- Each field has `type`, `scalar`, and `intuition`
- Non-scalar fields have `list_element_type` and `sample_list_element`
- Field names are normalized consistently across sections

## Recommended Test Workflow

```bash
# 1. Upload a test textbook to S3
aws s3 cp algebra1.pdf s3://your-bucket/textbooks/algebra1.pdf

# 2. Create input.json with correct page_1_offset and TOC pages

# 3. Run W1-W4 (cheap — only W2 and W3 call Claude)
python -m textbook_extraction run-pipeline -i input.json --through w4 -o state_w4.json

# 4. Inspect the manifest
#    - Download extraction_manifest.json from S3
#    - Check leaf_count, page ranges, output paths
#    - Pick a few units to test

# 5. Test W5 on 2-3 sections
python -m textbook_extraction run-pipeline -i input.json --through w5 --range 0-2

# 6. Check the content.json files on S3
#    - Verify quality, completeness, LaTeX formatting
#    - If good, run more sections or the full pipeline

# 7. Full extraction (when confident)
python -m textbook_extraction run-pipeline -i input.json --through w5

# 8. Run W6 to enrich TOC with content URIs
python -m textbook_extraction run-pipeline -i input.json --through w6

# 9. Run W7 for coverage cleanup (gap detection)
python -m textbook_extraction run-pipeline -i input.json --through w7

# 10. Run W8 for section keys (full pipeline)
python -m textbook_extraction run-pipeline -i input.json
```

## Output Structure (S3)

```
s3://output-bucket/extractions/algebra1/
├── toc_raw.json                           # W2: flat TOC entries
├── toc_structured.json                    # W3: nested hierarchy (enriched by W5/W6)
├── extraction_manifest.json               # W4: fan-out manifest
├── coverage_report.json                   # W6: per-chapter coverage stats
├── chapter 1/
│   ├── section 1/
│   │   ├── topic 1/
│   │   │   └── content.json               # W5: extracted content (+ W7 section_keys)
│   │   └── topic 2/
│   │       └── content.json
│   ├── section 2/
│   │   └── ...
│   ├── additional section 1/
│   │   └── content.json                   # W6: gap content
│   └── additional section 2/
│       └── content.json
├── chapter 2/
│   └── ...
└── ...
```

## AWS Deployment

```bash
# Build and deploy with SAM
sam build
sam deploy --parameter-overrides Stage=dev AnthropicApiKey=$ANTHROPIC_API_KEY \
  --resolve-s3 --resolve-image-repos

# Start a pipeline execution via Step Functions
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-west-1:ACCOUNT:stateMachine:textbook-extraction-dev \
  --input file://input.json
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key |
| `AWS_REGION` | No | `us-west-1` | AWS region |
| `TEXTBOOK_OUTPUT_BUCKET` | No | `textbook-extraction-output` | Default output S3 bucket |
| `TEXTBOOK_MODEL` | No | `claude-opus-4-6` | Claude model to use |
| `TEXTBOOK_IMAGE_DPI` | No | `200` | PDF rendering DPI |
