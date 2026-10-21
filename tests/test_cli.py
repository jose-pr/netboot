"""End-to-end tests for the `pixie` CLI: config loading, dispatch, exit codes.

The command path had no coverage at all, so a break in `main()` -- a bad
config surfacing as a traceback, a Windows path parsed as a URI, `-v` never
reaching netboot's logger -- shipped green. These drive `main(argv=...)` the
way a shell does, from a temporary working directory.
"""

import logging
import textwrap

import pytest

from netboot.main import main, parse_path

WORKING_CONFIG = """\
targets:
  web01:
    ip: 10.0.0.10
    image: debian
  node10:
    ip: 10.0.0.20
    image: debian
  node1:
    ip: 10.0.0.21
    image: debian
images:
  debian:
    template_path: []
dhcpzones:
  lan:
    network: 10.0.0.0/24
"""


def _config(tmp_path, text=WORKING_CONFIG, name="pixie.yaml"):
    directory = tmp_path / "config"
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_initiate_succeeds_with_a_discovered_config(in_tmp_cwd):
    _config(in_tmp_cwd)
    assert main(argv=["initiate", "web01"]) == 0


def test_unknown_target_exits_1(in_tmp_cwd, caplog):
    _config(in_tmp_cwd)
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["initiate", "nosuchhost"]) == 1
    assert "not found" in caplog.text.lower()


def test_ambiguous_target_exits_1_without_acting(in_tmp_cwd, caplog):
    _config(in_tmp_cwd)
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["initiate", "node"]) == 1
    assert "ambiguous" in caplog.text.lower()


def test_missing_config_file_is_one_line_and_exits_2(in_tmp_cwd, caplog):
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", "nosuch.yaml", "initiate", "web01"]) == 2
    assert "nosuch.yaml" in caplog.text


def test_malformed_yaml_is_reported_not_raised(in_tmp_cwd, caplog):
    path = _config(in_tmp_cwd, "targets: [\n", name="broken.yaml")
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", str(path), "initiate", "web01"]) == 2
    assert "could not parse" in caplog.text


def test_unknown_image_names_the_target_and_exits_2(in_tmp_cwd, caplog):
    path = _config(
        in_tmp_cwd,
        """\
        targets:
          h1: {ip: 10.0.0.9, image: nosuchimage}
        dhcpzones:
          lan: {network: 10.0.0.0/24}
        """,
        name="noimage.yaml",
    )
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", str(path), "initiate", "h1"]) == 2
    assert "nosuchimage" in caplog.text and "h1" in caplog.text


def test_empty_config_is_not_an_attribute_error(in_tmp_cwd, caplog):
    path = _config(in_tmp_cwd, "# nothing here\n", name="empty.yaml")
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", str(path), "initiate", "h1"]) == 1
    assert "not found" in caplog.text.lower()


def test_non_mapping_config_is_rejected_clearly(in_tmp_cwd, caplog):
    path = _config(in_tmp_cwd, "- just\n- a\n- list\n", name="list.yaml")
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", str(path), "initiate", "h1"]) == 2
    assert "mapping" in caplog.text


def test_an_absolute_config_path_is_not_parsed_as_a_uri(in_tmp_cwd):
    # On Windows `C:\...` must be a path, not URI scheme "c", which cannot be
    # read at all.
    path = _config(in_tmp_cwd, name="absolute.yaml")
    assert main(argv=["-c", str(path.resolve()), "initiate", "web01"]) == 0


def test_parse_path_treats_a_drive_letter_as_a_local_path():
    assert "://" not in str(parse_path(r"C:\srv\tftp"))
    assert "://" not in str(parse_path("C:/srv/tftp"))
    assert str(parse_path("http://host/boot")).startswith("http://")


def test_verbosity_reaches_netboots_own_logger(in_tmp_cwd):
    _config(in_tmp_cwd)
    logger = logging.getLogger("netboot")
    try:
        main(argv=["-q", "-q", "initiate", "web01"])
        quiet = logger.level
        main(argv=["-v", "initiate", "web01"])
        verbose = logger.level
    finally:
        logger.setLevel(logging.NOTSET)
    # -v/-q must move the logger this package writes to.
    assert verbose < quiet


def test_sandboxed_interpolation_refuses_to_escape(in_tmp_cwd, caplog):
    path = _config(
        in_tmp_cwd,
        """\
        globals:
          pwned: "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        targets: {}
        images: {}
        dhcpzones: {}
        """,
        name="ssti.yaml",
    )
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", str(path), "initiate", "web01"]) == 2
    assert "unsafe" in caplog.text or "security" in caplog.text.lower()


def test_ordinary_interpolation_still_works(in_tmp_cwd):
    path = _config(
        in_tmp_cwd,
        """\
        globals:
          site: hq
          greeting: "{{ globals.site }}-boot"
        targets:
          web01: {ip: 10.0.0.10, image: debian}
        images:
          debian: {template_path: []}
        dhcpzones:
          lan: {network: 10.0.0.0/24}
        """,
        name="interp.yaml",
    )
    assert main(argv=["-c", str(path), "initiate", "web01"]) == 0


