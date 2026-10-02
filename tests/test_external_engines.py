"""ERB and EPP, rendered by the real `ruby` and `puppet`.

These skip where the program is absent, which includes most CI runners -- so the
engine-shape tests (registration, suffixes, the error when the program is
missing) are written to run *everywhere*, and only the rendering tests skip.
"""

import logging
import shutil

import pytest
from pathlib_next import PosixPathname

import netboot
from netboot.templates import (
    TEMPLATE_TYPES,
    CopyTemplate,
    EppTemplate,
    ERBTemplate,
    Loader,
    TemplateEngineError,
)

has_ruby = shutil.which("ruby") is not None
has_puppet = shutil.which("puppet") is not None


@pytest.fixture
def templates(tmp_path):
    root = tmp_path / "templates"
    root.mkdir()
    return root


def _write(root, name, text):
    (root / name).write_bytes(text.encode("utf-8"))


def _ctx(root):
    engine = netboot.Pixie(
        templates=[root],
        images={"debian": {"template_path": [], "globals": {"kernel": "vmlinuz"}}},
        dhcpzones={
            "lan": {"network": "10.0.0.0/24", "nameservers": ["10.0.0.53", "10.0.0.54"]}
        },
        targets={"host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}},
    )
    return engine.make_context(engine.lookup_target("host1"))


# -- shape: runs with or without the programs -------------------------------


@pytest.mark.parametrize("engine", [ERBTemplate, EppTemplate])
def test_each_engine_is_registered_before_the_fallback(engine):
    types = Loader([]).template_types
    assert engine in TEMPLATE_TYPES
    assert types.index(engine) < types.index(CopyTemplate)


@pytest.mark.parametrize(
    "engine, mine, foreign",
    [(ERBTemplate, "boot.erb", "boot.epp"), (EppTemplate, "boot.epp", "boot.erb")],
)
def test_each_engine_claims_its_own_suffix(engine, mine, foreign):
    assert engine.can_process(PosixPathname(mine), "") is True
    assert engine.can_process(PosixPathname(foreign), "") is False


@pytest.mark.parametrize(
    "engine, env_var",
    [(ERBTemplate, "PIXIE_RUBY"), (EppTemplate, "PIXIE_PUPPET")],
)
def test_a_missing_program_is_reported_with_the_env_override(engine, env_var, tmp_path):
    missing = tmp_path / "definitely-not-here"

    class _Missing(engine):
        COMMAND = str(missing)

    with pytest.raises(TemplateEngineError) as excinfo:
        _Missing("x")
    assert env_var in str(excinfo.value)


@pytest.mark.parametrize(
    "engine, env_var", [(ERBTemplate, "PIXIE_RUBY"), (EppTemplate, "PIXIE_PUPPET")]
)
def test_the_env_var_wins_over_path(engine, env_var, monkeypatch, tmp_path):
    fake = tmp_path / "fake-program"
    fake.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setenv(env_var, str(fake))
    assert engine.executable() == str(fake)


def test_a_failing_program_reports_its_stderr(templates, monkeypatch):
    # The engine must surface the renderer's own message: a syntax error in a
    # template is the common case, and "exit 1" alone is useless.
    import sys

    class _Failing(ERBTemplate):
        COMMAND = sys.executable

        def command(self, program, template_path, values_path):
            return [program, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]

    template = _Failing("x")
    template._globals_ = {}
    with pytest.raises(TemplateEngineError, match="boom"):
        template.render()


def test_a_hanging_program_times_out():
    import sys

    class _Hanging(ERBTemplate):
        COMMAND = sys.executable
        TIMEOUT = 1

        def command(self, program, template_path, values_path):
            return [program, "-c", "import time; time.sleep(30)"]

    template = _Hanging("x")
    template._globals_ = {}
    with pytest.raises(TemplateEngineError, match="within 1s"):
        template.render()


def test_crlf_from_the_program_is_normalised_when_the_engine_asks(tmp_path):
    # Ruby-based programs translate stdout on Windows; an artifact must not
    # depend on which host rendered it. ERB stops ruby doing it ($stdout.binmode);
    # EPP cannot, so it normalises -- this covers that path without puppet.
    import sys

    emit = "import sys; sys.stdout.buffer.write(b'a\\r\\nb\\r\\n')"

    class _Crlf(EppTemplate):
        COMMAND = sys.executable

        def command(self, program, template_path, values_path):
            return [program, "-c", emit]

    template = _Crlf("x")
    template._globals_ = {}
    assert template.render() == "a\nb\n"

    class _Kept(_Crlf):
        NEWLINES = None

    kept = _Kept("x")
    kept._globals_ = {}
    assert kept.render() == "a\r\nb\r\n"


def test_the_erb_script_stops_ruby_translating_stdout():
    # The fix has to live in the script, because normalising afterwards would
    # also strip CRLF a template emitted on purpose.
    from netboot.templates.external import _ERB_SCRIPT

    assert "$stdout.binmode" in _ERB_SCRIPT
    assert ERBTemplate.NEWLINES is None


# -- rendering: needs the real programs -------------------------------------

ERB_SOURCE = """\
host <%= @target['hostname'] %> image <%= @image['_id'] %>
kernel <%= @context['ctx']['image']['globals']['kernel'] %>
<% @dhcpzone['nameservers'].each do |ns| -%>
nameserver <%= ns %>
<% end -%>
"""

ERB_EXPECTED = """\
host host1 image debian
kernel vmlinuz
nameserver 10.0.0.53
nameserver 10.0.0.54
"""

EPP_SOURCE = """\
host <%= $target['hostname'] %> image <%= $image['_id'] %>
kernel <%= $ctx['image']['globals']['kernel'] %>
<% $dhcpzone['nameservers'].each |$ns| { -%>
nameserver <%= $ns %>
<% } -%>
"""


@pytest.mark.skipif(not has_ruby, reason="needs ruby installed")
def test_erb_renders_through_ruby(templates):
    _write(templates, "boot.erb", ERB_SOURCE)
    assert _ctx(templates).render("boot.erb") == ERB_EXPECTED


@pytest.mark.skipif(not has_ruby, reason="needs ruby installed")
def test_erb_reports_a_template_error_from_ruby(templates):
    _write(templates, "bad.erb", "<%= @target[ %>")
    with pytest.raises(TemplateEngineError, match="ruby failed to render"):
        _ctx(templates).render("bad.erb")


@pytest.mark.skipif(not has_ruby, reason="needs ruby installed")
def test_erb_keeps_the_templates_own_bytes(templates):
    # No trailing newline added or removed: `print`, not `puts`.
    _write(templates, "nonl.erb", "label <%= @target['hostname'] %>")
    assert _ctx(templates).render("nonl.erb") == "label host1"


@pytest.mark.skipif(not has_puppet, reason="needs puppet installed")
def test_epp_renders_through_puppet(templates):
    _write(templates, "boot.epp", EPP_SOURCE)
    assert _ctx(templates).render("boot.epp") == ERB_EXPECTED


@pytest.mark.skipif(not has_puppet, reason="needs puppet installed")
def test_epp_reports_a_template_error_from_puppet(templates):
    _write(templates, "bad.epp", "<%= $target[ %>")
    with pytest.raises(TemplateEngineError, match="puppet failed to render"):
        _ctx(templates).render("bad.epp")


# -- the JSON the programs receive ------------------------------------------


def test_the_context_is_marshalled_without_the_engine_behind_it():
    # `ctx._netboot_` reaches the whole program and `ctx._renderer` holds a
    # jinja Environment: a naive walk would serialise both and never finish.
    from netboot.templates.data import jsonable, template_data

    engine = netboot.Pixie(
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}},
    )
    ctx = engine.make_context(engine.lookup_target("host1"))
    data = jsonable(template_data({"ctx": ctx}))
    import json

    json.dumps(data)  # must not raise, and must terminate
    assert data["target"]["hostname"] == "host1"
    assert data["image"]["_id"] == "debian"  # `_id` survives the underscore rule
    assert "_netboot_" not in data["ctx"]
    assert "_renderer" not in data["ctx"]
    assert data["target"]["ip"] == "10.0.0.5"  # an address arrives as its string
