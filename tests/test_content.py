"""Tests for content repositories, resource resolution and `Host.ip`.

These assemble every artifact URL a rendered template embeds, so the joining
and host fill-in rules are shipped contract. DNS is monkeypatched throughout --
nothing here resolves a real name.
"""

import importlib.util
import types

import pytest

import netboot
from netboot import content
from netboot.content import Repository, Resource
from netboot.utils.net import Host

#: http(s) service URIs go through pathlib_next's http handler, which imports
#: `requests` -- netboot's `http` extra. Without it those cases are skipped, not
#: failed, so a `pip install -e .[dev]` tree still runs the rest of the file.
requires_http = pytest.mark.skipif(
    importlib.util.find_spec("requests") is None,
    reason="http repository services need the 'http' extra (requests)",
)


def _repo(**kwargs):
    kwargs.setdefault("address", "10.0.0.1")
    kwargs.setdefault("services", {"http": "/boot"})
    return Repository(**kwargs)


@requires_http
def test_service_fills_in_the_scheme_and_host_from_the_address():
    uri = _repo().service("http")
    assert str(uri).startswith("http://10.0.0.1/")


def test_service_none_uses_local_with_the_file_scheme():
    uri = _repo(local="/srv/tftp").service(None)
    # `service(None)` passes an empty host, so the authority is omitted and the
    # URI is the path-absolute form `file:/srv/tftp`, not `file:///srv/tftp`.
    assert str(uri) == "file:/srv/tftp"


def test_service_returns_none_for_an_unknown_name():
    assert _repo().service("tftp") is None


def test_service_returns_none_when_there_is_no_local_path():
    assert _repo(local=None).service(None) is None


@requires_http
def test_get_joins_the_relative_path_onto_the_service_uri():
    assert str(_repo().get("images/vmlinuz", service="http")).endswith(
        "/boot/images/vmlinuz"
    )


@requires_http
def test_get_strips_a_leading_slash_so_the_base_path_survives():
    # Without the lstrip an absolute rel_path would replace "/boot" entirely.
    assert "/boot/vmlinuz" in str(_repo().get("/vmlinuz", service="http"))


@requires_http
def test_get_accepts_multiple_path_segments():
    assert str(_repo().get("images", "vmlinuz", service="http")).endswith(
        "/boot/images/vmlinuz"
    )


def test_get_returns_none_for_an_unknown_service():
    assert _repo().get("vmlinuz", service="tftp") is None


@requires_http
def test_getitem_is_get_with_the_service_in_the_key():
    repo = _repo()
    assert str(repo["images/vmlinuz", "http"]) == str(
        repo.get("images/vmlinuz", service="http")
    )


@requires_http
def test_joinpath_extends_every_service_and_the_local_path():
    repo = _repo(local="/srv/tftp") / "debian"
    assert str(repo.service("http")).endswith("/boot/debian")
    assert "/srv/tftp/debian" in str(repo.service(None))


@requires_http
def test_address_is_resolved_when_it_is_a_hostname(monkeypatch):
    monkeypatch.setattr(
        __import__("netimps"),
        "get_ip",
        lambda name, *a, **kw: netboot.netutils.parse("192.0.2.20"),
    )
    uri = _repo(address="mirror.example").service("http")
    assert "192.0.2.20" in str(uri)


def _context_with_resources(tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir(exist_ok=True)
    config = {
        "templates": [templates],
        "images": {"debian": {"template_path": []}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}
        },
        "repos": {"mirror": {"address": "10.0.0.1", "services": {"http": "/boot"}}},
    }
    p = netboot.Pixie(**config)
    ctx = p.make_context(p.lookup_target("host1"))
    ctx.resources = {"kernel": Resource(path="images/vmlinuz", src="mirror")}
    return ctx


@requires_http
def test_context_resource_resolves_by_id(tmp_path):
    ctx = _context_with_resources(tmp_path)
    assert str(ctx.resource("kernel", service="http")).endswith("/boot/images/vmlinuz")


@requires_http
def test_context_resource_resolves_a_resource_instance(tmp_path):
    ctx = _context_with_resources(tmp_path)
    resource = Resource(path="images/initrd", src="mirror")
    assert str(ctx.resource(resource, service="http")).endswith("/boot/images/initrd")


def test_context_resource_is_none_for_an_unknown_id(tmp_path):
    ctx = _context_with_resources(tmp_path)
    assert ctx.resource("nosuchresource", service="http") is None


def test_context_resource_is_none_for_an_unknown_repo(tmp_path):
    ctx = _context_with_resources(tmp_path)
    assert ctx.resource(Resource(path="x", src="nosuchrepo"), service="http") is None


def test_context_resource_repo_returns_the_owning_repository(tmp_path):
    ctx = _context_with_resources(tmp_path)
    assert ctx.resource_repo("kernel") is ctx.repos["mirror"]
    assert ctx.resource_repo("nosuchresource") is None


def test_host_ip_short_circuits_an_ip_literal(monkeypatch):
    import netimps

    def _boom(*a, **kw):  # a literal must never reach DNS
        raise AssertionError("get_ip() called for an IP literal")

    monkeypatch.setattr(netimps, "get_ip", _boom)
    assert str(Host("10.0.0.7").ip()) == "10.0.0.7"


