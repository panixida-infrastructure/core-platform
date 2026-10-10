# SonarQube repositories

`repositories.json` is the declarative inventory for repository integrations.
It does not register or configure GitHub Apps.

Each entry contains:

- `repository`: GitHub repository in `owner/name` format;
- `projectKey`: stable SonarQube project key;
- `projectName`: display name in SonarQube;
- `newCode`: `NUMBER_OF_DAYS` (30 days for services) or `PREVIOUS_VERSION`
  (the version cycle from `version.json` for NuGet libraries and their template).

The reconciliation workflow:

1. creates a private SonarQube project when it is missing;
2. binds it to the existing global GitHub integration;
3. assigns the `Sonar way` quality gate;
4. creates a project analysis token when required;
5. writes `SONAR_TOKEN` and `SONAR_PROJECT_KEY` to the repository;
6. writes `SONAR_HOST_URL` once per GitHub organization with visibility for all
   repositories and removes obsolete repository-level copies.
7. reconciles the project's new-code policy and removes branch overrides so
   branches inherit that policy;
8. enables the SonarQube summary comment in GitHub pull requests.

The chart configures the server-wide default to 30 days. Inventory policies are
explicit, including for existing projects. Pull request analysis always compares
the source with its target branch; the project policy applies to branch analysis.
Target branches (including `development`) must have a successful analysis before
their pull requests are analyzed.

Library callers pass `project-version-file: version.json` to the reusable .NET
Sonar workflow. It sends the stable `.version` field as `sonar.projectVersion`.
Changing that field starts the next version cycle after a successful branch
analysis. Do not use a changing CI run number or the computed package build
version: it would move the baseline on every build. The initial versioned analysis
establishes the first real version; verify its resulting period in SonarQube.
Accepted issues stay in the overall backlog when they age out of the new-code
period. The policy changes which code contributes to the new-code Quality Gate.

Changes to `repositories.json` are reconciled automatically after they reach
`main`. Manual dispatch remains available for a single-repository sync or token
rotation.

Existing analysis tokens are retained. Use the workflow's `rotate_tokens`
input only when token rotation is required. Consumer workflow changes are made
through a separate pull request because test job names and dependency graphs
differ between repositories. The SonarQube job must depend on the test job to
consume its coverage report. Every image, package, release, or deployment job
that must be blocked by the Quality Gate must include the SonarQube job in its
dependency chain.

GitHub App registrations, names, permissions, installation approvals, IDs, and
private keys are managed outside Git. The workflow only reads the credential
contract from OpenBao.
