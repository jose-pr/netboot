"""Unit tests for netboot.utils helpers (flatten / shell_quote / arr_get)."""

from argparse import Namespace

from netboot.utils import flatten, shell_quote
from netboot.utils.dicts import arr_get


def test_flatten_nested_mapping():
    assert flatten({"a": {"b": 1, "c": 2}, "d": 3}) == {"a_b": 1, "a_c": 2, "d": 3}


def test_flatten_nested_list():
    # Regression: the old code did ``index + "_" + index`` -> TypeError.
    assert flatten({"x": ["p", "q"]}) == {"x_0": "p", "x_1": "q"}


def test_flatten_deeply_nested_and_namespace():
    ns = Namespace(host=Namespace(name="h1", tags=["a", "b"]))
    assert flatten(ns) == {"host_name": "h1", "host_tags_0": "a", "host_tags_1": "b"}


def test_flatten_scalar_passthrough():
    assert flatten(5) == 5


def test_shell_quote_scalar_and_list():
    assert shell_quote("x") == ['"x"']
    assert shell_quote(["a", "b"], quote="'") == ["'a'", "'b'"]


def test_arr_get():
    assert arr_get([1, 2], 0) == 1
    assert arr_get([1, 2], 5, default="z") == "z"
