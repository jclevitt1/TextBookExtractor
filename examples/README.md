# Testing Individual Workers

This directory contains example inputs for testing individual workers in isolation.

## Available Examples

### W8 Section Keys Test
**File:** `w8_test.json`

Tests W8 (Section Keys Generator) in isolation. Useful for:
- Testing homework template generation without running full pipeline
- Fixing W8 failures (e.g., API credit issues) without re-running W1-W7

**Usage:**
```bash
python -m textbook_extraction run-worker w8_section_keys -i examples/w8_test.json
```

**Requirements:**
- W5 must have already run and generated `content.json` files in S3
- Output S3 prefix must match the extraction you want to process

## Testing Any Worker

### List Available Workers
```bash
python -m textbook_extraction list-workers
```

### Run a Single Worker
```bash
python -m textbook_extraction run-worker <worker_name> -i <input_file>
```

### Example: Test W1 (S3 Fetch)
```json
{
  "worker": "w1_s3_fetch",
  "textbook_s3_uri": "s3://usepen-textbook-data/textbooks/algebra1.pdf",
  "output_s3_prefix": "s3://textbook-extraction-dev-909126987514/extractions/test/"
}
```

```bash
python -m textbook_extraction run-worker w1_s3_fetch -i examples/w1_test.json
```

### Example: Test W5 (Extractor)
You need an extraction unit from W4's manifest:
```json
{
  "worker": "w5_extractor",
  "textbook_s3_uri": "s3://usepen-textbook-data/textbooks/algebra1.pdf",
  "output_s3_prefix": "s3://textbook-extraction-dev-909126987514/extractions/test/",
  "text_only_mode": false,
  "unit": {
    "title": "The Basics of Sets",
    "printed_page_range": [2, 4],
    "pdf_page_range": [14, 16],
    "output_path": "chapter 1/section 1.1/topic 1.1.1/",
    "path": [
      {"level": "chapter", "key": "chapter 1", "title": "Working with Real Numbers"},
      {"level": "section", "key": "section 1.1", "title": "Sets and Expressions"},
      {"level": "topic", "key": "topic 1.1.1", "title": "The Basics of Sets"}
    ]
  }
}
```

## When to Test Individual Workers

- **Debugging:** When a specific worker fails in the pipeline
- **Development:** When making changes to a worker's logic
- **Cost saving:** Re-run only the failed worker instead of entire pipeline
- **Experimentation:** Test different parameters/prompts without full pipeline cost

## Worker Dependencies

Some workers require outputs from previous workers:
- **W2** requires: W1 (downloads PDF first)
- **W3** requires: W2 (toc_raw.json)
- **W4** requires: W3 (toc_structured.json)
- **W5** requires: W1 (PDF), W4 (extraction units)
- **W6** requires: W3 (toc_structured.json), W5 results
- **W7** requires: W1, W3, W4 outputs
- **W8** requires: W5/W6/W7 content files in S3

If prerequisites aren't met, the worker will fail. Run earlier workers first or use saved state from previous pipeline runs.
