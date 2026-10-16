"""Where a CLI config's template roots resolve.

The config file's directory, the same base `!include` uses, so a config means
the same thing whichever directory pixie runs from.
"""

import types

import pytest
from pathapi import LocalPath

from netboot.main import _load_config


def _load(base, text):
    base.mkdir(parents=True, exist_ok=True)
    (base / "pixie.yaml").write_text(text, encoding="utf-8")
    return _load_config(types.SimpleNamespace(config=None, baseconfig=str(base)))


def _roots(conf):
    return [str(root) for root in conf["templates"]]


@pytest.mark.parametrize("declared", ["", "templates: \n", "templates: []\n"])
def test_the_default_root_is_templates_beside_the_config(tmp_path, declared):
    conf = _load(tmp_path / "etc", declared + "targets: {}\n")
    assert _roots(conf) == [str(tmp_path / "etc" / "templates")]


def test_a_relative_root_resolves_against_the_config_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conf = _load(tmp_path / "etc", "templates: [tpl, ../shared]\n")
    assert _roots(conf) == [
        str(tmp_path / "etc" / "tpl"),
        str(tmp_path / "etc" / ".." / "shared"),
    ]


def test_a_single_root_may_be_written_as_a_scalar(tmp_path):
    conf = _load(tmp_path / "etc", "templates: tpl\n")
    assert _roots(conf) == [str(tmp_path / "etc" / "tpl")]


def test_an_absolute_root_or_a_uri_is_used_as_given(tmp_path):
    absolute = (tmp_path / "abs").as_posix()
    conf = _load(tmp_path / "etc", f"templates: ['{absolute}', 'http://boot/tpl']\n")
    roots = conf["templates"]
    assert str(roots[0]) == str(LocalPath(absolute))
    assert str(roots[1]) == "http://boot/tpl"


def test_the_working_directory_is_not_a_root(tmp_path, monkeypatch):
    (tmp_path / "templates").mkdir()
    monkeypatch.chdir(tmp_path)
    conf = _load(tmp_path / "etc", "templates: [tpl]\n")
    assert str(tmp_path / "templates") not in _roots(conf)
