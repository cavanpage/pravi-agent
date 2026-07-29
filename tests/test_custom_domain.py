"""Custom domains on the production Pages deploy (ADR 0007).

The load-bearing behavior here is the DEGRADED path: a token with only
`Account → Pages → Edit` can register the domain but not write DNS, and
that outcome must stay useful (domain registered + one copy-pasteable
record) rather than raising.
"""

from __future__ import annotations

import pytest

from pravi.api.schemas import CreateRepoRequest
from pravi.services import cloudflare as cf


class TestZoneCandidates:
    def test_walks_suffixes_longest_first(self):
        assert cf._zone_candidates("app.staging.example.com") == [
            "app.staging.example.com",
            "staging.example.com",
            "example.com",
        ]

    def test_stops_at_two_labels(self):
        # A single label can never be a zone.
        assert cf._zone_candidates("example.com") == ["example.com"]


class TestHostnameValidation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("app.example.com", "app.example.com"),
            ("  APP.Example.COM  ", "app.example.com"),
            ("app.example.com.", "app.example.com"),  # trailing dot stripped
            ("", None),
            (None, None),
        ],
    )
    def test_normalization(self, raw, expected):
        req = CreateRepoRequest(name="x", custom_domain=raw)
        assert req.custom_domain == expected

    @pytest.mark.parametrize(
        "bad",
        [
            "my-app.pages.dev",  # assigned automatically, not user-supplied
            "not a hostname",
            "no-tld",
            "-leading.example.com",
            "http://app.example.com",
        ],
    )
    def test_rejected(self, bad: str):
        with pytest.raises(ValueError):
            CreateRepoRequest(name="x", custom_domain=bad)


class _FakeCF:
    """Stand-in for the three network calls `setup_custom_domain` composes."""

    def __init__(self, *, zone: cf.ZoneRef | None, cname: tuple[bool, str | None]):
        self.zone = zone
        self.cname = cname
        self.cname_calls: list[dict] = []

    async def attach(self, *, project: str, hostname: str) -> cf.CustomDomainStatus:
        return cf.CustomDomainStatus(
            hostname=hostname, status="pending", url=f"https://{hostname}"
        )

    async def find_zone(self, hostname: str) -> cf.ZoneRef | None:
        return self.zone

    async def ensure(self, **kw) -> tuple[bool, str | None]:
        self.cname_calls.append(kw)
        return self.cname

    async def get_domain(self, *, project: str, hostname: str):
        return cf.CustomDomainStatus(
            hostname=hostname, status="active", url=f"https://{hostname}"
        )

    def install(self, monkeypatch):
        monkeypatch.setattr(cf, "attach_custom_domain", self.attach)
        monkeypatch.setattr(cf, "find_zone_for_hostname", self.find_zone)
        monkeypatch.setattr(cf, "ensure_cname", self.ensure)
        monkeypatch.setattr(cf, "get_custom_domain", self.get_domain)


async def test_happy_path_writes_the_cname_and_reports_active(monkeypatch):
    fake = _FakeCF(zone=cf.ZoneRef(id="z1", name="example.com"), cname=(True, None))
    fake.install(monkeypatch)

    status = await cf.setup_custom_domain(project="my-app", hostname="app.example.com")

    assert status.dns_configured is True
    assert status.status == "active"
    assert status.manual_dns_record is None
    # Proxied CNAME pointing at the project's pages.dev host.
    assert fake.cname_calls[0]["target"] == "my-app.pages.dev"
    assert fake.cname_calls[0]["zone_id"] == "z1"


async def test_missing_zone_scope_still_registers_and_hands_back_a_record(monkeypatch):
    """The whole point of the degraded path: useful, not broken."""
    fake = _FakeCF(zone=None, cname=(True, None))
    fake.install(monkeypatch)

    status = await cf.setup_custom_domain(project="my-app", hostname="app.example.com")

    assert status.dns_configured is False
    # Registered on the Pages project regardless.
    assert status.hostname == "app.example.com"
    assert status.status == "pending"
    assert status.manual_dns_record == "app.example.com  CNAME  my-app.pages.dev  (proxied)"
    assert status.dns_skipped_reason
    assert "Zone:DNS:Edit" in status.dns_skipped_reason
    # No CNAME attempt without a zone.
    assert fake.cname_calls == []


async def test_dns_write_refused_surfaces_the_reason(monkeypatch):
    fake = _FakeCF(
        zone=cf.ZoneRef(id="z1", name="example.com"),
        cname=(False, cf._DNS_SCOPE_HINT),
    )
    fake.install(monkeypatch)

    status = await cf.setup_custom_domain(project="my-app", hostname="app.example.com")

    assert status.dns_configured is False
    assert status.manual_dns_record
    assert status.dns_skipped_reason == cf._DNS_SCOPE_HINT


async def test_setup_never_raises_on_permission_problems(monkeypatch):
    """`find_zone_for_hostname` and `ensure_cname` are contractually
    non-raising; this guards that the composer honors it."""

    async def boom(*a, **kw):
        raise AssertionError("should not be reached")

    fake = _FakeCF(zone=None, cname=(True, None))
    fake.install(monkeypatch)
    monkeypatch.setattr(cf, "ensure_cname", boom)

    status = await cf.setup_custom_domain(project="p", hostname="a.example.com")
    assert status.dns_configured is False


class TestDomainStatusParsing:
    def test_reads_the_cloudflare_payload(self):
        status = cf._domain_status_from(
            {
                "name": "app.example.com",
                "status": "active",
                "validation_data": {"method": "http", "status": "active"},
                "verification_data": {"status": "active"},
            },
            "fallback.example.com",
        )
        assert status.hostname == "app.example.com"
        assert status.status == "active"
        assert status.url == "https://app.example.com"
        assert status.validation_data == {"method": "http", "status": "active"}

    def test_falls_back_to_the_requested_hostname(self):
        status = cf._domain_status_from({}, "app.example.com")
        assert status.hostname == "app.example.com"
        assert status.status == "unknown"
