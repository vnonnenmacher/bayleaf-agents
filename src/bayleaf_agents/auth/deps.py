# src/bayleaf_agents/auth/deps.py
from fastapi import Header, HTTPException
from typing import List, Optional, Dict, Any
import json, base64, time


class Principal:
    def __init__(
        self,
        user_id: Optional[str],
        sub: Optional[str],
        scopes: List[str],
        patient_id: Optional[str],   # segue opcional (pode vir de outro fluxo)
        raw: Dict[str, Any],
        raw_token: str,
    ):
        self.user_id = user_id
        self.sub = sub
        self.scopes = scopes
        self.patient_id = patient_id
        self.raw = raw
        self.raw_token = raw_token


def _parse_unverified_jwt(token: str) -> Dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # padding
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return {}


def require_auth(required_scopes: Optional[List[str]] = None):
    async def _dep(authorization: str = Header(None)):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing_token")

        token = authorization.split(" ", 1)[1].strip()
        claims = _parse_unverified_jwt(token) or {}

        # fail fast on an expired token instead of accepting the request and only
        # discovering it later when the downstream Bayleaf call rejects it.
        exp = claims.get("exp")
        if isinstance(exp, (int, float)) and exp < time.time():
            raise HTTPException(status_code=401, detail={"error": "token_expired"})

        # scopes (best-effort)
        scopes: List[str] = []
        scope_val = claims.get("scope") or claims.get("scopes")
        if isinstance(scope_val, str):
            scopes = [s for s in scope_val.split() if s]
        elif isinstance(scope_val, list):
            scopes = [str(s) for s in scope_val if s]

        if required_scopes:
            missing = [s for s in required_scopes if s not in scopes]
            if missing:
                raise HTTPException(status_code=403, detail="insufficient_scope")

        user_id = claims.get("user_id") or claims.get("sub")
        if user_id is not None:
            user_id = str(user_id)

        # Não há patient_id no token atual
        patient_id = None
        sub = claims.get("sub")

        return Principal(
            user_id=user_id,
            sub=sub,
            scopes=scopes,
            patient_id=patient_id,
            raw=claims,
            raw_token=token,
        )
    return _dep
