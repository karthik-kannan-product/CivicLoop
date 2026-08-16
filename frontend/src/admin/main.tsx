/// <reference types="vite/client" />

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AdminApp } from "./AdminApp";
import "./admin.css";
import "./security.css";
import "./integrations.css";

createRoot(document.getElementById("admin-root")!).render(
  <StrictMode><AdminApp /></StrictMode>,
);
