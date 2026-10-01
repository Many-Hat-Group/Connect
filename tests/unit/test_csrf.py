import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import web
from app.auth.websession import WebSession


def make_request(method="POST", headers=None, body=b"", ctype="application/x-www-form-urlencoded"):
    hdrs = [(b"content-type", ctype.encode())] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "method": method, "path": "/x", "headers": hdrs, "query_string": b""}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request(scope, receive)


def with_session(req, csrf="tok123"):
    req.state.ws = WebSession(sid="s", csrf=csrf)
    return req


async def test_get_is_exempt():
    await web.csrf_protect(make_request("GET"))


async def test_header_token_accepted():
    await web.csrf_protect(with_session(make_request(headers={"X-CSRF-Token": "tok123"})))


async def test_form_token_accepted():
    await web.csrf_protect(with_session(make_request(body=b"csrf=tok123&x=1")))


@pytest.mark.parametrize("req_kwargs", [
    {"body": b"x=1"},                                              # missing
    {"body": b"csrf=wrong"},                                       # wrong
    {"headers": {"X-CSRF-Token": "tok123", "Origin": "https://evil.example"}},  # cross-origin
])
async def test_rejected(req_kwargs):
    with pytest.raises(HTTPException) as e:
        await web.csrf_protect(with_session(make_request(**req_kwargs)))
    assert e.value.status_code == 403


async def test_no_session_rejected():
    req = make_request(headers={"X-CSRF-Token": "tok123"})
    req.state.ws = None
    with pytest.raises(HTTPException):
        await web.csrf_protect(req)
