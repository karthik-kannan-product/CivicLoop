# Owner event drafts

The owner event flow prepares event copy for Hermes and provider request review. It accepts manual briefs and corrected Eventbrite imports. The synthetic LaunchLoop demonstration remains a separate workflow with its existing audience, sponsor and independent approval rules.

An owner confirms the title, date, start and end times, timezone, city, region, country, venue name, address, access instructions and signup URL. Saving facts creates a new immutable event revision. Invalid or incomplete facts prevent generation. The event package makes no audience membership or sponsor discount claim: recipient lists and suppressions are selected during the exact provider request review.

Hermes admission requires an active owner session with full MFA, a current ready package and matching revision and package digests. The run binds that session and records `pilot_minimized` with no synthetic fixture. The worker checks the binding again before execution. MCP exposes event facts through a fixed allowlist; integration credentials and recipient records remain outside the model's context.

Generation produces proposals and pending intents. It does not create provider drafts by itself. Eventbrite draft creation requires review and approval of the exact request, followed by an unpublished draft readback. Iterable content and campaign creation use separate approved operations; campaign requests explicitly set `scheduleSend: false` and include the chosen audience and suppression IDs, including the global suppression list. The provider readback must confirm an unscheduled, unactivated campaign. An uncertain outcome must be reconciled before another operation can be attempted.

No owner event operation grants authority to send, schedule delivery, activate a campaign or publish an Eventbrite event. Synthetic evaluation does not accept the real event package. Production readiness additionally requires the reviewed runtime, deployment controls and actual provider acceptance; source tests alone do not establish a completed live demo.
