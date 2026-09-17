import time

from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import create_token, verify_token


def test_password_hash_roundtrip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong password", stored)


def test_password_hashes_are_salted_differently():
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b


def test_verify_password_rejects_malformed_hash():
    assert not verify_password("anything", "not-a-valid-hash")


def test_token_roundtrip():
    token = create_token({"user_id": 42, "role": "user"}, secret="test-secret")
    payload = verify_token(token, secret="test-secret")
    assert payload is not None
    assert payload["user_id"] == 42
    assert payload["role"] == "user"


def test_token_rejects_wrong_secret():
    token = create_token({"user_id": 1}, secret="secret-a")
    assert verify_token(token, secret="secret-b") is None


def test_token_rejects_tampering():
    token = create_token({"user_id": 1, "role": "user"}, secret="test-secret")
    body, sig = token.split(".")
    tampered = body.replace("dXNlcg", "YWRtaW4") + "." + sig  # best-effort mutation
    if tampered == token:
        tampered = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + sig
    assert verify_token(tampered, secret="test-secret") is None


def test_token_expires():
    token = create_token({"user_id": 1}, secret="test-secret", ttl_seconds=-1)
    assert verify_token(token, secret="test-secret") is None


def test_token_survives_until_expiry():
    token = create_token({"user_id": 1}, secret="test-secret", ttl_seconds=2)
    assert verify_token(token, secret="test-secret") is not None
    time.sleep(2.2)
    assert verify_token(token, secret="test-secret") is None
