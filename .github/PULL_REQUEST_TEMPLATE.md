## Summary

What changed, in 2-5 bullets?

## Verification

- [ ] `cargo fmt --all --check`
- [ ] `cargo clippy --all-targets -- -D warnings`
- [ ] `cargo test`
- [ ] `python -m pytest -q` (if Python behavior changed)
- [ ] `ruff check` (if Python files changed)

## Scope notes

- Does this touch `record`, `replay`, `fork`, `verify`, or the debugger CLI?
- Does it change the `.rewind` format, signing, Merkle/chain logic, or causal-id semantics?
- If behavior is partial or intentionally deferred, where is that called out in docs/tests?

## Risk

What is most likely to break, and how did you check it?
