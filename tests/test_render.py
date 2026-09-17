"""End-to-end-ish test: build a Pixie from config and render a template.

Exercises target/image/dhcpzone lookup, context construction, and both the
Jinja2 and shell template engines against a temporary search path.
"""

import pytest

import netboot
from netboot.templates.shell import ShellTemplate


@pytest.fixture
def templates_dir(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "boot.j2").write_text("host={{ ctx.target.hostname }} img={{ ctx.image._id }}")
    (d / "boot.sh").write_text("HOST=%{TARGET_HOSTNAME} IMG=%{IMAGE__ID}")
    return d


def _make_netboot(templates_dir):
    config = {
        "templates": [templates_dir],
        "images": {"debian": {"template_path": []}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"},
        },
    }
    return netboot.Pixie(**config)


def test_lookup_and_make_context(templates_dir):
    p = _make_netboot(templates_dir)
    target = p.lookup_target("host1")
    assert target is not None
    ctx = p.make_context(target)
    assert ctx.image._id == "debian"
    assert str(ctx.dhcpzone.network.network_address) == "10.0.0.0"


def test_render_jinja(templates_dir):
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    assert ctx.render("boot.j2") == "host=host1 img=debian"


def test_render_shell(templates_dir):
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    out = ctx.render("boot.sh")
    assert "HOST=host1" in out
    assert "IMG=debian" in out


def test_lookup_target_by_ip(templates_dir):
    p = _make_netboot(templates_dir)
    assert p.lookup_target("10.0.0.5") is p.lookup_target("host1")


KICKSTART = """\
%packages
@core
%end

%pre --log=/tmp/pre.log
echo host=%{TARGET_HOSTNAME} ip=%{TARGET_IP}
%post
echo done
%end
"""


def test_shell_engine_renders_a_kickstart_with_percent_sections(templates_dir):
    # Regression: `%packages`/`%pre`/`%post` used to be read as placeholders,
    # so a normal kickstart file could not be rendered at all.
    (templates_dir / "install.ks").write_text(KICKSTART)
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    out = ctx.render("install.ks")
    assert "%packages" in out and "%pre --log=/tmp/pre.log" in out and "%end" in out
    assert "echo host=host1 ip=10.0.0.5" in out


def test_shell_engine_leaves_the_unbraced_form_literal(templates_dir):
    # Only `%{NAME}` substitutes; a bare `%NAME` is text, even when it names a
    # real context variable.
    (templates_dir / "old.sh").write_text("HOST=%TARGET_HOSTNAME")
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    assert ctx.render("old.sh") == "HOST=%TARGET_HOSTNAME"


def test_shell_engine_leaves_an_unknown_bare_percent_word_alone(templates_dir):
    (templates_dir / "plain.sh").write_text("cp %sourcefile /boot && date +%Y%m%d")
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    assert ctx.render("plain.sh") == "cp %sourcefile /boot && date +%Y%m%d"


def test_shell_engine_still_escapes_a_doubled_percent(templates_dir):
    (templates_dir / "esc.sh").write_text("100%% done %{TARGET_HOSTNAME}")
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    assert ctx.render("esc.sh") == "100% done host1"


def test_shell_engine_raises_for_an_unknown_braced_name(templates_dir):
    (templates_dir / "bad.sh").write_text("X=%{NOSUCHVARIABLE}")
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    with pytest.raises(KeyError):
        ctx.render("bad.sh")


def test_image_without_template_path_still_renders(templates_dir):
    # Regression: a missing `template_path` made `ctx.searchpaths` raise
    # AttributeError, so every render for that image failed.
    config = {
        "templates": [templates_dir],
        "images": {"debian": {}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}
        },
    }
    p = netboot.Pixie(**config)
    ctx = p.make_context(p.lookup_target("host1"))
    assert ctx.image.template_path == []
    assert ctx.render("boot.j2") == "host=host1 img=debian"


