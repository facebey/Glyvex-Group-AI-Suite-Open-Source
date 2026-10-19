"""Tests de themes.py — temas externos desde archivos (TH-2)."""

import os

import themes as themes_module


def _make_theme(name, css="--color-glyvex-accent: #ff00aa;\n", extra=None):
    d = themes_module.THEMES_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    if css is not None:
        (d / "theme.css").write_text(css, encoding="utf-8")
    for fname, content in (extra or {}).items():
        p = d / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return d


# --------------------------------------------------------------------------
# Puros (sin app)
# --------------------------------------------------------------------------


async def test_swatch_prefers_accent():
    css = ":root { --color-glyvex-bg: #101010; --color-glyvex-accent: #123456; }"
    assert themes_module.swatch_from_css(css) == "#123456"


async def test_swatch_falls_back_to_bg():
    css = ":root { --color-glyvex-bg: #f0f0f0; }"
    assert themes_module.swatch_from_css(css) == "#f0f0f0"


async def test_swatch_rejects_var_and_garbage():
    css = ":root { --color-glyvex-accent: var(--x); --color-glyvex-bg: not a color }"
    assert themes_module.swatch_from_css(css) == themes_module.DEFAULT_SWATCH


async def test_swatch_no_tokens():
    assert themes_module.swatch_from_css("body { color: red }") == themes_module.DEFAULT_SWATCH


async def test_theme_name_re():
    rx = themes_module.THEME_NAME_RE
    assert rx.match("Mi Tema")
    assert rx.match("my-theme.v2")
    assert rx.match("a")
    assert not rx.match("")
    assert not rx.match(" leading")
    assert not rx.match(".hidden")
    assert not rx.match("con/slash")
    assert not rx.match("x" * 65)


# --------------------------------------------------------------------------
# Listado (sobre THEMES_DIR redirigido por _isolated_state)
# --------------------------------------------------------------------------


async def test_list_empty_when_no_dir():
    assert themes_module.list_themes() == []


async def test_list_ignores_loose_files_and_cssless_folders():
    themes_module.THEMES_DIR.mkdir(parents=True, exist_ok=True)
    (themes_module.THEMES_DIR / "suelta.css").write_text("a{color:red}", encoding="utf-8")
    (themes_module.THEMES_DIR / "sin-css").mkdir()
    (themes_module.THEMES_DIR / "sin-css" / "leeme.txt").write_text("no soy tema", encoding="utf-8")
    assert themes_module.list_themes() == []


async def test_list_detects_theme_and_parses_swatch():
    _make_theme("Mi Tema")
    found = themes_module.list_themes()
    assert [t["name"] for t in found] == ["Mi Tema"]
    t = found[0]
    assert t["files"] == ["theme.css"]
    assert t["swatch"] == "#ff00aa"
    assert isinstance(t["mtime"], int) and t["mtime"] > 0


async def test_list_multiple_css_alphabetical_and_hidden_skipped():
    d = _make_theme("multi", css=None)
    (d / "b.css").write_text(":root { --color-glyvex-bg: #222; }", encoding="utf-8")
    (d / "a.css").write_text(":root { --color-glyvex-accent: #abcabc; }", encoding="utf-8")
    hidden = themes_module.THEMES_DIR / ".oculto"
    hidden.mkdir()
    (hidden / "theme.css").write_text("a{}", encoding="utf-8")
    found = themes_module.list_themes()
    assert [t["name"] for t in found] == ["multi"]
    assert found[0]["files"] == ["a.css", "b.css"]
    # El swatch sale del primer CSS en orden de carga (a.css).
    assert found[0]["swatch"] == "#abcabc"


async def test_mtime_tracks_texture_edits():
    d = _make_theme("tex", extra={"fondo.png": b"\x89PNG"})
    m1 = themes_module.list_themes()[0]["mtime"]
    (d / "fondo.png").write_bytes(b"\x89PNG\x02")
    os.utime(d / "fondo.png", (2_000_000_000, 2_000_000_000))
    m2 = themes_module.list_themes()[0]["mtime"]
    assert m2 == 2_000_000_000 > m1


# --------------------------------------------------------------------------
# API (contra la app; el endpoint lee THEMES_DIR en cada request)
# --------------------------------------------------------------------------


async def test_api_list_empty(client):
    res = await client.get("/api/themes")
    assert res.status_code == 200
    assert res.json() == {"themes": []}


async def test_api_serve_css(client):
    _make_theme("Mi Tema", css=":root { --color-glyvex-accent: #123456; }\n")
    res = await client.get("/api/themes/Mi%20Tema/theme.css")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/css")
    assert res.headers["cache-control"] == "no-cache"
    assert "--color-glyvex-accent" in res.text


async def test_api_serve_texture(client):
    png = b"\x89PNG\r\n\x1a\n"
    _make_theme("tex", extra={"sub/fondo.png": png})
    res = await client.get("/api/themes/tex/sub/fondo.png")
    assert res.status_code == 200
    assert res.content == png
    assert res.headers["content-type"] == "image/png"


async def test_api_404s(client):
    _make_theme("ok")
    for path in (
        "/api/themes/ausente/theme.css",        # carpeta inexistente
        "/api/themes/ok/falta.css",              # archivo inexistente
        "/api/themes/a%2fb/theme.css",           # slash en el nombre
        "/api/themes/..%2fconfig.json",          # traversal en el nombre
        "/api/themes/ok/..%2f..%2fconfig.json",  # traversal en el archivo
        "/api/themes/ok/",                       # sin archivo
    ):
        res = await client.get(path)
        assert res.status_code == 404, path
