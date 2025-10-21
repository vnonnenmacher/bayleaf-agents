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
        """
        Server infers patient from token. No query params.
        Returns a normalized list shape for the agent to speak about.
        """
        # NOTE: trailing slash matters in DRF routing if your project uses it
        data = self._get("/api/medications/my-medications/", principal=principal)

        items = data.get("results", data)  # support both paginated and plain list
        normalized = []
        for m in items or []:
            med = m.get("medication") or {}
            unit = m.get("dosage_unit") or {}
            freq_hours = m.get("frequency_hours")

            normalized.append({
                "id": m.get("id"),
                "medication": {
                    "id": med.get("id"),
                    "name": med.get("name"),
                    "description": med.get("description"),
                },
                "dosage": {
                    "amount": m.get("dosage_amount"),
                    "unit_code": unit.get("code"),
                    "unit_name": unit.get("name"),
                },
                "frequency_hours": freq_hours,
                "instructions": m.get("instructions"),
                "total_unit_amount": m.get("total_unit_amount"),
                # optionally add a human-readable frequency like "q8h"
                "frequency_text": f"q{freq_hours}h" if isinstance(freq_hours, int) else None,
            })
        return normalized


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
