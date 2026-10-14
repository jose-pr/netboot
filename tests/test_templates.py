"""What a template sees when netboot renders it, in every engine.

The engines are tplx's and tested there; this pins netboot's side of the
contract: the names a template reads (`ctx` and each context field), the
helpers (`shell_quote`, `Path`, `Uri`), the per-target file names, the image's
`template_path`, includes that follow both, and `_id` reaching the engines
that drop other `_`-prefixed names.
"""

import importlib.util
import shutil

import pytest
import tplx

import netboot


def _needs(module):
    return pytest.mark.skipif(
        importlib.util.find_spec(module) is None, reason=f"needs {module}"
    )


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "templates"
    path.mkdir()
    return path


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


def context(root, target="web01", **config):
    pixie = netboot.Pixie(
        templates=[root],
        globals={"domain": "example.com"},
        images={
            "debian": {"template_path": ["debian"], "globals": {"kernel": "vmlinuz"}}
        },
        dhcpzones={
            "lan": {"network": "10.0.0.0/24", "nameservers": ["10.0.0.53", "10.0.0.54"]}
        },
        targets={
            "web01": {"ip": "10.0.0.10", "image": "debian"},
            "aa:bb:cc:dd:ee:ff": {"ip": "10.0.0.11", "image": "debian"},
        },
        **config,
    )
    return pixie.make_context(pixie.lookup_target(target))


@pytest.mark.parametrize(
    "name, source",
    [
        pytest.param(
            "b.j2",
            "{{ ctx.target.hostname }} {{ target.hostname }} {{ image._id }} {{ kernel }}",
            id="jinja",
        ),
        pytest.param(
            "b.mako",
            "${ctx.target.hostname} ${target.hostname} ${image._id} ${kernel}",
            id="mako",
            marks=_needs("mako"),
        ),
        pytest.param(
            "b.liquid",
            "{{ ctx.target.hostname }} {{ target.hostname }} {{ image._id }} {{ kernel }}",
            id="liquid",
            marks=_needs("liquid"),
        ),
        pytest.param(
            "b.hbs",
            "{{ctx.target.hostname}} {{target.hostname}} {{image._id}} {{kernel}}",
            id="handlebars",
            marks=_needs("pybars"),
        ),
        pytest.param(
            "b.mustache",
            "{{ctx.target.hostname}} {{target.hostname}} {{image._id}} {{kernel}}",
            id="mustache",
            marks=_needs("chevron"),
        ),
        pytest.param(
            "b.shtpl",
            "%{CTX_TARGET_HOSTNAME} %{TARGET_HOSTNAME} %{IMAGE__ID} %{KERNEL}",
            id="shell",
        ),
        pytest.param(
            "b.erb",
            "<%= @ctx['target']['hostname'] %> <%= @target['hostname'] %> <%= @image['_id'] %> <%= @kernel %>",
            id="erb",
            marks=pytest.mark.skipif(
                shutil.which("ruby") is None, reason="needs ruby installed"
            ),
        ),
        pytest.param(
            "b.epp",
            "<%= $ctx['target']['hostname'] %> <%= $target['hostname'] %> <%= $image['_id'] %> <%= $kernel %>",
            id="epp",
            marks=pytest.mark.skipif(
                shutil.which("puppet") is None, reason="needs puppet installed"
            ),
        ),
    ],
)
def test_every_engine_sees_ctx_and_the_context_fields(root, name, source):
    write(root, name, source)
    assert context(root).render(name) == "web01 web01 debian vmlinuz"


def test_the_helpers_are_in_scope(root):
    write(
        root, "h.j2", "{{ shell_quote('a b') }} {{ Uri('tftp://h/p') }} {{ Path('x') }}"
    )
    assert context(root).render("h.j2") == "'a b' tftp://h/p x"


def test_a_mac_and_an_address_render_as_text_in_every_data_engine(root):
    write(root, "m.shtpl", "%{TARGET_MAC} %{TARGET_IP}")
    ctx = context(root, "aa:bb:cc:dd:ee:ff")
    assert ctx.render("m.shtpl") == "aa:bb:cc:dd:ee:ff 10.0.0.11"
    if importlib.util.find_spec("liquid"):
        write(root, "m.liquid", "{{ target.mac }} {{ target.ip }}")
        assert ctx.render("m.liquid") == "aa:bb:cc:dd:ee:ff 10.0.0.11"


def test_per_target_files_and_the_image_path_apply_to_includes(root):
    write(root, "debian/page.j2", '[{% include "head" %}]')
    write(root, "head.j2", "generic")
    write(root, "web01.head.j2", "web01 head")
    assert context(root).render("page") == "[web01 head]"
    assert context(root, "aa:bb:cc:dd:ee:ff").render("page") == "[generic]"


def test_an_include_of_another_engine_renders_with_the_context(root):
    write(root, "page.j2", '{% include "net" %}')
    write(root, "net.shtpl", "IP=%{TARGET_IP}")
    assert context(root).render("page.j2") == "IP=10.0.0.10"


def test_a_plugin_engine_registered_with_tplx_is_used(root):
    class Shouty(tplx.Template):
        EXT = ".shouty"

        def render_values(self, values):
            return self.source.upper()

    tplx.register_engine(Shouty)
    try:
        write(root, "boot.shouty", "quiet")
        assert context(root).render("boot") == "QUIET"
    finally:
        tplx.unregister_engine(Shouty)


def test_a_missing_template_is_not_found_or_none(root):
    with pytest.raises(tplx.TemplateNotFound):
        context(root).render("nope")
    assert context(root).render("nope", strict=False) is None
