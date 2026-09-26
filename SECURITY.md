# Security Policy

## Supported Versions

Solo se dan actualizaciones de seguridad a la última versión publicada.

| Version | Supported          |
| ------- | ------------------ |
| 5.1.x   | :white_check_mark: |
| < 5.1   | :x:                |

## Reporting a Vulnerability

Si encontrás una vulnerabilidad de seguridad, **no abras un issue público**.

Reportala de forma privada por uno de estos medios:
- **GitHub Security Advisories**: usá el botón "Report a vulnerability" en la pestaña **Security** de este repo (forma preferida — crea un draft privado).
- **Email**: acebeyfabian@gmail.com

### Qué esperar

- Confirmación de recepción dentro de 72 horas.
- Una primera evaluación (aceptada / rechazada / necesita más info) dentro de 7 días.
- Si se acepta, te mantengo al tanto del progreso hasta que salga el fix. El crédito se da en el changelog/release notes, salvo que pidas lo contrario.
- Si se rechaza, te explico el motivo.

### Alcance

Este proyecto orquesta y lanza binarios de terceros (por ejemplo, `llama-server` de llama.cpp). Vulnerabilidades en esos binarios en sí deben reportarse a sus proyectos correspondientes; acá se atienden vulnerabilidades en el código de Glyvex-AI-Suite (backend, frontend, configuración, manejo de procesos/datos).
