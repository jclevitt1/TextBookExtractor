"""S3 read/write utilities."""
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import boto3
from rich.console import Console

console = Console()

_s3_client = None


def _get_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    """Parse s3://bucket/key into (bucket, key)."""
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Not an S3 URI: {s3_uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def build_s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def download_file(s3_uri: str, local_path: str) -> str:
    """Download a file from S3 to a local path. Returns the local path."""
    bucket, key = parse_s3_uri(s3_uri)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    console.print(f"  [dim]Downloading s3://{bucket}/{key} -> {local_path}[/dim]")
    _get_client().download_file(bucket, key, local_path)
    return local_path


def upload_file(local_path: str, s3_uri: str) -> str:
    """Upload a local file to S3. Returns the S3 URI."""
    bucket, key = parse_s3_uri(s3_uri)
    console.print(f"  [dim]Uploading {local_path} -> s3://{bucket}/{key}[/dim]")
    _get_client().upload_file(local_path, bucket, key)
    return s3_uri


def read_json(s3_uri: str) -> dict:
    """Download and parse a JSON file from S3."""
    bucket, key = parse_s3_uri(s3_uri)
    response = _get_client().get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def write_json(data: dict | list, s3_uri: str) -> str:
    """Serialize data as JSON and upload to S3. Returns the S3 URI."""
    bucket, key = parse_s3_uri(s3_uri)
    body = json.dumps(data, indent=2, ensure_ascii=False)
    console.print(f"  [dim]Writing JSON -> s3://{bucket}/{key}[/dim]")
    _get_client().put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"), ContentType="application/json")
    return s3_uri


def ensure_local_pdf(s3_uri: str) -> str:
    """Download PDF to /tmp if not already cached. Returns local path.

    Uses a deterministic path based on the S3 key so repeated calls
    within the same Lambda invocation reuse the cached file.
    """
    bucket, key = parse_s3_uri(s3_uri)
    local_path = f"/tmp/{bucket}/{key}"
    if os.path.exists(local_path):
        console.print(f"  [dim]PDF cached at {local_path}[/dim]")
        return local_path
    return download_file(s3_uri, local_path)


def head_object(s3_uri: str) -> dict:
    """HEAD an S3 object. Returns metadata dict."""
    bucket, key = parse_s3_uri(s3_uri)
    return _get_client().head_object(Bucket=bucket, Key=key)
