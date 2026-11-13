"""Shared test setup

`app` is a Flask test client on a fresh editor holding `case9`; `post`
sends a JSON API request with the required `X-PPEdit` header.
"""

import pytest

from ppedit import Case, create_app

@pytest.fixture
def client():
    """Flask test client editing a fresh copy of case9"""
    app = create_app(Case.builtin("case9"))
    app.config["TESTING"] = True
    return app.test_client()

@pytest.fixture
def post(client):
    """POST JSON to the API and return (status, json)"""
    def _post(path,body=None):
        res = client.post(path,json=body or {},headers={"X-PPEdit":"1"})
        return res.status_code,res.get_json()
    return _post
