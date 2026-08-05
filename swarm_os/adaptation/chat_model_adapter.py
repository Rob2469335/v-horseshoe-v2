from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict


@dataclass
class GenerateResult:
    ok: bool
    provider: str
    content: str


class ChatModelAdapter:
    def __init__(self, primary_provider: str, providers: Dict[str, Callable[[str], str]]):
        self.primary_provider = primary_provider
        self.providers = dict(providers)

    def generate(self, prompt: str, retries: int = 1) -> GenerateResult:
        # If retries == 0, test only the primary provider and do not fall back.
        if retries == 0:
            try:
                fn = self.providers.get(self.primary_provider)
                if fn is None:
                    return GenerateResult(ok=False, provider=self.primary_provider, content="no provider")
                content = fn(prompt)
                return GenerateResult(ok=True, provider=self.primary_provider, content=content)
            except Exception as e:
                return GenerateResult(ok=False, provider=self.primary_provider, content=str(e))

        providers_to_try = [self.primary_provider] + [p for p in self.providers if p != self.primary_provider]
        last_exc = None
        for provider in providers_to_try:
            try:
                fn = self.providers.get(provider)
                if fn is None:
                    continue
                content = fn(prompt)
                return GenerateResult(ok=True, provider=provider, content=content)
            except Exception as e:
                last_exc = e
                continue
        return GenerateResult(ok=False, provider=self.primary_provider, content=str(last_exc))

    def rotate_provider(self) -> str:
        # naive rotate: pick any provider not equal to primary
        for p in self.providers:
            if p != self.primary_provider:
                self.primary_provider = p
                return p
        return self.primary_provider

