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
    # On Windows `C:\...` used to be read as URI scheme "c", which then failed
    # with NotImplementedError instead of loading the file.
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
    # -v/-q used to adjust a logger this package never writes to.
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


def test_wants_netboot_signature_detection():
    # Regression: only a 3-arg run(netboot, args, conf) gets the netboot-first call.
    from netboot.main import _wants_netboot

    def netboot_run(netboot, args, conf): ...

    def duho_run(args): ...

    assert _wants_netboot(netboot_run) is True
    assert _wants_netboot(duho_run) is False
    assert _wants_netboot(None) is False
