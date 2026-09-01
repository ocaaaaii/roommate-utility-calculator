"""室友水電分帳試算 APP（Streamlit 前端）。

對應 skill.md 階段二、三：表單輸入 + 分帳結果表格 + 一鍵複製 LINE 通知文字。
"""
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src.calculator import calculate_all, generate_line_message, load_rooms_config

QR_CODE_PATH = Path(__file__).resolve().parent / "img" / "bank_account.jpg"

st.set_page_config(page_title="葫洲美好際寓 水電分帳試算", layout="wide")

# ---------------------------------------------------------------------------
# 配色：莫蘭迪綠底色 + 米色卡片 + 灰棕色文字
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    :root {
        --morandi-green: #8A9A82;
        --morandi-green-deep: #6B7A63;
        --beige: #F7F2E6;
        --beige-soft: #FBF8F1;
        --gray-brown: #5C4F42;
        --gray-brown-deep: #4A4038;
        --border-soft: #D8CDB8;
    }

    .stApp {
        background-color: var(--morandi-green);
    }

    section[data-testid="stSidebar"] {
        background-color: var(--beige);
        border-right: 1px solid var(--border-soft);
    }

    /* 頁面層級的標題與說明文字，直接顯示在綠色底色上 */
    h1, h2, h3, h4, h5 {
        color: var(--beige-soft) !important;
        font-weight: 700 !important;
    }

    div[data-testid="stCaptionContainer"], .stApp > div p {
        color: var(--beige-soft);
    }

    hr {
        border-color: var(--border-soft) !important;
    }

    /* 卡片式容器：表單 */
    div[data-testid="stForm"] {
        background-color: var(--beige);
        border: 1px solid var(--border-soft);
        border-radius: 16px;
        padding: 1.5rem 1.5rem 0.5rem 1.5rem;
    }

    /* 表單內的文字改回灰棕色，因為底色變成米色 */
    div[data-testid="stForm"] label,
    div[data-testid="stForm"] p,
    div[data-testid="stForm"] span {
        color: var(--gray-brown-deep) !important;
    }

    div[data-testid="stMetric"] {
        background-color: var(--beige);
        border: 1px solid var(--border-soft);
        border-radius: 14px;
        padding: 0.9rem 1.2rem;
    }

    /* 用萬用選擇器涵蓋卡片內所有子元素（含 label、標題標籤等），避免被全域 h1-h5 規則蓋掉 */
    div[data-testid="stMetric"],
    div[data-testid="stMetric"] * {
        color: var(--gray-brown) !important;
    }

    div[data-testid="stMetric"] div[data-testid="stMetricValue"],
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] * {
        color: var(--morandi-green-deep) !important;
    }

    /* 輸入元件 */
    .stNumberInput input, .stTextInput input, .stDateInput input {
        background-color: #FFFFFF;
        border: 1px solid var(--border-soft) !important;
        border-radius: 8px !important;
        color: var(--gray-brown-deep) !important;
    }

    /* 按鈕 */
    .stButton > button, .stFormSubmitButton > button {
        background-color: var(--morandi-green-deep);
        color: #FFFFFF;
        border: none;
        border-radius: 10px;
        padding: 0.5rem 1.6rem;
        font-weight: 600;
        transition: background-color 0.2s ease-in-out;
    }
    .stButton > button:hover, .stFormSubmitButton > button:hover {
        background-color: var(--gray-brown-deep);
        color: #FFFFFF;
    }

    /* 表格 */
    div[data-testid="stDataFrame"] {
        border: 1px solid var(--border-soft);
        border-radius: 12px;
        overflow: hidden;
    }

    /* LINE 通知文字區塊，改成米色系而非預設深色 */
    div[data-testid="stCodeBlock"] pre {
        background-color: var(--beige-soft) !important;
        border: 1px solid var(--border-soft);
        border-radius: 10px;
    }
    div[data-testid="stCodeBlock"] code {
        color: var(--gray-brown-deep) !important;
    }

    /* 分隔用的小標籤 */
    .section-tag {
        display: inline-block;
        background-color: var(--morandi-green-deep);
        color: white;
        padding: 0.15rem 0.7rem;
        border-radius: 999px;
        font-size: 0.85rem;
        font-weight: 600;
        margin-bottom: 0.4rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# 歷史紀錄（下載/上傳 JSON 檔案；app 重新部署時伺服器上的資料不會保留）
# ---------------------------------------------------------------------------
def _period_key(record: dict) -> tuple:
    return (
        record["water_period_start"],
        record["water_period_end"],
        record["electricity_period_start"],
        record["electricity_period_end"],
    )


def _merge_history(records: list[dict]) -> None:
    """把 records 併入 st.session_state['history']，同一期（四個期間欄位相同）會被覆蓋更新，不會重複累加。"""
    existing = {_period_key(r): i for i, r in enumerate(st.session_state["history"])}
    for r in records:
        key = _period_key(r)
        if key in existing:
            st.session_state["history"][existing[key]] = r
        else:
            st.session_state["history"].append(r)
            existing[key] = len(st.session_state["history"]) - 1


if "history" not in st.session_state:
    st.session_state["history"] = []

# ---------------------------------------------------------------------------
# 資料
# ---------------------------------------------------------------------------
rooms, _ = load_rooms_config()

st.markdown("# 葫洲美好際寓 水電分帳試算")
st.caption("透天厝分租套房水電分帳小工具 — 電費依用電量分攤，水費依天數與人數加權分攤。")

st.markdown('<span class="section-tag">歷史紀錄</span>', unsafe_allow_html=True)
st.caption(
    "app 重新部署或閒置太久重啟時，伺服器上的資料會清空，歷史紀錄請自行下載保存。"
    "下次要接續使用時，把上次下載的檔案上傳回來，之前算過的期數就會全部帶回來。"
)
uploaded_history = st.file_uploader(
    "上傳先前下載的歷史紀錄（JSON，選填）", type="json", key="history_upload"
)
if uploaded_history is not None:
    try:
        loaded = json.load(uploaded_history)
        _merge_history(loaded.get("records", []))
    except (json.JSONDecodeError, AttributeError, KeyError):
        st.error("這個檔案看起來不是有效的歷史紀錄 JSON，請確認上傳的檔案內容。")

st.markdown("---")

with st.form("bill_form"):
    st.markdown('<span class="section-tag">房客異動</span>', unsafe_allow_html=True)
    st.caption(
        "平常不需要打開這個區塊。只有在有人搬入/搬出、換人、或人數變動時才需要調整；"
        "其餘每期照常只要填下面的帳單金額與電表度數就好。"
    )
    with st.expander("展開以修改房客資料（簽約日／入住日／退租日／人數）"):
        room_overrides = {}
        for room_id, room in rooms.items():
            st.markdown(f"**{room_id}**")
            oc1, oc2, oc3, oc4 = st.columns(4)
            with oc1:
                o_contract_date = st.date_input(
                    "簽約日", value=room.contract_date, key=f"contract_{room_id}"
                )
            with oc2:
                o_move_in_date = st.date_input(
                    "實際入住日", value=room.move_in_date, key=f"movein_{room_id}"
                )
            with oc3:
                o_move_out_date = st.date_input(
                    "退租日（尚未退租可留原值）",
                    value=room.move_out_date,
                    key=f"moveout_{room_id}",
                )
            with oc4:
                o_headcount = st.number_input(
                    "人數", min_value=1, max_value=4, value=room.headcount,
                    step=1, key=f"headcount_{room_id}",
                )
            room_overrides[room_id] = {
                "contract_date": o_contract_date,
                "move_in_date": o_move_in_date,
                "move_out_date": o_move_out_date,
                "headcount": o_headcount,
            }

    st.markdown('<span class="section-tag">電費資訊</span>', unsafe_allow_html=True)
    col_e1, col_e2, col_e3 = st.columns(3)
    with col_e1:
        electricity_period_start = st.date_input(
            "電費帳單期間開始", value=date(2026, 6, 17)
        )
    with col_e2:
        electricity_period_end = st.date_input(
            "電費帳單期間結束", value=date(2026, 8, 18)
        )
    with col_e3:
        meter_reading_date = st.date_input("抄表日", value=date(2026, 8, 19))

    col_e4, col_e5 = st.columns(2)
    with col_e4:
        total_electricity_bill = st.number_input(
            "本期總電費 (元)", min_value=0.0, step=100.0, value=0.0
        )
    with col_e5:
        unit_price = st.number_input(
            "當期每度平均電價 (元/度)", min_value=0.0, step=0.1, value=0.0,
            help="以台電帳單上的每度電價填入，用來計算各房自家用電費用；"
            "總電費扣除各房自家用電費後的公電（公共用電）將依人數比例分攤。",
        )

    st.markdown('<span class="section-tag">水費資訊</span>', unsafe_allow_html=True)
    st.caption(
        "計費開始日／結束日請直接照台水帳單上「用水計費期間」欄位填寫；"
        "台水與台電是不同單位、抄表週期本來就不一樣，不需要對齊。"
    )
    col_w1, col_w2 = st.columns(2)
    with col_w1:
        water_period_start = st.date_input("計費開始日", value=date(2026, 6, 9))
    with col_w2:
        water_period_end = st.date_input("計費結束日", value=date(2026, 8, 5))

    col_w3, col_w4, col_w5 = st.columns(3)
    with col_w3:
        total_water_base_fee = st.number_input(
            "基本費 (元)", min_value=0.0, step=10.0, value=0.0
        )
    with col_w4:
        water_usage_fee = st.number_input(
            "用水費 (元)", min_value=0.0, step=10.0, value=0.0
        )
    with col_w5:
        water_conservation_fee = st.number_input(
            "水源保育與回饋費 (元)", min_value=0.0, step=1.0, value=0.0
        )
    total_water_usage_fee = water_usage_fee + water_conservation_fee

    st.markdown('<span class="section-tag">6 間房電表度數</span>', unsafe_allow_html=True)
    st.caption(
        "「起始度數」預設帶入上一期存的度數，若你手邊記的是上一期期末度數，直接覆蓋成那個數字即可；"
        "這樣下一期也只要換數字繼續用，不需要改設定檔。"
    )
    initial_readings = {}
    new_readings = {}
    reading_cols = st.columns(3)
    for i, (room_id, room) in enumerate(rooms.items()):
        with reading_cols[i % 3]:
            headcount_note = f"（{room_overrides[room_id]['headcount']}人）" if room_overrides[room_id]["headcount"] == 2 else ""
            st.markdown(f"{room_id}{headcount_note}")
            initial_readings[room_id] = st.number_input(
                "起始度數", min_value=0.0, value=float(room.initial_reading),
                step=1.0, key=f"initial_{room_id}",
            )
            new_readings[room_id] = st.number_input(
                "期末度數", min_value=float(initial_readings[room_id]),
                value=float(initial_readings[room_id]),
                step=1.0, key=f"reading_{room_id}",
            )

    st.markdown('<span class="section-tag">通知訊息設定</span>', unsafe_allow_html=True)
    col6, col7 = st.columns(2)
    with col6:
        remittance_account = st.text_input(
            "匯款帳號", value="中國信託(822) 274540384326"
        )
    with col7:
        due_date = st.date_input("匯款截止日", value=date(2026, 8, 25))

    if QR_CODE_PATH.exists():
        st.image(str(QR_CODE_PATH), caption="中國信託銀行 轉帳 QR Code", width=220)

    submitted = st.form_submit_button("開始計算分帳")

if submitted:
    working_rooms = {
        room_id: replace(
            room,
            contract_date=room_overrides[room_id]["contract_date"],
            move_in_date=room_overrides[room_id]["move_in_date"],
            move_out_date=room_overrides[room_id]["move_out_date"],
            headcount=room_overrides[room_id]["headcount"],
        )
        for room_id, room in rooms.items()
    }
    summary = calculate_all(
        working_rooms,
        initial_readings,
        new_readings,
        unit_price,
        total_electricity_bill,
        electricity_period_start,
        electricity_period_end,
        total_water_base_fee,
        total_water_usage_fee,
        water_period_start,
        water_period_end,
    )
    st.session_state["summary"] = summary
    st.session_state["water_period_start"] = water_period_start
    st.session_state["water_period_end"] = water_period_end
    st.session_state["electricity_period_start"] = electricity_period_start
    st.session_state["electricity_period_end"] = electricity_period_end
    st.session_state["meter_reading_date"] = meter_reading_date
    st.session_state["remittance_account"] = remittance_account
    st.session_state["due_date"] = due_date

    tenant_total = sum(
        info["total"] for room_id, info in summary.items() if not room_id.startswith("__")
    )
    record = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "water_period_start": water_period_start.isoformat(),
        "water_period_end": water_period_end.isoformat(),
        "electricity_period_start": electricity_period_start.isoformat(),
        "electricity_period_end": electricity_period_end.isoformat(),
        "meter_reading_date": meter_reading_date.isoformat(),
        "inputs": {
            "total_electricity_bill": total_electricity_bill,
            "unit_price": unit_price,
            "total_water_base_fee": total_water_base_fee,
            "water_usage_fee": water_usage_fee,
            "water_conservation_fee": water_conservation_fee,
            "initial_readings": initial_readings,
            "new_readings": new_readings,
        },
        "rooms": {
            room_id: {
                "contract_date": o["contract_date"].isoformat(),
                "move_in_date": o["move_in_date"].isoformat(),
                "move_out_date": o["move_out_date"].isoformat() if o["move_out_date"] else None,
                "headcount": o["headcount"],
            }
            for room_id, o in room_overrides.items()
        },
        "results": {
            room_id: {k: v for k, v in info.items() if k != "name"}
            for room_id, info in summary.items()
            if not room_id.startswith("__")
        },
        "totals": {
            "tenant_total": tenant_total,
            "landlord_absorbed": summary["__landlord_absorbed_base_fee__"],
            "public_electricity": summary["__public_electricity__"],
        },
    }
    _merge_history([record])

