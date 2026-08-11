# CivicLoop development-to-server delivery runbook

This is the canonical, resumable workflow for developing CivicLoop locally,
syncing reviewed changes through GitHub, and deploying a pinned revision to a
self-hosted Linux server such as Vultr. It is written for both people and AI
agents. Provider-specific account details and current deployment state belong
in the private production operations repository.

## Safety rules

- Never put `.env`, passwords, API keys, cookies, TOTP seeds, recovery codes,
  encryption-key contents, database dumps, or browser-auth state in Git, chat,
  command arguments, screenshots, logs, or telemetry.
- Ask the human operator to enter passwords and MFA material interactively.
  Agents must not request these values in chat.
- Inspect before changing: confirm repository path, branch, revision, working
  tree, containers, and backups before a deployment.
- Deploy a commit SHA that exists on GitHub. Do not deploy an uncommitted local
  working tree.
- Keep the administrator identity feature disabled until migrations, owner
  bootstrap, and readiness gates succeed.
- Do not delete Docker volumes or reverse database migrations as a routine
  rollback. Disable the feature and return to the last known-good application
  revision first.
- Never use `git reset --hard` or overwrite a dirty server checkout. Stop and
  resolve unexpected changes with the operator.

## Repository boundary

The public CivicLoop repository contains reusable application source, tests,
OpenAPI 3.1 and JSON Schema 2020-12 contracts, Compose configuration, and this
generic runbook. The private production repository contains hostnames, current
deployment state, firewall details, backups, incident notes, and release
promotion records. See [repository-boundary.md](repository-boundary.md).

## 1. Prepare a workstation

Install Git, Docker Desktop or Docker Engine with Compose v2, Python through
`uv`, Node.js/npm, OpenSSH, and optionally GitHub CLI. Clone the public repo and
verify its remotes before making changes.

```powershell
git clone git@github.com:karthik-kannan-product/CivicLoop.git
Set-Location CivicLoop
git remote -v
git status --short
```

Use a focused branch. When another checkout already uses that branch, create a
worktree rather than switching or disturbing it.

```powershell
git fetch origin
git worktree add .worktrees/<worktree-name> -b feature/<feature-name> origin/main
Set-Location .worktrees/<worktree-name>
```

If resuming an existing remote branch:

```powershell
git fetch origin
git worktree add .worktrees/<worktree-name> feature/<feature-name>
```

Read `AGENTS.md` when present, the relevant files under `docs/`, and any
implementation plan before editing. Preserve unrelated changes.

## 2. Develop in verified increments

Use this loop for each coherent slice:

1. Write or update the smallest regression/contract test.
2. Run it and confirm the expected failure when fixing a bug.
3. Implement one focused change.
4. Run targeted tests and static checks.
5. Inspect `git diff` and `git status` for unintended files or secrets.
6. Commit the verified slice with a descriptive conventional commit message.
7. Push the branch as a recoverable checkpoint.

Do not add real provider credentials to make tests pass. Use synthetic values
and local/disposable services.

## 3. Run the release candidate gates

From the repository root on PowerShell:

```powershell
uv sync --locked
$env:CIVICLOOP_ENV='test'
$env:DATABASE_URL='sqlite:///:memory:'
uv run ruff check backend tests scripts
uv run mypy scripts
uv run python backend/manage.py check
uv run python backend/manage.py makemigrations --check --dry-run
uv run pytest -q
uv run python scripts/validate_api_contracts.py
python .\loops\launchloop\launchloop.py

Set-Location frontend
npm ci
npm test -- --run
npm run build
npm audit --audit-level=high
Set-Location ..

$env:CIVICLOOP_IDENTITY_KEY_HOST_PATH='C:/absolute/path/to/non-production-keyring.json'
docker compose config --quiet
git diff --check
```

Use disposable PostgreSQL and Valkey instances for the PostgreSQL-only audit,
concurrency, and real rate-limit tests. Never remove an existing named volume
to create a test environment. CI also runs these gates on Linux and exercises
the enabled Compose path.

For a user-interface change, test the compiled application in an isolated
browser at 320px, 768px, and 1440px. Check keyboard-accessible controls,
console/network failures, secret persistence, and horizontal overflow.

## 4. Review, commit, and sync through GitHub

Review correctness, error paths, security boundaries, readability,
architecture, performance, test coverage, and dependency changes. Before the
commit, inspect both the staged diff and likely secret terms; a match is a
review prompt, not automatically a leak, because documentation may correctly
mention words such as `password` or `secret`.

```powershell
git status --short
git diff --staged
git diff --staged | Select-String -Pattern 'password|secret|api[_-]?key|token' -CaseSensitive:$false
git commit -m "<type>: <specific outcome>"
$env:GIT_SSH_COMMAND='C:/Windows/System32/OpenSSH/ssh.exe'
git push origin feature/<feature-name>
git status --short
git rev-parse HEAD
```

Record the exact pushed SHA in the private handoff. Wait for required GitHub
checks before production promotion. A branch name is mutable; the SHA is the
release candidate identity.

## 5. Restore SSH access safely

