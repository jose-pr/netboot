"""Tests for content repositories, resource resolution and `Host.try_ip`.

These assemble every artifact URL a rendered template embeds, so the joining
and host fill-in rules are shipped contract. DNS is monkeypatched throughout --
nothing here resolves a real name.
"""

import netboot
from netboot.content import Repository, Resource
from netboot.utils.net import Host


def _repo(**kwargs):
    kwargs.setdefault("address", "10.0.0.1")
    kwargs.setdefault("services", {"http": "/boot"})
    return Repository(**kwargs)


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


def test_get_joins_the_relative_path_onto_the_service_uri():
    assert str(_repo().get("images/vmlinuz", service="http")).endswith(
        "/boot/images/vmlinuz"
    )


def test_get_strips_a_leading_slash_so_the_base_path_survives():
    # Without the lstrip an absolute rel_path would replace "/boot" entirely.
    assert "/boot/vmlinuz" in str(_repo().get("/vmlinuz", service="http"))


def test_get_accepts_multiple_path_segments():
    assert str(_repo().get("images", "vmlinuz", service="http")).endswith(
        "/boot/images/vmlinuz"
    )


def test_get_returns_none_for_an_unknown_service():
    assert _repo().get("vmlinuz", service="tftp") is None


def test_getitem_is_get_with_the_service_in_the_key():
    repo = _repo()
    assert str(repo["images/vmlinuz", "http"]) == str(
        repo.get("images/vmlinuz", service="http")
    )


def test_joinpath_extends_every_service_and_the_local_path():
    repo = _repo(local="/srv/tftp") / "debian"
    assert str(repo.service("http")).endswith("/boot/debian")
    assert "/srv/tftp/debian" in str(repo.service(None))


def test_address_is_resolved_when_it_is_a_hostname(monkeypatch):
    monkeypatch.setattr(
        netboot.netutils,
        "resolve",
        lambda name, *a, **kw: [netboot.netutils.parse("192.0.2.20")],
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


def test_context_resource_resolves_by_id(tmp_path):
    ctx = _context_with_resources(tmp_path)
    assert str(ctx.resource("kernel", service="http")).endswith("/boot/images/vmlinuz")


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


def test_host_try_ip_short_circuits_an_ip_literal(monkeypatch):
    def _boom(*a, **kw):  # a literal must never reach DNS
        raise AssertionError("resolve() called for an IP literal")

    monkeypatch.setattr(netboot.netutils, "resolve", _boom)
    assert str(Host("10.0.0.7").try_ip()) == "10.0.0.7"


def test_host_try_ip_resolves_a_hostname(monkeypatch):
    monkeypatch.setattr(
        netboot.netutils,
        "resolve",
        lambda name, *a, **kw: [netboot.netutils.parse("192.0.2.30")],
    )
    assert str(Host("mirror.example").try_ip()) == "192.0.2.30"


def test_host_try_ip_falls_back_to_the_raw_string(monkeypatch):
    monkeypatch.setattr(netboot.netutils, "resolve", lambda name, *a, **kw: [])
    assert Host("mirror.example").try_ip() == "mirror.example"


def test_host_try_ip_of_an_empty_address_is_empty():
    assert Host().try_ip() == ""
