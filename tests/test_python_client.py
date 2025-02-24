# this one first, so we can find metacat, etc.
from env import env, token
from metacat.webapi import MetaCatClient, AuthenticationError, MCError

# other imports
import pytest
import time
import os

@pytest.fixture
def client(token):
    return MetaCatClient()

@pytest.fixture
def bearer_token():
    token = os.environ.get("BEARER_TOKEN")
    if not token:
        token_file = os.environ.get("BEARER_TOKEN_FILE")
        if not token_file:
            uid = os.environ.get("ID", str(os.geteuid()))
            token_dir = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
            token_file = token_dir + "/" + "bt_u" + uid
    token = open(token_file, "r").read().strip()
    return token

def test_token_auth(client, bearer_token):
    user, expiration = client.login_token(os.environ["USER"], bearer_token)
    assert(user == os.environ["USER"])
    assert(expiration > time.time())
    print(f"Authenticated as {user}")

