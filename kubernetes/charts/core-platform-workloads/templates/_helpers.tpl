{{- define "core-platform-workloads.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
app.kubernetes.io/part-of: core-platform
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}

{{- define "core-platform-workloads.selectorLabels" -}}
app.kubernetes.io/name: {{ .name | quote }}
app.kubernetes.io/part-of: core-platform
{{- end -}}

{{- define "core-platform-workloads.preferAwayFromSonarqube" -}}
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          topologyKey: kubernetes.io/hostname
          namespaces:
            - {{ .Values.sonarqube.namespace | quote }}
          labelSelector:
            matchLabels:
              app.kubernetes.io/name: sonarqube
              app.kubernetes.io/part-of: core-platform
{{- end -}}

{{/* Keep rule-to-panel mappings beside the rules; encode metric labels as URL values. */}}
{{- define "core-platform-workloads.metricDashboardUrl" -}}
https://{{ .root.Values.grafana.host }}/d/{{ .dashboard }}/{{ .dashboard }}?orgId=1&from={{ "{{ ($activeAt.Add (parseDurationTime \"-1h\")).UnixMilli }}" }}&to=now&timezone=browser&refresh=30s
{{- with .panel }}&viewPanel=panel-{{ . }}{{ end -}}
{{- range $variable, $label := .variables -}}
{{ printf "{{ with $labels.%s }}" $label }}&var-{{ $variable }}={{ "{{ . | queryEscape }}{{ end }}" }}
{{- end -}}
{{- end -}}
