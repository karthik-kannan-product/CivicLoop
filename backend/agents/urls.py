from django.urls import path

from agents import views

# Not included by civicloop.urls: only the dedicated MCP process installs these.
internal_urlpatterns = [path("internal/v1/mcp", views.mcp_endpoint, name="internal-mcp")]

urlpatterns = [
    path("agent-runs/<uuid:run_id>", views.run_detail, name="agent-run-detail"),
    path("agent-runs/<uuid:run_id>/steps", views.run_steps, name="agent-run-steps"),
    path(
        "agent-runs/<uuid:run_id>/evaluations",
        views.run_evaluations,
        name="agent-run-evaluations",
    ),
    path("agent-runs/<uuid:run_id>/usage", views.run_usage, name="agent-run-usage"),
]
