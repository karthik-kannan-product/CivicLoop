# Iterable marketing draft content

New CivicLoop template requests append a fixed HTML footer containing only
`{{unsubscribeUrl}}` and `{{physicalAddress}}`. Hermes cannot author these tags
or other template expressions: its subject and plain text remain literal,
escaped content. Owner review shows the exact HTML, including this footer.

This supports projects where automatic unsubscribe blocks are disabled without
changing project settings. Iterable's project setting supplies the address
when a message is rendered; configuring a real mailing address remains necessary
before any future sending workflow. CivicLoop's current workflow permits only
unscheduled, unactivated drafts and grants no send authority.

New requests also explicitly set `cacheDataFeed:false`. Readback must match every
reviewed field and must not add recipients, feeds, or unreviewed content. A blank
or mismatched provider template remains UNKNOWN, never a successful draft.

Legacy requests retain their original fields, bytes, digests, and GET-only
reconciliation. They cannot be approved or dispatched after this change. A new
Hermes draft and fresh exact owner review are required for new content; neither
UNKNOWN upserts nor existing provider templates are automatically retried or
updated. A campaign remains a separate exact approval with `scheduleSend:false`.

References: [Iterable Project Settings](https://support.iterable.com/hc/en-us/articles/206382645-Project-Settings),
[API template endpoints](https://support.iterable.com/hc/en-us/articles/204780579-Iterable-API-Endpoints-and-Sample-Payloads),
[official API schema](https://raw.githubusercontent.com/Iterable/api-client/main/api-docs.json).
