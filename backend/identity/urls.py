from django.urls import path

from identity import views

urlpatterns = [
    path("security/status", views.security_status, name="admin-security-status"),
    path("auth/password", views.password_challenge, name="admin-auth-password"),
    path("auth/totp", views.totp_challenge, name="admin-auth-totp"),
    path("auth/recovery", views.recovery_challenge, name="admin-auth-recovery"),
    path("auth/logout", views.logout, name="admin-auth-logout"),
]
