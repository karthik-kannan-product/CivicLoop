from django.urls import path

from integrations import views

urlpatterns = [
    path("integrations", views.connections, name="admin-integration-list"),
    path(
        "integrations/<str:provider>/credential",
        views.credential,
        name="admin-integration-credential",
    ),
    path(
        "integrations/<str:provider>/configuration",
        views.configuration,
        name="admin-integration-configuration",
    ),
    path("integrations/<str:provider>/test", views.test, name="admin-integration-test"),
    path("integrations/<str:provider>/disable", views.disable, name="admin-integration-disable"),
    path("integrations/<str:provider>/audit", views.audit, name="admin-integration-audit"),
]
