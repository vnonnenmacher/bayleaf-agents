import uuid, structlog
from datetime import date
import requests
from typing import Any, Dict, List, Optional
from .bayleaf_auth import TokenProvider
from ..auth.deps import Principal


class BayleafClient:
    def __init__(self, base: str, timeout: int = 15):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.tokens = TokenProvider()
        self.log = structlog.get_logger("bayleaf_client")

    def _auth_headers(
        self,
        principal: Optional[Principal] = None,
        use_auth: bool = True,
        bearer_token: Optional[str] = None,
    ) -> dict:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        elif use_auth:
            token = self.tokens.get_token(principal)
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _safe_json(self, r: requests.Response) -> Any:
        try:
            return r.json()
        except ValueError:
            return r.text

    def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        principal: Optional[Principal] = None,
        *,
        use_auth: bool = True,
        bearer_token: Optional[str] = None,
    ):
        r = requests.get(
            f"{self.base}{path}",
            headers=self._auth_headers(principal, use_auth, bearer_token),
            params=params,
            timeout=self.timeout,
        )
        try:
            r.raise_for_status()
        except requests.HTTPError:
            return {
                "error": "request_failed",
                "status_code": r.status_code,
                "details": self._safe_json(r),
            }
        return self._safe_json(r)

    def _post(
        self,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        principal: Optional[Principal] = None,
        *,
        use_auth: bool = True,
        bearer_token: Optional[str] = None,
    ):
        r = requests.post(
            f"{self.base}{path}",
            headers=self._auth_headers(principal, use_auth, bearer_token),
            json=json_data or {},
            timeout=self.timeout,
        )
        try:
            r.raise_for_status()
        except requests.HTTPError:
            return {
                "error": "request_failed",
                "status_code": r.status_code,
                "details": self._safe_json(r),
            }
        return self._safe_json(r) or {}

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

    # ---------- Appointment flow tools ----------

    def create_patient(
        self,
        first_name: str,
        email: str,
        is_adult: bool,
        principal: Optional[Principal] = None,
    ) -> Dict[str, Any]:
        """
        Registration endpoint does not require auth. Uses static mock data for required fields beyond name/email.
        """
        payload = {
            "first_name": first_name,
            "last_name": "Patient",
            "email": email,
            "password": "password123",
            "birth_date": "1992-07-21",
            # optional fields from the API are omitted intentionally
        }
        # debug: log the outgoing payload for troubleshooting 400s
        print("create_patient payload:", payload)  # keep simple stdout for now
        self.log.info("create_patient_payload", payload=payload)
        data = self._post("/api/patients/register/", json_data=payload, principal=principal, use_auth=False)
        return {
            "id": data.get("id") or data.get("patient_uuid"),
            "first_name": data.get("first_name") or first_name,
            "email": data.get("email") or email,
            "is_adult": data.get("is_adult", is_adult),
            "status": data.get("status") or "created",
        }

    def list_available_slots(
        self,
        *,
        principal: Optional[Principal] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        service_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        # Always send an end_date; default to the same day as start_date (or today).
        start = start_date or date.today().isoformat()
        end = end_date or start
        params["start_date"] = start
        params["end_date"] = end
        if service_id is not None:
            params["services"] = service_id
        else:
            # keep default service id 1 for now to match backend expectations
            params["services"] = 1

        data = self._get("/api/appointments/available-slots/", params=params or None, principal=principal, use_auth=False)
        # Debug logging to help diagnose malformed responses
        self.log.info("list_available_slots_response", params=params, data=data)

        # handle explicit error shape from _get
        if isinstance(data, dict) and data.get("error"):
            return [{"error": data.get("details") or data.get("error")}]

        professionals_map: Dict[Any, Dict[str, Any]] = {}
        if isinstance(data, dict):
            for prof in data.get("professionals") or []:
                if isinstance(prof, dict) and prof.get("id") is not None:
                    professionals_map[prof["id"]] = prof

        # normalize list of slots
        items: List[Dict[str, Any]] = []
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict):
                    items.append(entry)
        elif isinstance(data, dict):
            raw_items = data.get("results", [])
            if isinstance(raw_items, list):
                items = [entry for entry in raw_items if isinstance(entry, dict)]
            else:
                return [{"error": "unexpected_response", "details": raw_items}]
        else:
            return [{"error": "unexpected_response", "details": data}]

        slots: List[Dict[str, Any]] = []
        for s in items or []:
            provider_id = s.get("professional_id") or s.get("provider") or s.get("provider_id")
            provider_info = professionals_map.get(provider_id) if provider_id in professionals_map else None
            provider_payload = None
            if isinstance(provider_info, dict):
                provider_payload = {
                    "id": provider_info.get("id"),
                    "first_name": provider_info.get("first_name"),
                    "last_name": provider_info.get("last_name"),
                    "email": provider_info.get("email"),
                    "avatar": provider_info.get("avatar"),
                }
            elif provider_id:
                provider_payload = {"id": provider_id}

            slots.append({
                "id": s.get("id") or s.get("slot_id"),
                "start": s.get("start") or s.get("start_time"),
                "end": s.get("end") or s.get("end_time"),
                "location": s.get("location"),
                "provider": provider_payload or s.get("provider") or s.get("provider_name") or s.get("professional_id"),
                "service": s.get("service", {}) or {"id": s.get("service_id"), "name": s.get("service_name")},
                "type": s.get("type") or s.get("visit_type"),
            })
        return slots

    def list_available_professionals(
        self,
        *,
        principal: Optional[Principal] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        service_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns professionals with their available slots. Endpoint does not require auth.
        """
        params: Dict[str, Any] = {"services": service_id or 1}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date

        data = self._get(
            "/api/appointments/available-professionals/",
            params=params,
            principal=principal,
            use_auth=False,
        )
        self.log.info("list_available_professionals_response", params=params, data=data)

        if isinstance(data, dict) and data.get("error"):
            return [{"error": data.get("details") or data.get("error")}]
        if not isinstance(data, list):
            return [{"error": "unexpected_response", "details": data}]

        normalized: List[Dict[str, Any]] = []
        for prof in data:
            if not isinstance(prof, dict):
                continue
            slots: List[Dict[str, Any]] = []
            for slot in prof.get("slots") or []:
                if not isinstance(slot, dict):
                    continue
                slots.append({
                    "id": slot.get("slot_id"),
                    "shift_id": slot.get("shift_id"),
                    "start": slot.get("start_time"),
                    "end": slot.get("end_time"),
                    "service_id": slot.get("service_id"),
                })
            normalized.append({
                "id": prof.get("id"),
                "first_name": prof.get("first_name"),
                "last_name": prof.get("last_name"),
                "email": prof.get("email"),
                "avatar": prof.get("avatar"),
                "slots": slots[:10],  # cap to avoid overloading the model
            })
        return normalized

    def list_available_specializations(
        self,
        *,
        principal: Optional[Principal] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        service_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns specializations with their upcoming slots. Endpoint does not require auth.
        """
        params: Dict[str, Any] = {"services": service_id or 1}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date

        data = self._get(
            "/api/appointments/available-specializations/",
            params=params,
            principal=principal,
            use_auth=False,
        )
        self.log.info("list_available_specializations_response", params=params, data=data)

        if isinstance(data, dict) and data.get("error"):
            return [{"error": data.get("details") or data.get("error")}]
        if not isinstance(data, list):
            return [{"error": "unexpected_response", "details": data}]

        normalized: List[Dict[str, Any]] = []
        for spec in data:
            if not isinstance(spec, dict):
                continue
            slots: List[Dict[str, Any]] = []
            for slot in spec.get("slots") or []:
                if not isinstance(slot, dict):
                    continue
                doctor = slot.get("doctor") or {}
                slots.append({
                    "id": slot.get("slot_id"),
                    "shift_id": slot.get("shift_id"),
                    "start": slot.get("start_time"),
                    "end": slot.get("end_time"),
                    "service_id": slot.get("service_id"),
                    "doctor": {
                        "id": doctor.get("id"),
                        "first_name": doctor.get("first_name"),
                        "last_name": doctor.get("last_name"),
                        "email": doctor.get("email"),
                    } if isinstance(doctor, dict) else None,
                })
            normalized.append({
                "id": spec.get("id"),
                "name": spec.get("name"),
                "slots": slots[:10],
            })
        return normalized

    def chat_token(self, email: str, password: str) -> Dict[str, Any]:
        payload = {"email": email, "password": password}
        return self._post("/api/users/chat-token/", json_data=payload, use_auth=False)

    def book_appointment(
        self,
        slot_id: str,
        access_token: Optional[str] = None,
        principal: Optional[Principal] = None,
    ) -> Dict[str, Any]:
        payload = {
            "service_slot_id": slot_id,
        }
        self.log.info(
            "book_appointment_request",
            slot_id=slot_id,
            use_access_token=bool(access_token),
            payload=payload,
        )
        data = self._post(
            "/api/appointments/book/",
            json_data=payload,
            principal=principal if not access_token else None,
            use_auth=access_token is None,
            bearer_token=access_token,
        )
        self.log.info("book_appointment_response", data=data)
        return {
            "appointment_id": data.get("id") or data.get("appointment_id"),
            "slot_id": data.get("slot_id", slot_id) or data.get("service_slot_id", slot_id),
            "status": data.get("status") or data.get("state") or "scheduled",
            "payment_status": data.get("payment_status") or data.get("payment", {}).get("status"),
            "start": data.get("start"),
            "end": data.get("end"),
            "location": data.get("location"),
        }


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
        },
        {
          "name": "create_patient",
          "description": "Register a new patient after confirming they are 18+ using first name and email. Other required fields are filled with static test values.",
          "parameters": {
            "type":"object",
            "properties": {
              "first_name": { "type":"string", "minLength": 1 },
              "email": { "type":"string", "format": "email" },
              "is_adult": { "type":"boolean", "description":"True if the patient confirmed they are 18 or older." }
            },
            "required": ["first_name","email","is_adult"],
            "additionalProperties": False
          }
        },
        {
          "name": "list_available_slots",
          "description": "List appointment slots available for booking. start_date/end_date are optional; if omitted, the backend returns the next 30 days. Use the patient's preferred date range and service when provided.",
          "parameters": {
            "type":"object",
            "properties": {
              "start_date": { "type":"string", "description":"Start date in ISO format (YYYY-MM-DD)." },
              "end_date": { "type":"string", "description":"End date in ISO format (YYYY-MM-DD)." },
              "service_id": { "type":"integer", "description":"Service ID to filter slots (maps to query param 'services')." }
            },
            "additionalProperties": False
          }
        },
        {
          "name": "list_available_professionals",
          "description": "List professionals with available slots. Use this to offer provider-specific options.",
          "parameters": {
            "type":"object",
            "properties": {
              "start_date": { "type":"string", "description":"Start date in ISO format (YYYY-MM-DD). Optional; default search window is provided by the backend." },
              "end_date": { "type":"string", "description":"End date in ISO format (YYYY-MM-DD). Optional; default search window is provided by the backend." },
              "service_id": { "type":"integer", "description":"Service ID to filter (maps to query param 'services'). Defaults to 1." }
            },
            "additionalProperties": False
          }
        },
        {
          "name": "list_available_specializations",
          "description": "List specializations with available slots. Helpful when the patient prefers a specialty over a specific doctor.",
          "parameters": {
            "type":"object",
            "properties": {
              "start_date": { "type":"string", "description":"Start date in ISO format (YYYY-MM-DD). Optional." },
              "end_date": { "type":"string", "description":"End date in ISO format (YYYY-MM-DD). Optional." },
              "service_id": { "type":"integer", "description":"Service ID to filter (maps to query param 'services'). Defaults to 1." }
            },
            "additionalProperties": False
          }
        },
        {
          "name": "chat_token",
          "description": "Obtain a chat access token using the patient's email. Use the default password if none is provided.",
          "parameters": {
            "type":"object",
            "properties": {
              "email": { "type":"string", "format":"email" },
              "password": { "type":"string", "minLength": 1, "description":"Optional; defaults to the standard onboarding password." }
            },
            "required": ["email"],
            "additionalProperties": False
          }
        },
        {
          "name": "book_appointment",
          "description": "Book an appointment using a selected slot. Use chat access token if available.",
          "parameters": {
            "type":"object",
            "properties": {
              "slot_id": { "type":"string", "description":"Slot id returned by list_available_slots (maps to service_slot_id payload)." },
              "access_token": { "type":"string", "description":"Chat token obtained via chat_token tool; used as Bearer for booking." }
            },
            "required": ["slot_id"],
            "additionalProperties": False
          }
        }
    ]
