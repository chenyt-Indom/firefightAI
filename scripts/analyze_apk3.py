"""深度解析Firefight APK - 提取现代MOD单位、武器、协同关系"""
import zipfile
import re
import json
from pathlib import Path
from collections import defaultdict

APK_PATH = r"C:\Users\19853\Documents\xwechat_files\wxid_zjc4zsi32xqc22_042d\msg\file\2026-07\base.apk.1"
OUTPUT = Path("apk_analysis")
OUTPUT.mkdir(exist_ok=True)

# 现代战斗单位关键词 (排除WW2装备)
MODERN_KEYWORDS = {
    "tank": ["M1A1", "M1A2", "ABRAMS", "T-90", "T-80", "T-72", "LEOPARD", "LECLERC",
              "CHALLENGER", "MERKAVA", "TYPE_99", "TYPE_96", "K2", "ARIETE", "ALTAY",
              "T_14", "ARMATA", "ZTZ", "TYPE_10", "TYPE_90", "K1", "STRV"],
    "ifv": ["BMP", "BTR", "BRADLEY", "M2A", "M3A", "WARRIOR", "STRYKER", "M113",
            "LAV", "PANDUR", "PIRANHA", "PATRIA", "BOXER", "VBCI", "CV90", "PUMA",
            "MARDER", "LYNX", "ASCOD", "ZBD", "ZBL", "TYPE_89", "TYPE_87",
            "AMX_10P", "VAB", "FUCHS", "BMD", "BMPT", "TIGR", "GAZ"],
    "infantry": ["RIFLEMAN", "GRENADIER", "MACHINE_GUNNER", "ANTI_TANK", "ENGINEER",
                 "MEDIC", "SCOUT", "DESIGNATED_MARKSMAN", "CREW_SERVED_WEAPON",
                 "ASSAULT", "RECON", "PARATROOPER", "MARINE", "SPECIAL_FORCES",
                 "MILITIA", "IRREGULAR", "MECHANIZED_INF"],
    "sniper": ["SNIPER", "MARKSMAN", "DESIGNATED_MARKSMAN", "SNIPER_TEAM",
               "COUNTER_SNIPER", "RECON_SNIPER"],
    "helicopter": ["AH-64", "APACHE", "MI-24", "MI-28", "MI-35", "KA-50", "KA-52",
                    "TIGER", "A129", "MANGUSTA", "Z-10", "Z-19", "UH-1", "UH-60",
                    "BLACK_HAWK", "MI-8", "MI-17", "CH-47", "CHINOOK", "NH90",
                    "MH-6", "LITTLE_BIRD", "AH-1", "COBRA", "OH-58", "KIOWA"],
    "building": ["BUNKER", "FOB", "HQ", "COMMAND_POST", "WATCHTOWER", "SANDBAG",
                 "HESCO", "AA_EMPLACEMENT", "ATGM_POSITION", "MORTAR_PIT",
                 "ARTILLERY_POSITION", "RADAR", "SAM_SITE", "BARRACKS"],
}

