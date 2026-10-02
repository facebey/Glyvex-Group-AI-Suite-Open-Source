# Security Policy

> **Language:** [Español](SECURITY.es.md)

## Supported Versions

Only the latest released version receives security updates.

| Version | Supported          |
| ------- | ------------------ |
| 0.7.x   | :white_check_mark: |
| < 0.7   | :x:                |

## Reporting a Vulnerability

If you find a security vulnerability, **do not open a public issue**.

Report it privately through one of these channels:
- **GitHub Security Advisories**: use the "Report a vulnerability" button on this repo's **Security** tab (preferred — creates a private draft).
- **Email**: info@glyvexgroup.com

### What to expect

- Acknowledgement of receipt within 72 hours.
- A first assessment (accepted / rejected / needs more info) within 7 days.
- If accepted, you will be kept informed of the progress until the fix ships. Credit goes in the changelog/release notes unless you ask otherwise.
- If rejected, we explain why.

### Scope

This project orchestrates and launches third-party binaries (for example, `llama-server` from llama.cpp). Vulnerabilities in those binaries themselves should be reported to their respective projects; here we handle vulnerabilities in the Glyvex-AI-Suite code (backend, frontend, configuration, process/data handling).
