"""Gemini on Google Cloud's Vertex AI (stage 30): the Gemini adapter (hub/gateway/gemini_provider.py) with Google
Cloud's sign-in and address - the request, the streamed answer, thinking, calls and usage are the same.

What differs (cloud.google.com/vertex-ai, read 2026-11-07, and live with a service account the same day):
- sign-in: a service account's key file - the window's Connections keeps its project, account, key and the key's id
  (hub/connections.service_account); the adapter signs a JWT with the key (RS256) and trades it at Google's token
  service for an access token of an hour, kept until TOKEN_MARGIN_S before it lapses
- the address: aiplatform.googleapis.com, projects/<project>/locations/global/publishers/google/models/<model>
- the catalog: Vertex's list of Google's models says which Gemini models there are - not what this account may do,
  nor their limits. The right to call them (the role «Vertex AI User»: aiplatform.endpoints.predict) is asked by a
  free countTokens, so a missing role is said when the key file is saved, not at the first chat
- prices: Vertex AI's Standard prices for Gemini are the Gemini API's paid tier - the same table (Google's pricing
  page did not load for reading on 2026-11-07; the published tables quoting it agree)
- Google Cloud does not use the requests to train its models: no free tier here (D10)
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx

from .base import Model, ProviderError, scheduled
from .gemini_provider import _CATALOG, PRICES_ASOF, GeminiProvider

API = "https://aiplatform.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
LOCATION = "global"  # serves every model, the newest too
TOKEN_MARGIN_S = 300  # a token this close to lapsing is traded for a new one
NO_ROLE = (
    "the service account may not call Vertex AI models (403): give it the role «Vertex AI User» in its Google Cloud "
    "project (the console's IAM)"
)


class VertexProvider(GeminiProvider):
    service = "Vertex AI"
    rejected = "Google did not accept the service account's token"
    forbidden = NO_ROLE

    def __init__(
        self,
        account_getter: Callable[[], str | None],
        name: str = "vertex",
        api: str = API,
        transport: httpx.AsyncBaseTransport | None = None,
        today: Callable[[], date] = date.today,
        clock: Callable[[], float] = time.time,
    ):
        super().__init__(account_getter, name=name, api=api, transport=transport, today=today)
        self._clock = clock
        self._token: tuple[tuple[str, str], str, float] = (("", ""), "", 0.0)  # whose, the token, when it lapses

    def _account(self) -> dict[str, Any] | None:
        raw = self._key()
        if not raw:
            return None
        try:
            account = json.loads(raw)
        except ValueError:
            account = None
        if not isinstance(account, dict) or not {"project_id", "client_email", "private_key"} <= set(account):
            raise ProviderError(f"{self.name}: the kept key file cannot be read; save it again in the Connections")
        return account

    async def _headers(self) -> dict[str, str]:
        account = self._account()
        if account is None:
            raise ProviderError("no Vertex AI service account is set: add its key file in the window's Connections")
        whose = (str(account["client_email"]), str(account.get("private_key_id") or ""))
        kept_for, token, lapses = self._token
        now = self._clock()
        if kept_for != whose or lapses - now < TOKEN_MARGIN_S:
            token, lapses = await self._sign_in(account, now)
            self._token = (whose, token, lapses)
        return {"Authorization": f"Bearer {token}"}

    async def _sign_in(self, account: dict[str, Any], now: float) -> tuple[str, float]:
        """A token for the account: the JWT it signs, traded at Google's token service."""
        import jwt

        claims = {"iss": account["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": int(now),
                  "exp": int(now) + 3600}  # fmt: skip
        try:
            assertion = jwt.encode(claims, account["private_key"], algorithm="RS256",
                                   headers={"kid": str(account.get("private_key_id") or "")})  # fmt: skip
        except (ValueError, TypeError, jwt.PyJWTError) as exc:
            raise ProviderError(f"{self.name}: the key in the key file cannot sign; download a new key") from exc
        form = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
        try:
            async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as http:
                response = await http.post(TOKEN_URL, data=form)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: cannot reach Google's sign-in ({type(exc).__name__})") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code != 200 or not body.get("access_token"):
            said = str(body.get("error_description") or body.get("error") or f"HTTP {response.status_code}")[:200]
            raise ProviderError(f"{self.name}: Google refused the service account's key ({said}); a key deleted in "
                                "the console stops working - download a new one")  # fmt: skip
        return str(body["access_token"]), now + float(body.get("expires_in") or 3600)

    def _model_url(self, model_ref: str, method: str) -> str:
        project = (self._account() or {}).get("project_id", "")
        return f"{self._api}/v1/projects/{project}/locations/{LOCATION}/publishers/google/models/{model_ref}:{method}"

    async def list_models(self) -> list[Model]:
        """The priced Gemini models of Vertex's list, once the account may call them. No key file - no models, and no
        network call."""
        if self._account() is None:
            return []
        headers = await self._headers()
        available: set[str] = set()
        page = ""
        async with httpx.AsyncClient(timeout=15.0, transport=self._transport) as http:
            try:
                for _ in range(20):
                    params = {"pageSize": 300, "listAllVersions": "false", **({"pageToken": page} if page else {})}
                    response = await http.get(f"{self._api}/v1beta1/publishers/google/models", params=params,
                                              headers=headers)  # fmt: skip
                    if response.status_code != 200:
                        raise ProviderError(f"{self.name}: cannot list models ({self._describe(response)})")
                    body = response.json()
                    available |= {str(m.get("name", "")).rsplit("/", 1)[-1] for m in body.get("publisherModels") or []}
                    page = body.get("nextPageToken") or ""
                    if not page:
                        break
                offered = [ref for ref in _CATALOG if ref in available]
                if offered:  # the account's right to call them: countTokens is free
                    asked = {"contents": [{"role": "user", "parts": [{"text": "AFO"}]}]}
                    response = await http.post(self._model_url(offered[-1], "countTokens"), json=asked,
                                               headers=headers)  # fmt: skip
                    if response.status_code != 200:
                        raise ProviderError(f"{self.name}: {self._describe(response)}")
            except httpx.HTTPError as exc:
                raise ProviderError(f"{self.name}: cannot reach Vertex AI ({type(exc).__name__})") from exc
        models = []
        for ref in offered:
            label, schedule = _CATALOG[ref]
            prices, caps = scheduled(schedule, self._today(), PRICES_ASOF)
            models.append(Model(id=f"{self.name}/{ref}", provider=self.name, model_ref=ref, name=label,
                                locality="cloud", capabilities={"vision": True, **caps}, prices=prices))  # fmt: skip
        return models
