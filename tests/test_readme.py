"""The README's and the examples' commands run as written."""

import pathlib
import re
import shlex
import sys

import pytest

from netboot.main import main

ROOT = pathlib.Path(__file__).resolve().parent.parent
FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)


def _blocks(page: pathlib.Path, language: str) -> "list[str]":
    text = page.read_text(encoding="utf-8")
    return [m.group(2) for m in FENCE.finditer(text) if m.group(1) == language]


def _pixie_lines(block: str) -> "list[list[str]]":
    """Each `pixie ...` line of a shell block as argv, any `VAR=x` prefix dropped."""
    commands = []
    for line in block.splitlines():
        words = shlex.split(line.split("  #", 1)[0])
        while words and "=" in words[0]:
            words.pop(0)
        if words[:1] == ["pixie"]:
            commands.append(words[1:])
    return commands


@pytest.fixture
def quick_start(tmp_path, monkeypatch):
    """The README's quick start on disk, its dnsmasq paths moved under tmp_path."""
    readme = ROOT / "README.md"
    config = tmp_path / "config"
    (config / "templates" / "debian").mkdir(parents=True)
    dnsmasq = tmp_path / "dnsmasq"
    (dnsmasq / "hosts").mkdir(parents=True)
    (dnsmasq / "opts").mkdir()
    yaml_text = _blocks(readme, "yaml")[0].replace(
        "/etc/dnsmasq.d/netboot/", dnsmasq.as_posix() + "/"
    )
    (config / "pixie.yaml").write_text(yaml_text, encoding="utf-8")
    (config / "templates" / "debian" / "boot.cfg.j2").write_text(
        _blocks(readme, "jinja")[0], encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _quick_start_commands():
    readme = ROOT / "README.md"
    section = readme.read_text(encoding="utf-8").split("## Quick start", 1)[1]
    section = section.split("\n## ", 1)[0]
    return [
        argv
        for m in FENCE.finditer(section)
        if m.group(1) == "sh"
        for argv in _pixie_lines(m.group(2))
    ]


def test_the_quick_start_has_commands():
    assert [argv[0] for argv in _quick_start_commands()] == [
        "render",
        "initiate",
        "complete",
    ]


@pytest.mark.parametrize("argv", _quick_start_commands(), ids=lambda a: a[0])
def test_a_quick_start_command_runs(quick_start, argv, capfd):
    assert main(argv) == 0


def test_the_quick_start_python_runs(quick_start, capfd):
    source = [b for b in _blocks(ROOT / "README.md", "python") if "Pixie(" in b][0]
    exec(compile(source, "README.md", "exec"), {})
    assert "hostname=web01" in capfd.readouterr().out


def _example_commands():
    return [
        argv
        for block in _blocks(ROOT / "examples" / "README.md", "sh")
        for argv in _pixie_lines(block)
    ]


@pytest.mark.parametrize("argv", _example_commands(), ids=lambda a: " ".join(a))
def test_an_example_command_runs(argv, monkeypatch, capfd):
    examples = ROOT / "examples"
    monkeypatch.chdir(examples)
    monkeypatch.syspath_prepend(str(examples / "plugins"))
    monkeypatch.delitem(sys.modules, "recording", raising=False)
    assert main(argv) == 0
    out = capfd.readouterr().out
    if argv[-2:] == ["initiate", "web01"]:
        expected = _blocks(examples / "README.md", "")[-1].splitlines()
        lines = [line for line in out.splitlines() if line.strip()]
        assert lines[0] == expected[0]
        assert lines[-2:] == expected[-2:]
