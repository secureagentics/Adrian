# Contributing to Adrian

Thanks for considering a contribution. Adrian is open-source under the [Apache 2.0 licence](LICENSE), and we welcome bug reports, feature requests, documentation improvements, and code.

## Before you start

1. **Read [CLA.md](CLA.md) and sign it.** Every contributor needs a signed Contributor Licence Agreement on file before we can merge any code or significant docs change. Sign-off is a one-line PR adding your name to the signers table; it only needs doing once.
2. **Skim [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** to see the runtime layout: containers, internal Go packages, and where state lives. Full docs [here](https://docs.adrian.secureagentics.ai/). If your change moves boxes around in the diagram, update the diagram in the same PR.
3. **Search existing [issues](https://github.com/secureagentics/Adrian/issues)** before opening a new one to avoid duplicates.


## Pull requests

- Branch off `main`. We don't gate on a `staging` branch at this stage.
- One logical change per PR. Refactors and feature work go in separate PRs.
- Use [`PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md), which ships with the repo.
- Keep commit messages focused: imperative subject, optional body explaining the why.
- Run any tests that exist for the component you touched before pushing.
- New source files get the [`LICENSE_HEADER.txt`](LICENSE_HEADER.txt) header.
- If you'd like credit, add yourself to [`CONTRIBUTORS.md`](CONTRIBUTORS.md) in the same PR or in a one-line follow-up. The list is maintained by hand.

## Local dev setup

The Python SDK lives at `sdk/python/`. From the repo root:

```sh
make sdk-install            # creates .venv and installs sdk + dev deps via uv
source .venv/bin/activate
pre-commit install          # wires the git hook
```

The TypeScript SDK lives at `sdk/typescript/` as an npm workspace with the core package (`@secureagentics/adrian`). From that directory:

```sh
cd sdk/typescript
npm install
npm run build
npm test
```

See [`sdk/typescript/README.md`](sdk/typescript/README.md) for usage examples.

After `pre-commit install`, every `git commit` runs the configured hooks on
staged files: `ruff format`, `ruff check --fix`, `basedpyright` on
`sdk/python/adrian/`, plus the standard whitespace / YAML / TOML checks. Hooks that
modify files (formatter, autofix) leave the commit aborted; re-stage the files
and commit again.

To run the hooks across the whole tree on demand without committing:

```sh
pre-commit run --all-files
```

The config lives at [`.pre-commit-config.yaml`](.pre-commit-config.yaml).
Only Python files under `sdk/python/` and `scripts/` are in scope.

## Style

Adrian's prose follows a small set of rules. Apply them to docs, READMEs, comments, and PR descriptions:

- **British English.** organisation, behaviour, optimisation, finalised, recognise, licence (noun) / license (verb).
- **No em-dashes.** Use hyphens, commas, parentheses, or rephrase.
- **No marketing fluff.** Direct prose; avoid AI cliches.

## Code of Conduct

This project follows the [Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating you agree to abide by its terms.

## Licensing

Every contribution is provided under the terms of the Apache 2.0 licence (see [LICENSE](LICENSE)). New source files must carry the SPDX header from [LICENSE_HEADER.txt](LICENSE_HEADER.txt).

## Questions

- Architecture / design discussions: open a GitHub Discussion.
- Bugs: open an [issue](https://github.com/secureagentics/Adrian/issues/new/choose).
- Feature requests: open an [issue](https://github.com/secureagentics/Adrian/issues/new/choose).
- Chat: [Discord](https://discord.gg/6nmJ9k3u6).
