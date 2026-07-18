from argparse import Namespace as _NS


class Namespace(_NS):
    """Base for pixy config objects.

    Applies ``_parse_<prop>`` coercion at construction, and is *opaque* to
    ``yaconfiglib``'s ``typed_merge``: a pixy Namespace is a fully-built object
    (its ``__init__`` already normalised IP/MAC/network fields via factory
    functions), so field-by-field re-merging is neither wanted nor safe -- the
    factory-function field annotations (e.g. ``network: IPNetwork``) are not
    classes and would break ``typed_merge``'s ``issubclass`` introspection.
    Last object wins.
    """

    def __init__(self, **kwargs):
        for prop, value in kwargs.items():
            parser = getattr(self, f"_parse_{prop}", None)
            if parser:
                kwargs[prop] = parser(value)
        super().__init__(**kwargs)

    @classmethod
    def __merge__(cls, *objects, init: bool = True):
        return objects[-1] if objects else None