def test_exact_filename_beats_a_stem_match(templates_dir):
    # A leftover `.bak` next to the real file must never win.
    (templates_dir / "boot.j2.bak").write_text("STALE")
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    assert ctx.render("boot.j2") == "host=host1 img=debian"


def test_stem_matches_are_resolved_in_a_stable_order(templates_dir):
    # `boot` matches boot.j2 and boot.sh; whichever wins, it must be the same
    # on every filesystem -- lowest name, not directory-listing order.
    out = _make_netboot(templates_dir)
    ctx = out.make_context(out.lookup_target("host1"))
    assert ctx.render("boot") == "host=host1 img=debian"  # boot.j2 < boot.sh


def test_templates_are_read_as_utf8(templates_dir):
    (templates_dir / "utf8.j2").write_text(
        "mot={{ ctx.image._id }}-café-日本", encoding="utf-8"
    )
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    assert ctx.render("utf8.j2") == "mot=debian-café-日本"


def test_jinja_keeps_the_templates_trailing_newline(templates_dir):
    # A boot artifact whose last line lost its newline is a different file, and
    # the shell engine kept it while Jinja did not.
    (templates_dir / "trailing.j2").write_text("label {{ ctx.target.hostname }}\n")
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    assert ctx.render("trailing.j2") == "label host1\n"


def test_mac_less_targets_do_not_share_a_null_mac_candidate(templates_dir):
    # Every MAC-less target used to look for `00-00-00-00-00-00.<name>` first,
    # so one stray file of that name applied to all of them.
    (templates_dir / "00-00-00-00-00-00.boot.sh").write_text("WRONG")
    (templates_dir / "boot.sh").write_text("HOST=%{TARGET_HOSTNAME}")
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    assert ctx.render("boot.sh") == "HOST=host1"


def test_a_uri_search_path_is_not_turned_into_a_local_directory():
    from netboot.templates import Loader

    loader = Loader(["http://boot.example/templates", "templates"])
    assert str(loader.searchpaths[0]).startswith("http://")


def test_shell_templates_notice_an_edited_file(templates_dir):
    # `is_up_to_date` was assigned the checker function itself, which is always
    # truthy, so a cached template never reloaded.
    from netboot.templates import Loader
    from netboot.templates.common import Renderer

    (templates_dir / "cached.sh").write_text("v1")
    p = _make_netboot(templates_dir)
    ctx = p.make_context(p.lookup_target("host1"))
    renderer = Renderer(loader=Loader([templates_dir]))
    renderer.globals["ctx"] = ctx
    template = renderer.get_template("cached.sh")
    assert template.is_up_to_date is True
    import os
    import time

    time.sleep(0.01)
    (templates_dir / "cached.sh").write_text("v2")
    os.utime(templates_dir / "cached.sh", (time.time() + 1, time.time() + 1))
    assert template.is_up_to_date is False


def test_template_names_accepts_options(tmp_path):
    # Regression: _template_names(**options) must not TypeError.
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


@pytest.mark.parametrize("unset_ip", ["", "0.0.0.0", "::"])
def test_template_names_skips_an_unset_ip(unset_ip):
    # An unset ip must not emit a name; MAC/hostname still do. The previous
    # version of this test never built a 0.0.0.0 target, so it did not pin the
    # guard it was named after.
    from netboot import PixieContext, PixieTarget

    ctx = PixieContext.__new__(PixieContext)
    ctx.target = PixieTarget(_id="aa:bb:cc:dd:ee:ff", ip=unset_ip)
    assert str(ctx.target.ip or "") in ("", unset_ip)
    names = ctx._template_names("boot.j2")
    assert not any(n.startswith(("0.0.0.0", "::")) for n in names)
    assert names == ["aa-bb-cc-dd-ee-ff.boot.j2", "boot.j2"]


def test_shell_template_renders_none_as_empty():
    # Regression: None must render as '' not the literal 'None'.
    from argparse import Namespace

    t = ShellTemplate("D=%{DOMAIN} B=%{FLAG}")
    t._globals_ = {"ctx": Namespace(domain=None, flag=True)}
    assert t.render() == "D= B=true"
