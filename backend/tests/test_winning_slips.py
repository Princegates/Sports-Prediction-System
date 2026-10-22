"""Screenshots of won slips: posted by a superadmin, shown publicly."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from app.main import app
from tests.test_admin_hardening import _headers, _make_user

client = TestClient(app)

# A real (tiny) PNG and JPEG header -- the endpoint sniffs the bytes.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def test_admin_posts_a_slip_and_it_shows_publicly(db_session):
    admin = _make_user(db_session, "admin@example.com", role="superadmin")
    body = {"image_data": _data_url(JPEG, "image/jpeg"), "caption": "5 legs, all won", "won_on": "2026-10-20",
            "width": 720, "height": 1280}
    created = client.post("/api/admin/winning-slips", json=body, headers=_headers(admin))
    assert created.status_code == 200, created.text
    slip = created.json()
    assert slip["caption"] == "5 legs, all won" and slip["published"] is True

    listed = client.get("/api/public/winning-slips").json()
    assert [s["id"] for s in listed] == [slip["id"]]
    assert client.get("/api/public/landing").json()["winning_slips"][0]["id"] == slip["id"]

    image = client.get(slip["image_path"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert "immutable" in image.headers["cache-control"]
    assert image.content == JPEG


def test_unpublished_and_deleted_slips_disappear(db_session):
    admin = _make_user(db_session, "admin@example.com", role="superadmin")
    headers = _headers(admin)
    slip = client.post("/api/admin/winning-slips", json={"image_data": _data_url(PNG)}, headers=headers).json()

    client.patch(f"/api/admin/winning-slips/{slip['id']}", json={"published": False}, headers=headers)
    assert client.get("/api/public/winning-slips").json() == []
    assert client.get(slip["image_path"]).status_code == 404
    # Still listed for the admin, to republish.
    assert len(client.get("/api/admin/winning-slips", headers=headers).json()) == 1

    assert client.delete(f"/api/admin/winning-slips/{slip['id']}", headers=headers).status_code == 200
    assert client.get("/api/admin/winning-slips", headers=headers).json() == []


def test_only_real_images_are_accepted(db_session):
    admin = _make_user(db_session, "admin@example.com", role="superadmin")
    headers = _headers(admin)
    # Claims to be a PNG, isn't.
    fake = client.post("/api/admin/winning-slips", json={"image_data": _data_url(b"<svg onload=alert(1)>")}, headers=headers)
    assert fake.status_code == 400
    assert client.post("/api/admin/winning-slips", json={"image_data": "not base64!!"}, headers=headers).status_code == 400
    too_big = _data_url(PNG + b"\x00" * 2_100_000)
    assert client.post("/api/admin/winning-slips", json={"image_data": too_big}, headers=headers).status_code in (400, 422)


def test_only_a_superadmin_can_post(db_session):
    member = _make_user(db_session, "member@example.com")
    response = client.post("/api/admin/winning-slips", json={"image_data": _data_url(PNG)}, headers=_headers(member))
    assert response.status_code == 403
