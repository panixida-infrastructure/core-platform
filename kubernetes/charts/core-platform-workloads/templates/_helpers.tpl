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
