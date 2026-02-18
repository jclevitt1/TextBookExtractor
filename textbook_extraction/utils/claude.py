"""Claude API wrapper with retry logic and token tracking."""
import time
from typing import Optional

from anthropic import Anthropic
from rich.console import Console

from ..config import Settings

console = Console()


class ClaudeClient:
    """Wrapper around Claude API with retry and token tracking."""

    def __init__(self, settings: Settings):
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.model
        self.max_retries = settings.max_retries
        self.retry_base_delay = settings.retry_base_delay
        self.default_max_tokens = settings.max_tokens

        # Cumulative tracking
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def call(
        self,
        *,
        system: str,
        user_content: list[dict] | str,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Send a message to Claude, return raw text response.

        Args:
            system: System prompt.
            user_content: Either a string or a list of content blocks
                (text, image, etc.) for the user message.
            max_tokens: Max output tokens (defaults to settings.max_tokens).
        """
        if isinstance(user_content, str):
            content = [{"type": "text", "text": user_content}]
        else:
            content = user_content

        return self._call_with_retry(system, content, max_tokens or self.default_max_tokens)

    def build_image_content(
        self,
        images: list[str],
        prompt: str,
        page_labels: Optional[list[str]] = None,
    ) -> list[dict]:
        """Build user content blocks with labeled images + a text prompt.

        Args:
            images: List of base64-encoded PNG images.
            prompt: Text prompt appended after images.
            page_labels: Optional labels prepended before each image.
        """
        content: list[dict] = []
        for i, img_b64 in enumerate(images):
            if page_labels and i < len(page_labels):
                content.append({"type": "text", "text": f"[{page_labels[i]}]"})
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64,
                },
            })
        content.append({"type": "text", "text": prompt})
        return content

    def _call_with_retry(self, system: str, content: list, max_tokens: int) -> str:
        last_exception = None
        for attempt in range(self.max_retries + 1):
            try:
                message = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": content}],
                )
                self.total_calls += 1
                input_tok = message.usage.input_tokens
                output_tok = message.usage.output_tokens
                self.total_input_tokens += input_tok
                self.total_output_tokens += output_tok

                console.print(
                    f"  [dim]API call #{self.total_calls}: "
                    f"{input_tok:,} in / {output_tok:,} out tokens[/dim]"
                )

                return message.content[0].text

            except Exception as e:
                error_str = str(e)
                retryable = any(
                    kw in error_str.lower()
                    for kw in ["529", "overloaded", "rate", "timeout", "500", "502", "503"]
                )
                if retryable and attempt < self.max_retries:
                    last_exception = e
                    delay = self.retry_base_delay * (2 ** attempt)
                    console.print(
                        f"  [yellow]Retryable error (attempt {attempt + 1}/{self.max_retries}), "
                        f"waiting {delay:.0f}s: {error_str[:100]}[/yellow]"
                    )
                    time.sleep(delay)
                    continue
                raise

        raise last_exception

    def get_usage_summary(self) -> str:
        return (
            f"API calls: {self.total_calls}, "
            f"Input tokens: {self.total_input_tokens:,}, "
            f"Output tokens: {self.total_output_tokens:,}"
        )
