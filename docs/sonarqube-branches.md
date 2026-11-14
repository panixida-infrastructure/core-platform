# SonarQube branches and pull requests

The server uses SonarQube Community Build **26.10.0.133078**. The chart installs
the Community Branch Plugin JAR and its matching patched webapp, with SHA256
verification, and enables both Java agents. The stock server image is retained.
The configuration job records plugin consent when this explicitly enabled plugin
is installed, so the web UI is usable after reconciliation.

## Plugin release

The chart uses [mc1arke 26.10.0](https://github.com/mc1arke/sonarqube-community-branch-plugin/releases/tag/26.10.0)
for SonarQube 26.10. When updating it, pin the JAR filename, both download URLs and
both SHA256 checksums from the same release.

The official image includes Temurin 25.0.5+8 with the fix for
[JDK-8375125](https://bugs.openjdk.org/browse/JDK-8375125). The web, Compute Engine
and Elasticsearch JVMs use `-XX:TrimNativeHeapInterval=300000` to return unused
native memory to Linux every five minutes.

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

## Closed pull request cleanup

The `quality/sonarqube-pr-cleanup` CronJob checks GitHub-bound Sonar projects every
minute, using the existing Sonar GitHub App with repository-scoped read tokens.
It deletes closed, unmerged PR analyses on the next pass. Merged PR analyses wait
for a completed target-branch analysis with an OK Quality Gate, dated after the
merge, whose Git revision contains the merge commit. Open PRs, running PR analyses
and entries with missing metadata or API failures are retained. GitHub state is
rechecked immediately before deletion. Only Sonar PR analyses are deleted.

The script defaults to dry run; `--apply` enables deletion and `--project KEY`
limits it to one project. The CronJob reads only the three required keys from
`sonarqube-secrets` and has no Kubernetes API token. Disable it with
`sonarqube.prCleanup.enabled=false`. Sonar's existing age-based housekeeping is
independent of this job.

## Rollout and recovery

Before updating SonarQube, restore a fresh database backup into an isolated
PostgreSQL instance and test the new image with its matching plugin. If SonarQube
reports that a database migration is needed, complete it at `/setup`. Check the
branch and PR APIs, Quality Gates and the settings UI. Isolate the test instance
from production integrations and use an unbound project for test analyses.

The Deployment uses `Recreate`, so rollout interrupts analysis until startup and
any required database migration finish. The init
container moves the previous JAR to `disabled-plugins`, and the new pod extracts
the matching webapp into an empty volume. Keep the previous Helm values, plugin
assets and database backup until live validation completes. After validation,
remove the superseded JAR from `disabled-plugins`. Set
`sonarqube.branchPlugin.enabled=false` to start the stock webapp without agents;
the init container moves only Community Branch Plugin JARs into `disabled-plugins`
on the existing PVC, leaving other plugins untouched. This provides a server
rollback, but the stock edition cannot display branch/PR data. Do not downgrade
Sonar or restore an old backup over newer data. Restore the pre-change database
only if plugin removal is insufficient, with the service stopped and after
preserving the current database.
