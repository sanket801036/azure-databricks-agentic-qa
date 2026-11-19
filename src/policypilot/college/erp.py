"""Client for College ERP's REST API (github.com/sanket801036/College-ERP-master).

Three calls, all as the signed-in student, with the token their own username and password
earn from /api/v1/auth/token/:

- attendance()  - per-course attendance, with the classes needed to reach 75% already
                  worked out by the ERP, so the agent never does that arithmetic itself;
- raise_doubt() - hands a question the agent could not answer to the student's teachers,
                  with what the agent already said;
- doubts()      - the student's own handed-off questions and where each one stands.
"""

from __future__ import annotations

import requests

DEFAULT_TIMEOUT = 15


class ERPError(Exception):
    """A failure with a message fit to show the student."""


class ERPClient:
    def __init__(self, base_url: str, token: str | None = None, *, session=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.username: str | None = None
        self.role: str | None = None
        self._http = session or requests.Session()

    @property
    def signed_in(self) -> bool:
        return bool(self.token)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = kwargs.pop("headers", {})
        if self.token:
            headers["Authorization"] = f"Token {self.token}"
        try:
            response = self._http.request(
                method, self._url(path), headers=headers, timeout=DEFAULT_TIMEOUT, **kwargs
            )
        except requests.RequestException as exc:
            # The free demo instance sleeps when idle and takes a while to wake.
            raise ERPError(f"College ERP could not be reached ({exc.__class__.__name__}).") from exc

        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            raise ERPError(_error_message(response.status_code, body))
        return body

    def login(self, username: str, password: str) -> str:
        body = self._request(
            "POST", "auth/token/", json={"username": username, "password": password}
        )
        data = body.get("data") or {}
        self.token = data.get("token")
        self.username = data.get("username")
        self.role = data.get("role")
        if not self.token:
            raise ERPError("College ERP did not return a token.")
        return self.role or ""

    def attendance(self) -> list[dict]:
        return self._request("GET", "attendance/").get("data") or []

    def raise_doubt(self, question: str, assistant_said: str = "", course_id=None) -> dict:
        payload = {"question": question, "assistant_said": assistant_said[:2000]}
        if course_id:
            payload["course_id"] = course_id
        return self._request("POST", "doubts/", json=payload).get("data") or {}

    def doubts(self) -> list[dict]:
        return self._request("GET", "doubts/").get("data") or []


def _error_message(status: int, body: dict) -> str:
    if status == 401 or (status == 400 and "non_field_errors" in body):
        return "College ERP did not accept that username and password."
    if status == 404:
        return "That account has no student record in College ERP."
    if status == 429:
        return "Too many attempts. Wait a few minutes and try again."
    errors = body.get("errors")
    if isinstance(errors, dict):
        messages = [
            m for msgs in errors.values() for m in (msgs if isinstance(msgs, list) else [msgs])
        ]
        if messages:
            return " ".join(str(m) for m in messages)
    return f"College ERP returned an error ({status})."