# 现代武器关键词
MODERN_WEAPON_KEYWORDS = [
    "M4", "M16", "AK_47", "AK_74", "AK_12", "AK_15", "HK_416", "HK_417", "G36",
    "G3", "FAMAS", "AUG", "SA80", "SCAR", "QBZ", "QBU", "QBS", "M249", "M240",
    "M60", "PKM", "PKP", "M249", "MG4", "MG5", "MINIMI", "RPK", "M2", "M2HB",
    "KPVT", "NSV", "DSHK", "M82", "M107", "AW", "AWM", "AWP", "PSG", "M24",
    "M40", "SVD", "VSS", "AS_VAL", "RPG_7", "RPG_16", "RPG_18", "RPG_22",
    "RPG_26", "RPG_27", "RPG_28", "RPG_29", "RPG_30", "RPG_32", "RPG_AT4",
    "RPG_CARL_GUSTAF", "RPG_M3_MAAWS", "RPG_JAVELIN", "RPG_NLAW", "RPG_LAW",
    "RPG_M72", "RPG_PANZERFAUST_3", "RPG_PF_89", "RPG_PF_98", "RPG_SAPFIR",
    "RPG_RGW90", "RPG_TYPE_01_LMAT", "RPG_TYPE_87", "RPG_TYPE_79",
    "RPG_FGM_148", "GRENADE_LAUNCHER_M203", "GRENADE_LAUNCHER_AG36",
    "GRENADE_LAUNCHER_GP_25", "GRENADE_LAUNCHER_40MM", "GRENADE_LAUNCHER_MILKOR",
    "GRENADE_LAUNCHER_HK69A1", "GRENADE_LAUNCHER_QLZ_87", "GRENADE_LAUNCHER_QTS_11",
    "GRENADE_LAUNCHER_RG_6", "GRENADE_LAUNCHER_GM_94",
    "CANNON_100", "CANNON_105", "CANNON_115", "CANNON_120", "CANNON_125",
    "CANNON_30", "CANNON_73", "CANNON_76", "CANNON_90",
    "CANNON_M242", "CANNON_MK20", "CANNON_RARDEN", "CANNON_2A70", "CANNON_2A42",
    "CANNON_2A46", "CANNON_2A72", "CANNON_2A28",
    "ATGM", "MILAN", "TOW", "KORNET", "KONKURS", "MALYUTKA", "FAGOT", "METIS",
    "SPIKE", "JAVELIN", "ERYX", "HOT", "HELLFIRE", "VIKHR", "AT",
    "MORTAR_120", "MORTAR_81", "MORTAR_82", "MORTAR_60", "MORTAR_",
    "HOWITZER", "MLRS", "GRAD", "BM", "SMERCH", "URAGAN",
    "STINGER", "IGLA", "MISTRAL", "STARSTREAK", "STRELA", "MANPADS",
    "MP5", "MP7", "UZI", "STERLING",
    "PISTOL_GLOCK", "PISTOL_HK", "PISTOL_MAKAROV", "PISTOL_QSZ", "PISTOL_NP",
    "PISTOL_CF", "PISTOL_BHP", "PISTOL_COLT",
    "SHOTGUN_BENELLI", "SHOTGUN_MOSSBERG", "SHOTGUN_REMINGTON", "SHOTGUN_SAIGA",
    "SHOTGUN_QBS", "SHOTGUN_HK_512", "SHOTGUN_M26", "SHOTGUN_NEOSTEAD",
    "SMG_QCQ", "SMG_QCW", "SMG_JH", "SMG_BJC", "SMG_CS",
    "RIFLE_HOWA_TYPE_20", "RIFLE_HOWA_TYPE_64", "RIFLE_HOWA_TYPE_89",
    "FLAMETHROWER", "CANNON_FLAK",
]


def parse_xml_fields(xml_text: str) -> dict:
    """解析XML文件，提取所有顶级字段"""
    fields = {}
    # 匹配 <tag>value</tag> 或 <tag attr="val">value</tag> 或 <tag attr="val"/>
    pattern = re.compile(r'<(\w+)(?:\s+[^>]*)?>(.*?)</\1>', re.DOTALL)
    for match in pattern.finditer(xml_text):
        tag = match.group(1).lower()
        content = match.group(2).strip()
        # 移除嵌套XML标签获取纯文本
        if '<' in content:
            content = re.sub(r'<[^>]+>', '', content).strip()
        if content:
            fields[tag] = content

    # 检查period
    for tag in ["period", "era"]:
        if tag in fields:
            fields["_period"] = fields[tag].upper()

    return fields


def parse_weapon_refs(xml_text: str) -> list:
    """提取武器引用"""
    refs = []
    # <weapon ref="WEAPON_XXX"/>
    refs += re.findall(r'weapon.*?ref="([^"]+)"', xml_text, re.IGNORECASE)
    # <weapon name="WEAPON_XXX"/>
    refs += re.findall(r'weapon.*?name="([^"]+)"', xml_text, re.IGNORECASE)
    # <weapon>WEAPON_XXX</weapon>
    refs += re.findall(r'<weapon[^>]*>([^<]+)</weapon>', xml_text, re.IGNORECASE)
    return list(set(refs))


def is_modern(fields: dict) -> bool:
    """判断是否为现代单位"""
    period = fields.get("_period", "")
    if period == "MODERN":
        return True

    # 如果没有period字段，通过单位名称和武器判断
    name = fields.get("name", "").upper()
    file_name = fields.get("_file", "").upper()

    # 检查单位名称
    for cat_keywords in MODERN_KEYWORDS.values():
        for kw in cat_keywords:
            if kw.upper() in name or kw.upper() in file_name:
                return True

    return False


