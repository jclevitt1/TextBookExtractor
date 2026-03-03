"""Kimi K2.5 API client — uses the openai SDK since Kimi's API speaks the same protocol.

Drop-in replacement for ClaudeClient — same call() and build_image_content() interface.
"""
import time
from typing import Optional

from openai import OpenAI
from rich.console import Console

from ..config import Settings

console = Console()


class KimiClient:
    """Wrapper around Kimi K2.5 API with retry and token tracking.

    Kimi's API uses the OpenAI chat completions protocol, so we use the
    openai SDK pointed at Moonshot's base URL with a Kimi API key.
    """

    def __init__(self, settings: Settings, model_override: str | None = None):
        self.client = OpenAI(
            api_key=settings.kimi_api_key,
            base_url=settings.openai_compat_base_url,
        )
        self.model = model_override or settings.model
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
        """Send a message, return raw text response.

        Same signature as ClaudeClient.call().
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

        Uses the OpenAI-style image_url format (data URI with base64).
        """
        content: list[dict] = []
        for i, img_b64 in enumerate(images):
            if page_labels and i < len(page_labels):
                content.append({"type": "text", "text": f"[{page_labels[i]}]"})
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_b64}",
                },
            })
        content.append({"type": "text", "text": prompt})
        return content

    def _call_with_retry(self, system: str, content: list, max_tokens: int) -> str:
        last_exception = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": content},
                    ],
                )
                self.total_calls += 1
                input_tok = getattr(response.usage, "prompt_tokens", 0) or 0
                output_tok = getattr(response.usage, "completion_tokens", 0) or 0
                self.total_input_tokens += input_tok
                self.total_output_tokens += output_tok

                console.print(
                    f"  [dim]API call #{self.total_calls}: "
                    f"{input_tok:,} in / {output_tok:,} out tokens[/dim]"
                )

                return response.choices[0].message.content

            except Exception as e:
                error_str = str(e)
                retryable = any(
                    kw in error_str.lower()
                    for kw in ["429", "rate", "timeout", "500", "502", "503", "529", "overloaded"]
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
