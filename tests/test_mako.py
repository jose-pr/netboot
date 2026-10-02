"""The mako engine, which is only usable with the `mako` extra installed.

The engine is registered either way on purpose: a `.mako` file must say what is
missing rather than fall through to the copy engine, so the one test that covers
the missing extra does not need mako absent.
"""

import sys

import pytest

import netboot
from netboot.templates import TEMPLATE_TYPES, Loader
from netboot.templates.mako import MakoTemplate

mako = pytest.importorskip("mako", reason="needs the 'mako' extra")


@pytest.fixture
def templates(tmp_path):
    root = tmp_path / "templates"
    root.mkdir()
    return root


def _write(root, name, text):
    """Write a template with LF endings whatever the platform."""
    (root / name).write_bytes(text.encode("utf-8"))


def _ctx(root):
    engine = netboot.Pixie(
        templates=[root],
        images={"debian": {"template_path": [], "globals": {"kernel": "vmlinuz"}}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}},
    )
    return engine.make_context(engine.lookup_target("host1"))


def test_the_engine_is_registered_ahead_of_the_fallback():
    types = Loader([]).template_types
    assert MakoTemplate in TEMPLATE_TYPES
    assert types.index(MakoTemplate) < types.index(
        __import__("netboot.templates", fromlist=["CopyTemplate"]).CopyTemplate
    )


def test_it_claims_the_mako_suffix_only(templates):
    from pathlib_next import PosixPathname

    assert MakoTemplate.can_process(PosixPathname("boot.mako"), "") is True
    assert MakoTemplate.can_process(PosixPathname("boot.j2"), "") is False


def test_it_renders_the_context(templates):
    _write(
        templates,
        "boot.mako",
        "host=${ctx.target.hostname} img=${ctx.image._id} kernel=${ctx.kernel}\n",
    )
    assert (
        _ctx(templates).render("boot.mako") == "host=host1 img=debian kernel=vmlinuz\n"
    )


def test_the_namespace_matches_the_jinja_engines(templates):
    # shell_quote, Path and Uri are in scope in both engines, so a template can
    # be ported between them without losing helpers.
    _write(templates, "q.mako", "${shell_quote('$6$salt$hash')} ${Uri('tftp://h/p')}")
    assert _ctx(templates).render("q.mako") == "'$6$salt$hash' tftp://h/p"


def test_a_control_structure_works(templates):
    # Not a smoke test of mako itself: it proves the source reaches mako intact
    # (indentation and the `%` line prefix survive the read).
    _write(templates, "loop.mako", "% for n in [1, 2]:\nline ${n}\n% endfor\n")
    assert _ctx(templates).render("loop.mako") == "line 1\nline 2\n"


def test_the_trailing_newline_survives(templates):
    # The loader reads bytes, and mako keeps the newlines the file has -- so
    # this also pins that a template's own line endings reach the artifact.
    _write(templates, "nl.mako", "label ${ctx.target.hostname}\n")
    assert _ctx(templates).render("nl.mako") == "label host1\n"


def test_strict_is_the_default_and_names_the_variable(templates):
    _write(templates, "typo.mako", "kernel=${kernl}")
    with pytest.raises(NameError, match="kernl"):
        _ctx(templates).render("typo.mako")


@pytest.mark.parametrize("mode", ["lenient", "debug"])
def test_lenient_renders_an_undefined_name_empty(templates, mode, caplog):
    # `debug` has no mako spelling -- a compiled template cannot re-emit the
    # `${...}` it came from -- so it behaves as lenient, and says so in the log.
    import logging

    _write(templates, "typo.mako", "kernel=[${kernl}]")
    engine = netboot.Pixie(
        templates=[templates],
        templates_undefined=mode,
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}},
    )
    ctx = engine.make_context(engine.lookup_target("host1"))
    with caplog.at_level(logging.WARNING, logger="netboot"):
        assert ctx.render("typo.mako") == "kernel=[]"
    assert "kernl" in caplog.text


def test_a_name_mako_reserves_is_dropped_with_a_warning(caplog):
    # mako owns `next`, `self`, `context`...; passing one through would raise
    # TypeError for a duplicate argument inside mako instead of saying why.
    import logging

    template = MakoTemplate("ok ${safe}")
    template._globals_ = {"next": "collides", "safe": "kept"}
    with caplog.at_level(logging.WARNING, logger="netboot"):
        assert template.render() == "ok kept"
    assert "next" in caplog.text and "reserved" in caplog.text


def test_without_the_extra_the_error_names_it(templates, monkeypatch):
    # `None` in sys.modules is the documented way to make an import fail; the
    # engine must wrap it with the extra to install rather than let mako's own
    # ImportError surface.
    monkeypatch.setitem(sys.modules, "mako.template", None)
    _write(templates, "boot.mako", "host=${ctx.target.hostname}")
    with pytest.raises(ImportError, match=r"netboot\[mako\]"):
        _ctx(templates).render("boot.mako")
