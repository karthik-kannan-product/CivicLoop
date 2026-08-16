# Integration administration runbook

Integration credential administration is an opt-in owner capability. It is not
enabled by shipping the application: both `CIVICLOOP_ADMIN_IDENTITY_ENABLED`
and `CIVICLOOP_INTEGRATIONS_ENABLED` must be `true`.

## Provision the separate key ring

Create the integration key ring outside the checkout, using a different file
from the owner-identity key ring. The following creates a new random 256-bit
AES key without printing it:

```sh
sudo install -d -m 0700 /srv/civicloop/secrets
sudo python3 -c "import base64,json,secrets; from pathlib import Path; p=Path('/srv/civicloop/secrets/integration-keyring.json'); k=base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode(); p.write_text(json.dumps({'active_key_id':'integration-2026-08','keys':{'integration-2026-08':k}})); p.chmod(0o600)"
sudo chown 10001:10001 /srv/civicloop/secrets/integration-keyring.json
sudo stat -c '%u:%g %a %n' /srv/civicloop/secrets/integration-keyring.json
```

Back up the file through the organization’s protected secret-backup process.
The database holds encrypted envelopes, not the key ring; losing both the
working file and its backup makes stored credentials unrecoverable.

Never commit the key ring, copy its contents into `.env`, send it in tickets,
or use a production credential in CI. The repository and CI only generate
ephemeral synthetic keys.

## Configure Compose

In the host-only `.env`, set the two feature flags and the two integration key
variables. Keep the key-file path at its container mount point and put only the
absolute host file path in the host-path variable:

```dotenv
CIVICLOOP_ADMIN_IDENTITY_ENABLED=true
CIVICLOOP_INTEGRATIONS_ENABLED=true
CIVICLOOP_INTEGRATION_KEY_FILE=/run/secrets/civicloop-integration-keyring.json
CIVICLOOP_INTEGRATION_KEY_HOST_PATH=/srv/civicloop/secrets/integration-keyring.json
```

`web` and `worker` receive a read-only mount of the integration key ring.
`db`, `valkey`, `migrate`, and `scheduler` do not. The worker does not receive
the owner identity key or an enabled owner-identity feature.

Start or update the stack normally, then verify both core and integration
readiness from the web container:

```sh
docker compose up -d --build
docker compose exec -T web python scripts/readiness.py --base-url http://localhost:8000 --require-admin-identity --require-admin-integrations
```

Core liveness remains available if the integration key is absent or invalid;
the optional integration readiness gate fails closed instead. Correct the
mount or key ring before entering `/admin/integrations` or storing credentials.

## Rotate a key

Add a new generated key and change `active_key_id`, keeping old key entries
until all credentials encrypted with them have been replaced. Restart `web` and
`worker`, verify integration readiness, then rotate credentials through the
administrator interface. Remove an old key only after its dependent encrypted
credentials are no longer present and after a restore test.
