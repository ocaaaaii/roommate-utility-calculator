"""室友水電分帳試算 APP（Streamlit 前端）。

對應 skill.md 階段二、三：表單輸入 + 分帳結果表格 + 一鍵複製 LINE 通知文字。
"""
from datetime import date
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
# 資料
# ---------------------------------------------------------------------------
rooms, default_billing_end_date = load_rooms_config()

st.markdown("# 葫洲美好際寓 水電分帳試算")
st.caption("透天厝分租套房水電分帳小工具 — 電費依用電量分攤，水費依天數與人數加權分攤。")

st.markdown("---")

with st.form("bill_form"):
    st.markdown('<span class="section-tag">帳單金額</span>', unsafe_allow_html=True)
    col1, col1b, col2, col3 = st.columns(4)
    with col1:
        total_electricity_bill = st.number_input(
            "台電總電費 (元)", min_value=0.0, step=100.0, value=0.0
        )
    with col1b:
        unit_price = st.number_input(
            "當期每度電價 (元/度)", min_value=0.0, step=0.1, value=0.0,
            help="以台電帳單上的每度電價填入，用來計算各房自家用電費用；"
            "總電費扣除各房自家用電費後的公電（公共用電）將依人數比例分攤。",
        )
    with col2:
        total_water_base_fee = st.number_input(
            "台水總基本費 (元)", min_value=0.0, step=10.0, value=0.0
        )
    with col3:
        total_water_usage_fee = st.number_input(
            "台水總用水費（含水源保育費）(元)", min_value=0.0, step=100.0, value=0.0
        )

    st.markdown('<span class="section-tag">計費區間</span>', unsafe_allow_html=True)
    st.caption(
        "上次帳單（水費繳費期限 2026-07-08，金額 754；電費繳費期限 2026-07-14，金額 2274）"
        "由房東支付。初始抄表日期為 7/3，室友分帳將從本期（7/3 ~ 8/19）開始試算。"
    )
    col4, col5 = st.columns(2)
    with col4:
        billing_period_start = st.date_input(
            "本期帳單起始日（上次抄表日）", value=date(2026, 7, 3)
        )
    with col5:
        billing_end_date = st.date_input(
            "本期抄表截止日", value=default_billing_end_date
        )

    st.markdown('<span class="section-tag">6 間房新電表度數</span>', unsafe_allow_html=True)
    new_readings = {}
    reading_cols = st.columns(3)
    for i, (room_id, room) in enumerate(rooms.items()):
        with reading_cols[i % 3]:
            new_readings[room_id] = st.number_input(
                f"{room_id} {room.name}（初始 {room.initial_reading:g}）",
                min_value=float(room.initial_reading),
                value=float(room.initial_reading),
                step=1.0,
                key=f"reading_{room_id}",
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
    full_period_days = (billing_end_date - billing_period_start).days + 1
    summary = calculate_all(
        rooms,
        billing_end_date,
        new_readings,
        unit_price,
        total_electricity_bill,
        total_water_base_fee,
        total_water_usage_fee,
        full_period_days,
        billing_period_start,
    )
    st.session_state["summary"] = summary
    st.session_state["billing_period_start"] = billing_period_start
    st.session_state["billing_end_date"] = billing_end_date
    st.session_state["remittance_account"] = remittance_account
    st.session_state["due_date"] = due_date

if "summary" in st.session_state:
    summary = st.session_state["summary"]
    landlord_absorbed = summary.get("__landlord_absorbed_base_fee__", 0.0)
    public_electricity = summary.get("__public_electricity__", 0.0)

    st.markdown("## 分帳結果")
    st.caption(
        f"本期公電（公共用電）總額約 ${public_electricity:,.0f}，"
        "已依各房人數比例分攤進電費欄位；水費與電費最終金額四捨五入到整數元。"
    )

    rows = [
        {
            "房號": room_id,
            "室友": info["name"],
            "人數": info["headcount"],
            "入住天數": info["occupied_days"],
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
        billing_start_date=st.session_state["billing_period_start"],
        billing_end_date=st.session_state["billing_end_date"],
        remittance_account=st.session_state["remittance_account"],
        due_date=st.session_state["due_date"],
    )
    st.code(message, language=None)
