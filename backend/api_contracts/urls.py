from django.urls import path

from . import views

urlpatterns = [
    path("docs", views.swagger_ui, name="swagger-ui"),
    path(
        "v1/contracts/<str:group>/<path:relative_path>",
        views.contract_asset,
        name="api-contract-asset",
    ),
]
