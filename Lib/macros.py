"""Syntactic macros (PEP 638).

A macro processor is a ``(func, kind, version, additional_names)`` tuple,
usually built with the :func:`macro_processor` decorator. The compiler calls
:func:`_expand` on any tree that contains :class:`ast.MacroStmt` or
:class:`ast.MacroExpr` nodes, before the tree is compiled to bytecode.
"""

import ast
import importlib
import linecache
from collections import ChainMap

__all__ = ["STMT_MACRO", "SIBLING_MACRO", "EXPR_MACRO", "macro_processor"]

STMT_MACRO = "STMT_MACRO"
SIBLING_MACRO = "SIBLING_MACRO"
EXPR_MACRO = "EXPR_MACRO"

_KINDS = (STMT_MACRO, SIBLING_MACRO, EXPR_MACRO)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def macro_processor(kind, version, *additional_names):
    """Mark *func* as a macro processor of the given *kind* and *version*."""
    def deco(func):
        return func, kind, version, additional_names
    return deco


class _Processor:
    __slots__ = ("func", "kind", "version", "additional_names")

    def __init__(self, func, kind, version, additional_names):
        self.func = func
        self.kind = kind
        self.version = version
        self.additional_names = additional_names


class _Expander:

    def __init__(self, filename):
        self.filename = filename

    def error(self, message, node):
        return SyntaxError(message, (self.filename, node.lineno,
                                     node.col_offset + 1,
                                     self.source_line(node.lineno),
                                     node.end_lineno,
                                     node.end_col_offset + 1))

    def source_line(self, lineno):
        linecache.checkcache(self.filename)
        return linecache.getline(self.filename, lineno) or None

    def processor(self, value, name, node):
        if not (isinstance(value, tuple) and len(value) == 4):
            raise self.error(f"{name!r} is not a macro processor", node)
        func, kind, version, additional_names = value
        if not callable(func):
            raise self.error(f"macro processor {name!r} is not callable", node)
        if kind not in _KINDS:
            raise self.error(f"macro processor {name!r} has unknown kind "
                             f"{kind!r}", node)
        if not isinstance(version, int):
            raise self.error(f"macro processor {name!r} has a non-integer "
                             f"version", node)
        if not (isinstance(additional_names, tuple)
                and all(isinstance(n, str) for n in additional_names)):
            raise self.error(f"macro processor {name!r} has invalid "
                             f"additional names", node)
        if additional_names and kind != STMT_MACRO:
            raise self.error(f"only statement macros may have additional "
                             f"names ({name!r})", node)
        return _Processor(func, kind, version, additional_names)

    def resolve(self, dotted, node):
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            try:
                obj = importlib.import_module(".".join(parts[:i]))
            except ModuleNotFoundError:
                continue
            try:
                for attr in parts[i:]:
                    obj = getattr(obj, attr)
            except AttributeError:
                break
            return obj
        raise self.error(f"cannot import macro {dotted!r}", node)

    def dotted_name(self, node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{self.dotted_name(node.value)}.{node.attr}"
        raise self.error("expected a dotted name", node)

    def register(self, node, scope):
        if node.body:
            raise self.error(f"{node.name}! does not take a body", node)
        if len(node.args) != 1:
            raise self.error(f"{node.name}! expects a dotted name", node)
        dotted = self.dotted_name(node.args[0])
        if node.name == "import":
            if node.importname is not None:
                raise self.error("import! does not take an 'import' clause",
                                 node)
            target = dotted
            name = node.asname or dotted.rpartition(".")[2]
        else:
            if node.importname is None:
                raise self.error("from! requires an 'import' clause", node)
            target = f"{dotted}.{node.importname}"
            name = node.asname or node.importname
        scope[name] = self.processor(self.resolve(target, node), target, node)

    def lookup(self, node, scope):
        try:
            return scope[node.name]
        except KeyError:
            raise self.error(f"macro {node.name}! is not defined", node) from None

    def call(self, proc, node, args):
        try:
            return proc.func(*args)
        except SyntaxError as exc:
            if exc.lineno is None:
                exc.filename = self.filename
                exc.lineno = node.lineno
                exc.offset = node.col_offset + 1
                exc.text = self.source_line(node.lineno)
            raise

    def locate(self, result, node):
        for child in ast.walk(result):
            if isinstance(child, (ast.stmt, ast.expr)):
                if getattr(child, "lineno", None) is None:
                    ast.copy_location(child, node)
        return result

    def block(self, stmts, scope):
        out = []
        i = 0
        while i < len(stmts):
            stmt = stmts[i]
            if not isinstance(stmt, ast.MacroStmt):
                out.append(self.visit(stmt, scope))
                i += 1
                continue
            if stmt.name in ("import", "from"):
                self.register(stmt, scope)
                i += 1
                continue
            proc = self.lookup(stmt, scope)
            if proc.kind == EXPR_MACRO:
                raise self.error(f"expression macro {stmt.name}! used as a "
                                 f"statement", stmt)
            if proc.kind == SIBLING_MACRO:
                if stmt.body:
                    raise self.error(f"sibling macro {stmt.name}! does not "
                                     f"take an indented body", stmt)
                if i + 1 >= len(stmts):
                    raise self.error(f"sibling macro {stmt.name}! must be "
                                     f"followed by a statement", stmt)
                stmt.body = [stmts[i + 1]]
                args = [stmt]
                i += 2
            else:
                parts = {}
                i += 1
                while (i < len(stmts)
                       and isinstance(stmts[i], ast.MacroStmt)
                       and stmts[i].name in proc.additional_names
                       and stmts[i].name not in parts):
                    parts[stmts[i].name] = stmts[i]
                    i += 1
                args = [stmt, *(parts.get(n) for n in proc.additional_names)]
            result = self.call(proc, stmt, args)
            if result is None:
                result = []
            elif not isinstance(result, list):
                result = [result]
            for r in result:
                if not isinstance(r, ast.stmt):
                    raise self.error(f"statement macro {stmt.name}! returned "
                                     f"{type(r).__name__}", stmt)
                self.locate(r, stmt)
            out.extend(self.block(result, scope))
        return out

    def expr(self, node, scope):
        while isinstance(node, ast.MacroExpr):
            proc = self.lookup(node, scope)
            if proc.kind != EXPR_MACRO:
                raise self.error(f"statement macro {node.name}! used as an "
                                 f"expression", node)
            result = self.call(proc, node, [node])
            if not isinstance(result, ast.expr):
                raise self.error(f"expression macro {node.name}! returned "
                                 f"{type(result).__name__}", node)
            node = self.locate(result, node)
        return self.visit(node, scope)

    def visit(self, node, scope):
        if isinstance(node, ast.MacroStmt):
            raise self.error(f"statement macro {node.name}! is not allowed "
                             f"here", node)
        inner = scope.new_child() if isinstance(node, _SCOPES) else scope
        for field, value in ast.iter_fields(node):
            child_scope = inner if field == "body" else scope
            if isinstance(value, list):
                if any(isinstance(v, ast.stmt) for v in value):
                    setattr(node, field, self.block(value, child_scope))
                else:
                    setattr(node, field,
                            [self.child(v, child_scope) for v in value])
            elif isinstance(value, ast.AST):
                setattr(node, field, self.child(value, child_scope))
        return node

    def child(self, value, scope):
        if isinstance(value, ast.expr):
            return self.expr(value, scope)
        if isinstance(value, ast.AST):
            return self.visit(value, scope)
        return value


def _expand(tree, filename):
    """Return *tree* with every macro replaced by its expansion."""
    expander = _Expander(filename)
    scope = ChainMap()
    if isinstance(tree, (ast.Module, ast.Interactive)):
        tree.body = expander.block(tree.body, scope)
    else:
        tree.body = expander.expr(tree.body, scope)
    return ast.fix_missing_locations(tree)
