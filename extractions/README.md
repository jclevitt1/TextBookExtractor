# Textbook Extraction Configs

This directory contains input configurations for processing textbooks.

## Available Textbooks

### Algebra 1
- **Full run:** `algebra1.json` (vision mode, all sections)
- **Preview:** `algebra1_preview.json` (vision mode, 1 section only)

### Law Textbook
- **Full run:** `law_textbook.json` (text-only mode)

## Running Extractions

### Locally (with CLI)
```bash
# Full extraction
python -m textbook_extraction run-pipeline -i extractions/algebra1.json

# Preview mode (first section only)
python -m textbook_extraction run-pipeline -i extractions/algebra1_preview.json

# With state saving for debugging
python -m textbook_extraction run-pipeline -i extractions/algebra1.json -s ./debug/
```

### On AWS (Step Functions)
```bash
# Deploy first (if changes were made)
./deploy.sh

# Start execution
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-west-1:909126987514:stateMachine:textbook-extraction-dev \
  --input file://extractions/algebra1.json \
  --region us-west-1
```

## Adding a New Textbook

1. Copy the template:
   ```bash
   cp templates/input_template.json extractions/your_textbook.json
   ```

2. Edit the config:
   - `textbook_s3_uri`: S3 path to your PDF
   - `page_1_offset`: Number of PDF pages before printed page 1 (front matter)
   - `toc_start_page`: 0-indexed PDF page where TOC starts
   - `toc_end_page`: 0-indexed PDF page where TOC ends
   - `output_s3_prefix`: S3 path for extraction outputs
   - `preview_mode`: `true` for testing (1 section only), `false` for full extraction
   - `text_only_mode`: `true` for text-dominant books (law, novels), `false` for math/visual books

3. Test with preview mode first:
   ```bash
   python -m textbook_extraction run-pipeline -i extractions/your_textbook.json
   ```

4. Once verified, run full extraction

## Modes

### Preview Mode (`preview_mode: true`)
- Processes only the first section (configurable with `preview_section_count`)
- Generates homework template from that one section
- Use this to verify extraction quality before processing entire textbook
- **Recommended workflow:** Preview → verify → full run

### Text-Only Mode (`text_only_mode: true`)
- Extracts text with pdfplumber instead of rendering images
- 10-20x cheaper for text-dominant books (law, novels, history)
- Use for textbooks without complex math/diagrams

### Vision Mode (`text_only_mode: false`, default)
- Renders PDF pages as images, sends to Claude Vision API
- Better for math-heavy textbooks with equations, diagrams, graphs
- W2 (TOC extraction) already uses text-first with vision fallback

## Output Structure

After extraction, outputs are written to S3:
```
s3://textbook-extraction-dev-{account}/extractions/{textbook_id}/
├── toc_raw.json                  # W2 output: flat TOC entries
├── toc_structured.json           # W3 output: nested hierarchy
├── extraction_manifest.json      # W4 output: fan-out plan
├── coverage_report.json          # W7 output: gap detection
├── homework_template.json        # W8 output: post-it note template
└── chapter/section/topic/
    └── content.json              # W5/W6/W7 output: extracted content
```

## Troubleshooting

**Q: How do I find page_1_offset and TOC page numbers?**
A: Open the PDF and check:
- `page_1_offset`: Count how many PDF pages come before printed page 1 (cover, copyright, etc.)
- `toc_start_page`: PDF page number (0-indexed) where the table of contents starts
- `toc_end_page`: PDF page number (0-indexed) where it ends

**Q: Should I use text-only or vision mode?**
A:
- Text-only: Law, novels, history (mostly text)
- Vision: Math, science, engineering (equations, diagrams)

**Q: What if W8 fails with "credit balance too low"?**
A: Add credits at https://console.anthropic.com/settings/plans, then re-run W8 only:
```bash
python -m textbook_extraction run-worker w8_section_keys -i examples/w8_test.json
```
