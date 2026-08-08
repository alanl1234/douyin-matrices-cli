# Structured Output Schema

`dy-cli` uses a shared agent-friendly envelope for machine-readable output.

## Success

```yaml
ok: true
schema_version: "1"
data: ...
```

## Error

```yaml
ok: false
schema_version: "1"
error:
  code: not_authenticated
  message: need login
```

## Notes

- `--yaml` and `--json` both use this envelope
- Non-TTY stdout defaults to YAML
- Listing commands return results under `data.items`
- `status` returns `data.authenticated`
- Common `error.code` values: `not_authenticated`, `verify_check`, `api_error`, `empty_response`, `network_error`
