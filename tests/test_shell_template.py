"""The shell engine's configuration surface and its default expressions.

The engine claims `.shtpl` and nothing else; `EXT`, `DELIMITER` and `PATTERN`
are the three class attributes a subclass sets to claim something different.
"""

from argparse import Namespace

import pytest
from pathlib_next import PosixPathname

import netboot
from netboot.templates import TemplateEngineError
from netboot.templates.jinja import JinjaTemplate
from netboot.templates.shell import ShellTemplate


def render(source, mode="strict", cls=ShellTemplate, **context):
    template = cls(source)
    template._globals_ = {"ctx": Namespace(**context)}
    template._undefined_ = mode
    return template.render()


# -- suffix gating ---------------------------------------------------------


@pytest.mark.parametrize(
    "name, claimed",
    [
        ("boot.shtpl", True),
        ("boot.SHTPL", True),  # suffix match is case-insensitive
        ("boot.cfg.shtpl", True),
        ("boot.cfg", False),  # the pre-0.3 catch-all is gone
        ("install.ks", False),
        ("boot.j2", False),
    ],
)
def test_the_shell_engine_claims_only_its_own_suffix(name, claimed):
    assert ShellTemplate.can_process(PosixPathname(name), "") is claimed


@pytest.mark.parametrize("ext", ["tpl", ".tpl", ("tpl",), [".tpl", ".cfg"]])
def test_ext_accepts_a_single_value_a_dotless_one_and_a_sequence(ext):
    class Custom(ShellTemplate):
        EXT = ext

    assert Custom.can_process(PosixPathname("boot.tpl"), "") is True
    assert Custom.can_process(PosixPathname("boot.shtpl"), "") is False


@pytest.mark.parametrize("ext", [None, "*", ("*",)])
def test_ext_can_restore_the_catch_all(ext):
    class CatchAll(ShellTemplate):
        EXT = ext

    assert CatchAll.can_process(PosixPathname("boot.cfg"), "") is True


def test_the_jinja_engine_claims_its_suffixes_through_ext():
    assert JinjaTemplate.EXT == (".j2", ".jinja", ".jinja2")
    assert JinjaTemplate.can_process(PosixPathname("a.jinja2"), "") is True
    assert JinjaTemplate.can_process(PosixPathname("a.shtpl"), "") is False


def test_a_file_no_engine_claims_is_an_error_naming_the_suffixes(tmp_path):
    root = tmp_path / "templates"
    root.mkdir()
    (root / "boot.cfg").write_text("kernel %{IMAGE__ID}")
    engine = netboot.Pixie(
        templates=[root],
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}},
    )
    ctx = engine.make_context(engine.lookup_target("host1"))
    with pytest.raises(TemplateEngineError) as excinfo:
        ctx.render("boot.cfg")
    message = str(excinfo.value)
    assert "boot.cfg" in message and ".shtpl" in message and ".j2" in message


# -- delimiter and pattern -------------------------------------------------


def test_a_subclass_can_change_the_delimiter():
    class Dollar(ShellTemplate):
        DELIMITER = "$"

    assert render("${HOST} %{HOST}", cls=Dollar, host="h1") == "h1 %{HOST}"
    assert render("$$done", cls=Dollar, host="h1") == "$done"


def test_the_unbraced_pattern_substitutes_the_bare_form_only():
    class Unbraced(ShellTemplate):
        PATTERN = "unbraced"

    assert render("%HOST %{HOST}", cls=Unbraced, host="h1") == "h1 %{HOST}"


def test_the_all_pattern_substitutes_both_forms():
    class Both(ShellTemplate):
        PATTERN = "all"

    assert render("%HOST %{HOST}", cls=Both, host="h1") == "h1 h1"


def test_the_default_pattern_is_braced_so_a_bare_percent_is_text():
    assert render("%HOST %{HOST}", host="h1") == "%HOST h1"


def test_an_unknown_pattern_is_reported_naming_the_accepted_words():
    class Broken(ShellTemplate):
        PATTERN = "curly"

    with pytest.raises(ValueError, match="braced, unbraced, all"):
        render("x", cls=Broken)


def test_a_subclass_does_not_inherit_its_parents_compiled_pattern():
    # Regression guard: the pattern is cached, and caching it as an inherited
    # attribute would give a subclass its parent's delimiter.
    assert render("%{HOST}", host="h1") == "h1"

    class Dollar(ShellTemplate):
        DELIMITER = "$"

    assert render("${HOST}", cls=Dollar, host="h1") == "h1"
    assert render("%{HOST}", host="h1") == "h1"


# -- POSIX default expressions --------------------------------------------


def test_colon_dash_applies_to_an_unset_and_an_empty_value():
    assert render("%{MISSING:-fallback}") == "fallback"
    assert render("%{HOST:-fallback}", host="") == "fallback"
    assert render("%{HOST:-fallback}", host="h1") == "h1"


def test_bare_dash_applies_only_to_an_unset_value():
    assert render("%{MISSING-fallback}") == "fallback"
    assert render("[%{HOST-fallback}]", host="") == "[]"


def test_one_layer_of_quotes_is_stripped_from_a_default():
    assert render("%{MISSING:-'a default value'}") == "a default value"
    assert render('%{MISSING:-"a default value"}') == "a default value"
    assert render("%{MISSING:-''}") == ""
    # Mismatched or inner quotes are part of the value.
    assert render("%{MISSING:-\"it's fine}") == "\"it's fine"


def test_a_default_is_literal_text():
    # No nesting, no expansion: whatever is written is what renders.
    assert render("%{MISSING:-%{HOST}}", host="h1") == "%{HOST}"


def test_a_default_satisfies_every_undefined_mode():
    for mode in ("strict", "lenient", "debug"):
        assert render("%{MISSING:-ok}", mode) == "ok"


def test_a_value_of_none_counts_as_no_value():
    # flatten renders None as "", so `:-` fills it in and `-` does not.
    assert render("%{HOST:-fallback}", host=None) == "fallback"


def test_an_unset_variable_without_a_default_still_follows_the_switch():
    with pytest.raises(KeyError):
        render("%{MISSING}")
    assert render("%{MISSING}", "lenient") == ""
    assert render("%{MISSING}", "debug") == "%{MISSING}"
    assert render("%{MISSING:?boom}", "debug") == "%{MISSING:?boom}"  # not implemented
