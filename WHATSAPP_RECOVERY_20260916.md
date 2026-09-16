# BRIMOON WhatsApp recovery 16 September 2026

## Scope and deployment

Only `/home/seradmin/anna`, `anna.service` workers, and the isolated
`brimoon-whatsapp-bridge.service` were updated. No other WhatsApp bridges,
their credentials, or application services were restarted. No OS reboot.

Before deployment, code and the full LocalAuth directory were copied to the
non-public, mode-700 directory
`/home/seradmin/anna/backups/whatsapp-before-20260916-v1`.
Saved WhatsApp login files were not deleted. The bridge was restarted to load
the new code, and Gunicorn workers were gracefully reloaded with HUP.

## Changes

- Serialize/idempotently register Puppeteer page bindings across navigation.
- Log navigation paths, logout redirect flags, event transitions and error stacks.
  Do not log QR contents or pairing codes.
- Detect failed browser injection, closed pages and browser command timeouts.
- Probe healthy QR pages without restarting ordinary waiting-for-scan sessions.
- Recover broken/stalled sessions using a bounded browser shutdown, preserving
  LocalAuth. Limit recovery to five attempts per hour and a cooldown.
- Bound pairing to 20 seconds; reject concurrent requests. Keep the Django
  transport timeout at 30 seconds; catch socket timeouts and display friendly
  Spanish errors instead of HTTP 500.
- No ordinary pairing path requests `wipeAuth`. Destructive reset requires an
  explicit endpoint confirmation and CLI `--confirm-delete-login`.
- Password-only access to QR and pairing, including token-based legacy pages.
  A normal site login no longer bypasses the separate gate. Permit only the
  configured connection name. Grants expire after 30 minutes. Five failed
  attempts per source address trigger a five-minute block, using shared
  file-backed cache across Gunicorn workers. Disable HTTP caching.
- Disable duplicate submit buttons. Provide a CSRF-protected POST action to
  return from pairing to a fresh QR without removing saved login files.
- Add isolated `brimoon-whatsapp-monitor.timer` every minute. After five minutes
  of outage, alert active owner devices using the existing Firebase integration.
  Throttle successful alerts to one per hour and retry failures every five
  minutes. Recovery clears the incident. Customers and employees are not
  recipients. On older APKs this is a system notification, not a booking link.

## Verification

- 31 Django WhatsApp tests passed, including gate, expiry, timeout and alert tests.
- 6 Node resilience tests passed; server syntax checks and Django checks passed.
- Live HTTPS smoke test: unauthenticated QR denied, pairing redirected to gate,
  wrong password rejected, configured password accepted, authenticated fresh
  QR PNG retrieved. Smoke test sends no WhatsApp messages or pairing requests.
- Live bridge reached `qr` with no error or fault, and generated changing QR
  codes without recovery loops. Human scanning is still required to authenticate.
- Firebase configured and two active owner devices present; monitor installed.

## Remaining physical and external limitations

Kernel/network logs show Ethernet carrier losses at 09:15:59 and 09:16:15.
Inspect the cable and router port physically; software changes do not repair
those components. No sustained CPU/RAM pressure or service restart was observed
at the original failure. The trigger of the first 08:21 navigation was not logged
and cannot be determined retroactively. WhatsApp may revoke authorization, and
preserving local files cannot guarantee that it accepts a previously revoked
login. The user-selected four-digit password is weak; replace it with a strong
secret for long-term use.

## Anna's connection page

https://brimoon.es/whatsapp/connect/main/

Open on a second screen. On the salon phone use WhatsApp > Linked devices > Link
a device, scan the QR, and wait for the page to show `Conectado`.
