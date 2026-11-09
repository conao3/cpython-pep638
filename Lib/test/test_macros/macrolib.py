"""Macro processors used by test_macros."""

import ast
import macros


@macros.macro_processor(macros.EXPR_MACRO, 1)
def quote(node):
    return ast.Constant(ast.unparse(node.args[0]))


@macros.macro_processor(macros.EXPR_MACRO, 1)
def requote(node):
    return ast.MacroExpr(name="quote", args=node.args)


@macros.macro_processor(macros.STMT_MACRO, 1)
def unroll(node):
    return [stmt for _ in range(node.args[0].value) for stmt in node.body]


@macros.macro_processor(macros.STMT_MACRO, 1)
def clauses(node):
    value = [[ast.unparse(a) for a in node.args], node.importname, node.asname]
    return ast.Assign(targets=[ast.Name("result", ast.Store())],
                      value=ast.Constant(repr(value)))


@macros.macro_processor(macros.SIBLING_MACRO, 1)
def twice(node):
    return node.body * 2


@macros.macro_processor(macros.STMT_MACRO, 1, "finally")
def try_(node, finally_):
    if finally_ is None:
        raise SyntaxError("try_! needs finally!")
    return ast.Try(body=node.body, handlers=[], orelse=[],
                   finalbody=finally_.body)


@macros.macro_processor(macros.STMT_MACRO, 1)
def switch(node):
    subject = f"$$switch_{node.lineno}_{node.col_offset}"
    chain = []
    for case in reversed(node.body):
        if case.name == "default":
            chain = case.body
            continue
        tests = [ast.Compare(ast.Name(subject, ast.Load()), [ast.Eq()], [v])
                 for v in case.args]
        test = tests[0] if len(tests) == 1 else ast.BoolOp(ast.Or(), tests)
        chain = [ast.If(test=test, body=case.body, orelse=chain)]
    return [ast.Assign(targets=[ast.Name(subject, ast.Store())],
                       value=node.args[0]), *chain]


@macros.macro_processor(macros.EXPR_MACRO, 1)
def tap(node):
    """tap!(expr) appends expr to `log` and evaluates to it."""
    tmp = f"$$tap_{node.lineno}_{node.col_offset}"
    stmt = ast.If(
        test=ast.Constant(True),
        body=[
            ast.Assign(targets=[ast.Name(tmp, ast.Store())], value=node.args[0]),
            ast.Expr(ast.Call(
                ast.Attribute(ast.Name("log", ast.Load()), "append", ast.Load()),
                [ast.Name(tmp, ast.Load())], [])),
        ],
        orelse=[])
    return ast.StmtExpr(stmt=stmt, value=ast.Name(tmp, ast.Load()))


@macros.macro_processor(macros.EXPR_MACRO, 1)
def escape(node):
    """escape!(stmt_kind) runs a break/continue/return inside a StmtExpr."""
    kind = node.args[0].id
    stmt = {"brk": ast.Break(), "cont": ast.Continue(),
            "ret": ast.Return(None)}[kind]
    return ast.StmtExpr(stmt=stmt, value=ast.Constant(None))


@macros.macro_processor(macros.EXPR_MACRO, 1)
def bad_result(node):
    return ast.Pass()


not_a_processor = 42
