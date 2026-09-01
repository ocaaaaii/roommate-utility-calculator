import unittest
from datetime import date

from src.calculator import (
    Room,
    calculate_all,
    calculate_electricity,
    calculate_electricity_usage_days,
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


class TestElectricityUsageDays(unittest.TestCase):
    def test_uses_period_start_when_contract_predates_period(self):
        """簽約日早於電費帳單起始日 -> 從帳單起始日算到結束日（不 +1）。"""
        days = calculate_electricity_usage_days(
            contract_date=date(2026, 1, 1),
            electricity_period_start=date(2026, 6, 17),
            electricity_period_end=date(2026, 8, 18),
        )
        self.assertEqual(days, (date(2026, 8, 18) - date(2026, 6, 17)).days)

    def test_uses_contract_date_when_signed_mid_period(self):
        """簽約日晚於電費帳單起始日 -> 從簽約日算到結束日。"""
        days = calculate_electricity_usage_days(
            contract_date=date(2026, 6, 24),
            electricity_period_start=date(2026, 6, 17),
            electricity_period_end=date(2026, 8, 18),
        )
        self.assertEqual(days, (date(2026, 8, 18) - date(2026, 6, 24)).days)


class TestElectricitySplit(unittest.TestCase):
    def setUp(self):
        # 三間房，用電量分別為 50 / 60 / 90 度，人數 1/1/2。
        # A、B 簽約日早於電費期間起始日 -> 用滿整個期間（30天）；
        # C 簽約日在期間中途（1/16）-> 只用期間後半（15天）。
        self.electricity_period_start = date(2026, 1, 1)
        self.electricity_period_end = date(2026, 1, 31)
        self.rooms = {
            "A": Room("A", "Room A", 100, date(2025, 12, 1), date(2025, 12, 1), 1),
            "B": Room("B", "Room B", 200, date(2025, 12, 1), date(2025, 12, 1), 1),
            "C": Room("C", "Room C", 300, date(2026, 1, 16), date(2026, 1, 16), 2),
        }
        self.initial_readings = {"A": 100, "B": 200, "C": 300}
        self.new_readings = {"A": 150, "B": 260, "C": 390}

    def _run(self):
        return calculate_electricity(
            self.rooms,
            self.initial_readings,
            self.new_readings,
            unit_price=8.0,
            total_electricity_bill=2000.0,
            electricity_period_start=self.electricity_period_start,
            electricity_period_end=self.electricity_period_end,
        )

    def test_metered_fee_uses_given_unit_price_not_derived_from_bill(self):
        """電價是直接輸入值（比照台電帳單），不是用總電費/總用電量反推。"""
        result = self._run()

        self.assertEqual(result["A"]["usage_kwh"], 50)
        self.assertAlmostEqual(result["A"]["metered_fee"], 400.0)
        self.assertAlmostEqual(result["B"]["metered_fee"], 480.0)
        self.assertAlmostEqual(result["C"]["metered_fee"], 720.0)

    def test_usage_days_from_contract_date(self):
        result = self._run()

        self.assertEqual(result["A"]["usage_days"], 30)  # 簽約日早於期間 -> 整期間
        self.assertEqual(result["B"]["usage_days"], 30)
        self.assertEqual(result["C"]["usage_days"], 15)  # 1/16 簽約 -> 只剩後半期間

    def test_public_electricity_is_remainder_split_by_headcount_times_days(self):
        """總電費扣除各房自家電費後的「公電」，依「人數 x 電費計費天數」比例分攤。"""
        result = self._run()

        # 自家電費合計 1600，公電 = 2000-1600 = 400
        self.assertAlmostEqual(result["__public_electricity__"], 400.0)
        # 權重：A=1*30=30, B=1*30=30, C=2*15=30，三房權重相同 -> 平分公電
        self.assertAlmostEqual(result["A"]["public_electricity_share"], 133.333, places=2)
        self.assertAlmostEqual(result["B"]["public_electricity_share"], 133.333, places=2)
        self.assertAlmostEqual(result["C"]["public_electricity_share"], 133.333, places=2)

    def test_final_fee_rounded(self):
        result = self._run()

        self.assertEqual(result["A"]["electricity_fee"], 533)
        self.assertEqual(result["B"]["electricity_fee"], 613)
        self.assertEqual(result["C"]["electricity_fee"], 853)


class TestWaterBaseFeeSplit(unittest.TestCase):
    def setUp(self):
        # 6 間房，入住天數分別為 10/20/30/15/25/5 天，滿額天數為 30 天。
        headcounts = {"A": 1, "B": 1, "C": 1, "D": 2, "E": 2, "F": 1}
        self.rooms = {
            room_id: Room(room_id, room_id, 0, date(2026, 1, 1), date(2026, 1, 1), hc)
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
            room_id: Room(room_id, room_id, 0, date(2026, 1, 1), date(2026, 1, 1), hc)
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


class TestExcelParityElectricityExample(unittest.TestCase):
    """對照房東 Excel 更新版「電費分攤」工作表的真實範例（含期末度數），
    確保公電依「人數 x 電費計費天數」分攤的結果與 Excel 完全一致。
    """

    def test_matches_landlord_spreadsheet_reference_period(self):
        rooms, _ = load_rooms_config()
        electricity_period_start = date(2026, 6, 17)
        electricity_period_end = date(2026, 8, 18)
        initial_readings = {
            "3A": 49, "3B": 106, "4C": 458, "4D": 54, "5E": 418, "6F": 357,
        }
        new_readings = {
            "3A": 183, "3B": 255, "4C": 919, "4D": 308, "5E": 874, "6F": 809,
        }

        result = calculate_electricity(
            rooms,
            initial_readings,
            new_readings,
            unit_price=6.22,
            total_electricity_bill=21040.0,
            electricity_period_start=electricity_period_start,
            electricity_period_end=electricity_period_end,
        )

        # Excel H13:H18（使用天數）= 55, 61, 62, 55, 62, 62
        self.assertEqual(result["3A"]["usage_days"], 55)
        self.assertEqual(result["3B"]["usage_days"], 61)
        self.assertEqual(result["4C"]["usage_days"], 62)
        self.assertEqual(result["4D"]["usage_days"], 55)
        self.assertEqual(result["5E"]["usage_days"], 62)
        self.assertEqual(result["6F"]["usage_days"], 62)

        # Excel K13:K18（應收電費）= 1899, 2109, 4069, 3711, 5239, 4013
        self.assertEqual(result["3A"]["electricity_fee"], 1899)
        self.assertEqual(result["3B"]["electricity_fee"], 2109)
        self.assertEqual(result["4C"]["electricity_fee"], 4069)
        self.assertEqual(result["4D"]["electricity_fee"], 3711)
        self.assertEqual(result["5E"]["electricity_fee"], 5239)
        self.assertEqual(result["6F"]["electricity_fee"], 4013)


class TestCalculateAllConservation(unittest.TestCase):
    def test_total_conservation_across_real_rooms_config(self):
        """整合測試：室友應繳水費總額 + 房東吸收的基本費 = 水費帳單總額。"""
        rooms, billing_end_date = load_rooms_config()

        initial_readings = {"3A": 49, "3B": 106, "4C": 458, "4D": 54, "5E": 418, "6F": 357}
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
        water_period_start = date(2026, 6, 9)

        summary = calculate_all(
            rooms,
            initial_readings,
            new_readings,
            unit_price,
            total_electricity_bill,
            date(2026, 6, 17),
            date(2026, 8, 18),
            total_water_base_fee,
            total_water_usage_fee,
            water_period_start,
            billing_end_date,
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
    def _build_summary(self, rooms, billing_end_date):
        initial_readings = {"3A": 49, "3B": 106, "4C": 458, "4D": 54, "5E": 418, "6F": 357}
        new_readings = {
            "3A": 49 + 100,
            "3B": 106 + 200,
            "4C": 458 + 300,
            "4D": 54 + 150,
            "5E": 418 + 400,
            "6F": 357 + 120,
        }
        return calculate_all(
            rooms,
            initial_readings,
            new_readings,
            4.0,
            5000.0,
            date(2026, 6, 17),
            date(2026, 8, 18),
            1200.0,
            3000.0,
            date(2026, 6, 9),
            billing_end_date,
        )

    def test_message_contains_every_room_and_matches_totals(self):
        rooms, billing_end_date = load_rooms_config()
        summary = self._build_summary(rooms, billing_end_date)

        message = generate_line_message(
            summary,
            water_period_start=date(2026, 6, 19),
            water_period_end=billing_end_date,
            remittance_account="1234-5678-9999",
            due_date=date(2026, 8, 25),
            electricity_period_start=date(2026, 6, 17),
            electricity_period_end=date(2026, 8, 18),
            meter_reading_date=date(2026, 8, 19),
        )

        for room_id, info in summary.items():
            if room_id.startswith("__"):
                continue
            self.assertIn(room_id, message)
            self.assertNotIn(info["name"], message)
            self.assertIn(f"{info['total']:,.0f}", message)

        self.assertIn("1234-5678-9999", message)
        self.assertIn("8/25", message)
        self.assertIn("6/19", message)
        self.assertIn("8/19", message)
        self.assertIn("水費計費區間", message)
        self.assertIn("電費計費區間", message)
        self.assertIn("6/17", message)
        self.assertIn("8/18", message)

    def test_electricity_period_omitted_when_not_provided(self):
        rooms, billing_end_date = load_rooms_config()
        summary = self._build_summary(rooms, billing_end_date)

        message = generate_line_message(
            summary,
            water_period_start=date(2026, 6, 19),
            water_period_end=billing_end_date,
            remittance_account="1234-5678-9999",
            due_date=date(2026, 8, 25),
        )

        self.assertNotIn("電費計費區間", message)


if __name__ == "__main__":
    unittest.main()
