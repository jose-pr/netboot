import importlib.util as _importlib_util
import typing as _ty
from typing import Union

from pathlib_next import Pathname
from pathlib_next import PosixPathname as RepoPath
from pathlib_next.uri import Source, UriPath

from ..utils.config import Namespace as _NS
from ..utils.net import Host

#: Schemes whose pathlib_next handler needs the ``http`` extra (``requests``).
_HTTP_SCHEMES = ("http", "https")


def _scheme_of(value: object) -> str:
    """The scheme of a configured service URI, or ``""`` for a bare path."""
    text = str(value)
    return text.split("://", 1)[0].lower() if "://" in text else ""


def _require_scheme_support(*schemes: str) -> None:
    """Raise an actionable :class:`ImportError` for a scheme we cannot serve.

    pathlib_next reaches http(s) through ``requests``, which neither netboot
    nor ``pathlib_next[uri]`` installs. Without it the scheme handler dies with
    a bare ``ModuleNotFoundError`` raised from inside the library, which says
    nothing about how to fix it.
    """
    for scheme in schemes:
        if scheme in _HTTP_SCHEMES and _importlib_util.find_spec("requests") is None:
            raise ImportError(
                f"{scheme}:// repository services require netboot's 'http' extra: "
                "pip install netboot[http]"
            )


class Resource(_NS):
    path: Pathname
    src: str

    def __truediv__(self, key):
        return Resource(path=self.path / key, src=self.src)

    @classmethod
    def _parse_path(cls, path: Union[str, Pathname]):
        if not isinstance(path, Pathname):
            path = RepoPath(path)
        return path


class Repository(_NS):
    address: Host
    services: dict[str, UriPath]
    local: "_ty.Optional[UriPath]"

    def __init__(self, **kwargs) -> None:
        self.services = {}
        kwargs.setdefault("address", None)
        kwargs.setdefault("local", None)
        super().__init__(**kwargs)
        if not isinstance(self.address, Host):
            self.address = Host(self.address)
        if self.local and not isinstance(self.local, UriPath):
            self.local = UriPath(self.local)

    def __getitem__(self, key: tuple[str, str]) -> UriPath:
        rel_path, service = key
        return self.get(rel_path, service=service)

    def __truediv__(self, key) -> "Repository":
        return self.joinpath(key)

    def joinpath(self, subpath: str = None):
        repo = Repository(address=self.address, services={}, local=self.local)
        for srvc in self.services:
            repo.services[srvc] = (
                f"{self.services[srvc]}/{subpath}" if subpath else self.services[srvc]
            )
        if self.local:
            repo.local = self.local / subpath if subpath else self.local
        else:
            # Keep .local a Pathname (not a raw str) so chained joinpath works.
            repo.local = RepoPath(subpath) if subpath else None
        return repo

    def get(self, *path: Union[RepoPath, str], service: str = None) -> UriPath:
        path: RepoPath = RepoPath(
            *[(p if isinstance(p, Pathname) else RepoPath(p)).as_posix() for p in path]
        )
        rel_path = path.as_posix().lstrip("/")
        baseuri = self.service(service)
        if not baseuri:
            return None
        return baseuri / rel_path

    def service(self, name: str):
        if name is None:
            baseuri = self.local
            name = "file"
            host = ""
        else:
            baseuri = self.services.get(name, None)
            if baseuri is None:
                return None
            _require_scheme_support(name, _scheme_of(baseuri))
            host = str(self.address.try_ip())
        if baseuri is None:
            return None
        if not isinstance(baseuri, UriPath):
            baseuri = UriPath(baseuri)
        if not baseuri.source:
            baseuri = baseuri.with_source(
                Source(scheme=name, userinfo=None, host=host, port=None)
            )

        return baseuri
