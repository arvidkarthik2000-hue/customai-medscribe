// Loads only if this browser understands the newer script features the demo pages use.
const probe = { a: { b: 1 } };
window.__msModern = (probe?.a?.b ?? 0) === 1;
