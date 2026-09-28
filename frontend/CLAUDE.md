# Frontend conventions

Read first: `docs/wiki/frontend/README.md`, `docs/wiki/contracts/`.

- **Stack:** static HTML, vanilla ES modules and one CSS file. No framework and no build step. Chart.js is the only library.
- **API calls:** all calls go through `js/api.js`. Use only the endpoints and fields documented in `docs/wiki/contracts/`; if something is missing there, report it instead of guessing.
- **AI labelling:** AI-extracted values show an "AI-generated" badge until the user edits or confirms them. Flags are shown with their `message`. Never show a confidence percentage.
- **Errors:** show `detail` from the error body. Use the `error` code to decide the action, for example Retry for `llm_unavailable`.
- **Accessibility:** use labels on every input and keyboard-usable controls, and don't signal a flag by colour alone.
- **Security:** build DOM nodes with `textContent`, never `innerHTML`, when inserting data (receipt text is untrusted).
