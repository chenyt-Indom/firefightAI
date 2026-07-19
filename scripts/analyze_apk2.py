"""深度分析Firefight APK中的现代MOD单位数据"""
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
import re

APK_PATH = r"C:\Users\19853\Documents\xwechat_files\wxid_zjc4zsi32xqc22_042d\msg\file\2026-07\base.apk.1"
OUTPUT_DIR = Path("apk_analysis")
OUTPUT_DIR.mkdir(exist_ok=True)

def extract_all_xml():
    """提取所有Mod_Data下的XML文件"""
    with zipfile.ZipFile(APK_PATH, "r") as z:
        for name in z.namelist():
            if "Mod_Data" in name and name.endswith(".xml"):
                try:
                    data = z.read(name)
                    text = data.decode("utf-8", errors="replace")
                    out_path = OUTPUT_DIR / name.replace("/", "_")
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(text, encoding="utf-8", errors="replace")
                except Exception:
                    pass

    xml_files = list(OUTPUT_DIR.glob("*.xml"))
    print(f"共提取 {len(xml_files)} 个XML文件")

    # 分类
    unit_files = []
    weapon_files = []
    for f in xml_files:
        name = f.name.lower()
        if "weapon" in name:
            weapon_files.append(f)
        elif any(kw in name for kw in ["unit", "infantry", "vehicle", "tank", "ifv", "sniper", "helicopter", "building"]):
            unit_files.append(f)

    print(f"  单位相关: {len(unit_files)}")
    print(f"  武器相关: {len(weapon_files)}")
    return unit_files, weapon_files, xml_files


def parse_weapon_file(filepath: Path) -> dict | None:
    """解析武器XML文件"""
    try:
        root = ET.parse(str(filepath)).getroot()
        info = {}
        info["file"] = filepath.name

        for child in root:
            tag = child.tag.lower()
            if child.text and child.text.strip():
                info[tag] = child.text.strip()
            # 子元素
            sub = {}
            for sc in child:
                if sc.text and sc.text.strip():
                    sub[sc.tag.lower()] = sc.text.strip()
            if sub:
                info[tag] = sub

        return info
    except Exception:
        return None


def parse_unit_file(filepath: Path) -> dict | None:
    """解析单位XML文件"""
    try:
        root = ET.parse(str(filepath)).getroot()
        info = {"file": filepath.name}
        for child in root:
            tag = child.tag.lower()
            if child.text and child.text.strip():
                info[tag] = child.text.strip()
            sub = {}
            for sc in child:
                if sc.text and sc.text.strip():
                    sub[sc.tag.lower()] = sc.text.strip()
            if sub:
                info[tag] = sub
        return info
    except Exception:
        return None


def parse_units():
    """解析所有单位数据"""
    data = {}
    with zipfile.ZipFile(APK_PATH, "r") as z:
        for name in z.namelist():
            if "Mod_Data/Units" in name and name.endswith(".xml"):
                try:
                    raw = z.read(name).decode("utf-8", errors="replace")
                    # 提取文件名中的单位名
                    unit_name = Path(name).stem
                    # 提取关键字段
                    fields = {}
                    for tag in ["name", "cost", "type", "period", "health", "speed",
                                "armour", "weapon", "crew", "size", "class", "role",
                                "description", "era", "infantry_type", "vehicle_type",
                                "air_type"]:
                        # 尝试匹配大小写
                        for variant in [tag, tag.upper(), tag.capitalize(),
                                        f"<{tag}>", f"<{tag.upper()}>"]:
                            pattern = re.compile(rf"<{tag}[^>]*>(.*?)</{tag}>", re.IGNORECASE)
                            match = pattern.search(raw)
                            if match:
                                fields[tag] = match.group(1).strip()
                                break

                    # 提取weapon引用
                    weapon_refs = re.findall(r'<weapon[^>]*(?:ref|name|type)="([^"]*)"', raw, re.IGNORECASE)
                    if weapon_refs:
                        fields["weapons"] = weapon_refs

                    if fields:
                        data[unit_name] = fields
                except Exception:
                    pass

    return data

def parse_weapons():
    """解析所有武器数据"""
    data = {}
    with zipfile.ZipFile(APK_PATH, "r") as z:
        for name in z.namelist():
            if "Mod_Data/Weapons" in name and name.endswith(".xml"):
                try:
                    raw = z.read(name).decode("utf-8", errors="replace")
                    weapon_name = Path(name).stem
                    fields = {}

                    for tag in ["name", "type", "calibre", "range", "damage", "penetration",
                                "fire_rate", "ammo", "era", "period", "weapon_type",
                                "infantry_weapon", "vehicle_weapon", "air_weapon", "guided",
                                "top_attack"]:
                        pattern = re.compile(rf"<{tag}[^>]*>(.*?)</{tag}>", re.IGNORECASE)
                        match = pattern.search(raw)
                        if match:
                            fields[tag] = match.group(1).strip()

                    # 提取ammo type
                    ammo_match = re.search(r'<ammo_type[^>]*>(.*?)</ammo_type>', raw, re.IGNORECASE)
                    if ammo_match:
                        fields["ammo_type"] = ammo_match.group(1).strip()

                    if fields:
                        data[weapon_name] = fields
                except Exception:
                    pass

    return data


def main():
    print("=" * 70)
    print("Firefight APK 现代MOD - 单位与武器详细分析")
    print("=" * 70)

    # 提取所有XML
    print("\n[1/3] 提取XML文件...")
    unit_files, weapon_files, all_xml = extract_all_xml()

    # 解析单位
    print("\n[2/3] 解析单位数据...")
    units = parse_units()
    print(f"  找到 {len(units)} 个单位")

    # 解析武器
    print("\n[3/3] 解析武器数据...")
    weapons = parse_weapons()
    print(f"  找到 {len(weapons)} 个武器")

    # 输出单位列表
    print(f"\n{'='*70}")
    print("现代MOD单位列表:")
    print(f"{'='*70}")
    for unit_name in sorted(units.keys()):
        info = units[unit_name]
        cost = info.get("cost", "?")
        u_type = info.get("type", info.get("infantry_type", info.get("vehicle_type", "?")))
        health = info.get("health", "?")
        weapons_list = info.get("weapons", [])
        print(f"  [{u_type}] {unit_name}  cost={cost}  hp={health}")
        if weapons_list:
            print(f"    武器: {', '.join(weapons_list[:5])}")

    # 输出武器列表
    print(f"\n{'='*70}")
    print("现代MOD武器列表:")
    print(f"{'='*70}")
    for weapon_name in sorted(weapons.keys()):
        info = weapons[weapon_name]
        w_type = info.get("type", info.get("weapon_type", "?"))
        calibre = info.get("calibre", "?")
        range_val = info.get("range", "?")
        print(f"  [{w_type}] {weapon_name}  calibre={calibre}  range={range_val}")

    # 保存结果
    import json
    result = {"units": units, "weapons": weapons}
    (OUTPUT_DIR / "unit_analysis.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n完整分析结果已保存到: {OUTPUT_DIR / 'unit_analysis.json'}")


if __name__ == "__main__":
    main()