@pytest.mark.parametrize(
    "signature, body, status",
    [
        ("netboot, args, conf", "len(locals())", 3),
        ("netboot, args, conf=None", "len(locals())", 3),
        ("*args", "len(args)", 3),
        ("args", "len(locals())", 1),
    ],
)
def test_only_a_run_taking_three_positionals_gets_the_engine(
    in_tmp_cwd, monkeypatch, signature, body, status
):
    # run(netboot, args, conf) is called with the engine, the parsed arguments
    # and the raw config (three arguments); any other shape goes through duho's
    # own dispatch, which passes the arguments alone. The status counts them.
    from netboot.cli import _root

    monkeypatch.setattr(_root, "build_engine", lambda instance: ("ENGINE", {"k": 1}))
    cmds = in_tmp_cwd / "cmds"
    cmds.mkdir()
    (cmds / "shapecmd.py").write_text(
        f"def register(parser, args):\n    pass\n\n"
        f"def run({signature}):\n    return {body}\n",
        encoding="utf-8",
    )
    assert main(argv=["--cmdspath", str(cmds), "shapecmd"]) == status


def test_a_plain_command_returning_a_value_has_it_printed(in_tmp_cwd, capsys):
    cmds = in_tmp_cwd / "cmds"
    cmds.mkdir()
    (cmds / "plaincmd.py").write_text(
        "def register(parser, args):\n    pass\n\ndef run(args):\n    return 'hello'\n",
        encoding="utf-8",
    )
    assert main(argv=["--cmdspath", str(cmds), "plaincmd"]) == 0
    assert capsys.readouterr().out.strip() == "hello"


def test_duho_traceback_prints_the_traceback_and_keeps_the_status(
    in_tmp_cwd, monkeypatch, capsys, caplog
):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", "nosuch.yaml", "initiate", "web01"]) == 2
    assert "Traceback" in capsys.readouterr().err
    assert "nosuch.yaml" in caplog.text


@pytest.mark.parametrize("value", ["0", "false", "no"])
def test_duho_traceback_off_values_print_no_traceback(
    in_tmp_cwd, monkeypatch, capsys, caplog, value
):
    monkeypatch.setenv("DUHO_TRACEBACK", value)
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", "nosuch.yaml", "initiate", "web01"]) == 2
    assert "Traceback" not in capsys.readouterr().err
    assert "nosuch.yaml" in caplog.text


def test_help_raises_system_exit_zero(capsys):
    with pytest.raises(SystemExit) as info:
        main(argv=["--help"])
    assert info.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_a_usage_error_raises_system_exit_two(capsys):
    with pytest.raises(SystemExit) as info:
        main(argv=["--no-such-option"])
    assert info.value.code == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_malformed_yaml_is_a_config_error_chained_to_the_parse_error(in_tmp_cwd):
    import anycfg

    from netboot import PixieConfigError
    from netboot.main import Pixie_, _load_config

    path = in_tmp_cwd / "bad.yaml"
    path.write_text("a: [1\n", encoding="utf-8")
    args = Pixie_()
    args.config = str(path)
    with pytest.raises(
        PixieConfigError, match="could not parse the configuration"
    ) as info:
        _load_config(args)
    assert isinstance(info.value.__cause__, anycfg.ParseError)
    assert "bad.yaml:2:1" in str(info.value)


_ZONE = """\
images:
  debian: {template_path: []}
dhcpzones:
  lan: {network: 10.0.0.0/24}
"""


@pytest.mark.parametrize(
    "text, says",
    [
        pytest.param(
            "targets:\n  web01: {ip: 10.0.0.10, mac: 'zz:zz', image: debian}\n",
            "targets.web01: Invalid MAC",
            id="bad-mac",
        ),
        pytest.param(
            "targets:\n  web01: {ip: 10.0.0.10, mac: 52:54:00:12:34:56, image: debian}\n",
            "quote it",
            id="base-60-mac",
        ),
        pytest.param(
            "targets:\n  52:54:00:12:34:56: {ip: 10.0.0.10, image: debian}\n",
            "the id 41135085296 is not text",
            id="base-60-key",
        ),
        pytest.param(
            "targets:\n  web01: {ip: 10.0.0.500, image: debian}\n",
            "targets.web01: '10.0.0.500'",
            id="bad-ip",
        ),
        pytest.param("targets:\n  - web01\n", "targets: expected a mapping", id="list"),
        pytest.param("1: x\n", "config key 1 is not text", id="int-top-level-key"),
        pytest.param(
            "targets:\n  web01: {ip: 10.0.0.10, image: debian}\n"
            "images:\n  debian: {template_path: []}\n"
            "dhcpzones:\n  lan: {network: 10.0.0.0/33}\n",
            "dhcpzones.lan: '10.0.0.0/33'",
            id="bad-network",
        ),
    ],
)
def test_a_config_mistake_is_one_line_and_exits_2(in_tmp_cwd, caplog, text, says):
    if "dhcpzones" not in text:
        text += _ZONE
    path = _config(in_tmp_cwd, text, name="mistake.yaml")
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", str(path), "initiate", "web01"]) == 2
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and says in errors[0].getMessage()
    assert "Traceback" not in caplog.text


