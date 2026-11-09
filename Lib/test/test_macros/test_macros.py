import ast
import symtable
import textwrap
import unittest
from test.support import os_helper

PRELUDE = "".join(
    f"from! test.test_macros.macrolib import {name}\n"
    for name in ("quote", "requote", "unroll", "clauses", "twice", "try_",
                 "switch", "tap", "escape", "bad_result")
)


def run(source):
    namespace = {"log": []}
    exec(compile(PRELUDE + textwrap.dedent(source), "<test>", "exec"),
         namespace)
    return namespace


class ParserTests(unittest.TestCase):

    def test_statement_form(self):
        tree = ast.parse("m! a, b import c as d:\n    pass\n")
        stmt = tree.body[0]
        self.assertIsInstance(stmt, ast.MacroStmt)
        self.assertEqual(stmt.name, "m")
        self.assertEqual([ast.unparse(a) for a in stmt.args], ["a", "b"])
        self.assertEqual((stmt.importname, stmt.asname), ("c", "d"))
        self.assertIsInstance(stmt.body[0], ast.Pass)

    def test_statement_form_without_body(self):
        stmt = ast.parse("m! x\n").body[0]
        self.assertIsInstance(stmt, ast.MacroStmt)
        self.assertEqual(stmt.body, [])

    def test_statement_form_without_args(self):
        stmt = ast.parse("m!:\n    pass\n").body[0]
        self.assertEqual(stmt.args, [])
        self.assertEqual(len(stmt.body), 1)

    def test_colon_without_indented_block(self):
        tree = ast.parse("m!:\ndef f(): pass\n")
        self.assertIsInstance(tree.body[0], ast.MacroStmt)
        self.assertEqual(tree.body[0].body, [])
        self.assertIsInstance(tree.body[1], ast.FunctionDef)

    def test_statement_form_takes_precedence(self):
        stmt = ast.parse("m!(x)\n").body[0]
        self.assertIsInstance(stmt, ast.MacroStmt)

    def test_expression_form(self):
        expr = ast.parse("y = m!(1, *z)\n").body[0].value
        self.assertIsInstance(expr, ast.MacroExpr)
        self.assertEqual(expr.name, "m")
        self.assertEqual(ast.unparse(expr), "m!(1, *z)")

    def test_keywords_as_macro_names(self):
        tree = ast.parse("import! a.b as c\nfrom! a import b\n"
                         "with! open!(f) as fd:\n    pass\n"
                         "finally!:\n    pass\n")
        self.assertEqual([s.name for s in tree.body],
                         ["import", "from", "with", "finally"])
        self.assertEqual(tree.body[2].args[0].name, "open")

    def test_bang_must_be_adjacent(self):
        with self.assertRaises(SyntaxError):
            ast.parse("m !(x)\n")

    def test_not_macros(self):
        tree = ast.parse("a != b\nf'{x!r}{None!a}'\n")
        self.assertIsInstance(tree.body[0].value, ast.Compare)
        values = tree.body[1].value.values
        self.assertEqual([v.conversion for v in values], [ord("r"), ord("a")])
        self.assertIsInstance(values[1].value, ast.Constant)

    def test_unparse_round_trip(self):
        source = "from! a.b import c as d\nm! x, y:\n    z = n!(1)"
        self.assertEqual(ast.unparse(ast.parse(source)), source)


class ExpansionTests(unittest.TestCase):

    def test_expression_macro(self):
        self.assertEqual(run("s = quote!(a + b * c)")["s"], "a + b * c")

    def test_expansion_is_repeated(self):
        self.assertEqual(run("s = requote!(x)")["s"], "x")

    def test_statement_macro(self):
        self.assertEqual(run("n = 0\nunroll! 3:\n    n += 1\n")["n"], 3)

    def test_clauses(self):
        ns = run("clauses! a, b + 1 import x as y\n")
        self.assertEqual(ast.literal_eval(ns["result"]),
                         [["a", "b + 1"], "x", "y"])

    def test_sibling_macro(self):
        self.assertEqual(run("n = 0\ntwice!\nn += 1\n")["n"], 2)

    def test_additional_names(self):
        ns = run("""
            def f(log):
                try_!:
                    return "body"
                finally!:
                    log.append("cleanup")
            """)
        log = []
        self.assertEqual(ns["f"](log), "body")
        self.assertEqual(log, ["cleanup"])

    def test_inner_macros_need_no_processor(self):
        ns = run("""
            def f(x):
                switch! x:
                    case! 1:
                        return "one"
                    case! 2, 3:
                        return "few"
                    default!:
                        return "many"
            """)
        self.assertEqual([ns["f"](i) for i in range(5)],
                         ["many", "one", "few", "few", "many"])

    def test_import_bang(self):
        ns = run("import! test.test_macros.macrolib.quote as q\ns = q!(k)\n")
        self.assertEqual(ns["s"], "k")

    def test_scopes(self):
        self.assertEqual(run("def f():\n    return quote!(z)\nr = f()\n")["r"],
                         "z")
        source = ("def f():\n"
                  "    from! test.test_macros.macrolib import quote as q\n"
                  "    return q!(1)\n"
                  "x = q!(2)\n")
        with self.assertRaisesRegex(SyntaxError, "macro q! is not defined"):
            compile(source, "<test>", "exec")

    def test_eval_mode(self):
        with self.assertRaisesRegex(SyntaxError, "macro m! is not defined"):
            compile("m!(1)", "<test>", "eval")

    def test_compile_ast_object(self):
        tree = ast.parse(PRELUDE + "s = quote!(a)\n")
        namespace = {}
        exec(compile(tree, "<test>", "exec"), namespace)
        self.assertEqual(namespace["s"], "a")

    def test_symtable(self):
        table = symtable.symtable(PRELUDE + "s = quote!(a)\n", "<test>", "exec")
        self.assertIn("s", table.get_identifiers())


