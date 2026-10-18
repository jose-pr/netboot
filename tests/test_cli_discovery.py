"""Command-discovery tests for the ``pixie`` CLI driver.

`netboot.main._discover` hands `duho.app` an explicit ``commands=`` list, which
before duho 0.4.1 disabled duho's own ``CMDS_PATH`` discovery -- so netboot
re-implemented the env lookup itself. duho now merges the env-derived commands
on top of any ``commands=`` list, the re-implementation is gone, and these
tests hold that contract: a command module reachable only through
``PIXIE_CMDS_PATH`` must still be found and run.
"""

import netboot.main as _main

_COMMAND_SOURCE = '''"""A throwaway command used by the discovery tests."""

MARKER = {marker!r}


def run(args):
    with open(MARKER, "w") as fh:
        fh.write("ran")
    return 0
'''


def _write_command(directory, name, marker):
    directory.mkdir(parents=True, exist_ok=True)
    module = directory / f"{name}.py"
    module.write_text(_COMMAND_SOURCE.format(marker=str(marker)), encoding="utf-8")
    return module


def _command_names(commands):
    return {
        getattr(c, "_parsername_", None) or getattr(c, "__name__", None)
        for c in commands
    }


def test_builtin_commands_are_discovered():
    names = _command_names(_main._discover([]))
    assert {"initiate", "complete"} <= names


def test_cmdspath_env_var_finds_a_command(tmp_path, monkeypatch):
    # The regression this file exists for: PIXIE_CMDS_PATH is resolved by duho's
    # own layer now, not by netboot, so dropping netboot's lookup must not make
    # an env-only command undiscoverable.
    marker = tmp_path / "env.marker"
    _write_command(tmp_path / "envcmds", "pixieenvonlycmd", marker)
    monkeypatch.setenv("PIXIE_CMDS_PATH", str(tmp_path / "envcmds"))

    assert _main.main(argv=["pixieenvonlycmd"]) == 0
    assert marker.read_text(encoding="utf-8") == "ran"


def test_cmdspath_option_finds_a_command(tmp_path):
    # --cmdspath is pre-parsed by netboot itself (duho never sees it), so it has
    # its own path through _discover and needs its own guard.
    marker = tmp_path / "opt.marker"
    _write_command(tmp_path / "optcmds", "pixieoptonlycmd", marker)

    argv = ["--cmdspath", str(tmp_path / "optcmds"), "pixieoptonlycmd"]
    assert _main.main(argv=argv) == 0
    assert marker.read_text(encoding="utf-8") == "ran"


def test_env_cmdspath_wins_over_the_option_on_a_name_clash(tmp_path, monkeypatch):
    # duho merges the env-derived commands on top of the commands= list netboot
    # builds from --cmdspath, so the env source is the more specific one. This
    # is documented in docs/cli.md; assert it rather than trusting the prose.
    env_marker = tmp_path / "clash-env.marker"
    opt_marker = tmp_path / "clash-opt.marker"
    _write_command(tmp_path / "envclash", "pixieclashcmd", env_marker)
    _write_command(tmp_path / "optclash", "pixieclashcmd", opt_marker)
    monkeypatch.setenv("PIXIE_CMDS_PATH", str(tmp_path / "envclash"))

    argv = ["--cmdspath", str(tmp_path / "optclash"), "pixieclashcmd"]
    assert _main.main(argv=argv) == 0
    assert env_marker.exists()
    assert not opt_marker.exists()


def test_env_cmdspath_is_not_read_twice(tmp_path, monkeypatch):
    # _discover must leave PIXIE_CMDS_PATH to duho: discovering it here as well
    # would import every module in the directory a second time.
    _write_command(tmp_path / "envcmds", "pixiedupcheckcmd", tmp_path / "dup.marker")
    monkeypatch.setenv("PIXIE_CMDS_PATH", str(tmp_path / "envcmds"))

    assert "pixiedupcheckcmd" not in _command_names(_main._discover([]))


def test_a_command_whose_body_is_main_gets_the_netboot_contract(tmp_path, monkeypatch):
    # duho resolves main -> run -> call, so a command written as
    # `main(netboot, args, conf)` must get the netboot contract too, not duho's
    # single-argument path.
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "viamain.py").write_text(
        "def register(parser, args):\n"
        "    pass\n"
        "\n"
        "def main(netboot, args, conf):\n"
        "    print('netboot-contract', type(netboot).__name__)\n"
        "    return 0\n",
        encoding="utf-8",
    )
    config = tmp_path / "config"
    config.mkdir()
    (config / "pixie.yaml").write_text(
        "targets: {}\nimages: {}\ndhcpzones: {}\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    from netboot.main import main as pixie_main

    assert pixie_main(argv=["--cmdspath", str(cmds), "viamain"]) == 0


def test_a_cmdspath_command_shadows_the_builtin_of_the_same_name(tmp_path):
    cmds = tmp_path / "cmds"
    _write_command(cmds, "initiate", tmp_path / "marker")
    found = [
        c
        for c in _main._discover(["--cmdspath", str(cmds)])
        if getattr(c, "_parsername_", None) == "initiate"
    ]
    assert len(found) == 1
    assert "cmds" in str(found[0].module.__file__)


def test_cmdspath_commands_follow_the_builtins_in_discovery_order(tmp_path):
    cmds = tmp_path / "cmds"
    _write_command(cmds, "aaa", tmp_path / "m1")
    _write_command(cmds, "zzz", tmp_path / "m2")
    _write_command(cmds, "initiate", tmp_path / "m3")
    names = [
        getattr(c, "_parsername_", None)
        for c in _main._discover(["--cmdspath", str(cmds)])
    ]
    assert names == ["complete", "dhcp-config", "initiate", "render", "aaa", "zzz"]


def test_a_cmdspath_class_command_gets_the_engine(tmp_path, monkeypatch, capsys):
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "hosts.py").write_text(
        '"""List the targets."""\n'
        "from netboot.cli import Command\n\n\n"
        "class Hosts(Command):\n"
        '    """List the targets."""\n\n'
        '    _parsername_ = "hosts"\n\n'
        "    def __call__(self):\n"
        "        print(sorted(self.netboot.targets))\n"
        "        return 0\n",
        encoding="utf-8",
    )
    config = tmp_path / "config"
    config.mkdir()
    (config / "pixie.yaml").write_text(
        "targets: {web01: {ip: 10.0.0.5}}\nimages: {}\ndhcpzones: {}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert _main.main(["--cmdspath", str(cmds), "hosts"]) == 0
    assert "['web01']" in capsys.readouterr().out
