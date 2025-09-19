import requests
from typing import Any, Dict, List, Optional
from .bayleaf_auth import TokenProvider
from ..auth.deps import Principal


class BayleafClient:
    def __init__(self, base: str, timeout: int = 15):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.tokens = TokenProvider()

    def _auth_headers(self, principal: Optional[Principal] = None) -> dict:
        token = self.tokens.get_token(principal)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None, principal: Optional[Principal] = None):
        r = requests.get(f"{self.base}{path}", headers=self._auth_headers(principal), params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---------- Token-scoped tools (no patient_id/user_id) ----------

    def current_patient_summary(self, principal: Principal) -> Dict[str, Any]:
        """Server infers patient from token."""
        data = self._get("/api/patients/profile/", principal=principal)
        return {
            "id": data.get("id") or data.get("patient_uuid"),
            "name": data.get("full_name") or data.get("name"),
            "gender": data.get("gender"),
            "age": data.get("age"),
            "active_episodes": data.get("active_episodes_count"),
            "last_updated": data.get("updated_at"),
        }

    def current_medications(self, principal: Principal) -> List[Dict[str, Any]]:
        """Server infers patient from token. No query params."""
        data = self._get("/api/medications/", principal=principal)
        results = data.get("results", data)  # support both list or paginated dict
        return [
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "dose": m.get("dose"),
                "route": m.get("route"),
                "frequency": m.get("frequency"),
                "status": m.get("status"),
            } for m in results or []
        ]


def tool_schemas() -> list[dict]:
    return [
        {
          "name": "current_patient_summary",
          "description": "Get the authenticated patient's safe, redacted summary (patient is inferred from the bearer token).",
          "parameters": { "type":"object", "properties": {}, "additionalProperties": False }
        },
        {
          "name": "current_medications",
          "description": "List active medications for the authenticated patient (patient is inferred from the bearer token).",
          "parameters": { "type":"object", "properties": {}, "additionalProperties": False }
        }
    ]
