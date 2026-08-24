# SonarQube repositories

`repositories.json` is the declarative inventory for repository integrations.
It does not register or configure GitHub Apps.

Each entry contains:

- `repository`: GitHub repository in `owner/name` format;
- `projectKey`: stable SonarQube project key;
- `projectName`: display name in SonarQube.

The reconciliation workflow:

1. creates a private SonarQube project when it is missing;
2. binds it to the existing global GitHub integration;
3. assigns the `Sonar way` quality gate;
4. creates a project analysis token when required;
5. writes `SONAR_TOKEN` and `SONAR_PROJECT_KEY` to the repository;
6. writes `SONAR_HOST_URL` once per GitHub organization with visibility for all
   repositories and removes obsolete repository-level copies.

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
