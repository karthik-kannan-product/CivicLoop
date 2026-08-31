# Eventbrite Live-Read Pilot

Task 10A adds a deliberately narrow bridge from real Eventbrite state into
CivicLoop's local review workflow.

An authenticated administrator can refresh up to two current events across up
to 10 Eventbrite organizations during the production pilot. The adapter uses
only HTTPS `GET` requests to the official organization and event-list
endpoints, rejects redirects, bounds response size and pagination, and
extracts only event ID, title, status, changed time, start/end time, and
timezone. Eventbrite's organization-list endpoint is paged without a
`page_size` parameter because that endpoint rejects it; event-list requests
remain explicitly bounded to two records. Descriptions, attendees, raw
provider payloads, and credentials never leave the adapter.

The workspace presents three safe outcomes:

- No events: explain the empty state and keep manual initiation available.
- One event: show its status and allow selection only for an available draft or
  live event.
- Many events: show a compact list without auto-selecting or auto-running work.

Selecting an event creates or updates a local draft. Each provider change gets
an immutable sanitized snapshot, and each imported event revision links to the
exact snapshot it used. Repeating an unchanged refresh or selection is
idempotent. Missing provider events remain visible as unavailable so that local
review history is preserved.

This pilot cannot create, edit, publish, cancel, price, discount, or otherwise
mutate an Eventbrite event. It also cannot send or schedule Iterable messages.
All consequential actions remain behind later capability and approval gates.
