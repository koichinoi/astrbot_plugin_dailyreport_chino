"""Resolve configured group IDs without guessing between bot accounts."""
import re

def config_entries(value):
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,，\s]+", value) if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []

def manual_targets(value):
    # Preserve existing complete UMO configuration and its order.
    return list(dict.fromkeys(config_entries(value)))

def resolve_groups(values, default_platform_id, qq_platform_ids):
    platform_ids=list(dict.fromkeys(qq_platform_ids))
    default_platform_id=str(default_platform_id or "").strip()
    bindings=[]; errors=[]
    for entry in config_entries(values):
        platform_id, sep, number=entry.partition("|")
        if not sep:
            number=entry
            platform_id=default_platform_id or (platform_ids[0] if len(platform_ids)==1 else "")
        platform_id,number=platform_id.strip(),number.strip()
        umo=None
        if not re.fullmatch(r"[0-9]{1,20}",number) or int(number)==0:
            errors.append("群号格式无效，请只填数字或 平台ID|群号")
        elif not platform_id:
            errors.append("没有唯一的 OneBot 连接，请填写订阅群的平台 ID")
        elif platform_id not in platform_ids:
            errors.append("指定的平台 ID 不属于已加载的 OneBot 连接，请检查机器人连接配置")
        else:
            umo=f"{platform_id}:GroupMessage:{int(number)}"
        bindings.append((entry,umo))
    return bindings,list(dict.fromkeys(errors))
