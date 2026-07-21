"""Regression tests for the issues found in the high-effort code review."""

import inspect

import pytest

import netboot
from netboot.content import Repository
from netboot.templates.shell import ShellTemplate


def _netboot_two_targets_one_image(templates_dir):
    config = {
        "templates": [templates_dir],
        "images": {"debian": {"template_path": [], "globals": {"kernel": "vmlinuz"}}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"},
            "host2": {"hostname": "host2", "ip": "10.0.0.6", "image": "debian"},
        },
    }
    return netboot.Pixie(**config)


def test_image_globals_survive_second_target(tmp_path):
    # Finding #1: make_context must not delattr globals off the shared image.
    d = tmp_path / "templates"
    d.mkdir()
    p = _netboot_two_targets_one_image(d)
    ctx1 = p.make_context(p.lookup_target("host1"))
    ctx2 = p.make_context(p.lookup_target("host2"))
    assert getattr(ctx1, "kernel", None) == "vmlinuz"
    assert getattr(ctx2, "kernel", None) == "vmlinuz"  # not dropped for host2


def test_template_names_accepts_options(tmp_path):
    # Finding #2: _template_names(**options) must not TypeError.
    d = tmp_path / "templates"
    d.mkdir()
    config = {
        "templates": [d],
        "images": {"deb": {"template_path": []}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {"h": {"hostname": "h", "ip": "10.0.0.9", "image": "deb"}},
    }
    p = netboot.Pixie(**config)
    ctx = p.make_context(p.lookup_target("h"))
    names = ctx._template_names("boot.j2", foo="bar")
    assert "boot.j2" in names
    assert any(n == "10.0.0.9.boot.j2" for n in names)  # IP stringified in name


def test_template_names_skips_empty_ip():
    # Finding #6: an unset ip must not emit a name; MAC/hostname still do.
    # Drive _template_names directly with a minimal fake context to avoid the
    # zone-by-network lookup (which needs a resolvable IP).
    from netboot import PixieContext, PixieTarget

    ctx = PixieContext.__new__(PixieContext)
    ctx.target = PixieTarget(_id="aa:bb:cc:dd:ee:ff")
    names = ctx._template_names("boot.j2")
    assert not any(n.startswith("0.0.0.0") for n in names)
    assert "aa-bb-cc-dd-ee-ff.boot.j2" in names
    assert "boot.j2" in names


def test_shell_template_renders_none_as_empty():
    # Finding #5: None must render as '' not the literal 'None'.
    from argparse import Namespace

    t = ShellTemplate("D=%{DOMAIN} B=%{FLAG}")
    t._globals_ = {"ctx": Namespace(domain=None, flag=True)}
    assert t.render() == "D= B=true"


def test_wants_netboot_signature_detection():
    # Finding #3: only a 3-arg run(netboot, args, conf) gets the netboot-first call.
    from netboot.main import _wants_netboot

    def netboot_run(netboot, args, conf):
        ...

    def duho_run(args):
        ...

    assert _wants_netboot(netboot_run) is True
    assert _wants_netboot(duho_run) is False
    assert _wants_netboot(None) is False


def test_repository_joinpath_chains_without_local():
    # Finding #10: .local must stay a Pathname so chained joins work.
    repo = Repository(address="host", services={"http": "http://host/base"})
    chained = repo.joinpath("a").joinpath("b")
    assert str(chained.services["http"]).endswith("/a/b")


class _Boom:
    def __init__(self, _id=None, **kw):
        raise ValueError("boom")


class _BoomPixie(netboot.Pixie):
    things: "dict[str, _Boom]"


def test_valctr_typeerror_only_not_bare_except():
    # Finding #4: a non-TypeError in a config value ctor must propagate, rather
    # than being swallowed by a bare except and retried without _id.
    with pytest.raises(ValueError):
        _BoomPixie(things={"x": {}})