def test_a_load_module_that_does_not_import_exits_2(in_tmp_cwd, caplog):
    _config(in_tmp_cwd)
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-l", "nosuch_netboot_module", "initiate", "web01"]) == 2
    assert "--load-module nosuch_netboot_module" in caplog.text


def test_a_server_that_cannot_be_reached_exits_2(in_tmp_cwd, caplog, dhcp_backend):
    def refuse(self, reservation):
        raise ConnectionRefusedError("refused")

    dhcp_backend("down", add=refuse, remove=refuse)
    path = _config(
        in_tmp_cwd,
        "targets:\n  web01: {ip: 10.0.0.10, image: debian}\n"
        "images:\n  debian: {template_path: []}\n"
        "dhcpzones:\n  lan: {network: 10.0.0.0/24, dhcpservers: ['down://x']}\n",
        name="down.yaml",
    )
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", str(path), "initiate", "web01"]) == 2
    assert "no DHCP server armed 'web01': refused" in caplog.text


@pytest.mark.parametrize("text", ["svn+ssh://host/repo", "x-y.z://h/p", "sftp://h/p"])
def test_parse_path_takes_every_rfc_3986_scheme(text):
    from uripath import URIPath

    assert isinstance(parse_path(text), URIPath)


@pytest.mark.parametrize("text", ["templates", "a/b:c", "C:/srv", "./x:y"])
def test_parse_path_keeps_a_path_local(text):
    from uripath import URIPath

    assert not isinstance(parse_path(text), URIPath)


def _render_config(tmp_path):
    templates = tmp_path / "config" / "templates"
    templates.mkdir(parents=True)
    (templates / "boot.cfg.j2").write_text("ip={{ target.ip }}\n", encoding="utf-8")
    (templates / "logo.bin").write_bytes(b"\x00\xffraw")
    _config(tmp_path)


def test_render_writes_each_artifact_to_standard_output(in_tmp_cwd, capfd):
    _render_config(in_tmp_cwd)
    assert main(argv=["-q", "render", "web01", "boot.cfg"]) == 0
    assert capfd.readouterr().out == "ip=10.0.0.10\n"


def test_render_out_writes_files_and_copies_bytes_exactly(in_tmp_cwd):
    _render_config(in_tmp_cwd)
    out = in_tmp_cwd / "out"
    assert (
        main(argv=["render", "web01", "boot.cfg", "logo.bin", "--out", str(out)]) == 0
    )
    assert (out / "boot.cfg").read_bytes() == b"ip=10.0.0.10\n"
    assert (out / "logo.bin").read_bytes() == b"\x00\xffraw"


def test_render_of_an_unknown_target_exits_1(in_tmp_cwd, caplog):
    _render_config(in_tmp_cwd)
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["render", "nosuch", "boot.cfg"]) == 1
    assert "not found" in caplog.text.lower()


def test_render_of_an_unknown_template_exits_2(in_tmp_cwd):
    _render_config(in_tmp_cwd)
    assert main(argv=["render", "web01", "nosuch.cfg"]) == 2


def test_anycfg_confine_to_confines_includes(in_tmp_cwd, caplog, monkeypatch):
    outside = in_tmp_cwd / "outside.yaml"
    outside.write_text("{}\n", encoding="utf-8")
    path = _config(
        in_tmp_cwd, f"globals: !include {outside.as_posix()}\n", name="c.yaml"
    )
    monkeypatch.setenv("ANYCFG_CONFINE_TO", str(path.parent))
    with caplog.at_level(logging.ERROR, logger="netboot"):
        assert main(argv=["-c", str(path), "initiate", "web01"]) == 2
    assert "outside.yaml" in caplog.text or "confine" in caplog.text.lower()


def test_main_takes_argv_first(capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["--version"])
    assert exit_.value.code == 0 and capsys.readouterr().out.strip()


def test_no_settings_module_is_imported_from_the_working_directory(in_tmp_cwd):
    import subprocess
    import sys

    (in_tmp_cwd / "pixie_env.py").write_text(
        "open('ran', 'w').close()\n", encoding="utf-8"
    )
    _config(in_tmp_cwd)
    subprocess.run(
        [sys.executable, "-m", "netboot", "-q", "initiate", "web01"],
        cwd=in_tmp_cwd,
        check=True,
        timeout=120,
    )
    assert not (in_tmp_cwd / "ran").exists()
