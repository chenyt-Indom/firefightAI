"""深度解析Firefight APK现代MOD - 完整单位/武器/协同分析"""
import zipfile
import re
import json
from pathlib import Path
from collections import defaultdict

APK_PATH = r"C:\Users\19853\Documents\xwechat_files\wxid_zjc4zsi32xqc22_042d\msg\file\2026-07\base.apk.1"
OUTPUT = Path("apk_analysis")
OUTPUT.mkdir(exist_ok=True)

def parse_xml_fields(xml_text: str) -> dict:
    """解析XML文件，提取所有顶级字段和嵌套标签"""
    fields = {}
    # 匹配所有顶级标签
    for match in re.finditer(r'<(\w+)(?:\s+[^>]*)?>(.*?)</\1>', xml_text, re.DOTALL):
        tag = match.group(1).lower()
        content = match.group(2).strip()
        if '<' in content:
            # 嵌套: 提取子标签
            sub = {}
            for sm in re.finditer(r'<(\w+)(?:\s+[^>]*)?>(.*?)</\1>', content, re.DOTALL):
                st = sm.group(1).lower()
                sv = sm.group(2).strip()
                if '<' in sv:
                    sv = re.sub(r'<[^>]+>', '', sv).strip()
                if sv:
                    if st in sub:
                        if isinstance(sub[st], list):
                            sub[st].append(sv)
                        else:
                            sub[st] = [sub[st], sv]
                    else:
                        sub[st] = sv
            if sub:
                fields[tag] = sub
            else:
                fields[tag] = re.sub(r'<[^>]+>', '', content).strip()
        else:
            fields[tag] = content
    return fields


def parse_equipment(equip_text: str) -> tuple[dict, str]:
    """解析装备列表，返回 {类别: [单位名]} 和国家名"""
    result = defaultdict(list)
    current_category = "other"
    nation = ""

    for line in equip_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("//") and "//" in line:
            comment = line.split("//")[1].strip().lower()
            if "infantry" in comment:
                current_category = "infantry"
            elif "at gun" in comment or "atgm" in comment:
                current_category = "at_guns"
            elif "vehicle" in comment or "tank" in comment or "half" in comment:
                current_category = "vehicles"
            elif "helicopter" in comment or "air" in comment:
                current_category = "helicopter"
            elif "artillery" in comment or "howitzer" in comment or "mortar" in comment:
                current_category = "artillery"
            elif "aa" in comment or "anti-air" in comment or "spaa" in comment:
                current_category = "aa"
            elif "recon" in comment or "scout" in comment:
                current_category = "recon"
            elif "sniper" in comment:
                current_category = "sniper"
            elif "atgm" in comment or "ifv" in comment:
                current_category = "ifv"
            continue

        if line.startswith("//"):
            continue

        # 国家名: 格式如 "USA-" 或 "Russian-" 等
        unit_name = line.split("//")[0].strip()
        if unit_name:
            result[current_category].append(unit_name)

    return result, nation


