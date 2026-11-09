cpython-pep638: CPython 3.15.2 with PEP 638 syntactic macros
==============================================================

This repository is CPython 3.15.2 with an implementation of
`PEP 638 – Syntactic Macros <https://peps.python.org/pep-0638/>`_
built into the parser, the AST and the compiler.

It tracks the ``v3.15.2`` tag of `python/cpython
<https://github.com/python/cpython>`_. It is an experiment and is not part of,
or endorsed by, the CPython project or the Python Software Foundation.

.. contents::

Example
-------

``mymacros.py`` defines the macro processors::

    import ast
    import macros

    @macros.macro_processor(macros.STMT_MACRO, 1, "finally")
    def try_(node, finally_):
        return ast.Try(body=node.body, handlers=[], orelse=[],
                       finalbody=finally_.body)

    @macros.macro_processor(macros.EXPR_MACRO, 1)
    def quote(node):
        return ast.Constant(ast.unparse(node.args[0]))

``main.py`` uses them::

    from! mymacros import try_
    from! mymacros import quote

    def read(log):
        try_!:
            return quote!(a + b * c)
        finally!:
            log.append("closed")

    log = []
    print(read(log), log)    # a + b * c ['closed']

Run it with the interpreter built from this repository: ``./python main.py``.

Syntax
------

A macro name is an identifier immediately followed by ``!``, with no space
between them. Keywords followed by ``!`` are macro names too, so ``import!``,
``from!``, ``with!`` and ``finally!`` are valid.

Statement form::

    name! [args] [import NAME] [as NAME] [: NEWLINE INDENT body DEDENT]

Expression form::

    name!(args)

``args`` is a comma-separated list of expressions. At the start of a statement
the statement form wins, so ``name!(x)`` on its own line is a statement macro.
A statement macro without an indented block may end with ``:``; the colon is
ignored.

Inside f-strings and t-strings, ``!`` keeps its meaning as the conversion
marker (``f"{x!r}"``).

Macro processors
----------------

A macro processor is a ``(func, kind, version, additional_names)`` tuple,
usually made with the ``macros.macro_processor(kind, version,
*additional_names)`` decorator.

``kind`` is one of:

``macros.STMT_MACRO``
    A statement macro. ``func`` receives the ``ast.MacroStmt`` node, whose
    ``body`` holds the indented block (empty when there is none). It may
    declare ``additional_names``: the ``name!:`` parts that directly follow it
    in the same block (``try_!`` + ``finally!``) are passed as extra
    arguments, in the order of ``additional_names``, or ``None`` when a part
    is absent.
``macros.SIBLING_MACRO``
    A statement macro without an indented block. The next statement of the
    same block becomes its ``body``.
``macros.EXPR_MACRO``
    An expression macro. ``func`` receives the ``ast.MacroExpr`` node.

A statement macro returns a statement, a list of statements, or ``None``. An
expression macro returns an expression. Nodes without a location get the
location of the macro. A ``SyntaxError`` raised by ``func`` without a location
is reported at the macro.

``import! dotted.name as name`` and ``from! dotted.name import name [as name]``
import a macro processor at compile time and register it for the current
scope and the scopes nested in it. Every macro has to be imported before it is
used.

Expansion
---------

Before a tree is compiled, the compiler checks whether it contains macros and,
if so, passes it to ``macros._expand()``. Expansion works from the outside in:
a processor receives the unexpanded tree of its macro, so inner macros such as
``case!`` in a ``switch!`` need no processor of their own. Whatever a
processor returns is expanded again.

``ast.parse()`` and ``compile(..., ast.PyCF_ONLY_AST)`` return the unexpanded
tree. ``compile()`` of a source string or of an AST object, ``exec()``,
``eval()``, imports and ``symtable.symtable()`` expand macros.

AST nodes
---------

``ast.MacroStmt(name, args, importname, asname, body)``
    A statement macro. ``importname`` and ``asname`` are ``None`` when the
    clause is absent.
``ast.MacroExpr(name, args)``
    An expression macro.
``ast.StmtExpr(stmt, value)``
    An expression that executes ``stmt`` and then evaluates to ``value``. It is
    compiled directly to bytecode, so it keeps Python's evaluation order in
    any position (the right operand of ``or``, a comprehension, a conditional
    expression). A ``break``, ``continue`` or ``return`` in ``stmt`` that would
    leave the ``StmtExpr`` is a ``SyntaxError``.

``ast.unparse()`` writes ``MacroStmt`` and ``MacroExpr`` back in macro syntax.
``StmtExpr`` has no source form and makes ``ast.unparse()`` raise
``ValueError``.

The names recommended by the PEP for hygiene (``$$switch_4_0``, ``$x_12_5``)
can be used for generated variables; the compiler accepts them in the AST.

Differences from PEP 638
------------------------

- The AST classes follow CPython's naming: ``ast.MacroStmt``,
  ``ast.MacroExpr`` and ``ast.StmtExpr`` instead of ``macro_stmt``,
  ``macro_expr`` and ``stmt_expr``.
- The body of a statement macro is parsed with Python's statement grammar. A
  body such as ``"red" = 1`` from the PEP's ``bijection!`` example is a syntax
  error.
- ``tokenize`` reports a macro name as a ``NAME`` token followed by an ``OP``
  token ``!``; there is no ``MACRO_NAME`` token type.
- The processor's ``version`` is validated, but cached bytecode is not
  invalidated when a processor changes.

Building
--------

Build it like CPython::

    ./configure --with-pydebug
    make -j

On Nix, ``nix develop nixpkgs#python315`` provides the build dependencies.

After editing ``Parser/Python.asdl`` or ``Grammar/python.gram``, regenerate
the AST and the parser with ``make regen-ast regen-pegen``.

Tests
-----

The tests for macros are in ``Lib/test/test_macros``::

    ./python -m test test_macros

The whole test suite runs with ``./python -m test -j0``.

Where the implementation lives
------------------------------

``Grammar/python.gram``
    The ``macro_stmt``, ``macro_expr`` and ``invalid_macro_expr`` rules.
``Parser/pegen.c``
    Keeps a keyword followed by ``!`` as a ``NAME`` token.
``Parser/Python.asdl`` and ``Parser/asdl_c.py``
    The new AST nodes, and the generated ``_PyAST_HasMacros()``.
``Python/compile.c``
    ``_PyCompile_ExpandMacros()``, called before a tree is compiled.
``Lib/macros.py``
    The ``macros`` module and the expander.
``Python/codegen.c``
    Bytecode for ``StmtExpr``.
``Python/ast.c``, ``Python/ast_preprocess.c``, ``Python/symtable.c``, ``Python/ast_unparse.c``, ``Lib/_ast_unparse.py``
    Validation, preprocessing, symbol tables and unparsing of the new nodes.

License
-------

This is a derivative of CPython and is distributed under the same license, the
Python Software Foundation License Version 2. See ``LICENSE`` for the full
text.

Copyright © 2001 Python Software Foundation. All rights reserved.
