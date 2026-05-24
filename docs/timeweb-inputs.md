# Timeweb Inputs

Do not paste provider tokens into chat or commit them to Git.

## Required first

- Timeweb Cloud API token for OpenTofu. Use `TF_VAR_twc_token` locally or map the GitHub secret to that env var in CI.
- Existing Timeweb resources must be imported into the S3-backed state before `plan` becomes authoritative.
- Primary region/location defaults to `ru-1`; existing resources may live in other availability zones.

For this repository, the expected GitHub Actions secret name is `TIMEWEB_TOKEN`.

## Current import scope

The sanitized inventory is stored in `inventory/timeweb/inventory.public.json`. Sensitive values discovered through the API stay outside Git.

OpenTofu currently models these existing Timeweb resources:

- Projects.
- Two cloud servers.
- One managed Postgres database cluster through `twc_database_cluster`.
- Two SSH public keys.
- Three floating IPs.
- The Timeweb S3 bucket used for state, as a data source.

The first state import is run through the manual `OpenTofu Import Existing Infra` workflow. It uses `tofu import` commands and then shows drift with `tofu plan`; it does not run `tofu apply`.

After the first import, the initial plan may show same-value in-place updates for provider fields that were not written into state during import, such as project names, server OS IDs, and SSH key bodies. Review that plan before running the manual `OpenTofu Apply` workflow.

## Secret mapping

Repository or organization secrets expected by the OpenTofu workflows:

```text
TIMEWEB_TOKEN
TOFU_STATE_ACCESS_KEY
TOFU_STATE_SECRET_KEY
```

Do not add database passwords or server root passwords to GitHub unless a workflow truly needs them.

## Not imported yet

These resources were discovered but are not yet managed as OpenTofu resources:

- Domains and mailboxes: provider resources are not currently modeled in this repository.
- Database users: the current cluster import does not require the discovered database login/password.
- The S3 state bucket as a resource: it is used by the backend and referenced as a data source first.
- Server system disks as separate `twc_server_disk` resources: the provider resource is documented for additional disks, while system disks are already visible as read-only server attributes.

## If creating new resources later

- OS image/version.
- CPU, RAM, disk type, disk size.
- Network/VPC layout.
- SSH public keys to inject.
- Firewall rules for SSH, HTTP, HTTPS, monitoring, and admin panels.
- Backup schedule and retention.

## DNS and TLS

- Domains/subdomains to route through Traefik.
- DNS zone owner: Timeweb DNS, Cloudflare, or another provider.
- Email for Let's Encrypt account registration.

## State and backups

- Where OpenTofu state should live: local for now, Timeweb S3-compatible object storage, GitHub Actions artifact, or a dedicated state backend.
- Object Storage bucket name, endpoint, access key, and secret key if we choose S3-compatible state.
- Restic repository backend and credentials for backups.

## Import notes

The Timeweb token can be used to inventory resources that the token is allowed to see across projects. Some sensitive fields may be returned by API, such as server root passwords, database passwords, and S3 credentials. These values must stay outside Git.

Because this repository is public, do not print full Timeweb inventory into GitHub Actions logs or public artifacts. Discovery output should either be heavily redacted or produced outside public CI.
