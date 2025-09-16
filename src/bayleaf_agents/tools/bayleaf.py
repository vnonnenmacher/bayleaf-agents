import requests
from typing import Any, Dict, List, Optional
from ..config import settings


class BayleafClient:
    def __init__(self, base: str, token: str, timeout: int = 15):
        self.base = base.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}" if token else "",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.timeout = timeout

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None):
        r = requests.get(f"{self.base}{path}", headers=self.headers, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---- Tool implementations (read-only) ----

    def patient_summary(self, patient_id: str) -> Dict[str, Any]:
        data = self._get(f"/api/patients/{patient_id}/")
        return {
            "id": data.get("id") or data.get("patient_uuid"),
            "name": data.get("full_name") or data.get("name"),
            "gender": data.get("gender"),
            "age": data.get("age"),
            "active_episodes": data.get("active_episodes_count"),
            "last_updated": data.get("updated_at"),
        }

    def list_medications(self, patient_id: str) -> List[Dict[str, Any]]:
        data = self._get("/api/medications/", params={"patient": patient_id, "limit": 100})
        return [
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "dose": m.get("dose"),
                "route": m.get("route"),
                "frequency": m.get("frequency"),
                "status": m.get("status"),
            } for m in data.get("results", [])
        ]


def tool_schemas() -> list[dict]:
    return [
        {
          "name": "patient_summary",
          "description": "Get safe, redacted patient summary.",
          "parameters": {
            "type":"object",
            "properties": { "patient_id": {"type": "string"}},
            "required": ["patient_id"]
          }
        },
        {
          "name": "list_medications",
          "description": "List active medications for a patient.",
          "parameters": {
            "type": "object",
            "properties": { "patient_id": {"type": "string"} },
            "required": ["patient_id"]
          }
        }
    ]
