import { SwaggerUIBundle, SwaggerUIStandalonePreset } from "swagger-ui-dist";
import "swagger-ui-dist/swagger-ui.css";

function cookie(name) {
  return document.cookie
    .split("; ")
    .find((item) => item.startsWith(`${name}=`))
    ?.split("=")[1] ?? "";
}

SwaggerUIBundle({
  url: "/api/v1/contracts/openapi/civicloop-v1.yaml",
  dom_id: "#swagger-ui",
  deepLinking: true,
  displayRequestDuration: true,
  persistAuthorization: false,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
  layout: "StandaloneLayout",
  requestInterceptor(request) {
    const csrfToken = cookie("csrftoken");
    if (csrfToken && request.method !== "GET") {
      request.headers["X-CSRFToken"] = csrfToken;
    }
    request.credentials = "same-origin";
    return request;
  },
});
