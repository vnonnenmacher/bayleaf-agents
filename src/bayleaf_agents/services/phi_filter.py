import structlog, re
from typing import List, TypedDict, Optional, Any
import requests
from ..config import settings


class PHIEntityResult(TypedDict, total=False):
    entity_type: str
    text: str
    start: Optional[int]
    end: Optional[int]
    placeholder: str


class PHIFilterResult(TypedDict):
    redacted_text: str
    entities: List[PHIEntityResult]


_PLACEHOLDER_MAP = {
    "first_name": "first_name",
    "last_name": "last_name",
    "person": "person",
    "email": "e_mail",
    "email_address": "e_mail",
    "emailaddr": "e_mail",
    "phone": "phone_number",
    "phone_number": "phone_number",
    "sin": "sin",
    "ssn": "ssn",
    "dob": "date_of_birth",
    "date_of_birth": "date_of_birth",
}


def _placeholder_for(label: str) -> str:
    key = (label or "phi").lower()
    mapped = _PLACEHOLDER_MAP.get(key, f"phi_{key}")
    return f"<{mapped}>"


def _email_fallback(text: str) -> list[PHIEntityResult]:
    """Minimal fallback: mask email-like strings if Presidio returns nothing."""
    email_re = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    entities: list[PHIEntityResult] = []
    for match in email_re.finditer(text):
        entities.append(
            {
                "entity_type": "EMAIL_ADDRESS",
                "text": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "placeholder": "<e_mail>",
            }
        )
    return entities


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text


def _apply_replacements(text: str, entities: List[PHIEntityResult]) -> str:
    """Apply placeholders to the text using the provided entity spans."""
    spans = []
    for ent in entities:
        if ent.get("start") is None or ent.get("end") is None:
            continue
        try:
            start = int(ent["start"])
            end = int(ent["end"])
        except (TypeError, ValueError):
            continue
        if start < 0 or end <= start or end > len(text):
            continue
        spans.append((start, end, ent["placeholder"]))

    if not spans:
        redacted = text
        for ent in entities:
            val = ent.get("text")
            if val:
                redacted = redacted.replace(str(val), ent["placeholder"])
        return redacted

    spans.sort(key=lambda x: x[0])
    out: list[str] = []
    cursor = 0
    for start, end, placeholder in spans:
        if start < cursor:
            continue  # overlapping span; skip to avoid corrupting output
        out.append(text[cursor:start])
        out.append(placeholder)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


class PHIFilterError(RuntimeError):
    pass


def _normalize_lang(lang: Optional[str]) -> str:
    if not lang:
        return "en"
    return str(lang).split("-")[0].lower() or "en"


class PHIFilterClient:
    """
    Thin HTTP client that calls the local spaCy + Presidio container to detect PHI
    and returns a redacted version of the input text along with entity metadata.
    """

    def __init__(self, url: Optional[str] = None, timeout: Optional[int] = None, entities: Optional[list[str]] = None):
        self.url = url or settings.PHI_FILTER_URL
        self.timeout = timeout or settings.PHI_FILTER_TIMEOUT
        cfg_entities = entities or [e.strip() for e in settings.PHI_FILTER_ENTITIES.split(",") if e.strip()]
        self.entities = cfg_entities or ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER"]
        self.log = structlog.get_logger("phi_filter")

    def redact(self, text: str, language: Optional[str] = None) -> PHIFilterResult:
        if not text:
            return {"redacted_text": text, "entities": []}

        lang = _normalize_lang(language)
        self.log.info("phi_filter_request", url=self.url, lang=lang, entities=self.entities)
        try:
            resp = requests.post(
                self.url,
                json={
                    "text": text,
                    "language": lang,
                    "entities": self.entities,
                    "return_decision_process": False,
                },
                timeout=self.timeout,
            )
        except Exception as exc:
            self.log.warning("phi_filter_call_failed", error=str(exc))
            raise PHIFilterError("phi_filter_unreachable") from exc

        if not resp.ok:
            self.log.warning(
                "phi_filter_non_200",
                status=resp.status_code,
                body=_safe_json(resp),
            )
            raise PHIFilterError(f"phi_filter_status_{resp.status_code}")

        data = _safe_json(resp)
        raw_entities = []
        suggested_text: Optional[str] = None
        if isinstance(data, dict):
            raw_entities = data.get("entities") or data.get("items") or []
            suggested_text = data.get("redacted_text")
        elif isinstance(data, list):
            raw_entities = data

        entities: List[PHIEntityResult] = []
        for ent in raw_entities or []:
            if not isinstance(ent, dict):
                continue
            label = ent.get("label") or ent.get("entity_type") or ent.get("type") or ""
            placeholder = _placeholder_for(str(label))
            entities.append(
                {
                    "entity_type": str(label),
                    "text": ent.get("text") or ent.get("value") or "",
                    "start": ent.get("start") or ent.get("begin") or ent.get("offset"),
                    "end": ent.get("end") or ent.get("stop"),
                    "placeholder": placeholder,
                }
            )

        # Minimal fallback for emails if Presidio found nothing
        if not entities:
            email_entities = _email_fallback(text)
            if email_entities:
                self.log.info("phi_filter_email_fallback_used", count=len(email_entities))
                entities = email_entities

        redacted_text = suggested_text if isinstance(suggested_text, str) else _apply_replacements(text, entities)
        if not entities:
            self.log.info("phi_filter_no_entities_passthrough")
            redacted_text = text
        self.log.info(
            "phi_filter_response",
            status=resp.status_code,
            entity_count=len(entities),
            changed=redacted_text != text,
        )
        return {"redacted_text": redacted_text, "entities": entities}
