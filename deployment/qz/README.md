# QZ Tray silent printing — certificate & key

The POS prints thermal receipts through **QZ Tray** running on each Windows
register. Without a trusted certificate, QZ shows a security popup on every
print. To print silently, the Django app signs QZ's request payloads with a
private key and serves the matching certificate to the page.

This directory holds that key pair. **Neither file is committed** (`.gitignore`
excludes them). Provision them on the server and on each register as below.

## Files

| File | Visibility | Used by |
|------|-----------|---------|
| `private-key.pem` | **secret** — server only | `menu/qz_signing.py` signs `/restpos/qz/sign/` payloads |
| `digital-certificate.txt` | public | served at `/restpos/qz/cert/`, and installed into QZ Tray on each register |

Paths are configurable via env vars (`QZ_PRIVATE_KEY_PATH`, `QZ_CERT_PATH`);
they default to this directory. See `config/settings/base.py`.

## 1. Generate a self-signed pair (rollout)

Run once, on a trusted machine. 10-year validity so registers don't break on
expiry mid-service:

```bash
cd deployment/qz
openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
  -keyout private-key.pem -out digital-certificate.txt \
  -subj "/C=KE/ST=Nairobi/L=Nairobi/O=Bean & Bite/CN=Bean & Bite POS"
```

- `private-key.pem` → put on the **Django server only** (this folder, or wherever
  `QZ_PRIVATE_KEY_PATH` points). Keep it `chmod 600`; never copy it to a register.
- `digital-certificate.txt` → public. The server serves it automatically; you
  also install it into QZ Tray on each register (step 2).

QZ Tray verifies signatures as **RSA / SHA-512 / PKCS#1 v1.5**, which is what
`menu/qz_signing.py` produces — no extra QZ configuration needed.

## 2. Trust the certificate on each register

So QZ stops prompting, tell QZ Tray to trust this certificate:

1. Copy `digital-certificate.txt` to the register.
2. Right-click the QZ Tray icon → **Advanced → Site Manager** (or copy the file
   to QZ's `override.crt`/allowed list — on Windows:
   `C:\Program Files\QZ Tray\demo\ssl\override.crt`, or via
   `%APPDATA%\qz\` — paste the certificate contents and **Allow**).
3. Restart QZ Tray.

After this, prints from the POS page are silent.

> During testing you can skip step 2 — QZ will prompt once and you can tick
> "Remember this decision". Step 2 is what makes it fleet-wide and permanent.

## 3. Verify

With the files in place, the endpoints stop returning `204`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<host>/restpos/qz/cert/   # 200
```

The POS page reads this automatically; nothing else to configure.

## Rotating / revoking

Generate a new pair, replace both files, restart the Django app (the private
key is cached per-process), and re-trust the new certificate on each register
(step 2). The old certificate stops working as soon as the key is replaced.
