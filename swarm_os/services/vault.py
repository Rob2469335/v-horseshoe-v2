from __future__ import annotations
import json
from pathlib import Path

VAULT_PATH = Path(__file__).parent.parent / "config" / "vault.json"

class Vault:
    def __init__(self):
        VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not VAULT_PATH.exists():
            VAULT_PATH.write_text("[]", encoding="utf-8")

    def list(self) -> list[str]:
        return json.loads(VAULT_PATH.read_text(encoding="utf-8"))

    def add(self, rule: str) -> list[str]:
        rules = self.list()
        if rule not in rules:
            rules.append(rule)
            VAULT_PATH.write_text(json.dumps(rules, indent=2), encoding="utf-8")
        return rules

    def remove(self, index: int) -> list[str]:
        rules = self.list()
        if 0 <= index < len(rules):
            rules.pop(index)
            VAULT_PATH.write_text(json.dumps(rules, indent=2), encoding="utf-8")
        return rules

vault = Vault()
