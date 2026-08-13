{{{{- define "zivo.name" -}}}}
{{{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}}}
{{{{- end -}}}}

{{{{- define "zivo.fullname" -}}}}
{{{{- printf "%s" (include "zivo.name" .) -}}}}
{{{{- end -}}}}
