import streamlit as st
import easyocr
import pandas as pd
import numpy as np
import cv2
from PIL import Image
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. ตั้งค่าหน้าเว็บ ---
st.set_page_config(page_title="GPS OCR Pro", page_icon="📍", layout="wide")
st.title("📍 ระบบอ่านพิกัดและแยกที่อยู่ลง Cloud (Pro)")

# --- 2. ฟังก์ชันเชื่อมต่อ Google Sheets ---
def connect_to_gsheet():
    try:
        if "gcp_service_account" in st.secrets:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds_dict = st.secrets["gcp_service_account"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            # ต้องสร้าง Sheet ชื่อ 'GPS_Database' รอไว้
            sheet = client.open("GPS_Database").sheet1
            return sheet
        st.warning("⚠️ ไม่พบ Secrets 'gcp_service_account' ระบบจะทำงานในโหมดออฟไลน์ (ไม่บันทึก)", icon="⚠️")
        return None
    except Exception as e:
        st.error(f"การเชื่อมต่อ Google Sheet ล้มเหลว: {e}")
        return None

# --- 3. ฟังก์ชัน OCR และ Parser ---
@st.cache_resource
def load_reader():
    # ใช้ 'en' เป็นหลักเพื่อให้การอ่านตัวเลขและพิกัดเสถียรขึ้น
    return easyocr.Reader(['en'], gpu=False)

reader = load_reader()

def extract_address_components(text):
    """ฟังก์ชันสำหรับแยกส่วนประกอบที่อยู่จากข้อความดิบ (ปรับปรุง Thai Regex)"""
    text = text.replace("\n", " ").replace("  ", " ")
    data = {
        "house_no": "", "moo": "", "road": "", 
        "tambon": "", "amphoe": "", "province": "", "zipcode": ""
    }
    
    # 1. หา จังหวัด
    prov_match = re.search(r'(จ\.|จังหวัด)\s*([ก-๙]+)', text)
    if prov_match: data['province'] = prov_match.group(2)

    # 2. หา อำเภอ
    amp_match = re.search(r'(อ\.|อำเภอ|เขต)\s*([ก-๙]+)', text)
    if amp_match: data['amphoe'] = amp_match.group(2)

    # 3. หา ตำบล
    # จับ ต. ตามด้วยชื่อภาษาไทย
    tam_match = re.search(r'(ต\.|ตำบล|แขวง)\s*([ก-๙]+)', text)
    if tam_match: data['tambon'] = tam_match.group(2)
    
    # 4. หา หมู่
    # จับ ม. ตามด้วยตัวเลขเท่านั้น
    moo_match = re.search(r'(ม\.|หมู่)\.?\s*(\d+)', text)
    if moo_match: data['moo'] = moo_match.group(2)

    # 5. หา ถนน
    road_match = re.search(r'(ถ\.|ถนน)\s*([ก-๙a-zA-Z0-9\s]+?)', text)
    if road_match:
        road_name = road_match.group(2).strip()
        # ตัดคำนำหน้าตัวต่อไปที่อาจจะติดมาออก
        for marker in ['ต\.', 'ตำบล', 'แขวง', 'อ\.', 'อำเภอ', 'เขต', 'จ\.', 'จังหวัด', '\d{5}']:
            road_name = re.sub(f'{marker}.*$', '', road_name).strip()
        data['road'] = road_name

    # 6. หา บ้านเลขที่ (ตัวเลขที่อยู่หน้าคำว่า ม. หรือ ต. หรือตัวเลขชุดแรก)
    # 168 ม.4 ต.โรงช้าง
    house_match = re.search(r'(\d+/\d+|\d+)(?=\s+(ม\.|ต\.|ถ\.))', text)
    if house_match: 
        data['house_no'] = house_match.group(1)
    else:
        # Fallback: หาตัวเลขชุดแรกที่อยู่หน้า ม. หรือ ต.
        first_num = re.search(r'^\s*(\d+)\s', text)
        if first_num:
            data['house_no'] = first_num.group(1)
            
    # 7. หา รหัสไปรษณีย์
    zip_match = re.search(r'\b\d{5}\b', text)
    if zip_match: data['zipcode'] = zip_match.group(0)
           
    return data

# --- 4. ส่วนอัปโหลดและประมวลผล (รองรับหลายไฟล์ + แก้ไขข้อมูล) ---
uploaded_files = st.file_uploader(
    "📸 อัปโหลดภาพถ่าย (เลือกหลายไฟล์ได้)", 
    type=['jpg', 'png', 'jpeg'], 
    accept_multiple_files=True
)

if uploaded_files:
    st.subheader("📋 ผลการประมวลผลและแก้ไขข้อมูล")
    
    for i, uploaded_file in enumerate(uploaded_files):
        with st.expander(f"🖼️ ไฟล์: {uploaded_file.name}", expanded=True):
            
            # 1. ประมวลผล OCR และพิกัด
            with st.spinner(f'กำลังแกะรอยพิกัดจาก {uploaded_file.name}...'):
                image = Image.open(uploaded_file)
                img_np = np.array(image)
                
                result = reader.readtext(img_np, detail=0)
                full_text = " ".join(result)
                
                # Clean text และทำให้เป็นตัวพิมพ์เล็กทั้งหมด
                clean_text = full_text.replace("`", "°").replace("'", "°").replace(",", " ").lower()
                clean_text = re.sub(r'\s+', ' ', clean_text) 
                
                lat, long = None, None
                
                # --- แก้ไข Logic การหาพิกัดโดยใช้ N และ E ---
                # ค้นหาตัวเลขทศนิยมที่ตามด้วย n (North)
                lat_match = re.search(r"(\d+\.\d+).*?n", clean_text)
                if lat_match:
                    try:
                        lat = float(lat_match.group(1))
                        # กรองค่า Lat สำหรับไทย (5.0 ถึง 21.0)
                        if not (5.0 <= lat <= 21.0): lat = None
                    except ValueError:
                        lat = None

                # ค้นหาตัวเลขทศนิยมที่ตามด้วย e (East)
                long_match = re.search(r"(\d+\.\d+).*?e", clean_text)
                if long_match:
                    try:
                        long = float(long_match.group(1))
                         # กรองค่า Lon สำหรับไทย (97.0 ถึง 106.0)
                        if not (97.0 <= long <= 106.0): long = None
                    except ValueError:
                        long = None
                # --- จบการแก้ไข Logic การหาพิกัด ---

                addr_data = extract_address_components(full_text)
            
            # 2. แสดงภาพและแผนที่
            col_img, col_map = st.columns([1, 1])
            with col_img:
                st.image(image, caption='ภาพต้นฉบับ', use_container_width=True)
            
            if lat and long:
                map_data = pd.DataFrame({'lat': [lat], 'lon': [long]})
                with col_map:
                    st.map(map_data, zoom=15)
                
                # 3. ส่วนแก้ไขข้อมูล (ใช้ st.form)
                with st.form(key=f'form_{i}'):
                    st.markdown("---")
                    st.write("**📝 แก้ไข/ยืนยันข้อมูลที่ OCR อ่านได้**")
                    
                    c1, c2, c3, c4, c5 = st.columns(5)
                    
                    edited_lat = c1.number_input("Latitude", value=lat, format="%.6f", key=f'lat_{i}')
                    edited_long = c2.number_input("Longitude", value=long, format="%.6f", key=f'lon_{i}')
                    
                    edited_house = c1.text_input("บ้านเลขที่", addr_data['house_no'], key=f'hn_{i}')
                    edited_moo = c2.text_input("หมู่", addr_data['moo'], key=f'moo_{i}')
                    edited_road = c3.text_input("ถนน", addr_data['road'], key=f'road_{i}')
                    edited_tambon = c4.text_input("ตำบล/แขวง", addr_data['tambon'], key=f'tambon_{i}')
                    edited_amphoe = c5.text_input("อำเภอ/เขต", addr_data['amphoe'], key=f'amphoe_{i}')
                    edited_province = c4.text_input("จังหวัด", addr_data['province'], key=f'province_{i}')
                    edited_zip = c5.text_input("รหัสไปรษณีย์", addr_data['zipcode'], key=f'zip_{i}')

                    save_button = st.form_submit_button(label='💾 ยืนยันและบันทึกลง Database', type="primary")

                    if save_button:
                        sheet = connect_to_gsheet()
                        if sheet:
                            google_map_link = f"https://www.google.com/maps?q={edited_lat},{edited_long}"
                            new_row = [
                                str(pd.Timestamp.now()),
                                edited_lat, edited_long,
                                edited_house, edited_moo, edited_road, 
                                edited_tambon, edited_amphoe, edited_province, 
                                edited_zip,
                                google_map_link,
                                uploaded_file.name
                            ]
                            sheet.append_row(new_row)
                            st.success(f"บันทึกไฟล์ **{uploaded_file.name}** เรียบร้อย!")
                        else:
                            st.error("ไม่สามารถบันทึกได้เนื่องจาก Google Sheet ไม่พร้อมใช้งาน")
            else:
                st.error("❌ ไม่พบพิกัด GPS ที่ชัดเจนในภาพนี้")
                with st.expander("ข้อความที่อ่านได้ทั้งหมด"):
                    st.write(full_text)
            st.markdown("---")

# --- 5. ส่วนค้นหาข้อมูลประวัติ (Live Search & Cascading Filter) ---

st.divider()
st.subheader("📊 ฐานข้อมูลที่บันทึกไว้ และระบบค้นหา")

sheet = connect_to_gsheet()
if sheet:
    if st.checkbox("แสดงและกรองข้อมูลทั้งหมด"):
        try:
            data = sheet.get_all_records()
            df = pd.DataFrame(data)

            if df.empty:
                st.info("ยังไม่มีข้อมูลในฐานข้อมูล")
            else:
                # เตรียมข้อมูลสำหรับ Filter
                for col in ['บ้านเลขที่', 'ตำบล', 'อำเภอ', 'หมู่']:
                    if col in df.columns: df[col] = df[col].astype(str).fillna('')
                
                df_filtered = df.copy()

                # 1. Live Search (บ้านเลขที่)
                search_term = st.text_input("🔍 ค้นหาบ้านเลขที่", "")
                if search_term:
                    df_filtered = df_filtered[df_filtered['บ้านเลขที่'].str.contains(search_term, case=False, na=False)]

                # 2. Cascading Filter
                col_a, col_t, col_m = st.columns(3)

                # A. กรองอำเภอ
                unique_amphoe = ['ทั้งหมด'] + sorted(df['อำเภอ'].unique().tolist())
                selected_amphoe = col_a.selectbox("เลือกอำเภอ/เขต", unique_amphoe)
                
                if selected_amphoe != 'ทั้งหมด':
                    df_filtered = df_filtered[df_filtered['อำเภอ'] == selected_amphoe]

                # B. กรองตำบล
                unique_tambon = ['ทั้งหมด'] + sorted(df_filtered['ตำบล'].unique().tolist())
                selected_tambon = col_t.selectbox("เลือกตำบล/แขวง", unique_tambon)
                
                if selected_tambon != 'ทั้งหมด':
                    df_filtered = df_filtered[df_filtered['ตำบล'] == selected_tambon]

                # C. กรองหมู่
                unique_moo = ['ทั้งหมด'] + sorted(df_filtered['หมู่'].unique().tolist())
                selected_moo = col_m.selectbox("เลือกหมู่บ้าน", unique_moo)
                
                if selected_moo != 'ทั้งหมด':
                    df_filtered = df_filtered[df_filtered['หมู่'] == selected_moo]

                # 3. แสดงผลลัพธ์
                st.dataframe(df_filtered, use_container_width=True)

        except Exception as e:
            st.error(f"เกิดข้อผิดพลาดในการดึงข้อมูลหรือกรองข้อมูล: {e}")
