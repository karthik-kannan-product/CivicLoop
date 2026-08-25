# Durable specialist runs

Type: task
Status: resolved

Create durable workflow-scoped specialist run records with a bounded status
and an append-only activity stream.

## Acceptance criteria

- Exactly three named specialist runs can be attached to a LaunchLoop workflow run.
- Run/activity records retain the revision, provider label, status, and safe message.
- Database constraints and tests prevent a fourth active specialist.

## Verification

- Focused Django model/service tests pass.
- Migration drift check passes.
