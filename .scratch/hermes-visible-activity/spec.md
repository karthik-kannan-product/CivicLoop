# Hermes Visible Activity

## Goal

Make the authenticated LaunchLoop journey show three durable specialist runs:
Event Readiness, Campaign Composer, and Audience & Policy. The operator starts
one workflow run; the app records bounded activity for each specialist and
produces the existing review-only package. No agent may invoke an external
action.

## Decisions

- The first delivery uses a deterministic, tool-free provider that implements
  the same adapter contract planned for Hermes Agent.
- A live Hermes provider is deliberately out of scope until explicit provider
  configuration is supplied; this slice remains safe without it.
- Each workflow run creates exactly three specialist records and never starts a
  fourth. The current single-workflow demo therefore cannot exceed the global
  cap of three active specialists.
- PostgreSQL stores run state and visible activity. The API returns this durable
  activity with the demo state whenever the React workspace refreshes it.
- The package remains assembled and validated by deterministic application code.

## Non-goals

- No live LLM key, provider inference, Hermes tool execution, or external
  Eventbrite/Iterable action.
- No Celery dispatch, durable outbox, cancellation, retry, or SSE in this slice.
- No change to the approval gate, package hashing, or synthetic demo accounts.
