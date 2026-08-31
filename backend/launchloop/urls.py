from django.urls import path

from . import views

urlpatterns = [
    path("auth/session", views.auth_session, name="auth-session"),
    path("auth/login", views.auth_login, name="auth-login"),
    path("auth/logout", views.auth_logout, name="auth-logout"),
    path("demo", views.demo_state, name="demo-state"),
    path("demo/reset", views.demo_reset, name="demo-reset"),
    path("events/manual", views.manual_event_start, name="manual-event-start"),
    path("eventbrite/events", views.eventbrite_events, name="eventbrite-events"),
    path(
        "eventbrite/events/refresh",
        views.eventbrite_events_refresh,
        name="eventbrite-events-refresh",
    ),
    path(
        "eventbrite/events/<uuid:source_id>/select",
        views.eventbrite_event_select,
        name="eventbrite-event-select",
    ),
    path(
        "workflows/<uuid:workflow_id>/runs",
        views.workflow_run,
        name="workflow-run",
    ),
    path(
        "workflows/<uuid:workflow_id>/answers",
        views.workflow_answers,
        name="workflow-answers",
    ),
    path(
        "workflows/<uuid:workflow_id>/submit",
        views.workflow_submit,
        name="workflow-submit",
    ),
    path(
        "approvals/<uuid:approval_id>/decision",
        views.approval_decision,
        name="approval-decision",
    ),
]
