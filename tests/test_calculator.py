import unittest
from datetime import date

from src.calculator import (
    Room,
    calculate_all,
    calculate_electricity,
    calculate_occupied_days,
    calculate_water_base_fee,
    calculate_water_usage_fee,
    generate_line_message,
    load_rooms_config,
)


class TestOccupiedDays(unittest.TestCase):
    def test_3a_actual_days_from_move_in_to_billing_date(self):
        """3A 吳小姐從 7/9 入住到 8/19，頭尾皆計應為 42 天（對齊房東試算表的
        =結束日-開始日+1 公式，比舊版的「不算頭」多 1 天）。"""
        move_in_date = date(2026, 7, 9)
        billing_end_date = date(2026, 8, 19)
        self.assertEqual(calculate_occupied_days(move_in_date, billing_end_date), 42)

    def test_3a_actual_days_matches_config(self):
        rooms, billing_end_date = load_rooms_config()
        room_3a = rooms["3A"]
        days = calculate_occupied_days(
            room_3a.move_in_date, billing_end_date, move_out_date=room_3a.move_out_date
        )
        self.assertEqual(days, 42)

    def test_move_out_date_caps_occupied_days(self):
        """若在計費區間內退租，結算日應以退租日為準，而非計費結束日。"""
        move_in_date = date(2026, 1, 1)
        billing_end_date = date(2026, 1, 31)
        move_out_date = date(2026, 1, 10)
        # 1/1 ~ 1/10 頭尾皆計 = 10 天
        self.assertEqual(
            calculate_occupied_days(move_in_date, billing_end_date, move_out_date=move_out_date),
            10,
        )

    def test_move_in_after_billing_period_gives_zero_days(self):
        move_in_date = date(2026, 9, 1)
        billing_end_date = date(2026, 8, 19)
        self.assertEqual(calculate_occupied_days(move_in_date, billing_end_date), 0)


class TestElectricitySplit(unittest.TestCase):
    def setUp(self):
        # 三間房，用電量分別為 50 / 60 / 90 度，人數 1/1/2。
        self.rooms = {
            "A": Room("A", "Room A", initial_reading=100, move_in_date=date(2026, 1, 1), headcount=1),
            "B": Room("B", "Room B", initial_reading=200, move_in_date=date(2026, 1, 1), headcount=1),
            "C": Room("C", "Room C", initial_reading=300, move_in_date=date(2026, 1, 1), headcount=2),
        }
        self.new_readings = {"A": 150, "B": 260, "C": 390}

    def test_metered_fee_uses_given_unit_price_not_derived_from_bill(self):
        """電價是直接輸入值（比照台電帳單），不是用總電費/總用電量反推。"""
        result = calculate_electricity(
            self.rooms, self.new_readings, unit_price=8.0, total_electricity_bill=2000.0
        )

        self.assertEqual(result["A"]["usage_kwh"], 50)
        self.assertAlmostEqual(result["A"]["metered_fee"], 400.0)
        self.assertAlmostEqual(result["B"]["metered_fee"], 480.0)
        self.assertAlmostEqual(result["C"]["metered_fee"], 720.0)

    def test_public_electricity_is_remainder_split_by_headcount(self):
        """總電費扣除各房自家電費後的「公電」，依人數比例分攤。"""
        result = calculate_electricity(
            self.rooms, self.new_readings, unit_price=8.0, total_electricity_bill=2000.0
        )

        # 自家電費合計 1600，公電 = 2000-1600 = 400；人數比例 1:1:2（共4人）
        self.assertAlmostEqual(result["__public_electricity__"], 400.0)
        self.assertAlmostEqual(result["A"]["public_electricity_share"], 100.0)
        self.assertAlmostEqual(result["B"]["public_electricity_share"], 100.0)
        self.assertAlmostEqual(result["C"]["public_electricity_share"], 200.0)

    def test_final_fee_rounded_and_sums_to_bill(self):
        result = calculate_electricity(
            self.rooms, self.new_readings, unit_price=8.0, total_electricity_bill=2000.0
        )

        self.assertEqual(result["A"]["electricity_fee"], 500)
        self.assertEqual(result["B"]["electricity_fee"], 580)
        self.assertEqual(result["C"]["electricity_fee"], 920)

        total_fees = sum(
            info["electricity_fee"] for key, info in result.items() if not key.startswith("__")
        )
        self.assertEqual(total_fees, 2000)


