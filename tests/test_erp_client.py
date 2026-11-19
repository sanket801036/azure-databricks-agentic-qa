import pytest
import requests

from policypilot.college.erp import ERPClient, ERPError


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, *responses, error=None):
        self._responses = list(responses)
        self._error = error
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers, **kwargs})
        if self._error:
            raise self._error
        return self._responses.pop(0)


def test_login_stores_the_token_and_sends_it_afterwards():
    session = FakeSession(
        FakeResponse(200, {"data": {"token": "abc", "username": "teststud", "role": "student"}}),
        FakeResponse(200, {"data": [{"course": "DBMS"}]}),
    )
    erp = ERPClient("https://erp.example/", session=session)

    assert erp.login("teststud", "pw") == "student"
    assert erp.attendance() == [{"course": "DBMS"}]

    assert session.calls[0]["url"] == "https://erp.example/api/v1/auth/token/"
    assert session.calls[1]["url"] == "https://erp.example/api/v1/attendance/"
    assert session.calls[1]["headers"]["Authorization"] == "Token abc"


def test_raise_doubt_posts_the_question_with_what_the_assistant_said():
    session = FakeSession(
        FakeResponse(201, {"data": {"id": 7, "outcome": "Waiting for a teacher"}})
    )
    erp = ERPClient("https://erp.example", token="abc", session=session)

    doubt = erp.raise_doubt("Can I sit the exam on 70%?", assistant_said="Not covered.")

    assert doubt["id"] == 7
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["json"] == {
        "question": "Can I sit the exam on 70%?",
        "assistant_said": "Not covered.",
    }


def test_wrong_password_reads_as_a_sign_in_problem():
    session = FakeSession(FakeResponse(400, {"non_field_errors": ["Unable to log in."]}))

    with pytest.raises(ERPError, match="did not accept that username and password"):
        ERPClient("https://erp.example", session=session).login("x", "y")


def test_the_erps_own_validation_message_is_passed_through():
    session = FakeSession(
        FakeResponse(409, {"errors": {"__all__": ["You already have 3 questions waiting."]}})
    )
    erp = ERPClient("https://erp.example", token="abc", session=session)

    with pytest.raises(ERPError, match="You already have 3 questions waiting."):
        erp.raise_doubt("One more question")


def test_an_unreachable_erp_is_an_erperror_not_a_crash():
    session = FakeSession(error=requests.ConnectTimeout())
    erp = ERPClient("https://erp.example", token="abc", session=session)

    with pytest.raises(ERPError, match="could not be reached"):
        erp.attendance()
