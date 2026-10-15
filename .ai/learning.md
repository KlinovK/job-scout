# Learning Protocol

JobScout development should improve the owner's engineering judgment, not only produce code. Explanations should be concise, grounded in the actual diff, and appropriate for a developer with Swift/iOS experience who is strengthening production Python and backend skills.

## Developer Goals

1. Build a genuinely useful vacancy-search product.
2. Learn professional Python backend engineering.
3. Learn fullstack architecture and development.
4. Learn AI engineering and AI-assisted development workflows.
5. Maintain high engineering quality.
6. Automate repetitive implementation work through Codex.

Support those goals through real production work. Explain relevant Python typing, async behavior, layered backend architecture, reliable integrations, persistence, migrations, testing, Git, and AI review when they matter. Compare Python protocols and concurrency with Swift/iOS concepts when that makes the trade-off clearer, while noting semantic differences.

## Explanation Pattern

When a task has a learning goal, include only the useful parts of this structure:

- **Concept:** the engineering idea in plain language.
- **JobScout example:** the concrete class, service, migration, test, or diff involved.
- **Swift analogy:** a short comparison where it clarifies rather than oversimplifies.
- **Trade-off:** why this design was chosen and what it costs.
- **Evidence:** the test or review step that protects the behavior.
- **AI review question:** one question the owner can ask to detect a plausible generated-code mistake.

Do not turn routine delivery reports into tutorials. Prefer one precise explanation over a glossary, and clearly separate implemented behavior from future design ideas.
