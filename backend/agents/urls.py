from django.urls import path
from integrations import draft_views, iterable_views

from agents import views

# Not included by civicloop.urls: only the dedicated MCP process installs these.
internal_urlpatterns = [path("internal/v1/mcp", views.mcp_endpoint, name="internal-mcp")]

urlpatterns = [
    path(
        "agent-runs/<uuid:run_id>/pending-operations/<uuid:intent_id>/iterable-review",
        iterable_views.prepare,
        name="iterable-prepare",
    ),
    path(
        "agent-runs/<uuid:run_id>/pending-operations/<uuid:intent_id>/iterable-campaign-review",
        iterable_views.prepare_campaign,
        name="iterable-campaign-prepare",
    ),
    path(
        "iterable-template-executions/<uuid:operation_id>",
        iterable_views.template_detail,
        name="iterable-template-detail",
    ),
    path(
        "iterable-template-executions/<uuid:operation_id>/approve",
        iterable_views.template_approve,
        name="iterable-template-approve",
    ),
    path(
        "iterable-template-executions/<uuid:operation_id>/execute",
        iterable_views.template_execute,
        name="iterable-template-execute",
    ),
    path(
        "iterable-template-executions/<uuid:operation_id>/reconcile",
        iterable_views.template_reconcile,
        name="iterable-template-reconcile",
    ),
    path(
        "iterable-campaign-executions/<uuid:operation_id>",
        iterable_views.campaign_detail,
        name="iterable-campaign-detail",
    ),
    path(
        "iterable-campaign-executions/<uuid:operation_id>/approve",
        iterable_views.campaign_approve,
        name="iterable-campaign-approve",
    ),
    path(
        "iterable-campaign-executions/<uuid:operation_id>/execute",
        iterable_views.campaign_execute,
        name="iterable-campaign-execute",
    ),
    path(
        "iterable-campaign-executions/<uuid:operation_id>/reconcile",
        iterable_views.campaign_reconcile,
        name="iterable-campaign-reconcile",
    ),
    path(
        "agent-runs/<uuid:run_id>/pending-operations/<uuid:intent_id>/draft-review",
        draft_views.prepare,
        name="draft-prepare",
    ),
    path("draft-executions/<uuid:operation_id>", draft_views.detail, name="draft-detail"),
    path("draft-executions/<uuid:operation_id>/approve", draft_views.approve, name="draft-approve"),
    path("draft-executions/<uuid:operation_id>/execute", draft_views.execute, name="draft-execute"),
    path(
        "draft-executions/<uuid:operation_id>/reconcile",
        draft_views.reconcile,
        name="draft-reconcile",
    ),
    path("workflows/<uuid:workflow_id>/hermes-runs", views.start_hermes, name="hermes-start"),
    path(
        "agent-runs/<uuid:run_id>/pending-operations",
        views.pending_operations,
        name="hermes-pending-operations",
    ),
    path("agent-runs/<uuid:run_id>/cancel", views.cancel_hermes, name="hermes-cancel"),
    path("agent-runs/<uuid:run_id>", views.run_detail, name="agent-run-detail"),
    path("agent-runs/<uuid:run_id>/steps", views.run_steps, name="agent-run-steps"),
    path(
        "agent-runs/<uuid:run_id>/evaluations",
        views.run_evaluations,
        name="agent-run-evaluations",
    ),
    path("agent-runs/<uuid:run_id>/usage", views.run_usage, name="agent-run-usage"),
]
