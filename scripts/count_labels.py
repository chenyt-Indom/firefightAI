"""统计标注数据"""
from pathlib import Path

labels_dir = Path("datasets/firefight_mod/labels")
files = list(labels_dir.glob("*.txt"))
total = 0
class_count = {}
class_names = {"0": "tank", "1": "ifv", "2": "infantry", "3": "sniper", "4": "helicopter", "5": "building"}

for f in files:
    for line in f.read_text().strip().split("\n"):
        if line:
            cls = line.split()[0]
            class_count[cls] = class_count.get(cls, 0) + 1
            total += 1

print(f"标注文件: {len(files)} 个")
print(f"总标注框: {total} 个")
for k in sorted(class_count.keys()):
    print(f"  类别{k} ({class_names.get(k, '?')}): {class_count[k]} 个")