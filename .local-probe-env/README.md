# Local probe environment snapshot (worlds branch + private HF)

This directory is a private machine-migration bundle for LongWorld synthesis
and extension. It is **not** a production KMS root and must **not** be merged
onto `main`.

Anyone with this bundle can validate already-signed P57/P64 manifests and
replay the same probe-role HMAC. Treat it as signing authority: keep the
GitHub `worlds` branch and the Hugging Face dataset private.

Private Hub copy: `Xnhyacinth/LongWorld-Probe-Trust`.

After clone on a new host, restore next to `$HOME` (outside the git checkout):

```bash
rsync -a .local-probe-env/.longworld* "$HOME/"
rsync -a .local-probe-env/.longworld_probe_keys "$HOME/"
chmod -R go-rwx "$HOME"/.longworld "$HOME"/.longworld-* "$HOME"/.longworld_probe_keys
```

Catalogs pin absolute paths under `/workspace/wynckeliao/.longworld-*`. On a
new machine either recreate that prefix or point `trust_file` at the restored
`$HOME` copies. File bytes and `key_id` values must match
`PROBE_TRUST_MANIFEST.json`.

Probe identities must not be mixed:

- P15 / P16 BEA: `p12-probe-12-v2`
- older Finance / P14 six-domain: `p12-probe-12-20260829-v1`
- P52 GovInfo / P54 EUR-Lex / P55 Alphabet / P46 Ofgem: `scaleout-20260906`
- P57 IETF unique products (ACME/DNSSEC/HTTP2/SSH/PKIX/TLS): `scaleout-20260906/ietf-*`
- P58–P64 Finance/CodeForge taskbank: `scaleout-20260906/{nvidia,micron,amazon,meta,amd,intel-p64,siriusxm-p64,p64-training}`
- P59 clinical domain: `.longworld-p59-domain-private/clinical`

`data/` (generated JSONL, source caches, promoted releases) is **not** in Git.
Copy it from private Hugging Face `Xnhyacinth/LongWorld-Synthesis-Workspace`,
`Xnhyacinth/LongWorld-Real-Workflows`, `Xnhyacinth/LongWorld-Worlds-State`,
and `Xnhyacinth/LongWorld-Training-State`. The `worlds` worktree `data` path is a
symlink to the primary `longworld/data` tree.
