# Local probe environment snapshot (worlds branch only)

This directory is a private machine-migration bundle for LongWorld synthesis
and extension. It is **not** a production KMS root and must **not** be merged
onto `main`.

After clone on a new host, restore next to `$HOME` (outside the git checkout):

```bash
rsync -a .local-probe-env/.longworld* "$HOME/"
rsync -a .local-probe-env/.longworld_probe_keys "$HOME/"
chmod -R go-rwx "$HOME"/.longworld "$HOME"/.longworld-* "$HOME"/.longworld_probe_keys
```

Probe identities must not be mixed:

- P15 / P16 BEA: `p12-probe-12-v2`
- older Finance / P14 six-domain: `p12-probe-12-20260829-v1`
- P52 GovInfo / P54 EUR-Lex / P55 Alphabet / P46 Ofgem: `scaleout-20260906`
- P57 IETF unique products (ACME/DNSSEC/HTTP2/SSH/PKIX/TLS): `scaleout-20260906/ietf-*`
- P58–P64 Finance/CodeForge taskbank: `scaleout-20260906/{nvidia,micron,amazon,meta,amd,intel-p64,siriusxm-p64,p64-training}`

`data/` (generated JSONL, source caches, promoted releases) and `.hf/`
(tokenizer/model cache) are **not** in Git. Copy those with rsync or from
private Hugging Face `Xnhyacinth/LongWorld-Synthesis-Workspace` and
`Xnhyacinth/LongWorld-Real-Workflows`. The `worlds` worktree `data` path is a
symlink to the primary `longworld/data` tree. HMAC keys stay off Hub.
