"""The data-language engines behind an extra: liquid, handlebars, mustache.

Each is registered whether or not its library is installed, so a file it claims
reports the missing extra instead of being copied by the fallback engine. The
fixtures write bytes: `Path.write_text` translates "\\n" to CRLF on Windows, and
only Jinja normalises newlines for itself.
"""

import logging
import re
import sys

import pytest
from pathlib_next import PosixPathname

import netboot
from netboot.templates import (
    TEMPLATE_TYPES,
    CopyTemplate,
    HandlebarsTemplate,
    Loader,
    LiquidTemplate,
    MustacheTemplate,
)

pytest.importorskip("liquid", reason="needs the 'liquid' extra")
pytest.importorskip("pybars", reason="needs the 'handlebars' extra")
pytest.importorskip("chevron", reason="needs the 'mustache' extra")

#: engine, filename, source, expected output.
CASES = [
    (
        LiquidTemplate,
        "boot.liquid",
        "host={{ ctx.target.hostname }} img={{ image._id }} k={{ ctx.kernel }}\n",
        "host=host1 img=debian k=vmlinuz\n",
    ),
    (
        HandlebarsTemplate,
        "boot.hbs",
        "host={{ ctx.target.hostname }} img={{ image._id }} k={{ ctx.kernel }}\n",
        "host=host1 img=debian k=vmlinuz\n",
    ),
    (
        MustacheTemplate,
        "boot.mustache",
        "host={{ ctx.target.hostname }} img={{ image._id }} k={{ ctx.kernel }}\n",
        "host=host1 img=debian k=vmlinuz\n",
    ),
]

IDS = [case[0].__name__ for case in CASES]


def _write(root, name, text):
    (root / name).write_bytes(text.encode("utf-8"))


def _ctx(root, mode=None):
    config = {
        "templates": [root],
        "images": {"debian": {"template_path": [], "globals": {"kernel": "vmlinuz"}}},
        "dhcpzones": {"lan": {"network": "10.0.0.0/24"}},
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}
        },
    }
    if mode is not None:
        config["templates_undefined"] = mode
    engine = netboot.Pixie(**config)
    return engine.make_context(engine.lookup_target("host1"))


@pytest.fixture
def templates(tmp_path):
    root = tmp_path / "templates"
    root.mkdir()
    return root


@pytest.mark.parametrize("engine, name, source, expected", CASES, ids=IDS)
def test_it_renders_the_context(templates, engine, name, source, expected):
    # `ctx.target.hostname` and the bare `image._id` both resolve: the data view
    # exposes the context's own attributes at the top level too.
    _write(templates, name, source)
    assert _ctx(templates).render(name) == expected


@pytest.mark.parametrize("engine, name, source, expected", CASES, ids=IDS)
def test_each_engine_is_registered_before_the_fallback(engine, name, source, expected):
    types = Loader([]).template_types
    assert engine in TEMPLATE_TYPES
    assert types.index(engine) < types.index(CopyTemplate)


@pytest.mark.parametrize(
    "engine, suffix, foreign",
    [
        (LiquidTemplate, "boot.liquid", "boot.hbs"),
        (HandlebarsTemplate, "boot.hbs", "boot.liquid"),
        (HandlebarsTemplate, "boot.handlebars", "boot.mustache"),
        (MustacheTemplate, "boot.mustache", "boot.liquid"),
    ],
)
def test_each_engine_claims_its_own_suffixes(engine, suffix, foreign):
    assert engine.can_process(PosixPathname(suffix), "") is True
    assert engine.can_process(PosixPathname(foreign), "") is False


