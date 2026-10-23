"""The news banner: headline parsing (and what it refuses), the slides a
member gets with sponsored items mixed in, and managing those items. The
property that matters most for the sponsored side is that nothing unsafe or
out of date can reach a member -- a ``javascript:`` link, an ended campaign --
whatever is in the database."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import banner, news_feed
from app.api.deps import get_current_user  # noqa: F401  (route wiring import check)
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AuditLog, BannerItem, User
from app.main import app
from app.timeutil import utcnow

client = TestClient(app)
NOW = dt.datetime(2026, 10, 23, 12, 0, 0)


def _rss(*items: str) -> bytes:
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>{''.join(items)}</channel></rss>".encode()


def _item(title="Arsenal win again", link="https://www.bbc.co.uk/sport/football/articles/abc", pub="Fri, 23 Oct 2026 10:00:00 GMT"):
    return f"<item><title>{title}</title><link>{link}</link><pubDate>{pub}</pubDate></item>"


# --- parsing --------------------------------------------------------------


def test_a_football_headline_is_parsed():
    got = news_feed.parse_feed(_rss(_item()), "BBC Sport", NOW)

    assert [(h.title, h.source, h.link) for h in got] == [
        ("Arsenal win again", "BBC Sport", "https://www.bbc.co.uk/sport/football/articles/abc")
    ]
    assert got[0].published_at == dt.datetime(2026, 10, 23, 10, 0)


def test_markup_in_a_title_is_flattened_to_text():
    got = news_feed.parse_feed(
        _rss(_item(title="<![CDATA[<b>Saka</b> &amp; Rice <script>alert(1)</script> return]]>")), "BBC", NOW
    )

    assert got[0].title == "Saka & Rice alert(1) return"


@pytest.mark.parametrize("item", [
    _item(link="http://www.bbc.co.uk/sport/football/articles/abc"),  # not https
    _item(link="javascript:alert(1)"),
    _item(link="https://www.skysports.com/f1/news/1/2/verstappen"),  # not football
    _item(pub="Mon, 01 Jun 2026 10:00:00 GMT"),  # too old
    _item(pub="not a date"),
    _item(title=""),
])
def test_unsuitable_items_are_dropped(item):
    assert news_feed.parse_feed(_rss(item), "BBC", NOW) == []


def test_espns_mislabelled_times_are_read_in_the_zone_the_feed_really_keeps():
    """ESPN's UK feed stamps London wall-clock times "EST". Taken at its word,
    05:30 would be 10:30 UTC -- hours in the future, so the item would be
    dropped as not yet published."""

    feed = _rss(_item(link="https://www.espn.co.uk/football/story/_/id/1/x", pub="Fri, 23 Oct 2026 05:30:23 EST"))

    trusted = news_feed.parse_feed(feed, "ESPN", NOW - dt.timedelta(hours=7))  # label believed: 10:30 UTC
    assert trusted == []

    got = news_feed.parse_feed(feed, "ESPN", NOW - dt.timedelta(hours=7), zone="Europe/London")
    assert [h.published_at for h in got] == [dt.datetime(2026, 10, 23, 4, 30, 23)]  # BST is UTC+1


def test_espn_soccer_links_count_as_football():
    got = news_feed.parse_feed(_rss(_item(link="https://www.espn.com/soccer/story/_/id/1/x")), "ESPN", NOW)

    assert len(got) == 1


def test_a_document_declaring_entities_is_refused():
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'

    assert news_feed.parse_feed(bomb, "BBC", NOW) == []
    assert news_feed.parse_feed(b"<not xml", "BBC", NOW) == []


def test_sources_are_interleaved_so_one_publisher_does_not_lead():
    def h(source, minutes):
        return news_feed.Headline(f"{source}{minutes}", f"https://x.com/football/{source}{minutes}", source, NOW - dt.timedelta(minutes=minutes))

    out = news_feed._interleave([h("A", 1), h("A", 2), h("A", 3), h("B", 10), h("C", 20)])

    assert [x.source for x in out] == ["A", "B", "C", "A", "A"]


# --- the slides -----------------------------------------------------------


@pytest.fixture()
def headlines(monkeypatch):
    items = [
        news_feed.Headline(f"Headline {i}", f"https://www.bbc.co.uk/sport/football/articles/{i}", "BBC Sport", NOW - dt.timedelta(minutes=i))
        for i in range(7)
    ]
    monkeypatch.setattr(news_feed, "headlines", lambda limit=12: items[:limit])
    return items


def _ad(db, **over):
    values = dict(sponsor="Acme Boots", headline="Boots for the weekend", link_url="https://acme.example/boots", cta_label="Shop")
    values.update(over)
    item = BannerItem(**values)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _user(db, role="user", email="u@example.com") -> User:
    user = User(email=email, name="U", password_hash=hash_password("a-good-password"), role=role, status="active")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _headers(user: User) -> dict:
    s = get_settings()
    return {"Authorization": f"Bearer {create_token({'user_id': user.id, 'role': user.role}, s.secret_key, s.session_ttl_seconds)}"}


def test_ads_are_spaced_through_the_headlines_and_labelled(db_session, headlines):
    _ad(db_session, sponsor="One")
    _ad(db_session, sponsor="Two")

    out = banner.slides(db_session)

    assert [s.kind for s in out] == ["news", "news", "news", "ad", "news", "news", "news", "ad", "news"]
    ads = [s for s in out if s.kind == "ad"]
    assert [a.label for a in ads] == ["One", "Two"]  # the sponsor's name travels with every ad
    assert all(a.id.startswith("ad:") and a.cta for a in ads)


def test_with_no_headlines_the_ads_still_run(db_session, monkeypatch):
    monkeypatch.setattr(news_feed, "headlines", lambda limit=12: [])
    _ad(db_session)

    assert [s.kind for s in banner.slides(db_session)] == ["ad"]


def test_only_live_ads_are_served(db_session, headlines):
    now = utcnow()
    _ad(db_session, sponsor="live")
    _ad(db_session, sponsor="off", active=False)
    _ad(db_session, sponsor="future", starts_at=now + dt.timedelta(days=1))
    _ad(db_session, sponsor="ended", ends_at=now - dt.timedelta(minutes=1))
    _ad(db_session, sponsor="inside", starts_at=now - dt.timedelta(days=1), ends_at=now + dt.timedelta(days=1))

    assert sorted(s.label for s in banner.slides(db_session) if s.kind == "ad") == ["inside", "live"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://acme.example/x", "https://", "https://has space.example/x", "//acme.example", "data:text/html,x"])
def test_an_unsafe_link_in_the_database_is_never_served(db_session, headlines, url):
    _ad(db_session, link_url=url)

    assert [s for s in banner.slides(db_session) if s.kind == "ad"] == []


def test_the_banner_endpoint_needs_a_login_not_an_access_grant(db_session, headlines):
    assert client.get("/api/banner").status_code in (401, 403)

    body = client.get("/api/banner", headers=_headers(_user(db_session))).json()
    assert body["slides"] and body["slides"][0]["kind"] == "news"


# --- counting -------------------------------------------------------------


def test_impressions_and_clicks_are_counted_for_live_ads_only(db_session, headlines):
    member = _headers(_user(db_session))
    live, ended = _ad(db_session), _ad(db_session, ends_at=utcnow() - dt.timedelta(days=1))

    assert client.post(f"/api/banner/ads/{live.id}/impression", headers=member).status_code == 204
    assert client.post(f"/api/banner/ads/{live.id}/impression", headers=member).status_code == 204
    assert client.post(f"/api/banner/ads/{live.id}/click", headers=member).status_code == 204
    assert client.post(f"/api/banner/ads/{ended.id}/click", headers=member).status_code == 404
    assert client.post("/api/banner/ads/9999/click", headers=member).status_code == 404

    db_session.expire_all()
    assert (live.impressions, live.clicks, ended.clicks) == (2, 1, 0)


# --- managing sponsored items ----------------------------------------------

PAYLOAD = {"sponsor": "Acme Boots", "headline": "Boots for the weekend", "link_url": "https://acme.example/boots"}


def test_only_a_superadmin_manages_sponsored_items(db_session):
    member = _headers(_user(db_session))

    assert client.get("/api/admin/banner-items", headers=member).status_code == 403
    assert client.post("/api/admin/banner-items", json=PAYLOAD, headers=member).status_code == 403


def test_a_superadmin_creates_edits_and_deletes_an_item_and_it_is_audited(db_session):
    admin = _user(db_session, role="superadmin", email="admin@example.com")
    h = _headers(admin)

    created = client.post("/api/admin/banner-items", json=PAYLOAD, headers=h)
    assert created.status_code == 200, created.text
    item = created.json()
    assert item["live"] is True and item["impressions"] == 0 and item["cta_label"] == "Learn more"

    patched = client.patch(f"/api/admin/banner-items/{item['id']}", json={"active": False, "headline": " New line "}, headers=h).json()
    assert patched["active"] is False and patched["live"] is False and patched["headline"] == "New line"

    assert client.delete(f"/api/admin/banner-items/{item['id']}", headers=h).status_code == 200
    assert client.get("/api/admin/banner-items", headers=h).json() == []
    actions = [a.action for a in db_session.query(AuditLog).order_by(AuditLog.id)]
    assert actions == ["banner_item.created", "banner_item.updated", "banner_item.deleted"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://acme.example", "acme.example", "https://a b.example", "ftp://acme.example"])
def test_a_link_that_is_not_plain_https_is_rejected_on_save(db_session, url):
    h = _headers(_user(db_session, role="superadmin", email="admin@example.com"))

    assert client.post("/api/admin/banner-items", json={**PAYLOAD, "link_url": url}, headers=h).status_code == 422
    created = client.post("/api/admin/banner-items", json=PAYLOAD, headers=h).json()
    assert client.patch(f"/api/admin/banner-items/{created['id']}", json={"link_url": url}, headers=h).status_code == 422


def test_the_end_date_must_follow_the_start_date_and_dates_can_be_cleared(db_session):
    h = _headers(_user(db_session, role="superadmin", email="admin@example.com"))
    bad = {**PAYLOAD, "starts_at": "2026-11-02T00:00:00Z", "ends_at": "2026-11-01T00:00:00Z"}
    assert client.post("/api/admin/banner-items", json=bad, headers=h).status_code == 422

    ok = client.post("/api/admin/banner-items", json={**PAYLOAD, "ends_at": "2030-01-01T00:00:00Z"}, headers=h).json()
    assert ok["ends_at"] is not None
    cleared = client.patch(f"/api/admin/banner-items/{ok['id']}", json={"clear_ends_at": True}, headers=h).json()
    assert cleared["ends_at"] is None
