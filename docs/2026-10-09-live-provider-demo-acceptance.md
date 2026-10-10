# Live provider demo acceptance

Owner-confirmed goal, October 9, 2026. The existing CivicLoop production
environment and private operations -> public application -> GitHub -> Vultr
release route remain the delivery route.

## Required journey

1. An authenticated owner uses the deployed Hermes agent with its real model
   provider. A successful isolated evaluator or a local proposal is insufficient.
2. CivicLoop can page through every accessible Eventbrite draft, showing whether
   more pages remain. The two-record production smoke limit is not the product
   browsing limit. A partial page must not invalidate unseen cached records.
3. Hermes proposes content; the authenticated owner reviews and approves the
   exact event revision and provider request. Approved execution creates an unpublished Eventbrite event.
   Its provider ID, `draft` status, and matching read-back establish success.
4. CivicLoop saves invitation and reminder campaigns in the real Iterable
   account through its API with `scheduleSend: false`. The owner accepted this
   route even when Iterable reports `Ready` rather than literal `Draft`.
   Receipts must show the actual state. Campaigns remain unscheduled and
   unactivated, with no sends, scheduling, proof sends, or activation calls.
5. Provider requests remain in CivicLoop's credential broker. Hermes never
   receives provider credentials. Existing pending broker intents remain
   separate from durable approved execution and provider receipts.
6. Repeated requests cannot duplicate confirmed operations. An ambiguous create
   or a crash after dispatch is reconciled rather than blindly repeated.

## Iterable contract decision

The owner first requested literal Draft campaigns, then explicitly accepted API
creation of unscheduled campaigns with `scheduleSend: false` after reviewing the
provider distinction. Template-only creation does not complete the campaign
journey. The request must set the flag explicitly because its default is true.

Primary reference: [Iterable's official API schema](https://github.com/Iterable/api-client/blob/main/api-docs.json),
`CreateCampaignRequest.scheduleSend`. A blast requires an existing template
and nonempty list IDs. Sender, approved audience and suppression configuration
must be resolved before the exact provider request is reviewed; no constituent
export or new segment creation is part of this journey.

## Review model decision

The owner explicitly chose owner review of Hermes's exact draft request for this
demo, replacing the older plan's second-human requirement for this draft-only
lane. Approval requires the existing full owner MFA session and immutable
revision/request digests. Sandbox personas cannot approve production provider
writes. This does not change the sandbox's separate approval flow or grant
send, publish, scheduling, or activation authority.

## Completion evidence

Report local implementation, focused verification, cloud synchronization, and
production activation separately. Production completion requires the owner UI
journey, actual provider IDs and read-backs, no-send controls, recovery evidence,
and the existing deployment controls. Neither pending operation counts nor
simulated `sandbox_iterable` receipts establish provider execution.

The previous isolated nine-scenario pass remains valid for its frozen sources.
It does not prove this expanded live provider journey or newly changed code.
