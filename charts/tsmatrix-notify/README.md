# tsmatrix-notify Helm chart

## Install

```bash
helm upgrade --install tsmatrix-notify ./charts/tsmatrix-notify \
  --set config.matrixHomeserver=https://matrix.example.org \
  --set config.matrixUserId='@bot:example.org' \
  --set config.matrixRoomId='!room:example.org' \
  --set existingSecret.enabled=true \
  --set existingSecret.name=tsmatrix-notify-secrets
```

## Secrets

Use one of:

1. `existingSecret.enabled=true` + `existingSecret.name` to reference an already-managed secret.
2. `secret.create=true` to have Helm create one (still supplied at deploy-time, never baked in image).

Expected secret keys:
- `MATRIX_ACCESS_TOKEN`
- `TS3_USER`
- `TS3_PASSWORD`

## Persistence

Chart defaults to stateless (`emptyDir`) and keeps working without PVs.
Enable `persistence.enabled=true` to keep Matrix session and stats across pod restarts.
