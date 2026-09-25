// Mermaid loader for mkdocs-material.
// Material for MkDocs natively initializes Mermaid when a page contains a
// mermaid fence; this loader pins the CDN version and hands the instance to
// Material (window.mermaid), which then does the rest (auto-theming, instant
// loading, light/dark scheme support).
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "loose",
});

window.mermaid = mermaid;
