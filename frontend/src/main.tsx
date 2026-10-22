import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { loadPreview, PREVIEW } from "./lib/preview";
import "./index.css";

const root = createRoot(document.getElementById("root")!);
const render = () =>
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );

if (PREVIEW)
  loadPreview().then(render, (e: Error) => {
    document.getElementById("root")!.textContent = `Preview data could not be loaded: ${e.message}`;
  });
else render();
