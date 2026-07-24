# LaunchLoop Implementation Guide

LaunchLoop is a human-approved event campaign launch loop for small nonprofits.

It helps an event operations lead or volunteer turn a draft event into a reviewable campaign package. The current implementation is a static browser demo plus a deterministic Python evaluator using synthetic data.

## What LaunchLoop Does

LaunchLoop:

- reads a synthetic Eventbrite-style event record
- checks required event fields
- drafts invitation and reminder copy
- drafts a LinkedIn/social post
- recommends an approved audience segment
- asks for clarification when no approved segment exists
- validates sponsor discount rules and price math
- preserves placeholders for missing details
- blocks incomplete or risky packages
- refuses unapproved send, publish, pricing, discount, or segment actions
- records trace-style evidence for human review

## What LaunchLoop Does Not Do

LaunchLoop does not:

- connect to live Eventbrite, Iterable, LinkedIn, Stripe, or a member database
- authenticate users
- enforce real role-based permissions
- send or schedule emails
- publish social posts
- publish Eventbrite pages
- change prices
- create discounts
- create audience segments
- export member/contact data

## Eval Cases

The current evaluator covers six cases:

1. happy path: complete Toronto event
2. missing venue: New York event blocked with placeholders
3. refresh: New York venue details added and package becomes ready
4. sponsor mismatch: bronze sponsor discount mismatch blocks approval
5. boundary refusal: request to send/publish without approval is refused
6. segment judgment: Philadelphia event has no approved Pennsylvania segment, so LaunchLoop asks for clarification instead of nearest-matching

Run:

```powershell
cd loops\launchloop
python .\launchloop.py
```

Expected result: `6 / 6` cases pass.

## Pilot Guardrails

For a real pilot, keep the workflow small, reversible, and observed:

- one nonprofit
- one lower-risk event
- approval-only workflow
- segment-level aggregate counts by default
- no bulk member/contact export
- no autonomous send, publish, price, discount, or segment action
- every output reviewed by a human approver
- immediate pause for privacy leak, missing traces, broken approval gate, or unauthorized action

## Adapting the Loop

To adapt LaunchLoop:

1. Replace synthetic event and audience data with your organization-approved data source.
2. Define required event fields and approved audience segments.
3. Encode sponsor discount rules as policy files or database records.
4. Add explicit submitter, approver, admin, privacy owner, and technical owner roles.
5. Add real audit logs and traces.
6. Add eval cases before expanding to new geographies, event types, sponsor tiers, or integrations.

Keep the first production pilot approval-only. Autonomy should be earned only after evidence shows the loop is reliable and safe.
