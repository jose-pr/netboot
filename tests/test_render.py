"""End-to-end-ish test: build a Piskie from config and render a template.

Exercises target/image/dhcpzone lookup, context construction, and both the
Jinja2 and shell template engines against a temporary search path.
"""

import pytest

import piskie


@pytest.fixture
def templates_dir(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "boot.j2").write_text("host={{ ctx.target.hostname }} img={{ ctx.image._id }}")
    (d / "boot.sh").write_text("HOST=%{TARGET_HOSTNAME} IMG=%{IMAGE__ID}")
    return d


def _make_piskie(templates_dir):
    config = {
        "templates": [templates_dir],
        "images": {"debian": {"template_path": []}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"},
        },
    }
    return piskie.Piskie(**config)


def test_lookup_and_make_context(templates_dir):
    p = _make_piskie(templates_dir)
    target = p.lookup_target("host1")
    assert target is not None
    ctx = p.make_context(target)
    assert ctx.image._id == "debian"
    assert str(ctx.dhcpzone.network.network_address) == "10.0.0.0"


def test_render_jinja(templates_dir):
    p = _make_piskie(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    assert ctx.render("boot.j2") == "host=host1 img=debian"


def test_render_shell(templates_dir):
    p = _make_piskie(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    out = ctx.render("boot.sh")
    assert "HOST=host1" in out
    assert "IMG=debian" in out


def test_lookup_target_by_ip(templates_dir):
    p = _make_piskie(templates_dir)
    assert p.lookup_target("10.0.0.5") is p.lookup_target("host1")