@pytest.mark.parametrize("engine, name, source, expected", CASES, ids=IDS)
def test_a_loop_over_a_context_list_works(templates, engine, name, source, expected):
    # A sequence in the context must survive the data view as a sequence, or
    # every `{{#each}}`/`{{% for %}}` over nameservers renders nothing.
    sources = {
        LiquidTemplate: "{% for n in dhcpzone.nameservers %}[{{ n }}]{% endfor %}",
        HandlebarsTemplate: "{{#each dhcpzone.nameservers}}[{{this}}]{{/each}}",
        MustacheTemplate: "{{#dhcpzone.nameservers}}[{{.}}]{{/dhcpzone.nameservers}}",
    }
    root = templates
    config = {
        "templates": [root],
        "images": {"debian": {"template_path": []}},
        "dhcpzones": {
            "lan": {"network": "10.0.0.0/24", "nameservers": ["10.0.0.53", "10.0.0.54"]}
        },
        "targets": {
            "host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}
        },
    }
    pixie = netboot.Pixie(**config)
    ctx = pixie.make_context(pixie.lookup_target("host1"))
    _write(root, name, sources[engine])
    assert ctx.render(name) == "[10.0.0.53][10.0.0.54]"


def test_liquid_honours_all_three_undefined_modes(templates):
    from liquid.exceptions import UndefinedError

    _write(templates, "u.liquid", "k=[{{ nosuchvar }}]")
    with pytest.raises(UndefinedError):
        _ctx(templates, "strict").render("u.liquid")
    assert _ctx(templates, "lenient").render("u.liquid") == "k=[]"
    # liquid's DebugUndefined names the variable in the output rather than
    # re-emitting `{{ ... }}`, which is the same intent: visible in the artifact.
    assert _ctx(templates, "debug").render("u.liquid") == "k=['nosuchvar' is undefined]"


@pytest.mark.parametrize(
    "engine, name",
    [(HandlebarsTemplate, "u.hbs"), (MustacheTemplate, "u.mustache")],
)
@pytest.mark.parametrize("mode", ["strict", "lenient", "debug"])
def test_the_logic_less_engines_are_always_lenient(templates, engine, name, mode):
    # Documented, not a bug: neither library can fail on an undefined name, so
    # `strict` renders an empty string like every other mode. The support matrix
    # in the docs says so, and this test is what keeps it honest.
    _write(templates, name, "k=[{{ nosuchvar }}]")
    assert _ctx(templates, mode).render(name) == "k=[]"


def test_strict_on_a_logic_less_engine_says_so_in_the_log(templates, caplog):
    _write(templates, "u.hbs", "k=[{{ nosuchvar }}]")
    with caplog.at_level(logging.DEBUG, logger="netboot"):
        _ctx(templates, "strict").render("u.hbs")
    assert "strict" in caplog.text


@pytest.mark.parametrize(
    "engine, name, module, extra",
    [
        (LiquidTemplate, "boot.liquid", "liquid", "netboot[liquid]"),
        (HandlebarsTemplate, "boot.hbs", "pybars", "netboot[handlebars]"),
        (MustacheTemplate, "boot.mustache", "chevron", "netboot[mustache]"),
    ],
)
def test_without_its_extra_the_error_names_it(
    templates, monkeypatch, engine, name, module, extra
):
    # `None` in sys.modules is the documented way to make an import fail. The
    # engine stays registered, so the file is claimed and the error is about the
    # missing extra -- not a silent copy.
    monkeypatch.setitem(sys.modules, module, None)
    _write(templates, name, "x")
    # re.escape, not an f-string with a backslash: 3.9 rejects that at parse time.
    with pytest.raises(ImportError, match=re.escape(extra)):
        _ctx(templates).render(name)


def test_registering_the_engines_imports_none_of_their_libraries():
    # A clean install must not pay for engines it does not use. Run in a
    # subprocess: by this point in the suite the libraries are long imported.
    import subprocess

    code = (
        "import sys\n"
        "for name in ('liquid', 'pybars', 'chevron', 'mako'):\n"
        "    sys.modules[name] = None\n"
        "import netboot.templates\n"
        "print(','.join(t.__name__ for t in netboot.templates.TEMPLATE_TYPES))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    # Every engine registers; none of their libraries is imported to do it.
    assert result.stdout.strip().split(",") == [t.__name__ for t in TEMPLATE_TYPES]
