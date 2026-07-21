from importlib import import_module


def import_(name: str):
    module, obj = name.rsplit(".", maxsplit=1)
    return getattr(import_module(module), obj)
