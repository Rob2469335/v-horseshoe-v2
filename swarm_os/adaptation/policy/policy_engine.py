from __future__ import annotations

from typing import Dict


class RemediationPolicyEngine:
    def __init__(self) -> None:
        # simple rule table
        self._rules = {
            ('system', 'restart_component'): {'permitted': False, 'reasons': ['approval required']},
            ('chat_model', 'retry_request'): {'permitted': True, 'reasons': []},
        }

    def evaluate(self, *, component: str, action: str, attempt_count: int = 1) -> Dict[str, object]:
        key = (component, action)
        if key in self._rules:
            entry = self._rules[key]
            return {'permitted': entry['permitted'], 'reasons': entry.get('reasons', [])}
        # default: require approval for unknown actions on system, allow otherwise
        if component == 'system':
            return {'permitted': False, 'reasons': ['approval required']}
        return {'permitted': True, 'reasons': []}

    def list_policies(self) -> Dict[str, Dict]:
        return {f"{c}/{a}": v for (c,a), v in self._rules.items()}

    def get_policy(self, component: str) -> Dict:
        return {k[1]: v for (k,v) in self._rules.items() if k[0] == component}

