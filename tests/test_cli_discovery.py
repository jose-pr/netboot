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
