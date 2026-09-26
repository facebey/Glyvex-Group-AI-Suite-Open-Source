# Ícono del instalador — Glyvex AI Suite

Para que el ícono (`glyvex.ico`) aparezca en el `.msi` (Explorer / Agregar o quitar programas) y en el `.exe` del bootstrapper, agregar en el `.wxs`:

## MSI (Explorer / Agregar o quitar programas / instalador)

```xml
<Product ...>
  <Icon Id="GlyvexIcon.exe" SourceFile="glyvex.ico" />
  <Property Id="ARPPRODUCTICON" Value="GlyvexIcon.exe" />
</Product>
```

`ARPPRODUCTICON` es lo que define el ícono que ve Windows para ese MSI (Explorer, Agregar/Quitar programas, y el propio instalador).

## Bootstrapper .exe (WiX Burn)

Si además se genera un **bootstrapper .exe** (el `setup.exe` que envuelve al MSI), ese ejecutable necesita su propio ícono en el `Bundle`:

```xml
<Bundle ... IconSourceFile="glyvex.ico">
```

## Accesos directos

Si la app crea accesos directos (Start Menu / Escritorio), usar el mismo `Icon` para el `Shortcut`:

```xml
<Shortcut Icon="GlyvexIcon.exe" ... />
```

---

Con esto, banner (493×58), fondo de diálogo (493×312) e ícono (`.ico`, 16–256px) quedan con la misma identidad de marca.
