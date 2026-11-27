"""Restrictions on bot code: static checks plus a restricted runtime environment.

Threat model: bots are written by AI models that are trying to win. They might,
deliberately or not, take shortcuts: read the engine's state, peek at the opponent,
monkeypatch shared classes, use unlimited compute, or share memory between robots.
This module (with ``meter.py`` and the sealed SDK types) makes those shortcuts fail
loudly. It is NOT a hardened security boundary against a determined human attacker;
run untrusted code from unknown sources in a container/VM as well.

Layers:
1. Static AST checks (``validate_source``): no dunder attribute/name access (blocks the
   classic ``().__class__.__base__.__subclasses__()`` escapes), no frame/generator
   introspection attributes, no ``str.format`` attribute traversal, no async, no
   metaclasses, imports only from a whitelist.
2. Restricted builtins: no ``open``/``eval``/``exec``/``type``/``id``/``vars``/``globals``...;
   ``getattr``-family re-checks names at runtime; ``print`` goes to the robot's log.
3. Per-robot module copies: every robot gets its *own* copy of each imported module
   (and its own copies of pure-Python functions/classes in it), so module state can't
   be used as a side channel between robots or to sabotage the other team.
4. The RobotController exposes no attributes at all; game state lives in the engine.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import random as _random
import string
import types

from .meter import METER

# --------------------------------------------------------------------------- whitelist

#: module -> exported names (None = every public name)
MODULE_EXPORTS: dict[str, tuple | None] = {
    "math": None,
    "random": ("random", "randint", "randrange", "choice", "choices", "shuffle", "sample", "uniform", "gauss"),
    "collections": ("deque", "defaultdict", "Counter", "OrderedDict", "namedtuple"),
    "heapq": ("heappush", "heappop", "heapify", "heappushpop", "heapreplace", "nlargest", "nsmallest", "merge"),
    "bisect": ("bisect", "bisect_left", "bisect_right", "insort", "insort_left", "insort_right"),
    "itertools": None,
    "functools": ("lru_cache", "cache", "partial", "reduce", "cmp_to_key", "total_ordering", "wraps", "cached_property"),
    "copy": ("copy", "deepcopy"),
    "dataclasses": ("dataclass", "field"),
    "enum": ("Enum", "IntEnum", "Flag", "IntFlag", "auto"),
    "typing": (
        "Any", "Optional", "Union", "List", "Dict", "Tuple", "Set", "FrozenSet", "Deque", "DefaultDict",
        "Callable", "Iterable", "Iterator", "Sequence", "Mapping", "NamedTuple", "TYPE_CHECKING", "ClassVar", "Final",
    ),
    "arena.sdk": None,  # resolved from arena.sdk.__all__
}

_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "callable", "chr", "classmethod",
    "complex", "dict", "divmod", "enumerate", "filter", "float", "frozenset", "hash", "hex", "int",
    "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min", "next", "object", "oct", "ord",
    "pow", "property", "range", "repr", "reversed", "round", "set", "slice", "sorted", "staticmethod", "str",
    "sum", "super", "tuple", "zip",
    "Exception", "ArithmeticError", "AssertionError", "AttributeError", "IndexError", "KeyError",
    "LookupError", "NameError", "NotImplementedError", "OverflowError", "RecursionError", "RuntimeError",
    "StopIteration", "TypeError", "ValueError", "ZeroDivisionError", "UnboundLocalError", "ImportError",
    "NotImplemented", "Ellipsis", "True", "False", "None",
    "__build_class__",
)

ALLOWED_DUNDER_NAMES = frozenset({"__name__"})
ALLOWED_DUNDER_ATTRS = frozenset({"__init__", "__name__", "__post_init__"})
ALLOWED_DUNDER_DEFS = frozenset({
    "__init__", "__post_init__", "__repr__", "__str__", "__eq__", "__ne__", "__lt__", "__le__", "__gt__",
    "__ge__", "__hash__", "__len__", "__iter__", "__next__", "__contains__", "__getitem__", "__setitem__",
    "__delitem__", "__bool__", "__add__", "__sub__", "__mul__", "__truediv__", "__floordiv__", "__mod__",
    "__neg__", "__pos__", "__abs__", "__radd__", "__rsub__", "__rmul__", "__iadd__", "__isub__", "__call__",
    "__reversed__",
})
FORBIDDEN_ATTRS = frozenset({
    "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "cr_origin",
    "ag_frame", "ag_code", "ag_await", "tb_frame", "tb_next", "f_back", "f_globals", "f_locals",
    "f_builtins", "f_code", "f_trace", "format_map", "mro",
})


class BotLoadError(Exception):
    def __init__(self, messages: list[str]):
        super().__init__("\n".join(messages))
        self.messages = messages


# --------------------------------------------------------------------------- static checks

def _plain_format_literal(s: str) -> bool:
    """True if ``s`` used with .format() can't traverse attributes/items."""
    try:
        for _lit, fname, spec, _conv in string.Formatter().parse(s):
            if fname and ("." in fname or "[" in fname):
                return False
            if spec and "{" in spec:
                return False
    except ValueError:
        return False
    return True


