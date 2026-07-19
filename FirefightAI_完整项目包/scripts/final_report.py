"""Firefight APK 现代MOD - 最终综合分析报告"""
import zipfile
import re
import json
from pathlib import Path
from collections import defaultdict, Counter

APK = r"C:\Users\19853\Documents\xwechat_files\wxid_zjc4zsi32xqc22_042d\msg\file\2026-07\base.apk.1"
OUT = Path("apk_analysis")
OUT.mkdir(exist_ok=True)

def parse_xml_text(raw: str) -> dict:
    """解析XML - 提取type, nationality, long_name, short_name, comment, quality等"""
    d = {}
    # 直接搜索关键标签
    for tag in ["type", "nationality", "long_name", "short_name", "comment", "quality"]:
        m = re.search(rf'<{tag}>(.*?)</{tag}>', raw, re.DOTALL)
        if m:
            d[tag] = m.group(1).strip()
    return d

def main():
    print("=" * 80)
    print("Firefight 现代MOD 完整分析报告")
    print("=" * 80)

    # 游戏内类型映射到6大类别
    TYPE_MAP = {
        "TYPE_TANK": "tank",
        "TYPE_ASSAULT_GUN": "tank",
        "TYPE_INFANTRY_FIGHTING_VEHICLE": "ifv",
        "TYPE_ARMOURED_CAR": "ifv",
        "TYPE_INFANTRY_SECTION": "infantry",
        "TYPE_HMG": "infantry",
        "TYPE_MORTAR": "infantry",
        "TYPE_BAZOOKA": "infantry",
        "TYPE_ATGUN": "infantry",
    }

    units = []  # list of dicts
    weapons = {}
    nations = set()
    equip_data = {}  # nation -> {category -> [ids]}

    with zipfile.ZipFile(APK, "r") as z:
        mod_files = [n for n in z.namelist() if n.startswith("assets/Mod/Data/")]

        # 解析装备列表
        for n in mod_files:
            if "Equipment-" in n:
                nat = n.split("Equipment-")[1].replace(".txt", "")
                try:
                    raw = z.read(n).decode("utf-8", errors="replace")
                    equip = defaultdict(list)
                    cat = "other"
                    for line in raw.split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("//"):
                            comment = line[2:].strip().lower()
                            if "infantry" in comment:
                                cat = "infantry"
                            elif "at gun" in comment or "atgm" in comment:
                                cat = "at_guns"
                            elif "vehicle" in comment or "tank" in comment or "half" in comment:
                                cat = "vehicles"
                            elif "helicopter" in comment or "air" in comment:
                                cat = "helicopter"
                            elif "artillery" in comment or "howitzer" in comment or "mortar" in comment:
                                cat = "artillery"
                            elif "aa" in comment or "anti-air" in comment or "spaa" in comment:
                                cat = "aa"
                            elif "recon" in comment or "scout" in comment:
                                cat = "recon"
                            continue
                        uid = line.split("//")[0].strip()
                        if uid:
                            equip[cat].append(uid)
                    equip_data[nat] = dict(equip)
                    nations.add(nat)
                except Exception:
                    pass

        # 解析所有单位
        for n in mod_files:
            if not any(n.startswith(p) for p in
                       ["assets/Mod/Data/Infantry/",
                        "assets/Mod/Data/Vehicles/",
                        "assets/Mod/Data/AT Guns/"]):
                continue
            try:
                raw = z.read(n).decode("utf-8", errors="replace")
                fields = parse_xml_text(raw)
                uid = Path(n).stem

                utype = fields.get("type", "")
                nat_field = fields.get("nationality", "")
                long_name = fields.get("long_name", uid)
                short_name = fields.get("short_name", "")
                comment = fields.get("comment", "")
                quality = fields.get("quality", "")

                # 提取武器
                wrefs = set(re.findall(r'WEAPON_\w+', raw))

                # 6类别映射
                category = TYPE_MAP.get(utype, "unknown")

                # 特殊处理
                if utype == "TYPE_ATGUN":
                    category = "infantry"  # AT guns are crew-served infantry

                # 提取cost
                cost = ""
                cm = re.search(r'<cost[^>]*>(.*?)</cost>', raw, re.IGNORECASE)
                if cm:
                    cost = cm.group(1).strip()

                # 提取availability年月
                dates = re.findall(r'<data><month>(\d+)</month><year>(\d+)</year><number>(\d+)</number></data>', raw)

                units.append({
                    "id": uid,
                    "type": utype,
                    "category": category,
                    "long_name": long_name,
                    "short_name": short_name,
                    "comment": comment,
                    "quality": quality,
                    "nationality": nat_field,
                    "cost": cost,
                    "weapons": sorted(wrefs),
                    "dates": [{"month": int(m), "year": int(y), "number": int(n)} for m, y, n in dates],
                    "file": n,
                })
            except Exception:
                pass

        # 解析武器
        for n in mod_files:
            if "Weapons" not in n:
                continue
            try:
                raw = z.read(n).decode("utf-8", errors="replace")
                wname = Path(n).stem
                fields = {}
                for attr in ["calibre", "range", "damage", "penetration", "fire_rate", "ammo_type", "type"]:
                    m = re.search(rf'<{attr}[^>]*>(.*?)</{attr}>', raw, re.IGNORECASE)
                    if m:
                        fields[attr] = m.group(1).strip()
                fm = re.search(r'<flavour[^>]*>(.*?)</flavour>', raw, re.IGNORECASE)
                if fm:
                    fields["flavour"] = fm.group(1).strip()
                weapons[wname] = fields
            except Exception:
                pass

    # ====== 统计 ======
    by_cat = defaultdict(list)
    for u in units:
        by_cat[u["category"]].append(u)

    by_nation = defaultdict(lambda: defaultdict(list))
    for nat, cats in equip_data.items():
        for cat, uid_list in cats.items():
            for uid in uid_list:
                by_nation[nat][cat].append(uid)

    # 武器分类
    weapon_types = Counter()
    for wname, winfo in weapons.items():
        wt = winfo.get("type", "unknown")
        if "SNIPER" in wname:
            weapon_types["sniper_rifle"] += 1
        elif "RPG" in wname or "LAUNCHER" in wname:
            weapon_types["rocket_launcher"] += 1
        elif "CANNON" in wname:
            weapon_types["cannon"] += 1
        elif "HMG" in wname:
            weapon_types["hmg"] += 1
        elif "LMG" in wname:
            weapon_types["lmg"] += 1
        elif "RIFLE" in wname:
            weapon_types["rifle"] += 1
        elif "SMG" in wname:
            weapon_types["smg"] += 1
        elif "PISTOL" in wname:
            weapon_types["pistol"] += 1
        elif "GRENADE" in wname:
            weapon_types["grenade"] += 1
        elif "MORTAR" in wname:
            weapon_types["mortar"] += 1
        elif "ATGM" in wname:
            weapon_types["atgm"] += 1
        elif "SHOTGUN" in wname:
            weapon_types["shotgun"] += 1
        elif "FLAMETHROWER" in wname:
            weapon_types["flamethrower"] += 1
        elif "CARBINE" in wname:
            weapon_types["carbine"] += 1
        elif "MLRS" in wname:
            weapon_types["mlrs"] += 1
        else:
            weapon_types["other"] += 1

    # ====== 输出报告 ======
    print(f"\n  [国家阵营] {len(nations)} 个")
    for nat in sorted(nations):
        cats = equip_data.get(nat, {})
        total = sum(len(v) for v in cats.values())
        cat_str = ", ".join(f"{k}({len(v)})" for k, v in sorted(cats.items()))
        print(f"    {nat}: {total}个单位 | {cat_str}")

    print(f"\n  [单位分类]")
    cat_names = {
        "tank": "主战坦克/突击炮",
        "ifv": "步兵战车/装甲车",
        "infantry": "步兵班组/重武器",
    }
    total_units = 0
    for cat in ["tank", "ifv", "infantry"]:
        count = len(by_cat[cat])
        total_units += count
        print(f"    {cat_names[cat]}: {count} 个")

    # 游戏内类型细分
    print(f"\n  [游戏内类型细分]")
    type_count = Counter(u["type"] for u in units)
    for t, c in type_count.most_common():
        print(f"    {t}: {c} 个")

    print(f"\n  [武器分类] ({len(weapons)} 个)")
    for wt, c in weapon_types.most_common():
        print(f"    {wt}: {c} 个")

    # 典型单位示例
    print(f"\n{'='*80}")
    print("典型单位示例 (每类各5个):")
    print(f"{'='*80}")

    for cat in ["tank", "ifv", "infantry"]:
        items = by_cat[cat]
        print(f"\n  [{cat_names[cat]}]")
        for u in items[:5]:
            print(f"    {u['id']}: {u['long_name']}")
            if u['comment']:
                print(f"      描述: {u['comment']}")
            if u['weapons']:
                print(f"      武器: {', '.join(u['weapons'][:6])}")

    # 协同关系总结
    print(f"\n{'='*80}")
    print("协同关系分析:")
    print(f"{'='*80}")
    print("""
  现代MOD单位协同体系:

  1. 坦克-步兵协同:
     - 主战坦克(M1A2/T-90/ZTZ-99等)提供正面火力压制和装甲突破
     - 步兵班组提供近距离掩护、反坦克火力、占领阵地
     - 典型组合: 1辆坦克 + 2个步兵班

  2. IFV-步兵协同:
     - BMP/BTR/Bradley等IFV提供机动火力和装甲运输
     - 步兵班组搭乘IFV快速机动，下车作战
     - 典型组合: 1辆IFV + 1个步兵班

  3. 坦克-IFV混编:
     - 坦克吸引敌方火力，IFV侧翼突袭
     - IFV的ATGM提供远程反坦克支援
     - 典型组合: 2辆坦克 + 1辆IFV(ATGM)

  4. 重武器班组(HMG/迫击炮/ATGM):
     - HMG班组压制步兵
     - 迫击炮班组提供间接火力支援
     - ATGM班组提供远程反坦克火力
     - 需要其他班组保护

  5. 侦察-突击协同:
     - 侦察班组(RS)前出侦察
     - 特种部队(SSO/VDV等)执行突击任务
     - 常规步兵提供火力支援
  """)

    # 保存
    result = {
        "nations": sorted(nations),
        "equipment": equip_data,
        "units": units,
        "weapons": weapons,
        "categories": {k: [u["id"] for u in v] for k, v in by_cat.items()},
        "summary": {
            "total_units": len(units),
            "total_weapons": len(weapons),
            "total_nations": len(nations),
            "tanks": len(by_cat["tank"]),
            "ifvs": len(by_cat["ifv"]),
            "infantry": len(by_cat["infantry"]),
        }
    }
    p = OUT / "final_report.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n完整报告已保存: {p.absolute()}")


if __name__ == "__main__":
    main()