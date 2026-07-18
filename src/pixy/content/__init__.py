import typing as _ty
from typing import Union

from pathlib_next import Pathname
from pathlib_next import PosixPathname as RepoPath
from pathlib_next.uri import Source, UriPath

from ..utils.config import Namespace as _NS
from ..utils.net import Host


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
            repo.local = subpath or None
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
