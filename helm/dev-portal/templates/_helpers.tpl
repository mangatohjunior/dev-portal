{{- define "dev-portal.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "dev-portal.fullname" -}}
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

{{- define "dev-portal.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "dev-portal.labels" -}}
helm.sh/chart: {{ include "dev-portal.chart" . }}
app.kubernetes.io/name: {{ include "dev-portal.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: dev-portal
{{- end }}

{{- define "dev-portal.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dev-portal.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "dev-portal.scheme" -}}
{{- if .Values.istio.tls }}https{{- else }}http{{- end -}}
{{- end }}

{{- define "dev-portal.portalOrigin" -}}
{{- if .Values.portal.publicUrl -}}
{{- trimSuffix "/" .Values.portal.publicUrl -}}
{{- else if .Values.istio.enabled -}}
{{- $host := required "istio.host is required when portal.publicUrl is empty" .Values.istio.host -}}
{{- printf "%s://%s" (include "dev-portal.scheme" .) $host -}}
{{- else -}}
{{- fail "Set portal.publicUrl or enable istio and set istio.host" -}}
{{- end -}}
{{- end }}

{{- define "dev-portal.keycloakOrigin" -}}
{{- required "keycloak.externalUrl is required" .Values.keycloak.externalUrl | trimSuffix "/" -}}
{{- end }}

{{- define "dev-portal.keycloakInternal" -}}
{{- required "keycloak.internalUrl is required" .Values.keycloak.internalUrl | trimSuffix "/" -}}
{{- end }}
