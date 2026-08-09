from django.urls import path

from identity import views

urlpatterns = [
    path("security/status", views.security_status, name="admin-security-status"),
    path("auth/password", views.password_challenge, name="admin-auth-password"),
]
