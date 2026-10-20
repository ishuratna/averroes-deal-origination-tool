"""ai/gemini_client is the ONE way to reach Gemini (Vertex or API key).
Run: python3 tests_gemini_client.py"""
import glob, os, re, sys

def test_no_direct_clients_outside_factory():
    bad = []
    for f in glob.glob("**/*.py", recursive=True):
        if ".venv" in f or f.startswith("tests_") or f == "ai/gemini_client.py":
            continue
        s = open(f).read()
        if re.search(r"genai\.Client\(", s):
            bad.append(f + ": genai.Client(")
        if "google.generativeai" in s:
            bad.append(f + ": old SDK")
        if re.search(r'getenv\("GEMINI_API_KEY"', s) and f != "auth.py":
            bad.append(f + ": reads GEMINI_API_KEY directly")
    assert not bad, bad

def test_backend_switch():
    from ai import gemini_client as g
    os.environ.pop("GEMINI_BACKEND", None); os.environ["GEMINI_API_KEY"] = "k"
    assert g.credential() == "k" and not g.use_vertex()
    os.environ["GEMINI_BACKEND"] = "vertex"; os.environ.pop("GEMINI_API_KEY", None)
    assert g.credential() == "vertex" and g.use_vertex() and g.vertex_location() == "global"
    os.environ.pop("GEMINI_BACKEND", None)
    assert g.credential() == "" and not g.configured()

def test_requirements():
    r = open("requirements.txt").read()
    assert "google-genai" in r and "google-generativeai" not in r

if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try: fn(); print(f"ok   {name}")
            except Exception as e: fails += 1; print(f"FAIL {name}: {e!r}")
    sys.exit(1 if fails else 0)
