import json, sys, os
sys.stdout.reconfigure(encoding="utf-8")
p = r"C:\Users\14294\AppData\Local\Temp\claude\D--BaiduSyncdisk-2024-to-future-koi-chen\a29678a4-d49a-4dad-b000-a427a0c722cf\tasks\wibciqyqh.output"
d = json.load(open(p, encoding="utf-8")); r = d.get("result", d)
fs = r["findings"]
print("candidates", r.get("n_candidates"), "kept", r.get("n_kept"), "\n")
order = {"bug-critical": 0, "bug": 1, "faithfulness": 2, "cleanup": 3}
fs.sort(key=lambda x: order.get(x.get("severity", ""), 9))
for i, f in enumerate(fs, 1):
    fn = os.path.basename(f.get("file", "") or "")
    print(f"#{i} [{f.get('severity','?')}|{f.get('verdict','?')}] {fn}:{f.get('line','')}")
    print("   SUM:", (f.get("summary", "") or "")[:280])