def classify_modern_unit(name: str, short_name: str, long_name: str, unit_type: str, equip_category: str) -> str:
    """分类现代单位到6大类别: tank/ifv/infantry/sniper/helicopter/building"""
    text = f"{name} {short_name} {long_name} {unit_type} {equip_category}".upper()

    # Helicopter
    if any(kw in text for kw in ["HELICOPTER", "AH-64", "APACHE", "MI-24", "MI-28", "MI-35",
                                    "KA-50", "KA-52", "TIGER", "A129", "MANGUSTA", "Z-10", "Z-19",
                                    "UH-1", "UH-60", "BLACK_HAWK", "MI-8", "MI-17", "CH-47",
                                    "CHINOOK", "NH90", "MH-6", "LITTLE_BIRD", "AH-1", "COBRA",
                                    "OH-58", "KIOWA", "HIND", "HAVOC", "HOKUM", "HELO"]):
        return "helicopter"

    # Building / Emplacement
    if any(kw in text for kw in ["BUNKER", "FOB", "HQ", "COMMAND_POST", "WATCHTOWER",
                                    "SANDBAG", "HESCO", "AA_EMPLACEMENT", "ATGM_POSITION",
                                    "MORTAR_PIT", "ARTILLERY_POSITION", "RADAR", "SAM_SITE",
                                    "BARRACKS", "EMPLACEMENT", "TOWER", "PILLBOX"]):
        return "building"

    # Sniper
    if any(kw in text for kw in ["SNIPER", "MARKSMAN", "SNIPER_TEAM", "COUNTER_SNIPER",
                                    "RECON_SNIPER", "DESIGNATED_MARKSMAN"]):
        return "sniper"

    # IFV
    if any(kw in text for kw in ["IFV", "INFANTRY_FIGHTING", "BMP", "BTR", "BRADLEY",
                                    "M2A", "M3A", "WARRIOR", "STRYKER", "LAV", "PANDUR",
                                    "PIRANHA", "PATRIA", "BOXER", "VBCI", "CV90", "PUMA",
                                    "MARDER", "LYNX", "ASCOD", "ZBD", "ZBL", "AMX_10P",
                                    "VAB", "FUCHS", "BMD", "BMPT", "TIGR", "GAZ",
                                    "VTT323", "HAMVE", "HUMVE", "PIKAP", "M113", "M2A1",
                                    "M2A2", "M2A3", "M2A4", "M2A", "M3A", "MTLB",
                                    "APC", "ARMOURED_PERSONNEL", "HALFTRACK"]):
        return "ifv"

    # Tank
    if any(kw in text for kw in ["TANK", "TYPE_TANK", "M1A1", "M1A2", "ABRAMS", "T-90",
                                    "T-80", "T-72", "T-64", "T-62", "T-55", "T-54", "LEOPARD",
                                    "LECLERC", "CHALLENGER", "MERKAVA", "ZTZ", "TYPE_99",
                                    "TYPE_96", "TYPE_98", "K2", "ARIETE", "ALTAY", "T_14",
                                    "ARMATA", "K1", "STRV", "M60", "M60A", "MAGACH", "M10",
                                    "M1", "HSTVL", "CATB", "M551", "M8", "M48", "M47",
                                    "TYPE_10", "TYPE_90", "TYPE_74", "TYPE_61", "PTZ",
                                    "PTL", "ZTL", "VT4", "MBT", "OBJ", "T90", "T72",
                                    "BARYS", "T55", "FATH", "T54", "T62"]):
        return "tank"

    # Infantry
    if any(kw in text for kw in ["TYPE_INFANTRY", "TYPE_SQUAD", "INFANTRY", "SOLDIER",
                                    "RIFLEMAN", "GRENADIER", "MACHINE_GUNNER", "ANTI_TANK",
                                    "ENGINEER", "MEDIC", "SCOUT", "ASSAULT", "RECON",
                                    "PARATROOPER", "MARINE", "SPECIAL_FORCES", "MILITIA",
                                    "IRREGULAR", "MECHANIZED_INF", "RIFLE", "SQUAD",
                                    "MG", "HMG", "LMG", "MMG", "MORTAR", "BAZOOKA",
                                    "FLAMETHROWER", "PIAT", "PANZER", "RPG", "AT_RIFLE"]):
        return "infantry"

    return "unknown"


