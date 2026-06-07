---
name: Bug report
about: Report a reproducible bug in rewind-core, rewind-cli, or the Python SDK
title: "[bug] "
labels: ["bug"]
assignees: []
---

## Summary

What broke?

## Scope

- Component: `rewind-core` / `rewind-cli` / `rewind-py` / `python/rewind`
- Version / commit:
- OS / toolchain:

## Reproduction

Provide the smallest reproduction that still fails.

```bash
# commands here
```

## Expected behavior

What should have happened?

## Actual behavior

What happened instead? Include exact error text, verifier output, or a short log excerpt.

## Notes

- Does this affect `record`, `replay`, `fork`, or `verify`?
- If the issue is about determinism, say whether the run is closed API or self-hosted OSS.
- If relevant, attach a minimal sanitized snippet or artifact metadata. Do not attach secrets or raw
  `.rewind` captures that contain sensitive data.
