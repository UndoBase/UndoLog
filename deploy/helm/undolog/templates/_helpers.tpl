{{/*
Expand the name of the chart.
*/}}
{{- define "undolog.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "undolog.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "undolog.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "undolog.labels" -}}
helm.sh/chart: {{ include "undolog.chart" . }}
{{ include "undolog.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "undolog.selectorLabels" -}}
app.kubernetes.io/name: {{ include "undolog.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Selector labels for engine
*/}}
{{- define "undolog.engine.selectorLabels" -}}
{{ include "undolog.selectorLabels" . }}
app.kubernetes.io/component: engine
{{- end }}

{{/*
Selector labels for proxy
*/}}
{{- define "undolog.proxy.selectorLabels" -}}
{{ include "undolog.selectorLabels" . }}
app.kubernetes.io/component: proxy
{{- end }}

{{/*
Service account name
*/}}
{{- define "undolog.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "undolog.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