class _Validator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[str] = []

    def err(self, node, msg: str) -> None:
        self.errors.append(f"line {getattr(node, 'lineno', '?')}: {msg}")

    def _check_name(self, node, name: str, what: str) -> None:
        if name.startswith("__") and name not in ALLOWED_DUNDER_NAMES:
            self.err(node, f"{what} '{name}' is not allowed (names starting with '__' are reserved)")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        a = node.attr
        if a.startswith("__") and a not in ALLOWED_DUNDER_ATTRS:
            self.err(node, f"access to attribute '.{a}' is not allowed (dunder attributes are blocked)")
        elif a in FORBIDDEN_ATTRS:
            self.err(node, f"access to attribute '.{a}' is not allowed (introspection is blocked)")
        elif a == "format":
            v = node.value
            if not (isinstance(v, ast.Constant) and isinstance(v.value, str) and _plain_format_literal(v.value)):
                self.err(node, "str.format() is only allowed on string literals without '.'/'[' lookups; use f-strings")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self._check_name(node, node.id, "name")

    def visit_arg(self, node: ast.arg) -> None:
        self._check_name(node, node.arg, "argument name")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        n = node.name
        if n.startswith("__") and n not in ALLOWED_DUNDER_DEFS:
            self.err(node, f"defining '{n}' is not allowed")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node) -> None:
        self.err(node, "async code is not supported")

    visit_AsyncFor = visit_AsyncWith = visit_Await = visit_AsyncFunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._check_name(node, node.name, "class name")
        if node.keywords:
            self.err(node, "class keyword arguments (such as metaclass=) are not allowed")
        self.generic_visit(node)

    def visit_Global(self, node) -> None:
        for n in node.names:
            self._check_name(node, n, "name")

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._check_name(node, node.name, "name")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name not in MODULE_EXPORTS:
                self.err(node, f"import of '{alias.name}' is not allowed; allowed modules: {', '.join(MODULE_EXPORTS)}")
            if alias.asname:
                self._check_name(node, alias.asname, "name")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self.err(node, "relative imports are not allowed (a bot is a single file)")
            return
        mod = node.module or ""
        if mod not in MODULE_EXPORTS:
            self.err(node, f"import from '{mod}' is not allowed; allowed modules: {', '.join(MODULE_EXPORTS)}")
            return
        exports = _exports_of(mod)
        for alias in node.names:
            if alias.name != "*" and alias.name not in exports:
                self.err(node, f"'{alias.name}' is not available from '{mod}' (available: {', '.join(exports)})")
            if alias.asname:
                self._check_name(node, alias.asname, "name")


