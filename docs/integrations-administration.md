# Integration administration runbook

Integration credential administration is an opt-in owner capability. It is not
enabled by shipping the application: both `CIVICLOOP_ADMIN_IDENTITY_ENABLED`
and `CIVICLOOP_INTEGRATIONS_ENABLED` must be `true`.

## Implementation handoff

The integration-administration foundation was completed and independently
reviewed on 2026-08-15. It includes the owner-only browser page, write-only
credential replacement, encrypted persistence, bounded provider connection
tests, redacted audit history, feature and readiness gates, OpenAPI/JSON Schema
contracts, PostgreSQL invariants, CI coverage, and the opt-in Compose key mount.

No production credential has been entered and no live provider connection has
been enabled by this implementation. Production activation still requires the
operator to provision the separate key ring, deploy the release, enable both
feature flags, enter credentials through `/admin/integrations`, and run the
documented harmless connection tests.

The next implementation phase is Hermes, LiteLLM, OpenTelemetry, and Phoenix;
those components were deliberately outside this administration slice. Resume
from the approved private design at
`develop/civicloop/specs/2026-08-08-civicloop-integrations-hermes-observability-design.md`
and create a separate implementation plan for the remaining milestones before
adding runtime or observability services.

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

The opt-in `compose.integrations.yaml` override gives `web` and `worker` a
read-only mount of the integration key ring. The base `compose.yaml` neither
requires the host path nor mounts the key when integrations are disabled.
`db`, `valkey`, `migrate`, and `scheduler` do not. The worker does not receive
the owner identity key or an enabled owner-identity feature.

Start or update the stack normally, then verify both core and integration
readiness from the web container:

```sh
docker compose -f compose.yaml -f compose.integrations.yaml up -d --build
docker compose -f compose.yaml -f compose.integrations.yaml exec -T web python scripts/readiness.py --base-url http://localhost:8000 --require-admin-identity --require-admin-integrations
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

## Disable and contain an integration incident

Disable administration first; this removes the integration browser route and
API on every recreated web process without stopping core liveness:

```sh
# Edit the host-only .env and set CIVICLOOP_INTEGRATIONS_ENABLED=false.
docker compose up -d --force-recreate web worker scheduler
docker compose exec -T web python scripts/readiness.py --base-url http://localhost:8000 --require-admin-identity
```

Confirm `/admin/integrations` and `/api/v1/admin/integrations` return 404.
This feature flag does not revoke a credential already accepted by an external
provider. In the affected provider’s console, disable or revoke that credential
and create a replacement with the minimum scopes required by CivicLoop. Record
the provider incident/reference ID outside CivicLoop; do not paste the old or
new credential into tickets, shell history, logs, or this repository.

After provider-side containment, either restore the known-good key ring or
create a new one as described above. Set
`CIVICLOOP_INTEGRATIONS_ENABLED=true`, force-recreate `web` and `worker`, run
the combined readiness command, then use `/admin/integrations` to replace and
test the provider credential. Verify the provider shows the old credential
revoked before closing the incident.

## Restore-test encrypted credentials

Perform this procedure only on an isolated, disposable restore environment;
never point it at production provider endpoints or reuse production session
cookies.

1. Restore a PostgreSQL backup into the isolated Compose database and restore
   the matching integration key-ring backup to the host path named by
   `CIVICLOOP_INTEGRATION_KEY_HOST_PATH`. Replace only the backup locations
   below; they must remain outside the checkout:

   ```sh
   docker compose up -d db
   docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" --clean --if-exists -d "$POSTGRES_DB"' < /secure/restore/civicloop-postgres.dump
   sudo install -m 0600 -o 10001 -g 10001 /secure/restore/integration-keyring.json /srv/civicloop/secrets/integration-keyring.json
   ```
2. Start the candidate image and apply its migrations:

   ```sh
   docker compose up -d db valkey
   docker compose run --rm migrate
   docker compose -f compose.yaml -f compose.integrations.yaml up -d web worker scheduler
   docker compose -f compose.yaml -f compose.integrations.yaml exec -T web python scripts/readiness.py --base-url http://localhost:8000 --require-admin-identity --require-admin-integrations
   ```

3. Authenticate as the test owner, inspect the restored provider metadata, and
   run a provider connection test only against a provider sandbox or a
   deliberately disabled credential. A successful key-ring readiness response
   and a redacted connection result prove that the database and key-ring backup
   pair can be read without exposing plaintext.
4. Destroy the disposable database volume and restore host, including any
   copied key ring, after recording only the pass/fail result and image digest.

## Roll back a release

Integration migrations are additive. Do not run reverse migrations against a
live incident database as a shortcut for rollback. Instead, record the current
image digest and migration state, disable the integrations feature flag, tag
the previously verified immutable image as the Compose local image, and
recreate the application processes:

```sh
# Replace only the digest with a previously verified release image.
docker image tag civicloop@sha256:PREVIOUS_VERIFIED_DIGEST civicloop:local
docker compose up -d --force-recreate web worker scheduler
docker compose exec -T web python scripts/readiness.py --base-url http://localhost:8000 --require-admin-identity
```

If the release changed persistent data, restore the pre-release PostgreSQL
backup together with the matching integration key-ring backup into an isolated
environment first, complete the restore test above, and schedule the live
restore under the organization’s change-control process.

After the prior image is running, verify core readiness with
`--require-admin-identity`; re-enable integrations only after the matching key
ring, database state, and provider-side credential status have been confirmed.
Hand off the rollback with the image digests, migration state, backup/restore
test result, readiness output status, provider incident reference, and the
operator responsible for the next review. Do not include credential values or
key-ring contents in the handoff.
