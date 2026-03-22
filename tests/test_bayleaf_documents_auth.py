import types

import pytest

from bayleaf_agents.tools.bayleaf import BayleafAuthError, BayleafClient


def test_documents_by_doc_key_raises_token_expired(monkeypatch):
    client = BayleafClient("http://bayleaf.test")

    def fake_get(self, path, params=None, principal=None, *, use_auth=True, bearer_token=None):
        _ = (path, params, principal, use_auth, bearer_token)
        return {
            "error": "request_failed",
            "status_code": 401,
            "details": {
                "code": "token_not_valid",
                "messages": [
                    {
                        "token_class": "AccessToken",
                        "token_type": "access",
                        "message": "Token is expired",
                    }
                ],
            },
        }

    monkeypatch.setattr(client, "_get", types.MethodType(fake_get, client))

    with pytest.raises(BayleafAuthError) as exc:
        client.documents_by_doc_key(doc_key="lab", principal=None)

    assert exc.value.status_code == 401
    assert exc.value.error == "token_expired"


def test_documents_by_doc_key_keeps_non_auth_errors_as_empty_list(monkeypatch):
    client = BayleafClient("http://bayleaf.test")

    def fake_get(self, path, params=None, principal=None, *, use_auth=True, bearer_token=None):
        _ = (path, params, principal, use_auth, bearer_token)
        return {
            "error": "request_failed",
            "status_code": 500,
            "details": {"detail": "backend failed"},
        }

    monkeypatch.setattr(client, "_get", types.MethodType(fake_get, client))

    out = client.documents_by_doc_key(doc_key="lab", principal=None)

    assert out == []