def classify_unit(fields: dict) -> str:
    """分类单位类型"""
    name = fields.get("name", "").upper()
    file_name = fields.get("_file", "").upper()
    combined = f"{name} {file_name}"

    for unit_type, keywords in MODERN_KEYWORDS.items():
        for kw in keywords:
            if kw.upper() in combined:
                return unit_type

    return "unknown"


def parse_unit_capabilities(xml_text: str, fields: dict) -> dict:
    """解析单位能力"""
    caps = {}

    # 装甲类型
    armour_match = re.search(r'<armour[^>]*>(.*?)</armour>', xml_text, re.DOTALL | re.IGNORECASE)
    if armour_match:
        caps["armour"] = armour_match.group(1).strip()

    # 速度
    speed_match = re.search(r'<speed[^>]*>(.*?)</speed>', xml_text, re.DOTALL | re.IGNORECASE)
    if speed_match:
        caps["speed"] = speed_match.group(1).strip()

    # 血量
    health_match = re.search(r'<health[^>]*>(.*?)</health>', xml_text, re.DOTALL | re.IGNORECASE)
    if health_match:
        caps["health"] = health_match.group(1).strip()

    # 视野范围
    sight_match = re.search(r'<sight[^>]*>(.*?)</sight>', xml_text, re.DOTALL | re.IGNORECASE)
    if sight_match:
        caps["sight"] = sight_match.group(1).strip()

    # 人员数量
    crew_match = re.search(r'<crew[^>]*>(.*?)</crew>', xml_text, re.DOTALL | re.IGNORECASE)
    if crew_match:
        caps["crew"] = crew_match.group(1).strip()

    # 花费
    cost_match = re.search(r'<cost[^>]*>(.*?)</cost>', xml_text, re.DOTALL | re.IGNORECASE)
    if cost_match:
        caps["cost"] = cost_match.group(1).strip()

    # 运输能力
    transport_match = re.search(r'<transport[^>]*>(.*?)</transport>', xml_text, re.DOTALL | re.IGNORECASE)
    if transport_match:
        caps["transport"] = transport_match.group(1).strip()

    return caps


def parse_weapon_detail(xml_text: str) -> dict:
    """解析武器详细信息"""
    detail = {}

    # 口径
    calibre_match = re.search(r'<calibre[^>]*>(.*?)</calibre>', xml_text, re.IGNORECASE)
    if calibre_match:
        detail["calibre"] = calibre_match.group(1).strip()

    # 射程
    range_match = re.search(r'<range[^>]*>(.*?)</range>', xml_text, re.IGNORECASE)
    if range_match:
        detail["range"] = range_match.group(1).strip()

    # 伤害
    damage_match = re.search(r'<damage[^>]*>(.*?)</damage>', xml_text, re.IGNORECASE)
    if damage_match:
        detail["damage"] = damage_match.group(1).strip()

    # 穿深
    pen_match = re.search(r'<penetration[^>]*>(.*?)</penetration>', xml_text, re.IGNORECASE)
    if pen_match:
        detail["penetration"] = pen_match.group(1).strip()

    # 弹药类型
    ammo_match = re.search(r'<ammo_type[^>]*>(.*?)</ammo_type>', xml_text, re.IGNORECASE)
    if ammo_match:
        detail["ammo_type"] = ammo_match.group(1).strip()

    # 射速
    rof_match = re.search(r'<fire_rate[^>]*>(.*?)</fire_rate>', xml_text, re.IGNORECASE)
    if rof_match:
        detail["fire_rate"] = rof_match.group(1).strip()

    # 武器类型
    type_match = re.search(r'<type[^>]*>(.*?)</type>', xml_text, re.IGNORECASE)
    if type_match:
        detail["type"] = type_match.group(1).strip()

    # 炮弹类型 (flavour)
    flavour_match = re.search(r'<flavour[^>]*>(.*?)</flavour>', xml_text, re.IGNORECASE)
    if flavour_match:
        detail["flavour"] = flavour_match.group(1).strip()

    return detail


