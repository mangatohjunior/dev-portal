#!/bin/sh
# Writes generated-ci.yml: one multi-project trigger per tenant in TENANT_LIST.
set -eu

case "${PIPELINE_TRIGGER:-}" in
  tenant_baseline|kasm_vdi|tenant_services) ;;
  *)
    echo "PIPELINE_TRIGGER must be tenant_baseline, kasm_vdi, or tenant_services" >&2
    exit 1
    ;;
esac

if [ -z "${TENANT_LIST:-}" ]; then
  echo "TENANT_LIST is empty" >&2
  exit 1
fi

yaml_quote() {
  if printf '%s' "$1" | grep -q '["\\[:cntrl:]]'; then
    echo "refusing to forward a variable that is not plain text" >&2
    exit 1
  fi
  printf '"%s"' "$1"
}

cat > generated-ci.yml <<EOF
stages:
  - downstream

.portal-variables:
  variables:
    TARGET_ENV: $(yaml_quote "${TARGET_ENV:-}")
    TENANT_LIST: $(yaml_quote "${TENANT_LIST:-}")
    PIPELINE_TRIGGER: $(yaml_quote "${PIPELINE_TRIGGER:-}")
    TAINT: $(yaml_quote "${TAINT:-}")
    TRIGGERED_BY: $(yaml_quote "${TRIGGERED_BY:-}")
    CR_NUMBER: $(yaml_quote "${CR_NUMBER:-}")
    REPLACE_RESOURCE: $(yaml_quote "${REPLACE_RESOURCE:-}")
    TF_REFRESH: $(yaml_quote "${TF_REFRESH:-}")
    REPLACE_STATE: $(yaml_quote "${REPLACE_STATE:-}")
    SERVICES_LIST: $(yaml_quote "${SERVICES_LIST:-}")
EOF

found=0
seen=" "
old_ifs=$IFS
IFS=','
for raw in $TENANT_LIST; do
  IFS=$old_ifs
  tenant=$(printf '%s' "$raw" | tr -d '[:space:]')
  if [ -z "$tenant" ]; then
    IFS=','
    continue
  fi
  printf '%s' "$tenant" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_-]*$' || {
    echo "invalid tenant name: $tenant" >&2
    exit 1
  }
  case "$seen" in
    *" $tenant "*)
      IFS=','
      continue
      ;;
  esac
  seen="$seen$tenant "
  found=1
  cat >> generated-ci.yml <<EOF
${tenant}:
  extends: .portal-variables
  stage: downstream
  trigger:
    project: tenants/${tenant}
    branch: main
    strategy: depend
    forward:
      pipeline_variables: true
EOF
  IFS=','
done
IFS=$old_ifs

if [ "$found" -ne 1 ]; then
  echo "TENANT_LIST did not contain a tenant" >&2
  exit 1
fi
