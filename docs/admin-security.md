# CivicLoop administrator security operations

This runbook operates the single CivicLoop owner identity on a self-hosted
server. Run host commands over SSH. Do not paste passwords, TOTP seeds, recovery
codes, key-ring contents, cookies, or API keys into GitHub issues, chat, shell
history, screenshots, logs, or telemetry.

The administrator feature is disabled by default. Deploying the code does not
authorize enabling it in production.

## Security model

The owner is separate from the synthetic LaunchLoop users. Password-only state
lasts five minutes and cannot call administrator APIs. Normal access requires
password plus TOTP. Recovery-code access is restricted to replacing the
authenticator and recovery codes. Sensitive dashboard actions require a fresh
password-plus-TOTP check from the previous ten minutes.

The browser entry is `/admin/security`. Django's framework administration is at
`/internal/django-admin/`; it is not a substitute for CivicLoop owner access.
Neither route should bypass Caddy. Only ports 80 and 443 are public; container
port 8000 remains bound to loopback.

## 1. Create the host key ring

Create the file outside the repository. The container runs as UID/GID 10001 and
must be the only non-root identity able to read it.

```bash
sudo install -d -m 0700 -o root -g root /srv/civicloop/secrets
sudo python3 -c "import base64,json,secrets; from pathlib import Path; p=Path('/srv/civicloop/secrets/identity-keyring.json'); k=base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode(); p.write_text(json.dumps({'active_key_id':'identity-2026-08','keys':{'identity-2026-08':k}})); p.chmod(0o600)"
sudo chown 10001:10001 /srv/civicloop/secrets/identity-keyring.json
sudo stat -c '%u:%g %a %n' /srv/civicloop/secrets/identity-keyring.json
```

The expected final ownership/mode is `10001:10001 600`. The command does not
print the generated key. Never use the documented key ID as a secret; it is only
a rotation label.

The file structure is:

```json
{
  "active_key_id": "identity-2026-08",
  "keys": {
    "identity-2026-08": "BASE64URL_32_RANDOM_BYTES"
  }
}
```

`BASE64URL_32_RANDOM_BYTES` is descriptive, not usable key material.

## 2. Configure without enabling

Set these values in the untracked production `.env`:

```dotenv
CIVICLOOP_ADMIN_IDENTITY_ENABLED=false
CIVICLOOP_IDENTITY_KEY_FILE=/run/secrets/civicloop-identity-keyring.json
CIVICLOOP_IDENTITY_KEY_HOST_PATH=/srv/civicloop/secrets/identity-keyring.json
CIVICLOOP_ADMIN_TRUSTED_PROXY_IPS=
```

Keep the trusted-proxy list empty unless Caddy reaches Django from a stable,
explicitly known address. CivicLoop otherwise trusts the direct peer and
ignores `X-Forwarded-For`, preventing client spoofing.

Validate configuration without printing the resolved Compose environment:

```bash
docker compose config --quiet
docker compose build
```

## 3. Migrate and bootstrap the owner

Apply migrations first. The PostgreSQL migration installs the trigger that
rejects update/delete operations on security events.

```bash
docker compose up -d --wait db valkey
docker compose up --no-deps --exit-code-from migrate migrate
docker compose run --rm --no-deps web manage bootstrap_owner
```

`bootstrap_owner` prompts interactively for username, email, and password. It
does not accept secret command-line flags or environment variables and never
creates a Django superuser, staff user, or LaunchLoop demo actor.

Only one non-disabled owner can exist. Store the password in the owner's normal
password manager; do not store it in the key-ring file.

## 4. Enable and enroll

Enabling production is a separate, explicit approval gate. After approval,
change only this value:

```dotenv
CIVICLOOP_ADMIN_IDENTITY_ENABLED=true
```

Then recreate the contexts that load identity settings:

```bash
docker compose up --no-deps --exit-code-from migrate migrate
docker compose up -d --no-deps --force-recreate web
docker compose exec -T web python scripts/readiness.py --base-url http://localhost:8000 --require-admin-identity
```

Open `https://civicloop.karthikkannan.ca/admin/security`, sign in with the owner
password, and enroll the TOTP authenticator. Scan the locally rendered QR code
or enter the manual secret. Save all ten recovery codes offline. They are shown
once; CivicLoop stores only hashes.

