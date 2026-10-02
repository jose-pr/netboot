"""Registering a template engine, and the priority that orders them.

The point of `PRIORITY` living apart from registration order: a plugin is
imported *after* the shipped engines, so if order decided, every plugin would
land behind the catch-all `CopyTemplate` and never see a file.
"""

import pytest

import netboot
from netboot.templates import (
    DEFAULT_PRIORITY,
    FALLBACK_PRIORITY,
    TEMPLATE_TYPES,
    CopyTemplate,
    JinjaTemplate,
    Loader,
    ShellTemplate,
    Template,
    by_priority,
    register_template_type,
    unregister_template_type,
)


@pytest.fixture(autouse=True)
def _restore_registry():
    """The registry is process-wide; put it back whatever a test does to it."""
    before = list(TEMPLATE_TYPES)
    yield
    TEMPLATE_TYPES[:] = before


class _Shouty(Template):
    """A toy engine, to be registered by the tests that need it."""

    EXT = ".shouty"

    def __init__(self, template: str) -> None:
        self._source = template

    def render(self, **extras):
        return self._source.upper()


def _engine_with(root):
    return netboot.Pixie(
        templates=[root],
        images={"debian": {"template_path": []}},
        dhcpzones={"lan": {"network": "10.0.0.0/24"}},
        targets={"host1": {"hostname": "host1", "ip": "10.0.0.5", "image": "debian"}},
    )


def test_the_shipped_engines_are_registered_with_the_fallback_last():
    names = [t.__name__ for t in TEMPLATE_TYPES]
    # Every shipped engine, optional ones included -- registration never
    # depends on a library being installed.
    assert names[0] == "JinjaTemplate"
    assert names[-1] == "CopyTemplate"
    assert {
        "MakoTemplate",
        "LiquidTemplate",
        "HandlebarsTemplate",
        "MustacheTemplate",
        "ERBTemplate",
        "EppTemplate",
        "ShellTemplate",
    } <= set(names)
    assert CopyTemplate.PRIORITY == FALLBACK_PRIORITY
    assert ShellTemplate.PRIORITY == JinjaTemplate.PRIORITY == DEFAULT_PRIORITY


def test_a_registered_engine_is_consulted_before_the_catch_all():
    # Registered *after* CopyTemplate, which is the realistic case: the plugin
    # module is imported once netboot itself is.
    register_template_type(_Shouty)
    types = Loader([]).template_types
    assert types.index(_Shouty) < types.index(CopyTemplate)
    assert types[-1] is CopyTemplate


def test_a_plugin_engine_renders_its_own_suffix_end_to_end(tmp_path):
    register_template_type(_Shouty)
    root = tmp_path / "templates"
    root.mkdir()
    (root / "boot.shouty").write_text("quiet")
    engine = _engine_with(root)
    ctx = engine.make_context(engine.lookup_target("host1"))
    # Without the priority split this would have been copied as bytes instead.
    assert ctx.render("boot.shouty") == "QUIET"


def test_register_returns_the_class_and_works_as_a_decorator():
    assert register_template_type(_Shouty) is _Shouty

    @register_template_type
    class Decorated(Template):
        EXT = ".dec"

    assert Decorated in TEMPLATE_TYPES


def test_register_can_set_a_priority_for_a_class_you_do_not_own():
    @register_template_type(priority=50)
    class First(Template):
        EXT = ".first"

    assert First.PRIORITY == 50
    assert Loader([]).template_types[0] is First


def test_registering_twice_does_not_duplicate():
    register_template_type(_Shouty)
    register_template_type(_Shouty)
    assert TEMPLATE_TYPES.count(_Shouty) == 1


def test_unregister_reports_whether_it_removed_anything():
    register_template_type(_Shouty)
    assert unregister_template_type(_Shouty) is True
    assert unregister_template_type(_Shouty) is False
    assert _Shouty not in TEMPLATE_TYPES


def test_equal_priorities_keep_the_order_they_were_given():
    # A stable sort, so an explicit template_types list still means what it says.
    assert by_priority([ShellTemplate, JinjaTemplate]) == (ShellTemplate, JinjaTemplate)
    assert by_priority([JinjaTemplate, ShellTemplate]) == (JinjaTemplate, ShellTemplate)


def test_an_explicit_list_is_still_ordered_by_priority():
    # A caller who puts the catch-all first does not thereby disable the others.
    types = Loader([], template_types=[CopyTemplate, ShellTemplate]).template_types
    assert types == (ShellTemplate, CopyTemplate)


def test_a_loader_snapshots_the_registry_at_construction():
    loader = Loader([])
    register_template_type(_Shouty)
    assert _Shouty not in loader.template_types
    assert _Shouty in Loader([]).template_types


def test_a_lower_priority_engine_can_sit_behind_the_catch_all():
    @register_template_type(priority=FALLBACK_PRIORITY - 1)
    class Lastest(Template):
        EXT = None

    assert Loader([]).template_types[-1] is Lastest
