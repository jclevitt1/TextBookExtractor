# Workers Schema - I/O Contracts

## Pipeline Input

```json
{
  "textbook_s3_uri": "s3://bucket/path/to/textbook.pdf",
  "page_1_offset": 14,
  "toc_start_page": 3,
  "toc_end_page": 7
}
```

- `textbook_s3_uri`: S3 URI pointing to the textbook PDF
- `page_1_offset`: Number of PDF pages before printed page 1 (PDF page index of printed page 1)
- `toc_start_page`: PDF page index where the TOC begins
- `toc_end_page`: PDF page index where the TOC ends

---

## Worker 1: S3 Fetch

**LLM**: No. Pure I/O.

**Purpose**: Download textbook PDF from S3 to local filesystem, mirroring S3 path structure.

**Input**:
```json
{
  "textbook_s3_uri": "s3://bucket/path/to/textbook.pdf"
}
```

**Output**:
```json
{
  "local_path": "/tmp/bucket/path/to/textbook.pdf",
  "page_count": 412,
  "file_size_bytes": 5989184
}
```

**Notes**:
- Local path mirrors S3 URI structure for traceability
- Returns page count (via pdfplumber) so downstream workers know bounds

---

## Worker 2: TOC Raw Extract

**LLM**: Yes (text-first with vision fallback).

**Purpose**: Read the TOC pages and produce a flat, line-by-line JSON representation of every TOC entry.

**Input**:
```json
{
  "local_path": "/tmp/bucket/path/to/textbook.pdf",
  "toc_start_page": 3,
  "toc_end_page": 7
}
```

**Mode 1 - Text-first** (try this first):
- Use pdfplumber to extract raw text from TOC pages
- If text is clean and parseable, pass text to LLM for structuring
- Send one TOC page as image for validation ("does this text match what you see?")

**Mode 2 - Vision fallback**:
- If text extraction yields garbage (scanned PDF, bad encoding), fall back to full vision
- Render TOC pages as images, send to Claude

**Output**: `toc_raw.json`
```json
{
  "entries": [
    { "title": "Chapter 1 — Working with Real Numbers", "page": 2, "level": 1 },
    { "title": "Section 1.1 — Sets and Expressions", "page": 2, "level": 2 },
    { "title": "Topic 1.1.1 — The Basics of Sets", "page": 2, "level": 3 },
    { "title": "Topic 1.1.2 — Subsets of the Real Numbers", "page": 5, "level": 3 }
  ]
}
```

**Notes**:
- Line-by-line. No hierarchy logic. Just what's on the page.
- `level` is the visual nesting depth as it appears in the TOC (indentation, font size, etc.)

---

## Worker 3: TOC Structure

**LLM**: Yes.

