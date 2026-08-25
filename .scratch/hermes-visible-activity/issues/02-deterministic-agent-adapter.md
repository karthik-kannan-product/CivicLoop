# Deterministic agent adapter

Type: task
Status: resolved
Blocked by: 01

Add the provider seam and deterministic specialist executor. It must emit
bounded, safe activity and allow the existing deterministic package engine to
remain the authority for package content and workflow transition.

## Acceptance criteria

- The three specialists each progress from queued to completed.
- Missing facts yield a completed readiness run with a needs-input outcome.
- No adapter exposes tools, network calls, credentials, or external mutation.

## Verification

- Focused API/service tests prove the three-run result and no-action boundary.
