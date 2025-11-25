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
            # ต้องสร้าง Google Sheet ชื่อ 'GPS_Database' รอไว้
            sheet = client.open("GPS_Database").sheet1
            return sheet
        st.warning("⚠️ ไม่พบ Secrets 'gcp_service_account' ระบบจะทำงานในโหมดออฟไลน์ (ไม่บันทึก)")
        return None
    except Exception as e:
        st.error(f"การเชื่อมต่อ Google Sheet ล้มเหลว: {e}")
        return None

# --- 3. ฟังก์ชัน OCR และ Parser ---
@st.cache_resource
def load_reader():
    # ใช้ ['en', 'th'] เพื่อให้อ่านพิกัด (en) และที่อยู่ (th) ได้ดีขึ้น
    return easyocr.Reader(['en', 'th'], gpu=False)

reader = load_reader()

def extract_address_components(text):
    """ฟังก์ชันสำหรับแยกส่วนประกอบที่อยู่จากข้อความดิบ (V11: Hardcoded Province)"""
    # ทำความสะอาดข้อความเบื้องต้น
    text = text.replace("\n", " ").replace("  ", " ").strip()
    data = {
        "house_no": "", "moo": "", "road": "", 
        "tambon": "", "amphoe": "", "province": ""
    }
    
    # Regex Pattern สำหรับชื่อ: อนุญาตให้มี ไทย, ช่องว่าง, ตัวเลข, จุด, เครื่องหมายทับ (Non-greedy)
    name_pattern = r'([ก-๙\s\d\.\/]+?)'
    
    # 7. หา บ้านเลขที่ และ 5. หมู่ (ใช้ข้อความดิบเดิม)
    # Logic 1: หาตัวเลขที่มีเครื่องหมาย / หรือตัวเลขที่อยู่หน้า ม. หรือ ถ. หรือ ซ. 
    house_match = re.search(r'(\d+/\d+|\d+)(?=\s*(ม\.|ถ\.|ซ\.))', text)
    if house_match: 
        data['house_no'] = house_match.group(1).strip()
    else:
        # Logic 2: Fallback สำหรับบ้านเลขที่ที่มี / 
        house_match_slash = re.search(r'(\d+/\d+)', text)
        if house_match_slash:
            data['house_no'] = house_match_slash.group(1).strip()
        else:
             # Logic 3: Fallback สำหรับตัวเลขที่ขึ้นต้นประโยค
            house_match_start = re.match(r'^\s*(\d+)', text)
            if house_match_start and len(house_match_start.group(1)) <= 4 and re.search(r'\d+\.\d{3,}', text) is None:
                 data['house_no'] = house_match_start.group(1).strip()

    # 5. หา หมู่ (ต้องเป็น ม. ตามด้วยตัวเลขเท่านั้น)
    moo_match = re.search(r'(ม\.|หมู่)\.?\s*(\d+)', text)
    if moo_match: 
        data['moo'] = moo_match.group(2).strip()
        
    # --- เริ่มการประมวลผลส่วนที่เหลืออย่างมีลำดับความสำคัญ (จังหวัด(Hardcode) > อำเภอ > ตำบล > ถนน) ---
    working_text = text
    
    # 1. Hardcode จังหวัด (ตามความต้องการของผู้ใช้)
    data['province'] = 'สุราษฎร์ธานี' 
    # V11: ลบ Logic การดึงจังหวัดออก เพื่อใช้ค่า Hardcode นี้

    # 2. หา อำเภอ/เขต
    # V11: ลบคำว่า 'จังหวัด' ออกจาก working_text เพื่อไม่ให้รบกวนการดึงส่วนประกอบอื่น
    working_text = re.sub(r'(จ\.|จังหวัด)\s*' + name_pattern, ' ', working_text)
    
    amp_match = re.search(r'(อ\.|อำเภอ|เขต)\s*' + name_pattern + r'(?=\s*(ต\.|แขวง|จ\.|จังหวัด|$))', working_text)
    if amp_match: 
        data['amphoe'] = amp_match.group(2).strip()
        working_text = working_text.replace(amp_match.group(0), ' ').strip()

    # 3. หา ตำบล/แขวง
    tam_match = re.search(r'(ต\.|ตำบล|แขวง)\s*' + name_pattern + r'(?=\s*(อ\.|เขต|จ\.|จังหวัด|$|ม\.|หมู่|ถ\.|ซ\.))', working_text)
    if tam_match: 
        data['tambon'] = tam_match.group(2).strip()
        working_text = working_text.replace(tam_match.group(0), ' ').strip()
        
    # 4. หา ถนน/ซอย
    road_match = re.search(r'(ถ\.|ถนน|ซ\.|ซอย)\s*' + name_pattern + r'(?=\s*(ต\.|ตำบล|แขวง|ม\.|หมู่|$))', working_text)
    if road_match:
        data['road'] = road_match.group(2).strip()
           
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
                
                # --- Logic 1: N/E-based (หลัก) ---
                lat_match_ne = re.search(r"(\d+\.\d{4,}).*?n", clean_text)
                long_match_ne = re.search(r"(\d+\.\d{4,}).*?e", clean_text)

                if lat_match_ne and long_match_ne:
                    try:
                        temp_lat = float(lat_match_ne.group(1))
                        temp_long = float(long_match_ne.group(1))
                        
                        if (5.0 <= temp_lat <= 21.0) and (97.0 <= temp_long <= 106.0):
                            lat, long = temp_lat, temp_long
                    except ValueError: pass 

                # --- Logic 2: Fallback Float-based (สำรอง) ---
                if lat is None or long is None:
                    potential_floats = [
                        float(f) for f in re.findall(r"(\d{1,3}\.\d{4,})", clean_text) 
                        if 5.0 <= float(f) <= 180.0
                    ]
                    
                    if len(potential_floats) >= 2:
                        potential_floats.sort()
                        temp_lat = potential_floats[0]
                        temp_long = potential_floats[1]
                        
                        if (5.0 <= temp_lat <= 21.0) and (97.0 <= temp_long <= 106.0):
                            lat, long = temp_lat, temp_long

                addr_data = extract_address_components(full_text)
            
            # 2. แสดงภาพและแผนที่
            col_img, col_map = st.columns([1, 1])
            with col_img:
                st.image(image, caption='ภาพต้นฉบับ', use_container_width=True)
            
            if lat and long:
                st.success("✅ อ่านพิกัดสำเร็จ")
                map_data = pd.DataFrame({'lat': [lat], 'lon': [long]})
                with col_map:
                    st.map(map_data, zoom=15)
                
                # 3. ส่วนแก้ไขข้อมูล (ใช้ st.form)
                with st.form(key=f'form_{i}'):
                    st.markdown("---")
                    st.write("**📝 แก้ไข/ยืนยันข้อมูลที่ OCR อ่านได้**")
                    
                    # ตัดช่อง รหัสไปรษณีย์ ออก
                    # V11: ปรับ UI ให้มี 4 คอลัมน์ (ตัดจังหวัดและรหัสไปรษณีย์ออกเป็นช่องเดียว)
                    c1, c2, c3, c4 = st.columns([1.5, 1, 1, 1.5])
                    
                    edited_lat = c1.number_input("Latitude", value=lat, format="%.6f", key=f'lat_{i}')
                    edited_long = c2.number_input("Longitude", value=long, format="%.6f", key=f'lon_{i}')
                    
                    # ใช้ค่าที่ดึงได้จาก addr_data ในการเติมฟอร์ม
                    edited_house = c1.text_input("บ้านเลขที่", addr_data['house_no'], key=f'hn_{i}')
                    edited_moo = c2.text_input("หมู่", addr_data['moo'], key=f'moo_{i}')
                    edited_road = c3.text_input("ถนน", addr_data['road'], key=f'road_{i}')
                    edited_tambon = c4.text_input("ตำบล/แขวง", addr_data['tambon'], key=f'tambon_{i}')
                    edited_amphoe = c3.text_input("อำเภอ/เขต", addr_data['amphoe'], key=f'amphoe_{i}')
                    
                    # V11: แสดงจังหวัดที่เป็น Hardcode
                    st.text_input("จังหวัด", value=addr_data['province'], disabled=True, key=f'province_{i}')

                    save_button = st.form_submit_button(label='💾 ยืนยันและบันทึกลง Database', type="primary")

                    if save_button:
                        sheet = connect_to_gsheet()
                        if sheet:
                            google_map_link = f"https://www.google.com/maps?q={edited_lat},{edited_long}"
                            new_row = [
                                str(pd.Timestamp.now()),
                                edited_lat, edited_long,
                                edited_house, edited_moo, edited_road, 
                                edited_tambon, edited_amphoe, addr_data['province'], # ใช้ค่า Hardcode
                                # รหัสไปรษณีย์ถูกลบออก
                                google_map_link,
                                uploaded_file.name
                            ]
                            sheet.append_row(new_row)
                            st.success(f"บันทึกไฟล์ **{uploaded_file.name}** เรียบร้อย!")
                        else:
                            st.error("ไม่สามารถบันทึกได้เนื่องจาก Google Sheet ไม่พร้อมใช้งาน")
            else:
                st.error("❌ ไม่พบพิกัด GPS ที่ชัดเจนในภาพนี้")
                
            # --- DEBUG SECTION: แสดงข้อความดิบเสมอ ---
            with st.expander("🔍 ข้อมูลที่ OCR อ่านได้ทั้งหมด (เพื่อการตรวจสอบ)"):
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
