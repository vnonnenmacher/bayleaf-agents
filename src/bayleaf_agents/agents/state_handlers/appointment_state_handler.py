from typing import Any, Dict, List, Optional
from datetime import datetime

from .base_state_handler import BaseStateHandler


class AppointmentStateHandler(BaseStateHandler):
    """
    Booking-specific state updates (slots cache, selection, booking result).
    """

    @staticmethod
    def _parse_dt(value: Optional[str]) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        try:
            # support both ISO with and without timezone "Z"
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            return datetime.fromisoformat(value)
        except Exception:
            return None

    def _human_slot_label(self, slot: Dict[str, Any]) -> str:
        start_dt = self._parse_dt(slot.get("start"))
        end_dt = self._parse_dt(slot.get("end"))
        date_part = start_dt.strftime("%a, %b %d") if start_dt else ""
        start_part = start_dt.strftime("%I:%M %p").lstrip("0") if start_dt else str(slot.get("start"))
        end_part = end_dt.strftime("%I:%M %p").lstrip("0") if end_dt else str(slot.get("end"))

        provider = slot.get("provider") or {}
        provider_id = provider.get("id") or slot.get("provider_id") or slot.get("professional_id")
        provider_name = None
        if isinstance(provider, dict):
            provider_name = " ".join(
                filter(None, [provider.get("first_name"), provider.get("last_name")])
            ) or provider.get("name")

        provider_str = f" — {provider_name}" if provider_name else (f" — provider (ID: {provider_id})" if provider_id else "")
        label = f"{date_part}, {start_part}–{end_part} (UTC){provider_str}"
        return label.strip(", ").strip()

    def _build_slot_options(self, slots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        options: List[Dict[str, Any]] = []
        for s in slots:
            if not isinstance(s, dict):
                continue
            label = self._human_slot_label(s)
            options.append(
                {
                    "slot_id": s.get("id") or s.get("slot_id"),
                    "start": s.get("start"),
                    "end": s.get("end"),
                    "provider_id": (s.get("provider") or {}).get("id") if isinstance(s.get("provider"), dict) else s.get("provider_id") or s.get("professional_id"),
                    "provider_name": (s.get("provider") or {}).get("name") if isinstance(s.get("provider"), dict) else None,
                    "label": label,
                }
            )
        return options

    @staticmethod
    def _looks_like_error_list(result: Any) -> bool:
        return (
            isinstance(result, list)
            and len(result) == 1
            and isinstance(result[0], dict)
            and result[0].get("error") is not None
        )

    def apply(self, tool_name: str, args: Dict[str, Any], result: Any, state: Dict[str, Any]) -> bool:
        changed = False

        if tool_name == "chat_token" and isinstance(result, dict) and result.get("access_token"):
            state["access_token"] = result.get("access_token")
            changed = True

        elif tool_name in ("list_available_slots", "available_slots") and isinstance(result, list):
            if not self._looks_like_error_list(result):
                state["last_slots"] = result[:10]
                state["last_slot_options"] = self._build_slot_options(state["last_slots"])
                state["last_slot_query"] = {
                    "start_date": args.get("start_date"),
                    "end_date": args.get("end_date"),
                    "service_id": args.get("service_id", 1),
                }
                self.log.info(
                    "slots_cached",
                    count=len(state["last_slots"]),
                    start=state["last_slot_query"]["start_date"],
                    end=state["last_slot_query"]["end_date"],
                    service_id=state["last_slot_query"]["service_id"],
                )
                changed = True
            else:
                state.pop("last_slots", None)
                state.pop("last_slot_options", None)

        elif tool_name in ("list_available_professionals", "available_professionals") and isinstance(result, list):
            if not self._looks_like_error_list(result):
                state["last_professionals"] = result[:8]
                state["last_professional_query"] = {
                    "start_date": args.get("start_date"),
                    "end_date": args.get("end_date"),
                    "service_id": args.get("service_id", 1),
                }
                self.log.info(
                    "professionals_cached",
                    count=len(state["last_professionals"]),
                    start=state["last_professional_query"]["start_date"],
                    end=state["last_professional_query"]["end_date"],
                    service_id=state["last_professional_query"]["service_id"],
                )
                changed = True

        elif tool_name in ("list_available_specializations", "available_specializations") and isinstance(result, list):
            if not self._looks_like_error_list(result):
                state["last_specializations"] = result[:8]
                state["last_specialization_query"] = {
                    "start_date": args.get("start_date"),
                    "end_date": args.get("end_date"),
                    "service_id": args.get("service_id", 1),
                }
                self.log.info(
                    "specializations_cached",
                    count=len(state["last_specializations"]),
                    start=state["last_specialization_query"]["start_date"],
                    end=state["last_specialization_query"]["end_date"],
                    service_id=state["last_specialization_query"]["service_id"],
                )
                changed = True

        elif tool_name == "book_appointment":
            state["selected_slot_id"] = args.get("slot_id")
            state["last_booking"] = result if isinstance(result, dict) else {"result": result}
            changed = True

        return changed
