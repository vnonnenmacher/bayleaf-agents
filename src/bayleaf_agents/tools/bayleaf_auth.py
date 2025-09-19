# src/bayleaf_agents/tools/bayleaf_auth.py
from typing import Optional
from ..auth.deps import Principal


class TokenProvider:
    """
    TokenProvider (passthrough only).

    - The browser (or app) gets a chat token from Bayleaf API
      via POST /api/users/chat-token/.
    - That token is passed to the Agent in the Authorization header.
    - The Agent simply forwards the exact same token when it calls Bayleaf.

    No static mode, no exchange, no refresh. 
    Bayleaf is the only issuer.
    """

    def get_token(self, principal: Optional[Principal] = None) -> str:
        if not principal or not getattr(principal, "raw_token", None):
            raise RuntimeError(
                "Principal with raw_token required in passthrough mode. "
                "Make sure your FastAPI auth dependency extracts the bearer token "
                "and sets Principal.raw_token."
            )
        return principal.raw_token
