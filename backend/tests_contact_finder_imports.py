"""The contact waterfall must not raise on its own helpers. On 16 Sep 2026
`_clean_domain` was deleted while two callers kept using it; every SmartFill
logged "contact waterfall failed (non-fatal): name '_clean_domain' is not
defined" for a month and nobody saw it. Run: python3 tests_contact_finder_imports.py"""
import ast, sys, builtins

SRC = "services/contact_finder.py"


def test_every_called_name_is_defined():
    tree = ast.parse(open(SRC).read())
    defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    defined |= {a.asname or a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    defined |= {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    defined |= set(dir(builtins))
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    # names bound inside functions (params, locals) are not module-level; only
    # flag underscore-prefixed module helpers, which is what went missing.
    missing = sorted(c for c in called - defined if c.startswith("_"))
    assert not missing, f"called but never defined at module level: {missing}"


def test_clean_domain():
    from services.contact_finder import _clean_domain
    assert _clean_domain("https://www.Foo.co.uk/x") == "foo.co.uk"
    assert _clean_domain("foo.com") == "foo.com"
    assert _clean_domain("") is None


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"ok   {name}")
            except Exception as e:
                fails += 1; print(f"FAIL {name}: {e!r}")
    sys.exit(1 if fails else 0)