class TestWaterBaseFeeSplit(unittest.TestCase):
    def setUp(self):
        # 6 間房，入住天數分別為 10/20/30/15/25/5 天，滿額天數為 30 天。
        headcounts = {"A": 1, "B": 1, "C": 1, "D": 2, "E": 2, "F": 1}
        self.rooms = {
            room_id: Room(room_id, room_id, 0, date(2026, 1, 1), hc)
            for room_id, hc in headcounts.items()
        }
        self.occupied_days = {"A": 10, "B": 20, "C": 30, "D": 15, "E": 25, "F": 5}
        self.total_base_fee = 600.0
        self.full_period_days = 30

    def test_base_fee_proportional_to_days(self):
        result = calculate_water_base_fee(
            self.rooms, self.occupied_days, self.total_base_fee, self.full_period_days
        )

        # 每房平分後為 100 元，再依天數比例分攤（未四捨五入的原始值）
        self.assertAlmostEqual(result["A"]["water_base_fee_raw"], 33.33, places=2)
        self.assertAlmostEqual(result["B"]["water_base_fee_raw"], 66.67, places=2)
        self.assertAlmostEqual(result["C"]["water_base_fee_raw"], 100.0, places=2)
        self.assertAlmostEqual(result["D"]["water_base_fee_raw"], 50.0, places=2)
        self.assertAlmostEqual(result["E"]["water_base_fee_raw"], 83.33, places=2)
        self.assertAlmostEqual(result["F"]["water_base_fee_raw"], 16.67, places=2)


class TestWaterUsageFeeSplit(unittest.TestCase):
    def setUp(self):
        # D、E 為雙人房（headcount=2），用來驗證人數 x 天數的加權邏輯。
        headcounts = {"A": 1, "B": 1, "C": 1, "D": 2, "E": 2, "F": 1}
        self.rooms = {
            room_id: Room(room_id, room_id, 0, date(2026, 1, 1), hc)
            for room_id, hc in headcounts.items()
        }
        self.occupied_days = {"A": 10, "B": 20, "C": 30, "D": 15, "E": 25, "F": 5}
        self.total_usage_fee = 1450.0

    def test_weight_is_headcount_times_days(self):
        result = calculate_water_usage_fee(self.rooms, self.occupied_days, self.total_usage_fee)

        self.assertEqual(result["A"]["weight"], 10)   # 1人 x 10天
        self.assertEqual(result["B"]["weight"], 20)   # 1人 x 20天
        self.assertEqual(result["C"]["weight"], 30)   # 1人 x 30天
        self.assertEqual(result["D"]["weight"], 30)   # 2人 x 15天
        self.assertEqual(result["E"]["weight"], 50)   # 2人 x 25天
        self.assertEqual(result["F"]["weight"], 5)    # 1人 x 5天

    def test_two_person_room_pays_like_a_one_person_room_with_double_the_days(self):
        """4D（雙人、15天）與 C（單人、30天）權重相同，驗證雙人加權邏輯正確。"""
        result = calculate_water_usage_fee(self.rooms, self.occupied_days, self.total_usage_fee)

        self.assertEqual(result["D"]["weight"], result["C"]["weight"])
        self.assertAlmostEqual(result["D"]["usage_fee_raw"], result["C"]["usage_fee_raw"], places=2)
        self.assertAlmostEqual(result["D"]["usage_fee_raw"], 300.0, places=2)

    def test_5e_gets_higher_weight_than_equal_headcount_shorter_stay(self):
        """5E（雙人、25天）應比同為雙人但天數較少的房間分攤更多用水費。"""
        result = calculate_water_usage_fee(self.rooms, self.occupied_days, self.total_usage_fee)

        self.assertGreater(result["E"]["weight"], result["D"]["weight"])
        self.assertGreater(result["E"]["usage_fee_raw"], result["D"]["usage_fee_raw"])
        self.assertAlmostEqual(result["E"]["usage_fee_raw"], 500.0, places=2)

    def test_fees_sum_to_total_usage_fee(self):
        result = calculate_water_usage_fee(self.rooms, self.occupied_days, self.total_usage_fee)

        total_fees = sum(info["usage_fee_raw"] for info in result.values())
        self.assertAlmostEqual(total_fees, self.total_usage_fee, places=2)