def main():
    print("=" * 80)
    print("Firefight APK 现代MOD - 完整单位分析")
    print("=" * 80)

    all_units = {}  # file_name -> unit info
    all_weapons = {}
    equipment_data = {}  # nation -> {category -> [unit_names]}
    nations = set()

    with zipfile.ZipFile(APK_PATH, "r") as z:
        mod_files = [n for n in z.namelist() if n.startswith("assets/Mod/Data/")]

        # 1. 解析装备列表
        print("\n[1/5] 解析装备列表...")
        for n in mod_files:
            if "Equipment-" in n:
                nation = n.split("Equipment-")[1].replace(".txt", "")
                try:
                    content = z.read(n).decode("utf-8", errors="replace")
                    equip, _ = parse_equipment(content)
                    equipment_data[nation] = dict(equip)
                    nations.add(nation)
                except Exception:
                    pass

        print(f"  国家阵营: {len(nations)} 个")
        for nat in sorted(nations):
            cats = list(equipment_data.get(nat, {}).keys())
            total = sum(len(v) for v in equipment_data.get(nat, {}).values())
            print(f"    {nat}: {total} 个单位, 类别: {cats}")

        # 2. 解析所有现代单位 (Infantry + Vehicles + AT Guns)
        print("\n[2/5] 解析单位文件...")
        unit_files = [n for n in mod_files
                      if any(n.startswith(p) for p in
                             ["assets/Mod/Data/Infantry/",
                              "assets/Mod/Data/Vehicles/",
                              "assets/Mod/Data/AT Guns/"])]

        infantry_count = len([n for n in unit_files if "Infantry" in n])
        vehicle_count = len([n for n in unit_files if "Vehicles" in n])
        atgun_count = len([n for n in unit_files if "AT Guns" in n])
        print(f"  步兵: {infantry_count}  车辆: {vehicle_count}  AT Guns: {atgun_count}")

        for uf in unit_files:
            try:
                raw = z.read(uf).decode("utf-8", errors="replace")
                fields = parse_xml_fields(raw)
                fields["_file"] = uf
                unit_name = Path(uf).stem

                # 提取关键信息
                desc = fields.get("description", {})
                if isinstance(desc, dict):
                    fields["_type"] = desc.get("type", "")
                    fields["_nationality"] = desc.get("nationality", "")
                    fields["_long_name"] = desc.get("long_name", "")
                    fields["_short_name"] = desc.get("short_name", "")
                    fields["_comment"] = desc.get("comment", "")
                    fields["_quality"] = desc.get("quality", "")

                # 提取武器
                weapons = []
                for key in fields:
                    if isinstance(fields[key], dict):
                        if "type" in fields[key]:
                            wtype = fields[key]["type"]
                            if wtype.startswith("WEAPON_"):
                                weapons.append(wtype)
                    elif isinstance(fields[key], str):
                        if fields[key].startswith("WEAPON_"):
                            weapons.append(fields[key])

                # 正则提取武器引用
                weapon_refs = re.findall(r'WEAPON_\w+', raw)
                weapons = list(set(weapons + weapon_refs))

                fields["_weapons"] = weapons

                # 提取统计数据
                health = fields.get("health", "")
                speed = fields.get("speed", "")
                fields["_health"] = health
                fields["_speed"] = speed

                # 提取cost
                cost_match = re.search(r'<cost[^>]*>(.*?)</cost>', raw, re.IGNORECASE)
                if cost_match:
                    fields["_cost"] = cost_match.group(1).strip()

                all_units[unit_name] = fields
            except Exception:
                pass

        # 3. 解析现代武器
        print("\n[3/5] 解析武器文件...")
        weapon_files = [n for n in mod_files if n.startswith("assets/Mod/Data/Weapons/")]
        for wf in weapon_files:
            try:
                raw = z.read(wf).decode("utf-8", errors="replace")
                fields = parse_xml_fields(raw)
                fields["_file"] = wf
                wname = Path(wf).stem

                # 提取武器属性
                for attr in ["calibre", "range", "damage", "penetration", "fire_rate", "ammo_type"]:
                    m = re.search(rf'<{attr}[^>]*>(.*?)</{attr}>', raw, re.IGNORECASE)
                    if m:
                        fields[f"_{attr}"] = m.group(1).strip()

                # 弹药类型
                flavour_m = re.search(r'<flavour[^>]*>(.*?)</flavour>', raw, re.IGNORECASE)
                if flavour_m:
                    fields["_flavour"] = flavour_m.group(1).strip()

                all_weapons[wname] = fields
            except Exception:
                pass

        print(f"  单位: {len(all_units)} 个, 武器: {len(all_weapons)} 个")

    # 4. 分类统计
    print("\n[4/5] 分类统计...")
    by_type = defaultdict(list)
    by_nation = defaultdict(lambda: defaultdict(list))

    for unit_name, info in all_units.items():
        # 确定装备类别(从equipment数据)
        equip_category = "unknown"
        for nat, cats in equipment_data.items():
            for cat, unit_list in cats.items():
                if unit_name in unit_list:
                    equip_category = cat
                    nation = nat
                    break
            if equip_category != "unknown":
                break

        # 武器分类
        unit_type = classify_modern_unit(
            unit_name,
            info.get("_short_name", ""),
            info.get("_long_name", ""),
            info.get("_type", ""),
            equip_category
        )

        info["_unit_type"] = unit_type
        info["_equip_category"] = equip_category
        by_type[unit_type].append(unit_name)

        # 按国家
        for nat, cats in equipment_data.items():
            for unit_list in cats.values():
                if unit_name in unit_list:
                    by_nation[nat][unit_type].append(unit_name)
                    break

    type_names = {
        "tank": "主战坦克", "ifv": "步兵战车/装甲车", "infantry": "步兵",
        "sniper": "狙击手", "helicopter": "直升机", "building": "建筑/阵地",
        "unknown": "未分类"
    }

    print(f"\n{'='*80}")
    print("单位分类统计:")
    print(f"{'='*80}")
    for utype in ["tank", "ifv", "infantry", "sniper", "helicopter", "building", "unknown"]:
        units = by_type.get(utype, [])
        if units:
            print(f"  {type_names[utype]}: {len(units)} 个")

    # 5. 详细输出
    print(f"\n{'='*80}")
    print("详细单位列表:")
    print(f"{'='*80}")

    for utype in ["tank", "ifv", "infantry", "sniper", "helicopter", "building", "unknown"]:
        units = sorted(by_type.get(utype, []))
        if not units:
            continue
        print(f"\n{'='*80}")
        print(f"  [{type_names[utype]}] ({len(units)} 个)")
        print(f"{'='*80}")

        for unit_name in units[:50]:  # 每类最多显示50个
            info = all_units[unit_name]
            long_name = info.get("_long_name", unit_name)
            short_name = info.get("_short_name", "")
            cost = info.get("_cost", "?")
            health = info.get("_health", "?")
            weapons = info.get("_weapons", [])
            comment = info.get("_comment", "")

            print(f"  [{unit_name}] {long_name}")
            if comment:
                print(f"    描述: {comment}")
            print(f"    cost={cost}  hp={health}")
            if weapons:
                key_weapons = [w for w in weapons if w in all_weapons][:5]
                if key_weapons:
                    print(f"    武器: {', '.join(key_weapons)}")

        if len(units) > 50:
            print(f"  ... 还有 {len(units)-50} 个")

    # 保存完整数据
    print(f"\n[5/5] 保存分析结果...")
    result = {
        "nations": list(nations),
        "equipment": {k: {kk: vv for kk, vv in v.items()} for k, v in equipment_data.items()},
        "units": {k: {kk: vv for kk, vv in v.items() if not isinstance(vv, dict)}
                  for k, v in all_units.items()},
        "weapons": {k: {kk: vv for kk, vv in v.items() if not isinstance(vv, dict)}
                    for k, v in all_weapons.items()},
        "by_type": {k: v for k, v in by_type.items()},
        "by_nation": {k: {kk: vv for kk, vv in v.items()} for k, v in by_nation.items()},
        "summary": {
            f"total_{k}": len(v) for k, v in by_type.items()
        }
    }

    out_path = OUTPUT / "modern_full_analysis.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  保存到: {out_path.absolute()}")


if __name__ == "__main__":
    main()