The Vultr firewall restricts port 22 to the operator's current public IPv4
address. Determine it from the workstation, then add `<current-ip>/32` to the
Vultr firewall before connecting. Remove obsolete client-IP rules after the new
rule works. Do not open SSH to `0.0.0.0/0`. Ports 80 and 443 may remain public.

```powershell
(Invoke-RestMethod -Uri 'https://api.ipify.org?format=text').Trim()
ssh -o BatchMode=yes linuxuser@<server-ip> "hostname"
```

Treat a TCP timeout as a network/firewall problem. Treat `Permission denied`
as an SSH account/key problem. Do not weaken SSH authentication to work around
either condition.

## 6. Inspect production before mutation

Run read-only checks first. Do not print `.env` or secret-file contents.

```bash
hostname
cd /srv/civicloop/app
git remote -v
git branch --show-current
git rev-parse HEAD
git status --short
docker compose ps
docker system df
test -f /srv/civicloop/secrets/identity-keyring.json && \
  stat -c '%u:%g %a %n' /srv/civicloop/secrets/identity-keyring.json
```

Stop if the checkout is dirty, storage is critically low, required containers
are unexpectedly unhealthy, or the repository/remote differs from the private
handoff. Capture a database backup and confirm the last known-good SHA before
migration.

## 7. Promote the pinned revision

Fetch the reviewed branch, verify the commit exists, and check out the exact
approved SHA. Never use an unreviewed server-local commit.

```bash
cd /srv/civicloop/app
git fetch origin
git cat-file -e <approved-sha>^{commit}
git checkout --detach <approved-sha>
git status --short
git rev-parse HEAD
```

For administrator identity, follow
[admin-security.md](admin-security.md). In summary:

1. Create `/srv/civicloop/secrets/identity-keyring.json` outside Git without
   printing its generated value; set ownership/mode to `10001:10001 600`.
2. Configure the untracked production `.env` with the container and host key
   paths while keeping `CIVICLOOP_ADMIN_IDENTITY_ENABLED=false`.
3. Render Compose configuration quietly and build the pinned revision.
4. Start PostgreSQL and Valkey, then run migrations.
5. Run `bootstrap_owner` interactively. The human enters the username, email,
   and password directly in the SSH/Vultr console.
6. Change only the feature flag to `true`, recreate the web service, and run
   administrator-aware readiness.

```bash
docker compose config --quiet
docker compose build
docker compose up -d --wait db valkey
docker compose up --no-deps --exit-code-from migrate migrate
docker compose run --rm --no-deps web manage bootstrap_owner
# Human changes CIVICLOOP_ADMIN_IDENTITY_ENABLED to true in the untracked .env.
docker compose up --no-deps --exit-code-from migrate migrate
docker compose up -d --no-deps --force-recreate web worker scheduler
docker compose exec -T web python scripts/readiness.py \
  --base-url http://localhost:8000 --require-admin-identity
```

Do not enable the feature before an owner exists. Worker and scheduler remain
explicitly unable to load administrator identity secrets.

## 8. Verify the public deployment

Verify locally on the host and through the public TLS endpoint:

```bash
docker compose ps
docker compose exec -T web python scripts/readiness.py \
  --base-url http://localhost:8000 --require-admin-identity
curl -fsS https://<public-hostname>/api/v1/health/live
curl -fsS https://<public-hostname>/api/v1/health/ready
```

Open `https://<public-hostname>/admin/security` in a fresh browser. Confirm:

- TLS is valid and no container port is publicly exposed;
- password alone does not grant administrator access;
- TOTP enrollment is local and produces ten one-time recovery codes;
- a new browser requires password plus TOTP;
- synthetic demo users cannot access administrator APIs;
- no secrets appear in browser storage, URLs, logs, traces, or screenshots.

After success, record the deployed SHA, timestamp, service health, readiness
result, and remaining human action in the private handoff. Do not record secret
values.

## 9. Roll back without destroying evidence

If administrator identity fails, first set
`CIVICLOOP_ADMIN_IDENTITY_ENABLED=false` and recreate `web`. If the application
revision must be rolled back, check out the recorded last known-good SHA,
rebuild, and recreate application services. Keep PostgreSQL data, the external
key ring, and security events. Do not run `docker compose down -v`.

```bash
docker compose up -d --no-deps --force-recreate web worker scheduler
curl -fsS https://<public-hostname>/api/v1/health/ready
```

Escalate before reversing migrations, deleting credentials, deleting volumes,
or rotating/revoking external provider keys.

## 10. Resumable AI-agent handoff

Every agent session that changes delivery state must update the private handoff
with only non-secret facts:

- UTC/local timestamp and operator-approved objective;
- local repository path, branch, clean/dirty status, and HEAD SHA;
- GitHub remote branch and pushed SHA;
- CI state and exact verification commands/results;
- server hostname/IP, SSH user, repository path, deployed SHA, and service
  health;
- feature-flag state, whether an owner exists, and whether TOTP enrollment is
  complete—never the associated values;
- backup identifier/location classification without backup contents;
- last successful step, current blocker, next safe command, rollback SHA, and
  actions requiring human interaction.

An agent resuming work must verify these facts against GitHub and the server;
it must not assume the handoff is current. When state differs, stop before
mutation and report the discrepancy.