Confirm that a new browser session requires password plus TOTP and that the
normal LaunchLoop demo accounts cannot open the administrator dashboard.

## 5. Routine session cleanup

Run cleanup from the web service context because it has the read-only key mount
and identity configuration. The command is idempotent.

```bash
docker compose run --rm --no-deps web manage purge_administrator_sessions
```

Example root cron entry (choose a time appropriate for the host):

```cron
17 3 * * * cd /srv/civicloop/app && /usr/bin/docker compose run --rm --no-deps web manage purge_administrator_sessions >>/var/log/civicloop-session-cleanup.log 2>&1
```

Restrict and rotate that log. It must never contain credentials; treat any
unexpected secret-like output as an incident.

## 6. Back up and restore-test

Back up PostgreSQL and the key ring separately. Encrypt both backups with the
organization's approved offline backup mechanism. Do not store their plaintext
copies together, and do not place either in the repository.

A restore test is incomplete until all of these succeed in an isolated host or
disposable volume:

1. Restore the PostgreSQL backup.
2. Restore the key ring with UID/GID 10001 and mode 0600.
3. Start only database, Valkey, migration, and web services.
4. Run readiness with `--require-admin-identity`.
5. Sign in with password and the enrolled TOTP.
6. Confirm security events remain readable and append-only.
7. Destroy the disposable restore environment and plaintext restored files.

## 7. Rotate the encryption key

Rotation is additive. Keep the previous key until every device seed has been
re-encrypted or the old device has been replaced and a verified backup no
longer needs it.

1. Make an encrypted backup of the current key ring.
2. Add a new 32-byte random entry.
3. Set `active_key_id` to the new ID while retaining the old entry.
4. Restore ownership/mode 10001:10001 and 0600.
5. Recreate `web`, run readiness, and complete a test authentication.
6. Replace/re-enroll the authenticator through the security workflow.
7. Remove the old key only after database and backup retention no longer refer
   to envelopes carrying its ID.

Example shape during rotation:

```json
{
  "active_key_id": "identity-2026-11",
  "keys": {
    "identity-2026-08": "OLD_BASE64URL_32_RANDOM_BYTES",
    "identity-2026-11": "NEW_BASE64URL_32_RANDOM_BYTES"
  }
}
```

The values above are placeholders and must never be deployed.

## 8. Lost factor or emergency recovery

Use a remaining recovery code through the browser whenever possible. It creates
only a restricted session and requires immediate authenticator replacement.

If neither TOTP nor a recovery code is available, connect over SSH and run:

```bash
docker compose run --rm --no-deps web manage reset_owner_mfa
```

The command requires typing the exact owner username. It revokes all
administrator sessions, disables TOTP devices, invalidates recovery codes, and
returns the owner to enrollment-required state. It does not reveal or generate
a temporary password. Re-enroll through `/admin/security` and record the event
in the organization's incident/change log.

## 9. Rollback and disablement

To remove the administrator surface without deleting evidence or credentials:

```dotenv
CIVICLOOP_ADMIN_IDENTITY_ENABLED=false
```

```bash
docker compose up -d --no-deps --force-recreate web
curl -fsS https://civicloop.karthikkannan.ca/api/v1/health/ready
```

Both `/admin/security` and all `/api/v1/admin/` routes then return 404. Database
records and encrypted factors remain for a later reviewed recovery. Do not
delete the key ring or PostgreSQL data as a rollback action.

For suspected credential compromise, disable the feature, revoke provider keys
at their providers, use the SSH reset, rotate the identity key ring if its host
confidentiality is in doubt, and review the append-only event history.

## Verification checklist

- Feature-disabled routes return 404.
- Readiness passes with `--require-admin-identity` after enablement.
- `/admin/security` requires owner password plus TOTP.
- Demo accounts cannot authorize administrator endpoints.
- Recovery sessions cannot access dashboard, LaunchLoop, or integration APIs.
- Password/recovery/session changes require fresh verification where specified.
- PostgreSQL rejects security-event update/delete.
- Valkey outage denies authentication attempts with a redacted 503.
- The key-ring file is absent from Git, image layers, worker, and scheduler.
- Backups are encrypted and a restore test has succeeded.
