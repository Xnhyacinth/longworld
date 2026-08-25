#!/usr/bin/env bash
# Download related-work datasets that are actually public.
# Usage: bash scripts/download_external.sh
#        bash scripts/download_external.sh DocQA-RL-1.6K
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="${EXTERNAL_DIR:-$ROOT/data/external}"
mkdir -p "$EXT"

if [[ -f "${HF_TOKEN_FILE:-$HOME/.cache/huggingface/token}" ]]; then
  export HUGGING_FACE_HUB_TOKEN="$(tr -d '\n' < "${HF_TOKEN_FILE:-$HOME/.cache/huggingface/token}")"
  export HF_TOKEN="$HUGGING_FACE_HUB_TOKEN"
fi

# id|hf_repo
ALL=(
  "DocQA-RL-1.6K|Tongyi-Zhiwen/DocQA-RL-1.6K"
  "ACC-dataset|groundhogLLM/ACC-dataset"
  "LoongRL-Train-Data|OldKingMeister/LoongRL-Train-Data"
  "LongTraceRL|THU-KEG/LongTraceRL"
  "prolong-ultrachat-64K|princeton-nlp/prolong-ultrachat-64K"
  "LongRLVR-Data|Guanzheng/LongRLVR-Data"
  "LongMIT-128K|donmaclean/LongMIT-128K"
  "LongAlign-10k|zai-org/LongAlign-10k"
)

want="${1:-}"
if ! command -v hf >/dev/null 2>&1; then
  echo "need Hugging Face CLI: pip/uv install huggingface_hub[cli]" >&2
  exit 1
fi

downloaded=()
for spec in "${ALL[@]}"; do
  name="${spec%%|*}"
  repo="${spec#*|}"
  if [[ -n "$want" && "$want" != "$name" && "$want" != "$repo" ]]; then
    continue
  fi
  dest="$EXT/$name"
  echo "download $repo -> $dest"
  hf download "$repo" --repo-type dataset --local-dir "$dest"
  downloaded+=("$name")
done

python3 - "$EXT" "${downloaded[@]}" <<'PY'
import json, sys
from pathlib import Path
ext = Path(sys.argv[1])
names = sys.argv[2:]
manifest_path = ext / "manifest.json"
manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"datasets": {}}
for name in names:
    p = ext / name
    files = sorted(str(x.relative_to(p)) for x in p.rglob("*") if x.is_file() and ".cache" not in x.parts)
    manifest["datasets"][name] = {
        "path": str(p),
        "n_files": len(files),
        "files_head": files[:12],
    }
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest, indent=2))
PY
echo "done. convert with:"
echo "  uv run --extra train python scripts/export_external_llamafactory.py"
echo "  uv run --extra train python scripts/export_swift.py"
echo "note: prolong-ultrachat-64K is Llama-3 MDS packed to 64k; skip that export."
echo "note: LongRLVR-Data parquet is 8k-64k QA (llama/qwen splits); LongMIT-128K is a 28G jsonl; LongAlign-10k is 8k-64k."
