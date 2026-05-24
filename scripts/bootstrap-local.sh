#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ansible-playbook \
  -i ansible/inventories/production/hosts.yml \
  ansible/playbooks/bootstrap.yml

ansible-playbook \
  -i ansible/inventories/production/hosts.yml \
  ansible/playbooks/deploy-compose.yml