if "summary" in st.session_state:
    summary = st.session_state["summary"]
    landlord_absorbed = summary.get("__landlord_absorbed_base_fee__", 0.0)
    public_electricity = summary.get("__public_electricity__", 0.0)

    st.markdown("## 分帳結果")
    st.caption(
        f"本期公電（公共用電）總額約 ${public_electricity:,.0f}，"
        "已依各房「人數 x 電費計費天數」加權分攤進電費欄位；水費與電費最終金額四捨五入到整數元。"
    )

    rows = [
        {
            "房號": room_id,
            "人數": info["headcount"],
            "水費入住天數": info["occupied_days"],
            "電費計費天數": info["electricity_usage_days"],
            "用電度數": info["electricity_usage_kwh"],
            "電費": info["electricity_fee"],
            "水費": info["water_fee"],
            "總計": info["total"],
        }
        for room_id, info in summary.items()
        if not room_id.startswith("__")
    ]
    df = pd.DataFrame(rows)

    st.dataframe(
        df.style.format(
            {
                "電費": "${:,.0f}",
                "水費": "${:,.0f}",
                "總計": "${:,.0f}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.metric("室友應繳總額", f"${df['總計'].sum():,.0f}")
    with col_b:
        st.metric("房東吸收基本費（未租期間）", f"${landlord_absorbed:,.0f}")
    with col_c:
        st.metric(
            "帳單總額",
            f"${(df['總計'].sum() + landlord_absorbed):,.0f}",
        )

    st.markdown("## 一鍵複製 LINE 群組通知")
    message = generate_line_message(
        summary,
        water_period_start=st.session_state["water_period_start"],
        water_period_end=st.session_state["water_period_end"],
        remittance_account=st.session_state["remittance_account"],
        due_date=st.session_state["due_date"],
        electricity_period_start=st.session_state["electricity_period_start"],
        electricity_period_end=st.session_state["electricity_period_end"],
        meter_reading_date=st.session_state["meter_reading_date"],
    )
    st.code(message, language=None)

st.markdown("---")
st.markdown('<span class="section-tag">歷史紀錄</span>', unsafe_allow_html=True)
if st.session_state["history"]:
    history_rows = sorted(
        st.session_state["history"], key=lambda r: r["water_period_start"], reverse=True
    )
    history_df = pd.DataFrame(
        [
            {
                "水費期間": f"{r['water_period_start']} ~ {r['water_period_end']}",
                "電費期間": f"{r['electricity_period_start']} ~ {r['electricity_period_end']}",
                "室友應繳總額": r["totals"]["tenant_total"],
                "房東吸收": r["totals"]["landlord_absorbed"],
                "計算時間": r["recorded_at"],
            }
            for r in history_rows
        ]
    )
    st.dataframe(
        history_df.style.format({"室友應繳總額": "${:,.0f}", "房東吸收": "${:,.0f}"}),
        use_container_width=True,
        hide_index=True,
    )

    history_json = json.dumps(
        {"records": st.session_state["history"]}, ensure_ascii=False, indent=2
    )
    st.download_button(
        "下載歷史紀錄（JSON）",
        data=history_json,
        file_name="roommate_utility_history.json",
        mime="application/json",
    )
else:
    st.caption("目前沒有歷史紀錄。算完一期並送出後，這裡會自動出現該期的紀錄。")
