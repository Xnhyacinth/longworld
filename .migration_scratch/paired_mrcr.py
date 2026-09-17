import json, statistics
def load(path):
    d = {}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get("status") == "ok":
                d[r["uid"]] = r["score"]
    return d

R = "/volume/pt-dev/qjiu/longworld/data/hf/LongWorld-Training-State/evaluations/runs"
for task in ["mrcr_2needle","mrcr_4needle"]:
    base = load(f"{R}/mrcr_graphwalks_20260901/b0_qwen35_4b_base/{task}/samples.jsonl")
    accb = load(f"{R}/mrcr_graphwalks_20260912/acc_base_ckpt680/{task}/samples.jsonl")
    ltb  = load(f"{R}/mrcr_graphwalks_20260912/longtrace_base_ckpt680/{task}/samples.jsonl")
    common = set(base)&set(accb)&set(ltb)
    print(f"\n== {task}: paired on {len(common)} common scored uids")
    for name, other in [("acc_base",accb),("longtrace_base",ltb)]:
        diffs = [other[u]-base[u] for u in common]
        mean_d = sum(diffs)/len(diffs)
        sd = statistics.stdev(diffs)
        se = sd/(len(diffs)**0.5)
        wins = sum(1 for d in diffs if d>0); losses = sum(1 for d in diffs if d<0)
        print(f"  {name} - base: mean_delta={mean_d:+.4f}  SE={se:.4f}  t={mean_d/se:+.2f}  wins/losses={wins}/{losses}")
