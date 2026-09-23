import sys


def test_interpreter_meets_requires_python():
    # pyproject.toml declara requires-python = ">=3.11" (asyncio.timeout en
    # backend/benchmark.py). Mientras no exista CI (L1/L2), este test es la
    # unica salvaguarda: si alguien corre la suite con un Python viejo,
    # falla explicitamente en vez de romperse mas adelante.
    assert sys.version_info >= (3, 11), (
        f"Glyvex AI Suite requiere Python >= 3.11 "
        f"(en uso: {sys.version_info.major}.{sys.version_info.minor})"
    )
