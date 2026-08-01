from django.urls import path

from . import views

urlpatterns = [
    path("auth/session", views.auth_session, name="auth-session"),
    path("auth/login", views.auth_login, name="auth-login"),
    path("auth/logout", views.auth_logout, name="auth-logout"),
    path("demo", views.demo_state, name="demo-state"),
    path("demo/reset", views.demo_reset, name="demo-reset"),
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
