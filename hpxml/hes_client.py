"""Client for the DOE Home Energy Score (HEScore) API.

Auth model (per LBNL/PNNL docs):
  * A developer **API key** identifies the Software Partner (header/param).
  * ``get_session_token(username, password)`` for a DOE-approved Qualified
    Assessor returns a ``session_token`` that must accompany every other call.

Typical workflow this client wraps:
  get_session_token -> submit_address -> submit_hpxml_inputs
    -> validate_hpxml -> retrieve_results -> generate_label

Configuration (env):
  HESCORE_BASE_URL          e.g. https://sandbox.hesapi.labworks.org/...
  HESCORE_API_KEY           developer/partner API key
  HESCORE_ASSESSOR_USERNAME qualified-assessor login
  HESCORE_ASSESSOR_PASSWORD qualified-assessor password

The exact request envelope differs between the sandbox and production
deployments; ``_call`` centralizes it so only one method changes when the
partner docs are confirmed.  Everything else is integration-test-mockable.
"""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional

import requests


class HESConfigError(RuntimeError):
    """Raised when required HEScore credentials/config are missing."""


class HESAPIError(RuntimeError):
    """Raised when the HEScore API returns an error response."""


class HESClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = 30,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = (base_url or os.getenv("HESCORE_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("HESCORE_API_KEY")
        self.username = username or os.getenv("HESCORE_ASSESSOR_USERNAME")
        self.password = password or os.getenv("HESCORE_ASSESSOR_PASSWORD")
        self.timeout = timeout
        self._session_token: Optional[str] = None
        self._http = session or requests.Session()

    # ------------------------------------------------------------------ core
    def _require_config(self):
        missing = [
            name for name, val in (
                ("HESCORE_BASE_URL", self.base_url),
                ("HESCORE_API_KEY", self.api_key),
                ("HESCORE_ASSESSOR_USERNAME", self.username),
                ("HESCORE_ASSESSOR_PASSWORD", self.password),
            ) if not val
        ]
        if missing:
            raise HESConfigError(f"Missing HEScore config: {', '.join(missing)}")

    def _call(self, method: str, params: Dict[str, Any], authed: bool = True) -> Dict[str, Any]:
        """Invoke a single HEScore API method.

        Confirm the envelope against the partner docs when the key arrives.
        Current assumption: POST {base_url}/{method} with a JSON body carrying
        the developer key and (when required) the session token.
        """
        payload = dict(params)
        if self.api_key:
            payload.setdefault("key", self.api_key)
        if authed:
            if not self._session_token:
                self.authenticate()
            payload.setdefault("session_token", self._session_token)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Token {self.api_key}"

        resp = self._http.post(
            f"{self.base_url}/{method}", json=payload, headers=headers, timeout=self.timeout
        )
        if resp.status_code >= 400:
            raise HESAPIError(f"{method} -> HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except ValueError:
            raise HESAPIError(f"{method} -> non-JSON response: {resp.text[:500]}")
        if isinstance(data, dict) and data.get("error"):
            raise HESAPIError(f"{method} -> {data['error']}")
        return data

    # ------------------------------------------------------------------ auth
    def authenticate(self) -> str:
        self._require_config()
        data = self._call(
            "get_session_token",
            {"user_name": self.username, "password": self.password},
            authed=False,
        )
        token = data.get("session_token") or data.get("token")
        if not token:
            raise HESAPIError("get_session_token returned no session_token")
        self._session_token = token
        return token

    # -------------------------------------------------------------- workflow
    def submit_address(
        self, address: Dict[str, Any], assessment_type: str = "initial",
        assessment_date: Optional[str] = None, external_id: Optional[str] = None,
    ) -> str:
        """Create a building; returns the HEScore building id."""
        params: Dict[str, Any] = {
            "address": address.get("street"),
            "city": address.get("city"),
            "state": address.get("state"),
            "zip_code": address.get("zip_code"),
            "assessment_type": assessment_type,
        }
        if assessment_date:
            params["assessment_date"] = assessment_date
        if external_id:
            params["external_building_id"] = external_id
        data = self._call("submit_address", params)
        building_id = data.get("building_id") or data.get("building")
        if not building_id:
            raise HESAPIError("submit_address returned no building_id")
        return str(building_id)

    def submit_hpxml_inputs(self, building_id: str, hpxml: str) -> Dict[str, Any]:
        """Submit HPXML (base64-encoded) for an existing building."""
        encoded = base64.b64encode(hpxml.encode("utf-8")).decode("ascii")
        return self._call(
            "submit_hpxml_inputs",
            {"building_id": building_id, "hpxml": encoded},
        )

    def validate_hpxml(self, hpxml: str) -> Dict[str, Any]:
        encoded = base64.b64encode(hpxml.encode("utf-8")).decode("ascii")
        return self._call("validate_hpxml", {"hpxml": encoded})

    def retrieve_results(self, building_id: str) -> Dict[str, Any]:
        return self._call("retrieve_results", {"building_id": building_id})

    def retrieve_recommendations(self, building_id: str) -> Dict[str, Any]:
        return self._call("retrieve_recommendations", {"building_id": building_id})

    def generate_label(self, building_id: str) -> Dict[str, Any]:
        return self._call("generate_label", {"building_id": building_id})

    # ------------------------------------------------------------- composite
    def score_building(
        self, hpxml: str, address: Dict[str, Any], assessment_type: str = "initial",
        assessment_date: Optional[str] = None, external_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """End-to-end: create building, submit HPXML, score, fetch label.

        Returns ``{building_id, score, label_url, results, recommendations}``.
        """
        building_id = self.submit_address(
            address, assessment_type, assessment_date, external_id
        )
        self.submit_hpxml_inputs(building_id, hpxml)
        results = self.retrieve_results(building_id)
        score = results.get("base_score") or results.get("score")
        recommendations = {}
        label_url = None
        try:
            recommendations = self.retrieve_recommendations(building_id)
            label = self.generate_label(building_id)
            label_url = label.get("label_url") or label.get("url")
        except HESAPIError:
            # Score still valid even if label/recs generation lags.
            pass
        return {
            "building_id": building_id,
            "score": score,
            "label_url": label_url,
            "results": results,
            "recommendations": recommendations,
        }
