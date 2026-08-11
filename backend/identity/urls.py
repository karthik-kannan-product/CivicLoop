from django.urls import path

from identity import views

urlpatterns = [
    path("security/status", views.security_status, name="admin-security-status"),
    path("auth/password", views.password_challenge, name="admin-auth-password"),
    path("auth/totp", views.totp_challenge, name="admin-auth-totp"),
    path("auth/recovery", views.recovery_challenge, name="admin-auth-recovery"),
    path("auth/logout", views.logout, name="admin-auth-logout"),
    path(
        "security/totp/enrollment",
        views.totp_enrollment,
        name="admin-totp-enrollment",
    ),
    path(
        "security/totp/confirmation",
        views.totp_confirmation,
        name="admin-totp-confirmation",
    ),
    path(
        "security/reauthentication",
        views.reauthentication,
        name="admin-reauthentication",
    ),
    path("security/password", views.password_change, name="admin-password-change"),
    path(
        "security/recovery-codes/regeneration",
        views.recovery_code_regeneration,
        name="admin-recovery-code-regeneration",
    ),
    path("security/sessions", views.session_list, name="admin-session-list"),
    path(
        "security/sessions/revoke-others",
        views.other_session_revocation,
        name="admin-session-revoke-others",
    ),
    path(
        "security/sessions/<uuid:session_id>/revocation",
        views.session_revocation,
        name="admin-session-revocation",
    ),
    path("security/events", views.security_event_list, name="admin-security-event-list"),
]
