{{/* Chart name / fullname / labels */}}
{{- define "kubesiesta.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "kubesiesta.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "kubesiesta.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "kubesiesta.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "kubesiesta.labels" -}}
helm.sh/chart: {{ include "kubesiesta.chart" . }}
{{ include "kubesiesta.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "kubesiesta.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kubesiesta.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "kubesiesta.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "kubesiesta.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Component names */}}
{{- define "kubesiesta.engine.fullname" -}}{{ include "kubesiesta.fullname" . }}-engine{{- end -}}
{{- define "kubesiesta.ui.fullname" -}}{{ include "kubesiesta.fullname" . }}-ui{{- end -}}
{{- define "kubesiesta.postgres.fullname" -}}{{ include "kubesiesta.fullname" . }}-postgres{{- end -}}

{{/* Database secret + DSN */}}
{{- define "kubesiesta.dbSecretName" -}}
{{- if .Values.database.existingSecret -}}
{{- .Values.database.existingSecret -}}
{{- else -}}
{{- include "kubesiesta.fullname" . }}-db
{{- end -}}
{{- end -}}

{{- define "kubesiesta.dbSecretKey" -}}
{{- .Values.database.existingSecretKey | default "dsn" -}}
{{- end -}}

{{- define "kubesiesta.dbDsn" -}}
{{- if .Values.postgres.enabled -}}
postgres://{{ .Values.postgres.auth.username }}:{{ .Values.postgres.auth.password }}@{{ include "kubesiesta.postgres.fullname" . }}:5432/{{ .Values.postgres.auth.database }}?sslmode=disable
{{- else -}}
postgres://{{ .Values.database.user }}:{{ .Values.database.password }}@{{ .Values.database.host }}:{{ .Values.database.port }}/{{ .Values.database.name }}?sslmode={{ .Values.database.sslmode }}
{{- end -}}
{{- end -}}

{{/* Reusable env: DB driver + DSN (from the secret) */}}
{{- define "kubesiesta.dbEnv" -}}
- name: KUBESIESTA_DB_DRIVER
  value: postgres
- name: KUBESIESTA_DB_DSN
  valueFrom:
    secretKeyRef:
      name: {{ include "kubesiesta.dbSecretName" . }}
      key: {{ include "kubesiesta.dbSecretKey" . }}
{{- end -}}

{{/* Hardened container security context */}}
{{- define "kubesiesta.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}
