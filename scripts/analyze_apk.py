"""分析Firefight APK包内容，提取现代MOD单位信息"""
import zipfile
import os
import json
from pathlib import Path

APK_PATH = r"C:\Users\19853\Documents\xwechat_files\wxid_zjc4zsi32xqc22_042d\msg\file\2026-07\base.apk.1"
OUTPUT_DIR = Path("apk_analysis")
OUTPUT_DIR.mkdir(exist_ok=True)

# 文件扩展名过滤
INTERESTING_EXT = {".json", ".xml", ".txt", ".lua", ".dat", ".ini", ".cfg", ".yaml", ".csv", ".bytes", ".asset"}
SKIP_PREFIX = {"lib/", "meta-inf/", "res/values", "res/layout", "smali",
               "android/", "androidx/", "kotlin/", "okhttp", "okio", "retrofit"}

# 关键词过滤 - 现代MOD相关
MOD_KEYWORDS = ["mod", "unit", "weapon", "tank", "ifv", "infantry", "sniper",
                "helicopter", "building", "vehicle", "soldier", "m1a", "t-90",
                "leopard", "bmp", "btr", "bradley", "ah-64", "mi-24", "apache",
                "abrams", "m2", "m3", "modern", "armor", "military", "army",
                "battle", "war", "combat", "firefight", "config", "data", "stat",
                "balance", "damage", "health", "speed", "range", "cost",
                "inf", "veh", "air", "helo", "unit_type", "unit_id", "class",
                "rifle", "mg", "missile", "cannon", "gun", "rocket", "atgm"]

# 分级关键词
HIGH_PRIORITY = ["unit", "weapon", "stats", "balance", "damage", "config", "mod",
                 "tank", "ifv", "infantry", "sniper", "helicopter", "building",
                 "armor", "vehicle", "m1a", "t-90", "leopard", "bmp", "btr",
                 "bradley", "ah-64", "mi-24", "apache", "abrams", "atgm"]


def should_extract(name: str, size: int) -> bool:
    """判断是否需要提取该文件"""
    lower = name.lower()
    # 跳过无关目录
    if any(lower.startswith(p) for p in SKIP_PREFIX):
        return False
    # 跳过过大文件
    if size > 5_000_000:  # 5MB
        return False
    # 检查扩展名
    if not any(lower.endswith(ext) for ext in INTERESTING_EXT):
        return False
    # 检查关键词
    if not any(kw.lower() in lower for kw in MOD_KEYWORDS):
        return False
    return True


def extract_and_analyze():
    print("=" * 70)
    print("Firefight APK 现代MOD内容分析")
    print("=" * 70)

    matched_files = []
    all_files = []

    with zipfile.ZipFile(APK_PATH, "r") as z:
        for name in z.namelist():
            info = z.getinfo(name)
            all_files.append((name, info.file_size))
            if should_extract(name, info.file_size):
                matched_files.append((name, info.file_size))

    # 按优先级排序
    def priority(name: str) -> int:
        lower = name.lower()
        for kw in HIGH_PRIORITY:
            if kw in lower:
                return 0
        return 1

    matched_files.sort(key=lambda x: (priority(x[0]), x[0]))

    print(f"\n总文件数: {len(all_files)}")
    print(f"匹配文件数: {len(matched_files)}")
    print(f"\n{'='*70}")
    print("匹配文件列表:")
    print(f"{'='*70}")

    extracted_count = 0
    with zipfile.ZipFile(APK_PATH, "r") as z:
        for name, size in matched_files:
            tag = "[HIGH]" if priority(name) == 0 else "[LOW] "
            print(f"  {tag} {size:>8d}  {name}")

            # 提取文件
            try:
                data = z.read(name)
                # 尝试解码
                text = None
                for encoding in ["utf-8", "utf-16", "latin-1", "gbk"]:
                    try:
                        text = data.decode(encoding)
                        if text.isprintable() or len(text) > 100:
                            break
                    except Exception:
                        continue

                if text and len(text) > 10:
                    out_path = OUTPUT_DIR / name.replace("/", "_")
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(text, encoding="utf-8", errors="replace")
                    extracted_count += 1
            except Exception as e:
                print(f"    [提取失败: {e}]")

    print(f"\n已提取 {extracted_count} 个文件到 {OUTPUT_DIR.absolute()}/")

    # 分析提取的文件内容，查找单位定义
    print(f"\n{'='*70}")
    print("搜索单位/武器定义关键词...")
    print(f"{'='*70}")

    search_keywords = [
        "unit_type", "unit_id", "unit_name", "weapon_type", "weapon_id",
        "tank", "ifv", "infantry", "sniper", "helicopter",
        "M1A", "T-90", "Leopard", "BMP", "BTR", "Bradley", "AH-64", "Mi-24",
        "Abrams", "rifle", "machinegun", "cannon", "missile", "rocket",
        "damage", "health", "speed", "range", "cost", "armor",
        "firefight", "modern", "balance"
    ]

    for txt_file in sorted(OUTPUT_DIR.glob("*.txt")):
        content = txt_file.read_text(encoding="utf-8", errors="replace")
        hits = []
        for kw in search_keywords:
            if kw.lower() in content.lower():
                hits.append(kw)

        if len(hits) >= 3:
            print(f"\n  文件: {txt_file.name}")
            print(f"  命中关键词: {', '.join(hits[:10])}")
            # 显示包含关键词的行的片段
            lines = content.split("\n")
            for i, line in enumerate(lines):
                for kw in hits[:5]:
                    if kw.lower() in line.lower() and len(line.strip()) > 5:
                        snippet = line.strip()[:150]
                        print(f"    L{i}: {snippet}")
                        break


if __name__ == "__main__":
    extract_and_analyze()