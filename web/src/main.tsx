import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Root from "./app/Root";
import "./ui/styles.css";
import "./ui/motion.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
