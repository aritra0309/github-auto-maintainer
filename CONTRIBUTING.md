# Contributing

## How To Add A New Skill

- Create a new folder for the skill under the skills directory.
- Add a `SKILL.md` file with purpose, scope, and usage guidance.
- Document required inputs, outputs, and guardrails.
- Include one end-to-end example with expected behavior.
- Keep instructions deterministic and testable.
- Add any prompt templates needed by the skill.
- Add validation checks for malformed or missing inputs.
- Add tests that cover successful and failure scenarios.
- Update README documentation with skill registration steps.
- Add a changelog note under Unreleased.

## How To Add A New Provider

- Add provider implementation in `src/github_auto_maintainer/providers/`.
- Implement the shared provider interface methods.
- Handle auth, retries, and timeout behavior explicitly.
- Add provider configuration in the central settings model.
- Register provider in the provider factory/registry.
- Add structured logging for provider requests/responses.
- Add unit tests for normal, timeout, and error paths.
- Add integration tests with mocked external responses.
- Document required env vars in `.env.example`.
- Update README and changelog with provider details.
