# Contributing to Mercado Discount Manager

Thank you for helping improve 美客多活动管家. Contributions should preserve the project's local-first design, explicit write confirmations, and strict separation between offline validation and real Mercado Libre operations.

## Before opening an issue

- Search existing issues and include a small, reproducible description.
- Remove tokens, client secrets, account identifiers, customer data, raw API bodies, and screenshots containing sensitive information.
- State whether the behavior occurred during a dry run, a local test, or an explicitly confirmed live operation. Do not perform a marketplace write merely to reproduce a problem.

## Development workflow

1. Discuss non-trivial changes in an issue before implementation.
2. Keep changes narrowly scoped and add or update regression tests.
3. Run the relevant local checks. The normal project command is:

   ```powershell
   & 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -NonInteractive -File scripts/validate.ps1 -Mode Quick
   ```

4. Do not include local runtime data, credentials, logs, exports, generated packages, or validation evidence in a pull request.
5. Explain the problem, the test evidence, and any security or marketplace-write implications in the pull request.

## Safety requirements

- A dry run is not authorization to write to Mercado Libre.
- Code must not make real marketplace changes unless the product's existing `mode=real` and `REAL_SUBMIT` safeguards are satisfied by a user who has explicitly confirmed the action.
- Keep all credential handling local. Do not add secrets to source, issues, tests, logs, or documentation.

By submitting a contribution, you agree to license it under the [Apache License 2.0](LICENSE).