**Purpose**: Take `toc_raw.json`, produce `toc_structured.json`. Organize flat entries into a nested hierarchy with self-describing keys. Determine what the textbook calls its levels (using the book's own terminology).

**Input**: `toc_raw.json`

**Output**: `toc_structured.json`

### Key format

Every key is `"<level_name> <number>"` — self-describing, no ambiguity. Every node has required fields `title` and `page_range`. Children are nested directly inside their parent.

```json
{
  "textbook_title": "California Algebra I",
  "structure": {
    "depth": 3,
    "level_names": ["chapter", "section", "topic"]
  },
  "toc": {
    "chapter 1": {
      "title": "Working with Real Numbers",
      "page_range": [2, 66],
      "section 1": {
        "title": "Sets and Expressions",
        "page_range": [2, 13],
        "topic 1": {
          "title": "The Basics of Sets",
          "page_range": [2, 4]
        },
        "topic 2": {
          "title": "Subsets of the Real Numbers",
          "page_range": [5, 6]
        },
        "topic 3": {
          "title": "Unions and Intersections",
          "page_range": [7, 8]
        }
      },
      "section 2": {
        "title": "The Real Number System",
        "page_range": [14, 36],
        "topic 1": {
          "title": "The Number System",
          "page_range": [14, 15]
        },
        "topic 2": {
          "title": "Identities and Inverses",
          "page_range": [16, 17]
        }
      }
    },
    "chapter 2": {
      "title": "Single Variable Linear Equations",
      "page_range": [67, 136],
      "section 1": {
        "title": "Algebra Basics",
        "page_range": [67, 75],
        "topic 1": {
          "title": "...",
          "page_range": [67, 69]
        }
      }
    }
  }
}
```

### Common node structure

Every level node, at any depth, has the same required shape:

| Field | Required | Description |
|-------|----------|-------------|
| `title` | Yes | Title of this unit as printed in the textbook |
| `page_range` | Yes | `[start, end]` printed page numbers |
| `<level_name> N` | Children | Nested children using `"<level_name> <number>"` keys |

### Granularity rule

Level names are capped at 3. Extraction happens at the deepest named level.

- 3 levels → chapter/section/topic (extract per topic)
- 2 levels → chapter/lesson (extract per lesson)
- 1 level → chapter (extract per chapter)

If the TOC has more than 3 levels of depth, the JSON captures ALL of them. But only 3 get named in `level_names`. Extra depth is nested inside the leaf extraction unit — it's preserved in the data but not a separate extraction target.

**Notes**:
- LLM determines hierarchy from raw entries, resolves ambiguities (inconsistent depth, unnumbered entries, mixed patterns)
- Level names come from the textbook's own terminology, not hardcoded
- The JSON structure itself does NOT limit depth — only `level_names` is capped at 3

---

## Worker 4: Granularity Extraction

**LLM**: No. Programmatic.

**Purpose**: Walk the `toc_structured.json` tree to the leaf level, produce the fan-out manifest of extraction units with computed page ranges and output paths.

**Input**:
```json
{
  "toc_structured": "toc_structured.json",
  "page_1_offset": 14,
  "page_count": 412
}
```

**Algorithm**:
1. Read `structure.level_names` to determine extraction depth (min of 3, total dimensions)
2. Walk the TOC tree using insertion order (do NOT assume integer keys — keys could be "1", "A", "1a", etc.)
3. At each leaf node, compute:
   - `pdf_page_range` = `printed_page_range` + `page_1_offset`
   - `output_path` = folder path mirroring TOC hierarchy (e.g., `chapter 1/section 1/topic 1/`)
4. Collect the `path` breadcrumb from root to leaf

**Output**: `extraction_manifest.json`
```json
{
  "granularity": {
    "depth": 3,
    "level_names": ["chapter", "section", "topic"],
    "leaf_level": "topic",
    "leaf_count": 127
  },
  "extraction_units": [
    {
      "title": "The Basics of Sets",
      "printed_page_range": [2, 4],
      "pdf_page_range": [16, 18],
      "output_path": "chapter 1/section 1/topic 1/",
      "path": [
        { "level": "chapter", "key": "chapter 1", "title": "Working with Real Numbers" },
        { "level": "section", "key": "section 1", "title": "Sets and Expressions" },
        { "level": "topic", "key": "topic 1", "title": "The Basics of Sets" }
      ]
    },
    {
      "title": "Subsets of the Real Numbers",
      "printed_page_range": [5, 6],
      "pdf_page_range": [19, 20],
      "output_path": "chapter 1/section 1/topic 2/",
      "path": [
        { "level": "chapter", "key": "chapter 1", "title": "Working with Real Numbers" },
        { "level": "section", "key": "section 1", "title": "Sets and Expressions" },
        { "level": "topic", "key": "topic 2", "title": "Subsets of the Real Numbers" }
      ]
    }
  ]
}
```

**Notes**:
- Purely programmatic — no LLM needed. Tree structure from Worker 3 is already resolved.
- Key ordering is preserved from insertion order in `toc_structured.json` (no integer parsing)
- Each unit's page range: starts at its own page, ends at (next unit's page - 1)
- Last unit in a chapter: ends at (next chapter's first page - 1), or last page of book
- `pdf_page_range` = `printed_page_range` + `page_1_offset`
- `output_path` mirrors the TOC hierarchy as a folder structure
- This output IS the fan-out manifest for Worker 5

---

## Worker 5: Granular Extractor

**LLM**: Yes.

**Purpose**: Extract content from one leaf-level unit of the textbook. Structure the output as closely to how the textbook presents it as possible.

**Input**: One extraction unit from the manifest + local PDF path.
```json
{
  "local_path": "/tmp/bucket/path/to/textbook.pdf",
  "title": "Unions and Intersections",
  "pdf_page_range": [21, 22],
  "output_path": "chapter 1/section 2/topic 3/",
  "path": [
    { "level": "chapter", "key": "chapter 1", "title": "Working with Real Numbers" },
    { "level": "section", "key": "section 2", "title": "The Real Number System" },
    { "level": "topic", "key": "topic 3", "title": "Unions and Intersections" }
  ]
}
```

**Output**: `{output_path}/content.json`

Folder structure mirrors the TOC hierarchy:
```
chapter 1/
  section 1/
    topic 1/
      content.json
    topic 2/
      content.json
  section 2/
    topic 1/
      content.json
    topic 3/
      content.json
```

Example `content.json`:
```json
{
  "title": "Unions and Intersections",
  "definitions": [
    { "term": "Union", "symbol": "$A \\cup B$", "definition": "The set of all elements in $A$ or $B$ or both." }
  ],
  "examples": [
    { "label": "Example 1", "problem": "Find $A \\cup B$ where...", "solution": "..." }
  ],
  "exercises": [
    { "number": "1", "text": "Find the union of...", "answer": "$\\{1,2,3,4,5\\}$" }
  ]
}
```

**Required fields**: `title`

**Recommended fields**: `examples`, `exercises` — include when the textbook has them for this section.

**Optional fields**: Claude adds whatever it finds on those pages — `definitions`, `theorems`, `proofs`, `vocabulary`, `guided_practice`, `applications`, `key_concepts`, `narrative`, `primary_sources`, etc. Structure the content as the textbook presents it.

**Post-extraction**: After writing `content.json`, the TOC structured JSON (`toc_structured.json`) is updated with the S3 URI of each leaf's content file:
```json
{
  "topic 1": {
    "title": "The Basics of Sets",
    "page_range": [2, 4],
    "content_uri": "s3://bucket/.../chapter 1/section 1/topic 1/content.json"
  }
}
```

**Notes**:
- Each invocation processes ONE leaf unit (typically 2-10 pages)
- Receives full `path` context so Claude knows where this unit sits in the hierarchy
- All math in LaTeX notation
- These run in parallel (fan-out from Worker 4's extraction_units)
- No `section_keys` here — that's Worker 8's job
- Claude should mirror the textbook's own structure, not impose a generic schema

---

## Worker 6: TOC Enrich

**LLM**: No. Pure programmatic.

**Purpose**: Merge W5's `content_uri` values back into `toc_structured.json` leaf nodes. After W5 extracts content and writes `content.json` files, this worker reads W5's results and sets `content_uri` on each matching leaf node in the TOC.

**Input**:
```json
{
  "toc_structured_uri": "s3://bucket/.../toc_structured.json",
  "w5_results": [
    { "content_uri": "s3://bucket/.../chapter 1/section 1/topic 1/content.json", "title": "The Basics of Sets", "output_path": "chapter 1/section 1/topic 1/" },
    { "content_uri": "s3://bucket/.../chapter 1/section 1/topic 2/content.json", "title": "Subsets of the Real Numbers", "output_path": "chapter 1/section 1/topic 2/" }
  ],
  "output_s3_prefix": "s3://output-bucket/extraction-run-id/"
}
```

**Output**:
```json
{
  "toc_structured_uri": "s3://bucket/.../toc_structured.json",
  "sections_enriched": 127,
  "sections_total": 130,
  "sections_skipped": 3
}
```

**Notes**:
- Runs immediately after W5 fan-out completes
- Walks the TOC tree recursively, matching leaf nodes to W5 results by `output_path`
- Writes the enriched TOC back to the same `toc_structured_uri` in S3
- Downstream workers (W7 coverage, W8 section_keys) benefit from having `content_uri` on leaf nodes
- Skipped nodes = leaf nodes with no matching W5 result (e.g., W5 was run with `--limit` or `--range`)

---

## Worker 7: Coverage Cleanup

**LLM**: Yes (for extracting gap content).

**Purpose**: Detect pages within a top-level chapter that were NOT covered by any Worker 5 leaf extraction. Extract content from those gap pages so nothing is silently lost.

**Why this exists**: Textbooks often have content outside named TOC sections — chapter introductions, "putting it all together" reviews, cumulative exercises, transition pages, appendices within chapters, etc. The TOC-driven leaf extraction from Worker 5 may not cover every page in a chapter.

**Input**:
```json
{
  "local_path": "/tmp/bucket/path/to/textbook.pdf",
  "toc_structured": "toc_structured.json",
  "extraction_manifest": "extraction_manifest.json",
  "page_1_offset": 14,
  "page_count": 412
}
```

**Algorithm**:
1. For each top-level chapter, collect its full `page_range`
2. Collect all `pdf_page_range` values from its leaf-level extractions (Worker 5 outputs)
3. Compute the set difference — which pages in the chapter range are NOT covered?
4. Group uncovered pages into consecutive ranges
5. If no gaps → no-op for this chapter
6. For each consecutive gap range → extract content via LLM (same approach as Worker 5)

**Output**: `coverage_report.json` + additional `content.json` files

`coverage_report.json`:
```json
{
  "chapters": [
    {
      "key": "chapter 1",
      "title": "Working with Real Numbers",
      "chapter_page_range": [16, 80],
      "covered_pages": [16, 17, 18, 19, 20, 23, 24, ...],
      "gaps": [
        { "pdf_page_range": [21, 22], "label": "additional section 1" },
        { "pdf_page_range": [75, 80], "label": "additional section 2" }
      ],
      "fully_covered": false
    },
    {
      "key": "chapter 2",
      "title": "Single Variable Linear Equations",
      "chapter_page_range": [81, 150],
      "covered_pages": [81, 82, ...],
      "gaps": [],
      "fully_covered": true
    }
  ]
}
```

Gap extractions are placed in the chapter folder:
```
chapter 1/
  section 1/
    topic 1/
      content.json
    topic 2/
      content.json
  additional section 1/
    content.json          ← gap pages [21, 22]
  additional section 2/
    content.json          ← gap pages [75, 80]
```

The TOC structured JSON is also updated with these additional sections:
```json
{
  "chapter 1": {
    "title": "Working with Real Numbers",
    "page_range": [2, 66],
    "section 1": { ... },
    "additional section 1": {
      "title": "(auto-detected from content)",
      "page_range": [7, 8],
      "content_uri": "s3://bucket/.../chapter 1/additional section 1/content.json"
    }
  }
}
```

**Notes**:
- Runs AFTER Worker 6 (TOC Enrich)
- Gap detection is programmatic; gap content extraction uses LLM (same as Worker 5)
- Gap extractions could fan-out in parallel if multiple gaps exist
- `additional section N` numbering is sequential per chapter
- The LLM extraction for gaps uses the same prompt approach as Worker 5 — `title` required, structure as the textbook presents it
- Claude should attempt to title the additional section based on what it finds on those pages

---

## Worker 8: Section Keys Generator

**LLM**: Yes.

**Purpose**: Take the raw content files from ALL Worker 5 + Worker 7 runs, generate self-describing `section_keys` metadata for each. Ensures consistency across sections by seeing the full picture.

**Input**: All `content.json` files from the output folder tree (both Worker 5 leaf extractions and Worker 7 gap extractions).

**Output**: Updated `content.json` files with `section_keys` added, OR separate `keys.json` files alongside each `content.json`.

`section_keys` describes each field in the content file:

```json
{
  "title": "Unions and Intersections",
  "section_keys": {
    "title": {
      "type": "str",
      "scalar": true,
      "intuition": "Title of the section."
    },
    "definitions": {
      "type": "list",
      "scalar": false,
      "intuition": "Formal definitions introduced in this section.",
      "list_element_type": "json",
      "sample_list_element": {
        "term": "Union",
        "symbol": "$A \\cup B$",
        "definition": "The set of all elements in $A$ or $B$ or both."
      }
    },
    "exercises": {
      "type": "list",
      "scalar": false,
      "intuition": "List of exercises given by the textbook.",
      "list_element_type": "json",
      "sample_list_element": {
        "number": "1",
        "text": "Find the union of $\\{1,2,3\\}$ and $\\{3,4,5\\}$.",
        "answer": "$\\{1,2,3,4,5\\}$"
      }
    }
  },
  "definitions": [...],
  "exercises": [...]
}
```

**Notes**:
- Runs AFTER Worker 7 (sees everything: Worker 5 leaf content + Worker 7 gap content)
- Sees all sections → can normalize inconsistent field names across sections (e.g., "exercises" vs "practice_problems" → standardize)
- `scalar`: true = single value (str, int, bool), false = collection (list, object)
- Non-scalar fields include `list_element_type` + `sample_list_element` to describe shape
- Each content file becomes fully self-describing — no external schema needed

---

## Orchestration (Step Functions)

```
Pipeline Input
    │
    ▼
┌────────────────────┐
│  Worker 1          │
│  S3 Fetch          │
│  (pure I/O)        │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│  Worker 2          │
│  TOC Raw Extract   │
│  (LLM: text/vision)│
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│  Worker 3          │
│  TOC Structure     │
│  (LLM)             │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│  Worker 4          │
│  Granularity       │
│  Extraction        │
│  (programmatic)    │
└────────┬───────────┘
         │
         ▼
┌────────────────────────────┐
│  Fan-out (Map state)       │
│  One Worker 5 per leaf     │
├────────┬────────┬──────────┤
│  W5    │  W5    │  W5      │  ... N parallel
│ t1.1.1 │ t1.1.2 │ t1.1.3  │
└────────┴────────┴──────────┘
         │
         ▼ (collect all)
┌────────────────────┐
│  Worker 6          │
│  TOC Enrich        │
│  (programmatic)    │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│  Worker 7          │
│  Coverage Cleanup  │
│  (programmatic     │
│   + LLM for gaps)  │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│  Worker 8          │
│  Section Keys      │
│  Generator         │
│  (LLM: batch pass) │
└────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│  Final output:                   │
│  - toc_structured.json           │
│    (enriched with content_uri    │
│     + additional sections)       │
│  - chapter N/section N/topic N/  │
│      content.json (with keys)    │
│  - chapter N/additional section/ │
│      content.json (with keys)    │
│  - coverage_report.json          │
└──────────────────────────────────┘
```

- Step Functions handles retry per worker
- Fan-out parallelism for Worker 5 bounded by Claude API rate limits
- Worker 6 enriches TOC with content_uri from W5 results (no LLM)
- Worker 7 detects gaps, extracts content from uncovered pages (may fan-out for multiple gaps)
- Worker 8 runs once after Worker 7 (sees full picture including gap content for consistency)
- Each worker independently testable via CLI
- State visible in Step Functions console
