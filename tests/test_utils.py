"""Unit tests for netboot.utils.shell_quote."""

import shlex

from netboot.utils import shell_quote

DANGEROUS = 'pass\'word $HOME `id` "x" ; rm -rf /'


def test_shell_quote_neutralizes_metacharacters():
    quoted = shell_quote(DANGEROUS)
    # Round-trip through a real POSIX parser: one word, unchanged.
    assert shlex.split(f"echo {quoted}") == ["echo", DANGEROUS]


def test_shell_quote_is_shlex_quote_for_a_str():
    for value in ["plain", "a b", "$6$salt$hash", "it's", ""]:
        assert shell_quote(value) == shlex.quote(value)
    assert shell_quote("web01.example.com") == "web01.example.com"


def test_shell_quote_maps_a_list_element_wise():
    assert shell_quote(["a", "b c"]) == ["a", "'b c'"]
    assert shell_quote(("x",)) == ["x"]


def test_shell_quote_handles_non_strings():
    assert shell_quote(None) == "''"
    assert shell_quote(10) == "10"
