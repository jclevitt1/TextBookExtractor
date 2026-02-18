"""Shared data models for the extraction pipeline."""
from pydantic import BaseModel


class PipelineInput(BaseModel):
    """Top-level input to start a textbook extraction."""
    textbook_s3_uri: str
    page_1_offset: int
    toc_start_page: int
    toc_end_page: int
    output_s3_prefix: str


class ExtractionUnit(BaseModel):
    """One leaf-level unit to extract (produced by W4, consumed by W5)."""
    title: str
    printed_page_range: list[int]  # [start, end]
    pdf_page_range: list[int]      # [start, end]
    output_path: str               # e.g. "chapter 1/section 1/topic 1/"
    path: list[dict]               # breadcrumb from root to leaf


class CoverageGap(BaseModel):
    """An uncovered page range within a chapter (produced by W6)."""
    pdf_page_range: list[int]
    label: str  # e.g. "additional section 1"