def test_host_ip_resolves_a_hostname(monkeypatch):
    import netimps

    monkeypatch.setattr(
        netimps, "get_ip", lambda name, *a, **kw: netboot.netutils.parse("192.0.2.30")
    )
    assert str(Host("mirror.example").ip()) == "192.0.2.30"


def test_an_unresolvable_host_falls_back_to_the_configured_text(monkeypatch):
    # netimps keeps `.ip()` Optional so the type is honest; `try_ip()` is
    # netboot's one-`or` wrapper, for the callers that must build a URL anyway.
    import netimps

    monkeypatch.setattr(netimps, "get_ip", lambda name, *a, **kw: None)
    host = Host("mirror.example")
    assert host.ip() is None
    assert host.try_ip() == "mirror.example"


def test_an_empty_host_resolves_to_nothing():
    assert Host().ip() is None
    assert Host().try_ip() == ""
    assert Host(None).try_ip() == ""


def test_try_ip_shares_netimps_caching(monkeypatch):
    # The duplicate this replaced re-resolved on every call; a repo asked for
    # several services paid for each one.
    import netimps

    calls = []
    monkeypatch.setattr(
        netimps, "get_ip", lambda name, *a, **kw: calls.append(name) or None
    )
    host = Host("mirror.example")
    assert host.try_ip() == "mirror.example"
    assert host.try_ip() == "mirror.example"
    assert len(calls) == 1


def test_a_failed_resolution_is_cached_until_asked_to_retry(monkeypatch):
    # netimps.Host caches failures, which netboot's own class did not: several
    # lookups on one object are the common case. Worth pinning, because a test
    # that patches `get_ip` *after* a first call would otherwise look broken.
    import netimps

    calls = []
    monkeypatch.setattr(
        netimps, "get_ip", lambda name, *a, **kw: calls.append(name) or None
    )
    host = Host("mirror.example")
    assert host.ip() is None and host.ip() is None
    assert len(calls) == 1
    assert host.ip(refresh=True) is None
    assert len(calls) == 2


def test_http_service_without_requests_names_the_extra(monkeypatch):
    # pathlib_next's http handler imports `requests` at module import, so
    # without the extra the user would otherwise get a bare ModuleNotFoundError
    # raised from inside the library.
    monkeypatch.setattr(
        content, "_importlib_util", types.SimpleNamespace(find_spec=lambda name: None)
    )
    with pytest.raises(ImportError, match=r"netboot\[http\]"):
        _repo().service("http")


def test_file_services_do_not_need_the_http_extra(monkeypatch):
    monkeypatch.setattr(
        content, "_importlib_util", types.SimpleNamespace(find_spec=lambda name: None)
    )
    assert str(_repo(local="/srv/tftp").service(None)) == "file:/srv/tftp"


def test_repository_joinpath_chains_without_local():
    # Regression: .local must stay a Pathname so chained joins work.
    repo = Repository(address="host", services={"http": "http://host/base"})
    chained = repo.joinpath("a").joinpath("b")
    assert str(chained.services["http"]).endswith("/a/b")


@requires_http
def test_a_host_and_port_address_builds_a_real_authority(monkeypatch):
    # "mirror.example:8080" used to percent-encode the colon into the hostname,
    # and the whole string was handed to DNS -- a lookup that can only fail, and
    # does so at different speeds on different machines. Nothing here resolves.
    monkeypatch.setattr(__import__("netimps"), "get_ip", lambda name, *a, **kw: None)
    repo = Repository(address="mirror.example:8080", services={"http": "/boot"})
    assert str(repo.service("http")).startswith("http://mirror.example:8080/")


@requires_http
def test_an_ipv6_literal_address_keeps_its_brackets(monkeypatch):
    monkeypatch.setattr(__import__("netimps"), "get_ip", lambda name, *a, **kw: None)
    repo = Repository(address="[2001:db8::1]:8080", services={"http": "/boot"})
    assert str(repo.service("http")).startswith("http://[2001:db8::1]:8080/")


@requires_http
def test_https_keeps_the_configured_name_so_tls_still_validates(monkeypatch):
    # Substituting the resolved IP breaks certificate checks and name-based
    # virtual hosts; other schemes still resolve, for clients without DNS.
    monkeypatch.setattr(
        __import__("netimps"),
        "get_ip",
        lambda name, *a, **kw: netboot.netutils.parse("192.0.2.40"),
    )
    repo = Repository(
        address="mirror.example", services={"https": "/boot", "http": "/boot"}
    )
    assert "mirror.example" in str(repo.service("https"))
    assert "192.0.2.40" in str(repo.service("http"))


@requires_http
def test_a_repo_without_an_address_warns(caplog):
    import logging

    repo = Repository(address=None, services={"http": "/boot"})
    with caplog.at_level(logging.WARNING, logger="netboot"):
        repo.service("http")
    assert "no address" in caplog.text


def test_joinpath_treats_an_absolute_subpath_as_relative():
    # `repo / "/abs"` used to discard the repository root entirely.
    repo = Repository(address="10.0.0.1", services={}, local="/srv/tftp")
    assert "/srv/tftp/abs" in str((repo / "/abs").service(None))
    assert str((repo / "sub/").service(None)).endswith("/srv/tftp/sub")
