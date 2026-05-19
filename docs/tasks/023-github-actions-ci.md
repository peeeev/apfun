# 023 — GitHub Actions CI

**Goal:** Run `make check` automatically on every push and PR via GitHub Actions, with a CI-aware skip for the synthetic-fixture forcing function and a separate manual trigger for the live-API integration suite.

**Complexity:** S

**Sequencing**: Between Phase E (task 019) and Phase F (task 020). Numerically 023 sits after the digest email, but execution-wise this should land *before* the high-stakes UI work (020-022) so PR review starts having automated gate teeth before the final tasks. Sequencing decision from orchestrator feedback 009.

Depends on: nothing strictly — the codebase as it stands.

## Deliverables

- `.github/workflows/check.yml`:
  - Triggers: `push` (to main), `pull_request`.
  - Steps: checkout, set up Python (3.11+), install `uv`, `uv sync` (frozen if uv version supports it), `make check`.
  - `permissions: contents: read` to keep tokens minimal.
- CI-aware skip in `tests/unit/test_anthropic_response_shape.py::test_fixture_is_real_capture`:
  ```python
  def test_fixture_is_real_capture() -> None:
      fixture = json.loads(_FIXTURE_PATH.read_text())
      if "_meta_note" not in fixture:
          return  # real capture in place
      if os.getenv("CI"):
          pytest.skip("synthetic fixture in CI; resolve locally before merge")
      pytest.fail(
          "synthetic fixture — run scripts/capture_response_fixture.py "
          "with APFUN_ANTHROPIC_API_KEY set"
      )
  ```
  Local developers still hit the forcing-function failure; CI doesn't block PRs while the fixture is being arranged.
- `.github/workflows/integration.yml`:
  - Trigger: `workflow_dispatch` (manual button in Actions UI). Don't run on every PR — burns API credits.
  - Pulls `APFUN_ANTHROPIC_API_KEY` from repo secrets; runs `make test-all`.

## Acceptance

- A push to a feature branch triggers the `check` workflow; PR shows the status.
- The `check` workflow passes against the current synthetic-fixture state (skip path engaged).
- `integration.yml` exists and can be dispatched manually with a green run after secret setup.

## Notes

- GitHub Actions sets `CI=true` automatically; the skip-condition leverages it.
- If we later switch to a self-hosted runner (Hetzner box), the workflow shape stays — only the `runs-on` line changes.
- The `make test-all` workflow is a deliberate manual gate; running it on every PR would cost ~$0.05/PR plus rate-limit pressure. The forcing-function fixture replacement happens out-of-band by the developer with their own API key.
- **`contents: read` only.** If we ever consider auto-committing captured fixtures from a CI dispatch, it must be gated behind `workflow_dispatch` with human review of the diff before merge. The fixture-as-contract pattern relies on a human-review moment; automation would silently launder API flakiness into the test suite.