def main():
    print("=" * 80)
    print("Firefight APK 现代MOD - 深度解析")
    print("=" * 80)

    modern_units = {}
    all_weapons = {}
    weapon_ref_map = defaultdict(list)  # weapon_name -> [unit_names]

    with zipfile.ZipFile(APK_PATH, "r") as z:
        names = z.namelist()

        # 第一步：提取所有单位的XML
        unit_files = [n for n in names if n.startswith("assets/Data/Units/") and n.endswith(".xml")]
        weapon_files = [n for n in names if n.startswith("assets/Data/Weapons/") and n.endswith(".xml")]

        print(f"\n[1/4] 扫描单位文件: {len(unit_files)} 个")
        print(f"     扫描武器文件: {len(weapon_files)} 个")

        # 第二步：解析武器(先解析武器，后续单位需要武器数据)
        print(f"\n[2/4] 解析武器数据...")
        for wf in weapon_files:
            try:
                raw = z.read(wf).decode("utf-8", errors="replace")
                fields = parse_xml_fields(raw)
                fields["_file"] = wf
                weapon_name = Path(wf).stem

                # 判断是否为现代武器
                if is_modern(fields):
                    detail = parse_weapon_detail(raw)
                    fields.update(detail)
                    all_weapons[weapon_name] = fields
            except Exception:
                pass

        # 第三步：解析单位
        print(f"\n[3/4] 解析单位数据...")
        for uf in unit_files:
            try:
                raw = z.read(uf).decode("utf-8", errors="replace")
                fields = parse_xml_fields(raw)
                fields["_file"] = uf
                unit_name = Path(uf).stem

                if is_modern(fields):
                    # 分类
                    fields["_unit_type"] = classify_unit(fields)
                    # 能力
                    caps = parse_unit_capabilities(raw, fields)
                    fields.update(caps)
                    # 武器
                    weapons = parse_weapon_refs(raw)
                    fields["_weapons"] = weapons
                    for w in weapons:
                        weapon_ref_map[w].append(unit_name)

                    modern_units[unit_name] = fields
            except Exception:
                pass

    print(f"     现代单位: {len(modern_units)} 个")
    print(f"     现代武器: {len(all_weapons)} 个")

    # 第四步：分类统计和协同分析
    print(f"\n[4/4] 分类统计和协同分析...")

    by_type = defaultdict(list)
    for name, info in modern_units.items():
        utype = info.get("_unit_type", "unknown")
        by_type[utype].append((name, info))

    # 输出分类结果
    type_names_cn = {
        "tank": "主战坦克", "ifv": "步兵战车", "infantry": "步兵",
        "sniper": "狙击手", "helicopter": "直升机", "building": "建筑",
        "unknown": "未分类"
    }

    print(f"\n{'='*80}")
    for utype in ["tank", "ifv", "infantry", "sniper", "helicopter", "building", "unknown"]:
        units = by_type.get(utype, [])
        if units:
            print(f"\n{'='*80}")
            print(f"  {type_names_cn.get(utype, utype)} ({len(units)} 个)")
            print(f"{'='*80}")
            for unit_name, info in sorted(units):
                cost = info.get("cost", "?")
                hp = info.get("health", "?")
                armour = info.get("armour", "?")
                speed = info.get("speed", "?")
                weapons = info.get("_weapons", [])
                print(f"\n  [{unit_name}]")
                print(f"    cost={cost}  hp={hp}  armour={armour}  speed={speed}")
                if weapons:
                    print(f"    武器数: {len(weapons)}")
                    for w in weapons[:8]:
                        w_info = all_weapons.get(w, {})
                        cal = w_info.get("calibre", "?")
                        rng = w_info.get("range", "?")
                        dmg = w_info.get("damage", "?")
                        pen = w_info.get("penetration", "?")
                        print(f"      - {w}  calibre={cal}  range={rng}  damage={dmg}  pen={pen}")

    # 保存完整数据
    result = {
        "units": {k: v for k, v in modern_units.items()},
        "weapons": {k: v for k, v in all_weapons.items()},
        "by_type": {k: [x[0] for x in v] for k, v in by_type.items()},
        "weapon_usage": {k: v for k, v in weapon_ref_map.items()},
        "summary": {
            f"total_{k}": len(v) for k, v in by_type.items()
        }
    }

    out_path = OUTPUT / "modern_analysis.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n\n完整分析数据已保存到: {out_path.absolute()}")
    print(f"  现代单位总数: {len(modern_units)}")
    for utype in ["tank", "ifv", "infantry", "sniper", "helicopter", "building", "unknown"]:
        count = len(by_type.get(utype, []))
        if count > 0:
            print(f"    {type_names_cn.get(utype, utype)}: {count}")


if __name__ == "__main__":
    main()