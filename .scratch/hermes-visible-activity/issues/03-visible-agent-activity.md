# Visible agent activity

Type: task
Status: resolved
Blocked by: 01, 02

Expose specialist runs in the authenticated and browser-local workspaces, with
clear queued/running/completed states, safe summaries, and a capacity indicator.

## Acceptance criteria

- The workspace shows all three specialists and their outcomes after a run.
- The UI communicates the three-agent limit and no-external-action boundary.
- Existing operator-to-approver flow remains usable on mobile and desktop.

## Verification

- Focused React tests and the full frontend suite pass.
- Browser inspection verifies the run activity and no console errors.
