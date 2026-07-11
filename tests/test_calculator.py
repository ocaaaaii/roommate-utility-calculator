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
        """3A 吳小姐從 7/9 入住到 8/19 應為 41 天（skill.md 第4節驗收標準）。"""
        move_in_date = date(2026, 7, 9)
        billing_end_date = date(2026, 8, 19)
        self.assertEqual(calculate_occupied_days(move_in_date, billing_end_date), 41)

    def test_3a_actual_days_matches_config(self):
        rooms, billing_end_date = load_rooms_config()
        room_3a = rooms["3A"]
        days = calculate_occupied_days(room_3a.move_in_date, billing_end_date)
        self.assertEqual(days, 41)


class TestElectricitySplit(unittest.TestCase):
    def setUp(self):
        # 三間房，用電量分別為 50 / 60 / 90 度，方便手算驗證單價與金額。
        self.rooms = {
            "A": Room("A", "Room A", initial_reading=100, move_in_date=date(2026, 1, 1), headcount=1),
            "B": Room("B", "Room B", initial_reading=200, move_in_date=date(2026, 1, 1), headcount=1),
            "C": Room("C", "Room C", initial_reading=300, move_in_date=date(2026, 1, 1), headcount=2),
        }
        self.new_readings = {"A": 150, "B": 260, "C": 390}

    def test_usage_and_unit_price(self):
        result = calculate_electricity(self.rooms, self.new_readings, total_electricity_bill=2000.0)

        self.assertEqual(result["A"]["usage_kwh"], 50)
        self.assertEqual(result["B"]["usage_kwh"], 60)
        self.assertEqual(result["C"]["usage_kwh"], 90)
        # 總用電量 200 度，總電費 2000 元 -> 單價 10 元/度
        self.assertAlmostEqual(result["A"]["unit_price"], 10.0)

    def test_fees_sum_to_total_bill(self):
        result = calculate_electricity(self.rooms, self.new_readings, total_electricity_bill=2000.0)

        self.assertAlmostEqual(result["A"]["electricity_fee"], 500.0)
        self.assertAlmostEqual(result["B"]["electricity_fee"], 600.0)
        self.assertAlmostEqual(result["C"]["electricity_fee"], 900.0)

        total_fees = sum(info["electricity_fee"] for info in result.values())
        self.assertAlmostEqual(total_fees, 2000.0, places=2)


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

        # 每房平分後為 100 元，再依天數比例分攤
        self.assertAlmostEqual(result["A"]["water_base_fee"], 33.33, places=2)
        self.assertAlmostEqual(result["B"]["water_base_fee"], 66.67, places=2)
        self.assertAlmostEqual(result["C"]["water_base_fee"], 100.0, places=2)
        self.assertAlmostEqual(result["D"]["water_base_fee"], 50.0, places=2)
        self.assertAlmostEqual(result["E"]["water_base_fee"], 83.33, places=2)
        self.assertAlmostEqual(result["F"]["water_base_fee"], 16.67, places=2)

    def test_landlord_absorbs_unoccupied_base_fee_and_total_is_conserved(self):
        result = calculate_water_base_fee(
            self.rooms, self.occupied_days, self.total_base_fee, self.full_period_days
        )

        landlord_absorbed = result["__landlord_absorbed__"]
        rooms_fee_total = sum(
            info["water_base_fee"]
            for room_id, info in result.items()
            if room_id != "__landlord_absorbed__"
        )

        # 驗收標準：室友應繳總額 + 房東吸收金額 = 基本費帳單總額
        self.assertAlmostEqual(rooms_fee_total + landlord_absorbed, self.total_base_fee, places=1)


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
        self.assertAlmostEqual(result["D"]["usage_fee"], result["C"]["usage_fee"], places=2)
        self.assertAlmostEqual(result["D"]["usage_fee"], 300.0, places=2)

    def test_5e_gets_higher_weight_than_equal_headcount_shorter_stay(self):
        """5E（雙人、25天）應比同為雙人但天數較少的房間分攤更多用水費。"""
        result = calculate_water_usage_fee(self.rooms, self.occupied_days, self.total_usage_fee)

        self.assertGreater(result["E"]["weight"], result["D"]["weight"])
        self.assertGreater(result["E"]["usage_fee"], result["D"]["usage_fee"])
        self.assertAlmostEqual(result["E"]["usage_fee"], 500.0, places=2)

    def test_fees_sum_to_total_usage_fee(self):
        result = calculate_water_usage_fee(self.rooms, self.occupied_days, self.total_usage_fee)

        total_fees = sum(info["usage_fee"] for info in result.values())
        self.assertAlmostEqual(total_fees, self.total_usage_fee, places=2)


class TestCalculateAllConservation(unittest.TestCase):
    def test_total_conservation_across_real_rooms_config(self):
        """整合測試：室友應繳總額 + 房東吸收的基本費 = 電費+水費(基本費+用水費)帳單總額。"""
        rooms, billing_end_date = load_rooms_config()

        new_readings = {
            "3A": 493 + 100,
            "3B": 1069 + 200,
            "4C": 4582 + 300,
            "4D": 541 + 150,
            "5E": 4180 + 400,
            "6F": 3575 + 120,
        }
        total_electricity_bill = 5000.0
        total_water_base_fee = 1200.0
        total_water_usage_fee = 3000.0
        full_period_days = 90  # 假設本期帳單完整計費區間長度，需依實際台水帳單調整

        summary = calculate_all(
            rooms,
            billing_end_date,
            new_readings,
            total_electricity_bill,
            total_water_base_fee,
            total_water_usage_fee,
            full_period_days,
        )

        landlord_absorbed = summary.pop("__landlord_absorbed_base_fee__")
        rooms_total = sum(info["total"] for info in summary.values())
        bill_total = total_electricity_bill + total_water_base_fee + total_water_usage_fee

        self.assertAlmostEqual(rooms_total + landlord_absorbed, bill_total, places=1)

        # 4D、5E 為雙人房，驗證其用水費確實反映了較高的人數權重（而非只看天數）。
        self.assertGreater(summary["4D"]["water_usage_fee"], 0)
        self.assertGreater(summary["5E"]["water_usage_fee"], 0)


class TestGenerateLineMessage(unittest.TestCase):
    def test_message_contains_every_room_and_matches_totals(self):
        rooms, billing_end_date = load_rooms_config()
        new_readings = {
            "3A": 493 + 100,
            "3B": 1069 + 200,
            "4C": 4582 + 300,
            "4D": 541 + 150,
            "5E": 4180 + 400,
            "6F": 3575 + 120,
        }
        summary = calculate_all(
            rooms,
            billing_end_date,
            new_readings,
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
