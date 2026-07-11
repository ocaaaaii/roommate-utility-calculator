"""室友水電分帳核心計算邏輯。

對應 skill.md 階段一：後端核心邏輯與測試。
涵蓋：
- 各房實際入住天數計算
- 電費依用電量分攤
- 水費基本費依「實際入住天數 / 滿額天數」分攤（未入住天數由房東吸收）
- 用水費及水源保育費依「人數 x 實際入住天數」權重分攤
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Optional

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "rooms_config.json"


@dataclass
class Room:
    room_id: str
    name: str
    initial_reading: float
    move_in_date: date
    headcount: int


def load_rooms_config(path: Path = CONFIG_PATH) -> tuple[Dict[str, Room], date]:
    """讀取 rooms_config.json，回傳 (房間資料, 帳單結算日)。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    billing_end_date = date.fromisoformat(data["billing_end_date"])
    rooms = {
        room_id: Room(
            room_id=room_id,
            name=info["name"],
            initial_reading=info["initial_reading"],
            move_in_date=date.fromisoformat(info["move_in_date"]),
            headcount=info["headcount"],
        )
        for room_id, info in data["rooms"].items()
    }
    return rooms, billing_end_date


def calculate_occupied_days(
    move_in_date: date,
    billing_end_date: date,
    billing_period_start: Optional[date] = None,
) -> int:
    """計算某房間在該期帳單計費區間內的實際入住天數。

    若房間早於帳單計費區間起始日就已入住，以 billing_period_start 為起算日；
    若房間是在計費區間中途才入住，則以 move_in_date 為起算日。
    """
    if billing_period_start is not None and billing_period_start > move_in_date:
        start = billing_period_start
    else:
        start = move_in_date

    if start > billing_end_date:
        return 0

    return (billing_end_date - start).days


def calculate_electricity(
    rooms: Dict[str, Room],
    new_readings: Dict[str, float],
    total_electricity_bill: float,
) -> Dict[str, dict]:
    """依各房用電量比例分攤總電費。"""
    usages = {
        room_id: new_readings[room_id] - room.initial_reading
        for room_id, room in rooms.items()
    }
    total_usage = sum(usages.values())
    unit_price = total_electricity_bill / total_usage if total_usage else 0.0

    return {
        room_id: {
            "usage_kwh": usage,
            "unit_price": unit_price,
            "electricity_fee": round(usage * unit_price, 2),
        }
        for room_id, usage in usages.items()
    }


def calculate_water_base_fee(
    rooms: Dict[str, Room],
    occupied_days: Dict[str, int],
    total_base_fee: float,
    full_period_days: int,
    num_rooms: int = 6,
) -> Dict[str, dict]:
    """水費基本費：先平分 6 等分，再依實際入住天數佔滿額天數比例分攤。

    未入住/未出租天數對應的基本費由房東吸收，回傳於 "__landlord_absorbed__"。
    """
    share_per_room = total_base_fee / num_rooms
    result: Dict[str, dict] = {}
    landlord_absorbed = 0.0

    for room_id in rooms:
        days = occupied_days.get(room_id, 0)
        ratio = days / full_period_days if full_period_days else 0.0
        fee = round(share_per_room * ratio, 2)
        result[room_id] = {
            "base_fee_share": share_per_room,
            "occupied_days": days,
            "full_period_days": full_period_days,
            "water_base_fee": fee,
        }
        landlord_absorbed += share_per_room - fee

    result["__landlord_absorbed__"] = round(landlord_absorbed, 2)
    return result


def calculate_water_usage_fee(
    rooms: Dict[str, Room],
    occupied_days: Dict[str, int],
    total_usage_fee: float,
) -> Dict[str, dict]:
    """用水費 + 水源保育費：依「人數 x 實際入住天數」權重分攤。"""
    weights = {
        room_id: room.headcount * occupied_days.get(room_id, 0)
        for room_id, room in rooms.items()
    }
    total_weight = sum(weights.values())

    return {
        room_id: {
            "weight": weight,
            "usage_fee": round(
                total_usage_fee * (weight / total_weight) if total_weight else 0.0, 2
            ),
        }
        for room_id, weight in weights.items()
    }


def calculate_all(
    rooms: Dict[str, Room],
    billing_end_date: date,
    new_readings: Dict[str, float],
    total_electricity_bill: float,
    total_water_base_fee: float,
    total_water_usage_fee: float,
    full_period_days: int,
    billing_period_start: Optional[date] = None,
) -> Dict[str, dict]:
    """整合電費、水費基本費、用水費，算出每房總計。"""
    occupied_days = {
        room_id: calculate_occupied_days(
            room.move_in_date, billing_end_date, billing_period_start
        )
        for room_id, room in rooms.items()
    }

    electricity = calculate_electricity(rooms, new_readings, total_electricity_bill)
    water_base = calculate_water_base_fee(
        rooms, occupied_days, total_water_base_fee, full_period_days
    )
    water_usage = calculate_water_usage_fee(rooms, occupied_days, total_water_usage_fee)

    summary: Dict[str, dict] = {}
    for room_id, room in rooms.items():
        elec = electricity[room_id]["electricity_fee"]
        base_fee = water_base[room_id]["water_base_fee"]
        usage_fee = water_usage[room_id]["usage_fee"]
        summary[room_id] = {
            "name": room.name,
            "headcount": room.headcount,
            "occupied_days": occupied_days[room_id],
            "electricity_fee": elec,
            "water_base_fee": base_fee,
            "water_usage_fee": usage_fee,
            "total": round(elec + base_fee + usage_fee, 2),
        }

    summary["__landlord_absorbed_base_fee__"] = water_base["__landlord_absorbed__"]
    return summary


def generate_line_message(
    summary: Dict[str, dict],
    billing_start_date: date,
    billing_end_date: date,
    remittance_account: str,
    due_date: date,
) -> str:
    """依 calculate_all() 的結果，產生可一鍵複製的 LINE 群組通知文字。"""
    lines = [
        "【葫洲美好際寓 水電費通知】",
        f"計算區間：{billing_start_date.month}/{billing_start_date.day} ~ "
        f"{billing_end_date.month}/{billing_end_date.day}",
        "",
    ]

    for room_id, info in summary.items():
        if room_id.startswith("__"):
            continue
        water_fee = info["water_base_fee"] + info["water_usage_fee"]
        lines.append(
            f"- {room_id} {info['name']}：電費 ${info['electricity_fee']:,.0f} "
            f"+ 水費 ${water_fee:,.0f} = 總計 ${info['total']:,.0f}"
        )

    lines += [
        "",
        f"匯款帳號：{remittance_account}",
        f"請於 {due_date.month}/{due_date.day} 前匯款，感謝大家配合！",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    rooms, billing_end_date = load_rooms_config()
    room_3a = rooms["3A"]
    days_3a = calculate_occupied_days(room_3a.move_in_date, billing_end_date)
    print(f"3A {room_3a.name} 從 {room_3a.move_in_date} 入住到 {billing_end_date} 共 {days_3a} 天")
