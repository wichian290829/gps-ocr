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
st.set_page_config(page_title="GPS OCR Pro", page_icon="📍", layout="centered")
st.title("📍 ระบบอ่านพิกัดและแยกที่อยู่ลง Cloud")

# --- 2. ฟังก์ชันช่วยแยกที่อยู่ (Address Parser) ---
def extract_address_components(text):
    """
    ฟังก์ชันสำหรับแยกส่วนประกอบที่อยู่จากข้อความดิบ
    คืนค่าเป็น Dictionary
    """
    # ลบตัวอักษรพิเศษและจัดระเบียบข้อความ
    text = text.replace("\n", " ").replace("  ", " ")
    data = {
        "house_no": "",
        "moo": "",
        "road": "",
        "tambon": "",
        "amphoe": "",
        "province": "",
        "zipcode": ""
    }
    
    # 1. หา รหัสไปรษณีย์ (5 หลัก)
    zip_match = re.search(r'\b\d{5}\b', text)
    if zip_match:
        data['zipcode'] = zip_match.group(0)

    # 2. หา จังหวัด (จ. หรือ จังหวัด)
    prov_match = re.search(r'(จ\.|จังหวัด)\s*([ก-๙]+)', text)
    if prov_match: data['province'] = prov_match.group(2)

    # 3. หา อำเภอ (อ. | อำเภอ | เขต)
    amp_match = re.search(r'(อ\.|อำเภอ|เขต)\s*([ก-๙]+)', text)
    if amp_match: data['amphoe'] = amp_match.group(2)

    # 4. หา ตำบล (ต. | ตำบล | แขวง)
    tam_match = re.search(r'(ต\.|ตำบล|แขวง)\s*([ก-๙]+)', text)
    if tam_match: data['tambon'] = tam_match.group(2)

    # 5. หา ถนน (ถ. | ถนน)
    road_match = re.search(r'(ถ\.|ถนน)\s*([ก-๙a-zA-Z0-9\s]+?)(?=\s(?:ต\.|แขวง|อ\.|เขต|จ\.|จังหวัด|$))', text)
    if road_match: 
        data['road'] = road_match.group(2).strip()

    # 6. หา หมู่ (ม. | หมู่)
    moo_match = re.search(r'(ม\.|หมู่)\.?\s*(\d+)', text)
    if moo_match: data['moo'] = moo_match.group(2)

    # 7. หา บ้านเลขที่ (ยากที่สุด เพราะรูปแบบเยอะ)
    # หาตัวเลขที่มี / หรือตัวเลขต้นประโยค
    house_match = re.search(r'(\d+/\d+|\d+(?=\s+ม\.))', text)
    if house_match:
        data['house_no'] = house_match.group(0)
    elif not data['house_no']: 
        # Fallback: หาตัวเลขชุดแรกที่เจอ
        first_num = re.search(r'^\D*(\d+)', text)
        if first_num:
           data['house_no'] = first_num.group(1) 
           
    return data

# --- 3. ฟังก์ชันเชื่อมต่อ Google Sheets ---
def connect_to_gsheet():
    try:
        if "gcp_service_account" in st.secrets:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds_dict = st.secrets["gcp_service_account"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            sheet = client.open("GPS_Database").sheet1
            return sheet
        return None
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return None

# --- 4. โหลด OCR ---
@st.cache_resource
def load_reader():
    return easyocr.Reader(['th', 'en'], gpu=False)

reader = load_reader()

# --- 5. ส่วน UI หลัก ---
uploaded_file = st.file_uploader("📸 อัปโหลดภาพ", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, caption='Source Image', use_container_width=True)
    
    if st.button('🚀 ประมวลผล'):
        with st.spinner('กำลังแกะรอย...'):
            img_np = np.array(image)
            result = reader.readtext(img_np, detail=0)
            full_text = " ".join(result)
            
            # --- ส่วน OCR พิกัด (เหมือนเดิม) ---
            clean_text = full_text.replace("`", " ").replace("'", " ").replace(",", " ").lower()
            potential_floats = re.findall(r"(\d{1,3}\.\d{4,})", clean_text)
            lat, long = None, None
            for num_str in potential_floats:
                try:
                    val = float(num_str)
                    if 5.0 <= val <= 21.0 and lat is None: lat = val
                    elif 97.0 <= val <= 106.0 and long is None: long = val
                except: continue
            
            # Fallback coordinate logic
            if (lat is None or long is None) and len(potential_floats) >= 2:
                lat, long = float(potential_floats[0]), float(potential_floats[1])

            # --- ส่วน OCR ที่อยู่ (ใหม่!) ---
            addr_data = extract_address_components(full_text)

            # แสดงผล
            if lat and long:
                st.success("✅ อ่านข้อมูลสำเร็จ")
                
                # แสดงข้อมูลที่แกะได้
                c1, c2 = st.columns(2)
                with c1:
                    st.metric("Latitude", lat)
                    st.text_input("บ้านเลขที่", addr_data['house_no'])
                    st.text_input("ถนน", addr_data['road'])
                    st.text_input("อำเภอ/เขต", addr_data['amphoe'])
                with c2:
                    st.metric("Longitude", long)
                    st.text_input("หมู่", addr_data['moo'])
                    st.text_input("ตำบล/แขวง", addr_data['tambon'])
                    st.text_input("จังหวัด", addr_data['province'])

                google_map_link = f"https://www.google.com/maps?q={lat},{long}"
                
                # บันทึก
                sheet = connect_to_gsheet()
                if sheet:
                    new_row = [
                        str(pd.Timestamp.now()),
                        lat,
                        long,
                        addr_data['house_no'],  # Col 4
                        addr_data['moo'],       # Col 5
                        addr_data['road'],      # Col 6
                        addr_data['tambon'],    # Col 7
                        addr_data['amphoe'],    # Col 8
                        addr_data['province'],  # Col 9
                        addr_data['zipcode'],   # Col 10
                        google_map_link,
                        uploaded_file.name
                    ]
                    sheet.append_row(new_row)
                    st.toast("บันทึกลงตารางแยกคอลัมน์เรียบร้อย!", icon="💾")
            else:
                st.error("ไม่พบพิกัด GPS ในภาพ")
                st.write("ข้อความที่อ่านได้:", full_text)