class TestExcelParityWaterExample(unittest.TestCase):
    """對照房東 Excel「水費分攤」工作表裡驗證過的上一期真實範例，
    確保我們的公式算出的每房應收金額與房東吸收金額和 Excel 完全一致。
    """

    def test_matches_landlord_spreadsheet_reference_period(self):
        rooms, _ = load_rooms_config()
        billing_period_start = date(2026, 4, 11)
        billing_end_date = date(2026, 6, 8)
        full_period_days = (billing_end_date - billing_period_start).days + 1
        self.assertEqual(full_period_days, 59)  # Excel D4/E4 = 59 天

        occupied_days = {
            room_id: calculate_occupied_days(
                room.move_in_date, billing_end_date, billing_period_start, room.move_out_date
            )
            for room_id, room in rooms.items()
        }
        # Excel F13:F18 = 0, 0, 11, 0, 15, 11
        self.assertEqual(occupied_days["3A"], 0)
        self.assertEqual(occupied_days["3B"], 0)
        self.assertEqual(occupied_days["4C"], 11)
        self.assertEqual(occupied_days["4D"], 0)
        self.assertEqual(occupied_days["5E"], 15)
        self.assertEqual(occupied_days["6F"], 11)

        water_base = calculate_water_base_fee(rooms, occupied_days, 748.0, full_period_days)
        water_usage = calculate_water_usage_fee(rooms, occupied_days, 5.0 + 1.0)

        from src.calculator import _round_half_up

        rounded = {
            room_id: _round_half_up(
                water_base[room_id]["water_base_fee_raw"] + water_usage[room_id]["usage_fee_raw"]
            )
            for room_id in rooms
        }
        # Excel M13:M18 = 0, 0, 25, 0, 35, 25
        self.assertEqual(rounded["3A"], 0)
        self.assertEqual(rounded["3B"], 0)
        self.assertEqual(rounded["4C"], 25)
        self.assertEqual(rounded["4D"], 0)
        self.assertEqual(rounded["5E"], 35)
        self.assertEqual(rounded["6F"], 25)

        # Excel E7（房客水費應收）= 85，E8（房東水費負擔）= 669
        tenant_total = sum(rounded.values())
        self.assertEqual(tenant_total, 85)
        self.assertEqual(754 - tenant_total, 669)


class TestCalculateAllConservation(unittest.TestCase):
    def test_total_conservation_across_real_rooms_config(self):
        """整合測試：室友應繳水費總額 + 房東吸收的基本費 = 水費帳單總額。"""
        rooms, billing_end_date = load_rooms_config()

        new_readings = {
            "3A": 49 + 100,
            "3B": 106 + 200,
            "4C": 458 + 300,
            "4D": 54 + 150,
            "5E": 418 + 400,
            "6F": 357 + 120,
        }
        unit_price = 4.0
        total_electricity_bill = 5000.0
        total_water_base_fee = 1200.0
        total_water_usage_fee = 3000.0
        full_period_days = 90  # 假設本期帳單完整計費區間長度，需依實際台水帳單調整

        summary = calculate_all(
            rooms,
            billing_end_date,
            new_readings,
            unit_price,
            total_electricity_bill,
            total_water_base_fee,
            total_water_usage_fee,
            full_period_days,
        )

        landlord_absorbed = summary.pop("__landlord_absorbed_base_fee__")
        summary.pop("__public_electricity__")
        tenant_water_total = sum(info["water_fee"] for info in summary.values())
        total_water_bill = total_water_base_fee + total_water_usage_fee

        # 水費帳單守恆：室友應繳 + 房東吸收 = 水費帳單總額（餘數式計算，恆成立）
        self.assertEqual(tenant_water_total + landlord_absorbed, total_water_bill)

        # 4D、5E 為雙人房，驗證確實分攤到大於 0 的水費。
        self.assertGreater(summary["4D"]["water_fee"], 0)
        self.assertGreater(summary["5E"]["water_fee"], 0)


class TestGenerateLineMessage(unittest.TestCase):
    def test_message_contains_every_room_and_matches_totals(self):
        rooms, billing_end_date = load_rooms_config()
        new_readings = {
            "3A": 49 + 100,
            "3B": 106 + 200,
            "4C": 458 + 300,
            "4D": 54 + 150,
            "5E": 418 + 400,
            "6F": 357 + 120,
        }
        summary = calculate_all(
            rooms,
            billing_end_date,
            new_readings,
            unit_price=4.0,
            total_electricity_bill=5000.0,
            total_water_base_fee=1200.0,
            total_water_usage_fee=3000.0,
            full_period_days=90,
        )

        message = generate_line_message(
            summary,
            billing_start_date=date(2026, 6, 19),
            billing_end_date=billing_end_date,
            remittance_account="1234-5678-9999",
            due_date=date(2026, 8, 25),
        )

        for room_id, info in summary.items():
            if room_id.startswith("__"):
                continue
            self.assertIn(room_id, message)
            self.assertIn(info["name"], message)
            self.assertIn(f"{info['total']:,.0f}", message)

        self.assertIn("1234-5678-9999", message)
        self.assertIn("8/25", message)
        self.assertIn("6/19", message)
        self.assertIn("8/19", message)


if __name__ == "__main__":
    unittest.main()