def validate_source(source: str, filename: str = "bot.py") -> tuple[ast.Module | None, list[str]]:
    """Parse and check bot source. Returns (tree, errors)."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return None, [f"line {e.lineno}: SyntaxError: {e.msg}"]
    v = _Validator()
    v.visit(tree)
    if not any(isinstance(n, ast.FunctionDef) and n.name == "turn" for n in tree.body):
        v.errors.append("the bot must define a top-level function `turn(rc)`")
    return tree, v.errors


# --------------------------------------------------------------------------- runtime environment

def _check_attr_name(name) -> None:
    if type(name) is not str:
        raise TypeError("attribute name must be a string")
    if (name.startswith("__") and name not in ALLOWED_DUNDER_ATTRS) or name in FORBIDDEN_ATTRS or name == "format":
        raise AttributeError(f"access to attribute '{name}' is not allowed in bots")


def _safe_getattr(obj, name, *default):
    _check_attr_name(name)
    return getattr(obj, name, *default)


def _safe_hasattr(obj, name):
    _check_attr_name(name)
    return hasattr(obj, name)


def _safe_setattr(obj, name, value):
    _check_attr_name(name)
    setattr(obj, name, value)


def _safe_delattr(obj, name):
    _check_attr_name(name)
    delattr(obj, name)


_SAFE_BUILTINS = {n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES}
_SAFE_BUILTINS.update(getattr=_safe_getattr, hasattr=_safe_hasattr, setattr=_safe_setattr, delattr=_safe_delattr)

_HEAPTYPE = 1 << 9
_IMMUTABLETYPE = 1 << 8

_REAL_MODULES: dict[str, types.ModuleType] = {}


def _real_module(name: str) -> types.ModuleType:
    mod = _REAL_MODULES.get(name)
    if mod is None:
        mod = importlib.import_module(name)
        if name != "arena.sdk":
            METER.instrument_module(mod)  # library Python code costs instructions too
        _REAL_MODULES[name] = mod
    return mod


def _exports_of(name: str) -> tuple:
    names = MODULE_EXPORTS[name]
    if names is not None:
        return names
    mod = _real_module(name)
    if name == "arena.sdk":
        return tuple(mod.__all__)
    return tuple(n for n in dir(mod) if not n.startswith("_"))


def _isolate(obj):
    """Per-robot copy of a pure-Python function or mutable class, so that setting
    attributes on it can't leak to other robots or the other team."""
    if isinstance(obj, types.FunctionType):
        f = types.FunctionType(obj.__code__, obj.__globals__, obj.__name__, obj.__defaults__, obj.__closure__)
        f.__kwdefaults__ = dict(obj.__kwdefaults__) if obj.__kwdefaults__ else None
        f.__dict__.update(obj.__dict__)
        f.__qualname__ = obj.__qualname__
        f.__doc__ = obj.__doc__
        f.__module__ = obj.__module__
        return f
    if isinstance(obj, type) and obj.__flags__ & _HEAPTYPE and not obj.__flags__ & _IMMUTABLETYPE:
        try:
            return types.new_class(
                obj.__name__, (obj,), {},
                lambda ns: ns.update({"__module__": obj.__module__, "__qualname__": obj.__qualname__}),
            )
        except TypeError:
            return obj
    return obj


def _build_module(name: str, rng_seed: str) -> types.ModuleType:
    m = types.ModuleType(name)
    if name == "random":
        _real_module("random")
        rng = _random.Random(rng_seed)
        for n in MODULE_EXPORTS["random"]:
            setattr(m, n, getattr(rng, n))
    else:
        real = _real_module(name)
        sdk = name == "arena.sdk"  # SDK classes are sealed; share them as-is
        for n in _exports_of(name):
            v = getattr(real, n)
            setattr(m, n, v if sdk else _isolate(v))
    m.__all__ = list(_exports_of(name))
    return m


def make_globals(module_name: str, rng_seed: str, log_sink, print_cost: int) -> dict:
    """Fresh module globals for one robot."""
    cache: dict[str, types.ModuleType] = {}

    def __import__(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if level:
            raise ImportError("relative imports are not allowed in bots")
        if name not in MODULE_EXPORTS:
            raise ImportError(f"module '{name}' is not available to bots; allowed: {', '.join(MODULE_EXPORTS)}")
        mod = cache.get(name)
        if mod is None:
            mod = cache[name] = _build_module(name, rng_seed)
        if name == "arena.sdk" and not fromlist:
            parent = cache.get("arena")
            if parent is None:
                parent = cache["arena"] = types.ModuleType("arena")
                parent.sdk = mod
            return parent
        return mod

    def print(*args, sep=" ", end="\n", file=None, flush=False):  # noqa: A001
        METER.charge(print_cost)
        sep = " " if sep is None else sep
        end = "\n" if end is None else end
        if type(sep) is not str or type(end) is not str:
            raise TypeError("print(): sep and end must be str")
        log_sink(sep.join([str(a) for a in args]) + end)

    b = dict(_SAFE_BUILTINS)
    b["__import__"] = __import__
    b["print"] = print
    return {"__name__": module_name, "__builtins__": b}


def _instrument_whitelist() -> None:
    for name in MODULE_EXPORTS:
        if name != "arena.sdk":
            _real_module(name)


_instrument_whitelist()
