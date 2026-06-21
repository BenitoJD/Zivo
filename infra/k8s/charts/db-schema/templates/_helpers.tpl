{{- define "db-schema.name" -}}
{{- default .Chart.Name .Values.jobName | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "db-schema.labels" -}}
app.kubernetes.io/name: {{ include "db-schema.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: db-schema
{{- end -}}
