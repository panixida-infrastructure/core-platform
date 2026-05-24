# Timeweb Inputs

Do not paste provider tokens into chat or commit them to Git.

## Required first

- Timeweb Cloud API token for OpenTofu. Use `TF_VAR_twc_token` locally or map the GitHub secret to that env var in CI.
- Decision: import the existing server into state, or create a new server from code.
- Timeweb project name or project ID.
- Primary region/location, for example `ru-1`.

For this repository, the expected GitHub Actions secret name is `TIMEWEB_TOKEN`.

## If importing the current server

- Server ID from Timeweb Cloud.
- Server name as shown in Timeweb.
- Public IPv4 used for inbound SSH/HTTP/HTTPS.
- SSH username used by CI deploys, probably `root` for the existing compose deployments.
- SSH port.
- Existing disks, backup schedules, networks, firewall rules, and floating/additional IPs.

## If creating new resources

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

The Timeweb token can be used to inventory resources that the token is allowed to see across projects. It cannot recover secrets that the provider/API does not expose, such as root passwords, database passwords, private SSH keys, or application-level secrets.

Because this repository is public, do not print full Timeweb inventory into GitHub Actions logs or public artifacts. Discovery output should either be heavily redacted or produced outside public CI.
