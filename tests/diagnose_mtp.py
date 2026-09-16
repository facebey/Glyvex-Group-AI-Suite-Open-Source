"""
diagnose_mtp.py — corré esto con el venv de glyvex activado, para ver
exactamente dónde falla la detección de MTP embebido (sin tragar excepciones).

Uso (con el venv activo):
    python diagnose_mtp.py "C:\LLM\gguf\Qwen3.8-27B\Qwen3.8-27B-UD-Q4_K_XL.gguf"
"""
import sys
import traceback

if len(sys.argv) != 2:
    print("Uso: python diagnose_mtp.py <path al .gguf>")
    sys.exit(1)

path = sys.argv[1]

print("=" * 70)
print("1) Version de la libreria gguf")
print("=" * 70)
try:
    import gguf
    print("gguf importado OK, desde:", gguf.__file__)
    print("version:", getattr(gguf, "__version__", "(no expone __version__)"))
except Exception:
    print("FALLO al importar gguf:")
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("2) Abrir el GGUFReader")
print("=" * 70)
try:
    from gguf import GGUFReader
    reader = GGUFReader(path, "r")
    print("Reader creado OK")
except Exception:
    print("FALLO al crear GGUFReader:")
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("3) Atributo .tensors")
print("=" * 70)
try:
    has_tensors_attr = hasattr(reader, "tensors")
    print("hasattr(reader, 'tensors'):", has_tensors_attr)
    if has_tensors_attr:
        tensors = reader.tensors
        print("tipo de reader.tensors:", type(tensors))
        print("cantidad de tensores:", len(tensors))
        first = tensors[0]
        print("tipo del primer tensor:", type(first))
        print("dir() del primer tensor (atributos publicos):")
        print("  ", [a for a in dir(first) if not a.startswith("_")])
        print("primer tensor .name:", repr(getattr(first, "name", "<<SIN ATRIBUTO name>>")))
except Exception:
    print("FALLO leyendo reader.tensors:")
    traceback.print_exc()

print()
print("=" * 70)
print("4) Buscar tensores .nextn. por nombre")
print("=" * 70)
try:
    nextn_tensors = [t.name for t in reader.tensors if ".nextn." in t.name]
    print("tensores .nextn. encontrados:", len(nextn_tensors))
    for n in nextn_tensors:
        print("  -", n)
except Exception:
    print("FALLO buscando tensores .nextn.:")
    traceback.print_exc()

print()
print("=" * 70)
print("5) KV general.architecture y {arch}.nextn_predict_layers")
print("=" * 70)
try:
    fields = {f.name: f for f in reader.fields.values()}
    arch_field = fields.get("general.architecture")
    print("existe KV general.architecture:", arch_field is not None)
    if arch_field is not None:
        print("  .contents():", repr(arch_field.contents()))

    arch = None
    if arch_field is not None:
        try:
            arch = arch_field.contents()
        except Exception:
            print("  FALLO en .contents() de architecture:")
            traceback.print_exc()

    if arch:
        key = f"{arch}.nextn_predict_layers"
        nextn_field = fields.get(key)
        print(f"existe KV '{key}':", nextn_field is not None)
        if nextn_field is not None:
            print("  .contents():", repr(nextn_field.contents()))
except Exception:
    print("FALLO general en el paso 5:")
    traceback.print_exc()

print()
print("=" * 70)
print("RESULTADO")
print("=" * 70)
try:
    layers = int(fields[f"{arch}.nextn_predict_layers"].contents())
    has_nextn = any(".nextn." in t.name for t in reader.tensors)
    print(f"layers={layers} has_nextn_tensors={has_nextn}")
    print("DEBERIA detectarse como embebido:", layers > 0 and has_nextn)
except Exception:
    print("No se pudo calcular el resultado final (ver errores arriba):")
    traceback.print_exc()
