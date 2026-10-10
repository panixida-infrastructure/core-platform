#!/usr/bin/env sh
set -eu

plugin_directory="${SONAR_PLUGIN_DIRECTORY:-/opt/sonarqube/extensions/plugins}"
disabled_directory="${plugin_directory%/plugins}/disabled-plugins"
web_directory="${SONAR_WEB_DIRECTORY:-/web}"
mkdir -p "$plugin_directory" "$disabled_directory"

if [ "$BRANCH_PLUGIN_ENABLED" = true ]; then
  temporary_directory="$(mktemp -d)"
  trap 'rm -rf "$temporary_directory"' EXIT
  wget -q -O "$temporary_directory/plugin.jar" "$BRANCH_PLUGIN_JAR_URL"
  wget -q -O "$temporary_directory/webapp.zip" "$BRANCH_PLUGIN_WEBAPP_URL"
  printf '%s  %s\n' "$BRANCH_PLUGIN_JAR_SHA256" "$temporary_directory/plugin.jar" | sha256sum -c -
  printf '%s  %s\n' "$BRANCH_PLUGIN_WEBAPP_SHA256" "$temporary_directory/webapp.zip" | sha256sum -c -
  unzip -q "$temporary_directory/webapp.zip" -d "$web_directory"
  chmod -R 755 "$web_directory"
  chown -R 1000:0 "$web_directory"
fi

# The extensions PVC survives deployments, including rollbacks to the stock server.
for installed_plugin in "$plugin_directory/sonarqube-community-branch-plugin.jar" \
  "$plugin_directory"/sonarqube-community-branch-plugin-*.jar; do
  [ -f "$installed_plugin" ] || continue
  mv "$installed_plugin" "$disabled_directory/$(basename "$installed_plugin").disabled"
done

if [ "$BRANCH_PLUGIN_ENABLED" = true ]; then
  cp "$temporary_directory/plugin.jar" "$plugin_directory/$BRANCH_PLUGIN_JAR_NAME.tmp"
  chmod 644 "$plugin_directory/$BRANCH_PLUGIN_JAR_NAME.tmp"
  chown 1000:0 "$plugin_directory/$BRANCH_PLUGIN_JAR_NAME.tmp"
  mv "$plugin_directory/$BRANCH_PLUGIN_JAR_NAME.tmp" "$plugin_directory/$BRANCH_PLUGIN_JAR_NAME"
fi
