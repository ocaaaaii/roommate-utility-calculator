"""室友水電分帳核心計算邏輯。

對應房東「水電費分攤試算表」的計算規則（已對照該 Excel 之公式校準）：
- 各房實際入住天數：計費區間頭尾皆計（結束日 - 開始日 + 1）
- 電費：自家用電度數 x 每度電價；扣除各房自家電費後的「公電」依人數比例分攤
- 水費基本費：先平分 6 房，再依「實際入住天數 / 滿額天數」比例分攤，未入住天數由房東吸收
- 用水費及水源保育費：依「人數 x 實際入住天數」權重分攤
- 每房最終水費 / 電費四捨五入到整數元（比照 Excel ROUND(x, 0)）
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
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
    move_out_date: Optional[date] = None


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
            move_out_date=date.fromisoformat(info["move_out_date"])
            if info.get("move_out_date")
            else None,
        )
        for room_id, info in data["rooms"].items()
    }
    return rooms, billing_end_date


def _round_half_up(value: float) -> int:
    """四捨五入到整數元，比照 Excel ROUND(x, 0)（非 Python 內建的銀行家捨入）。"""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_occupied_days(
    move_in_date: date,
    billing_end_date: date,
    billing_period_start: Optional[date] = None,
    move_out_date: Optional[date] = None,
) -> int:
    """計算某房間在該期帳單計費區間內的實際入住天數（頭尾皆計）。

    起算日：真正入住日與計費區間起始日兩者較晚者。
    結算日：退租日（若有且早於計費結束日）與計費結束日兩者較早者。
    """
    start = move_in_date
    if billing_period_start is not None and billing_period_start > start:
        start = billing_period_start

    end = billing_end_date
    if move_out_date is not None and move_out_date < end:
        end = move_out_date

    return max(0, (end - start).days + 1)


def calculate_electricity(
    rooms: Dict[str, Room],
    new_readings: Dict[str, float],
    unit_price: float,
    total_electricity_bill: float,
) -> Dict[str, dict]:
    """依各房電表用電度數 x 每度電價算出自家電費；總電費扣除各房自家電費後的
    「公電」（公共用電，如走廊、抽水馬達等）依各房人數比例分攤。
    """
    usages = {
        room_id: new_readings[room_id] - room.initial_reading
        for room_id, room in rooms.items()
    }
    metered_fees = {room_id: usage * unit_price for room_id, usage in usages.items()}
    public_electricity = total_electricity_bill - sum(metered_fees.values())
    total_headcount = sum(room.headcount for room in rooms.values())

    result: Dict[str, dict] = {}
    for room_id, room in rooms.items():
        public_share = (
            public_electricity * room.headcount / total_headcount
            if total_headcount
            else 0.0
        )
        raw_total = metered_fees[room_id] + public_share
        result[room_id] = {
            "usage_kwh": usages[room_id],
            "unit_price": unit_price,
            "metered_fee": metered_fees[room_id],
            "public_electricity_share": public_share,
            "electricity_fee": _round_half_up(raw_total),
        }

    result["__public_electricity__"] = public_electricity
    return result


def calculate_water_base_fee(
    rooms: Dict[str, Room],
    occupied_days: Dict[str, int],
    total_base_fee: float,
    full_period_days: int,
    num_rooms: int = 6,
) -> Dict[str, dict]:
    """水費基本費：先平分 6 等分，再依實際入住天數佔滿額天數比例分攤（未四捨五入）。"""
    share_per_room = total_base_fee / num_rooms

    return {
        room_id: {
            "base_fee_share": share_per_room,
            "occupied_days": occupied_days.get(room_id, 0),
            "full_period_days": full_period_days,
            "water_base_fee_raw": share_per_room
            * (occupied_days.get(room_id, 0) / full_period_days if full_period_days else 0.0),
        }
        for room_id in rooms
    }


def calculate_water_usage_fee(
    rooms: Dict[str, Room],
    occupied_days: Dict[str, int],
    total_usage_fee: float,
) -> Dict[str, dict]:
    """用水費 + 水源保育費：依「人數 x 實際入住天數」權重分攤（未四捨五入）。"""
    weights = {
        room_id: room.headcount * occupied_days.get(room_id, 0)
        for room_id, room in rooms.items()
    }
    total_weight = sum(weights.values())

    return {
        room_id: {
            "weight": weight,
            "usage_fee_raw": total_usage_fee * (weight / total_weight) if total_weight else 0.0,
        }
        for room_id, weight in weights.items()
    }


def calculate_all(
    rooms: Dict[str, Room],
    billing_end_date: date,
    new_readings: Dict[str, float],
    unit_price: float,
    total_electricity_bill: float,
    total_water_base_fee: float,
    total_water_usage_fee: float,
    full_period_days: int,
    billing_period_start: Optional[date] = None,
) -> Dict[str, dict]:
    """整合電費、水費，算出每房總計。

    水費（基本費 + 用水費）與電費（自家 + 公電分攤）都在合計後才四捨五入到整數元，
    比照房東試算表只在最終欄位 ROUND(x, 0)，不對中間欄位個別捨入。
    房東吸收的水費基本費以「水費帳單總額 - 各房水費四捨五入後加總」的餘數計算，
    確保室友應繳總額 + 房東吸收金額必定等於水費帳單總額。
    """
    occupied_days = {
        room_id: calculate_occupied_days(
            room.move_in_date, billing_end_date, billing_period_start, room.move_out_date
        )
        for room_id, room in rooms.items()
    }

    electricity = calculate_electricity(rooms, new_readings, unit_price, total_electricity_bill)
    water_base = calculate_water_base_fee(
        rooms, occupied_days, total_water_base_fee, full_period_days
    )
    water_usage = calculate_water_usage_fee(rooms, occupied_days, total_water_usage_fee)

    summary: Dict[str, dict] = {}
    tenant_water_total = 0
    for room_id, room in rooms.items():
        base_raw = water_base[room_id]["water_base_fee_raw"]
        usage_raw = water_usage[room_id]["usage_fee_raw"]
        water_fee = _round_half_up(base_raw + usage_raw)
        tenant_water_total += water_fee
        elec = electricity[room_id]

        summary[room_id] = {
            "name": room.name,
            "headcount": room.headcount,
            "occupied_days": occupied_days[room_id],
            "electricity_usage_kwh": elec["usage_kwh"],
            "electricity_metered_fee": round(elec["metered_fee"], 2),
            "electricity_public_share": round(elec["public_electricity_share"], 2),
            "electricity_fee": elec["electricity_fee"],
            "water_base_fee_raw": round(base_raw, 2),
            "water_usage_fee_raw": round(usage_raw, 2),
            "water_fee": water_fee,
            "total": elec["electricity_fee"] + water_fee,
        }

    total_water_bill = total_water_base_fee + total_water_usage_fee
    summary["__landlord_absorbed_base_fee__"] = total_water_bill - tenant_water_total
    summary["__public_electricity__"] = round(electricity["__public_electricity__"], 2)
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
        f"水費計費區間：{billing_start_date.month}/{billing_start_date.day} ~ "
        f"{billing_end_date.month}/{billing_end_date.day}",
        "",
    ]

    for room_id, info in summary.items():
        if room_id.startswith("__"):
            continue
        headcount_note = f"（{info['headcount']}人）" if info["headcount"] == 2 else ""
        lines.append(
            f"- {room_id}{headcount_note}：電費 ${info['electricity_fee']:,.0f} "
            f"+ 水費 ${info['water_fee']:,.0f} = 總計 ${info['total']:,.0f}"
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
    print(f"3A 從 {room_3a.move_in_date} 入住到 {billing_end_date} 共 {days_3a} 天")