class StmtExprTests(unittest.TestCase):

    def test_statement_runs_before_value(self):
        ns = run("y = tap!(6 * 7) + 1\n")
        self.assertEqual(ns["y"], 43)
        self.assertEqual(ns["log"], [42])

    def test_lazy_positions_keep_evaluation_order(self):
        ns = run("""
            a = 1 or tap!(2)
            b = [tap!(i) for i in range(3) if i]
            c = tap!(5) if False else 6
            """)
        self.assertEqual((ns["a"], ns["b"], ns["c"]), (1, [1, 2], 6))
        self.assertEqual(ns["log"], [1, 2])

    def test_control_flow_cannot_leave(self):
        for kind in ("brk", "cont", "ret"):
            with self.subTest(kind=kind):
                source = (f"def f():\n    for i in range(3):\n"
                          f"        x = escape!({kind})\n")
                with self.assertRaisesRegex(SyntaxError,
                                            "cannot leave a statement expression"):
                    compile(PRELUDE + source, "<test>", "exec")

    def test_compile_stmt_expr_node(self):
        tree = ast.Module(body=[
            ast.Assign(targets=[ast.Name("x", ast.Store())],
                       value=ast.StmtExpr(
                           stmt=ast.Assign(targets=[ast.Name("y", ast.Store())],
                                           value=ast.Constant(1)),
                           value=ast.BinOp(ast.Name("y", ast.Load()), ast.Add(),
                                           ast.Constant(1))))],
            type_ignores=[])
        namespace = {}
        exec(compile(ast.fix_missing_locations(tree), "<test>", "exec"),
             namespace)
        self.assertEqual((namespace["x"], namespace["y"]), (2, 1))


class ErrorTests(unittest.TestCase):

    def assertCompileError(self, source, message, lineno=None):
        with self.assertRaisesRegex(SyntaxError, message) as cm:
            compile(PRELUDE + source, "<test>", "exec")
        if lineno is not None:
            self.assertEqual(cm.exception.lineno, lineno + PRELUDE.count("\n"))

    def test_undefined_macro(self):
        self.assertCompileError("x = 1\ny = nope!(1)\n",
                                "macro nope! is not defined", lineno=2)

    def test_kind_mismatch(self):
        self.assertCompileError("quote! x\n", "used as a statement")
        self.assertCompileError("x = unroll!(1)\n", "used as an expression")
        self.assertCompileError("twice!:\n    pass\n",
                                "does not take an indented body")

    def test_missing_part(self):
        self.assertCompileError("try_!:\n    pass\n", "try_! needs finally!")
        self.assertCompileError("finally!:\n    pass\n",
                                "macro finally! is not defined")

    def test_bad_imports(self):
        self.assertCompileError(
            "from! test.test_macros.macrolib import missing\n", "cannot import")
        self.assertCompileError(
            "from! test.test_macros.macrolib import not_a_processor\n",
            "is not a macro processor")

    def test_bad_result(self):
        self.assertCompileError("x = bad_result!()\n", "returned Pass")

    def test_error_in_file(self):
        self.addCleanup(os_helper.unlink, os_helper.TESTFN)
        for line in ("y = nope!(1)\n", "try_!:\n"):
            source = PRELUDE + "x = 1\n" + line + "    pass\n" * line.endswith(":\n")
            with open(os_helper.TESTFN, "w") as f:
                f.write(source)
            with self.subTest(line=line):
                try:
                    compile(source, os_helper.TESTFN, "exec")
                except SyntaxError as exc:
                    self.assertEqual(exc.text, line)
                    self.assertIsNone(exc.__traceback__.tb_next)
                else:
                    self.fail("SyntaxError not raised")


if __name__ == "__main__":
    unittest.main()
