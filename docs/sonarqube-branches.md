# SonarQube branches and pull requests

The server stays on SonarQube Community Build **26.9.0.129388**. The chart installs
the Community Branch Plugin JAR and its matching patched webapp, with SHA256
verification, and enables both Java agents. The stock server image is retained.

## Pinned fork

- Source: [Adrian-Eckardt fork, commit a68cf75cc414a0725e54ec73d7a1ff6d51afadd7](https://github.com/Adrian-Eckardt/sonarqube-community-branch-plugin/tree/a68cf75cc414a0725e54ec73d7a1ff6d51afadd7).
- Upstream contribution: [mc1arke PR #1298](https://github.com/mc1arke/sonarqube-community-branch-plugin/pull/1298).
- Verified build: [run 34239488815](https://github.com/Adrian-Eckardt/sonarqube-community-branch-plugin/actions/runs/34239488815).
- Durable binaries, source archives, licenses and provenance:
  [our pinned release](https://github.com/panixida-infrastructure/core-platform/releases/tag/sonarqube-26.9-cbp-a68cf75).

The release mirrors that build unchanged; checksums are pinned in chart values.
GitHub Actions artifact archive digests were checked before extracting the JAR
and webapp. This avoids deployment relying on expiring upstream CI artifacts.
To rebuild, unpack both source archives, put the webapp source at
`sonarqube-webapp`, and follow the pinned `.github/workflows/build.yml`:
Java 21 `./gradlew clean build`, then Node 22, the addon setup script, Yarn install
and `yarn nx run sq-server:build`. Keep JAR and webapp from the same source revision.

**TODO: switch to an original mc1arke release supporting the deployed server
version once available.** Verify it on a restored database first, then replace
both asset URLs/checksums and the agent JAR filename together. Recheck branch/PR
APIs, Quality Gates and the general settings UI before production deployment.

## Analysis and new code

The [repository inventory](../inventory/sonarqube/README.md) owns project policies:
30 days for services, `Previous version` for libraries. The chart also sets the
server-wide default to 30 days. Reconciliation removes existing branch overrides.
For libraries, the reusable workflow reads the stable `.version` from
`version.json`; each successful branch analysis records that version.

PR analysis uses the PR head commit and the actual target branch, independently
of the 30-day/version window. Analyze `main` and `development` before their PRs.
The server's GitHub App needs Contents read, Pull requests read/write and Checks
read/write, and must be installed for the analyzed repository. Project bindings
enable summary comments. No App key belongs in consumer workflows.

After deployment, run the SonarQube repositories reconciliation workflow, check
`api/new_code_periods/show` globally and per project/branch, run target-branch
analysis, then PR analysis. Verify `api/project_branches/list`,
`api/project_pull_requests/list`, the PR's base branch/head SHA, the Quality Gate
check and the summary comment posted by the Sonar App.

## Rollout and recovery

Before changing the plugin, create a fresh database backup and verify restoring
it into an isolated PostgreSQL instance. Start the same Sonar image with the fork
against that restored database; do not run bound PR analyses there because they
could send comments to production repositories.

The Deployment uses `Recreate`, so rollout briefly interrupts analysis. Keep the
previous Helm values and backup until live validation completes. Set
`sonarqube.branchPlugin.enabled=false` to start the stock webapp without agents;
the init container moves only Community Branch Plugin JARs into `disabled-plugins`
on the existing PVC, leaving other plugins untouched. This provides a server
rollback, but the stock edition cannot display branch/PR data. Do not downgrade
Sonar or restore an old backup over newer data. Restore the pre-change database
only if plugin removal is insufficient, with the service stopped and after
preserving the current database.
