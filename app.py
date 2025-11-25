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
st.set_page_config(page_title="GPS OCR System", page_icon="📍")
st.title("📍 ระบบอ่านพิกัดและเก็บข้อมูลลง Cloud")

# --- 2. ฟังก์ชันเชื่อมต่อ Google Sheets (Database) ---
# เราจะใส่ Credentials ใน Secrets ของ Streamlit Cloud ภายหลัง
def connect_to_gsheet():
    try:
        # ดึงค่า Secret จาก Streamlit Config
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = st.secrets["gcp_service_account"] # ต้องตั้งค่าใน Cloud
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        # เปิด Sheet ตามชื่อ (ต้องสร้าง Sheet ชื่อนี้รอไว้)
        sheet = client.open("GPS_Database").sheet1
        return sheet
    except Exception as e:
        st.error(f"ยังไม่ได้เชื่อมต่อ Google Sheet หรือตั้งค่าผิดพลาด: {e}")
        return None

# --- 3. ฟังก์ชัน OCR ---
@st.cache_resource # Cache ตัวโมเดลไว้จะได้ไม่ต้องโหลดใหม่ทุกครั้ง
def load_reader():
    return easyocr.Reader(['th', 'en'], gpu=False) # Cloud ฟรีไม่มี GPU ให้ใช้ CPU

reader = load_reader()

# --- 4. ส่วนอัปโหลดและประมวลผล ---
uploaded_file = st.file_uploader("📸 อัปโหลดภาพถ่าย", type=['jpg', 'png', 'jpeg'])

if uploaded_file is not None:
    # แสดงภาพ
    image = Image.open(uploaded_file)
    st.image(image, caption='ภาพต้นฉบับ', use_container_width=True)
    
    if st.button('🔍 อ่านค่าและบันทึก'):
        with st.spinner('กำลังแกะรอยพิกัด... (อาจใช้เวลา 5-10 วินาทีบน Cloud)'):
            # แปลงภาพให้ OpenCV อ่านได้
            img_np = np.array(image)
            
            # อ่านข้อความ
            result = reader.readtext(img_np, detail=0)
            full_text = " ".join(result)
            
            # Clean Data
            full_text = full_text.replace("`", "°").replace("'", "°").replace("n,", "N,").replace("e", "E")
            
            # Regex หาพิกัด
            coords = re.findall(r"(\d+\.\d+)", full_text)
            lat, long = None, None
            if len(coords) >= 2:
                lat = float(coords[0])
                long = float(coords[1])
            
            # Regex หาที่อยู่
            address = "ไม่พบข้อมูล"
            if "จ." in full_text or "ต." in full_text:
                 # logic ง่ายๆ ตัดคำตั้งแต่ม. หรือตัวเลขบ้านเลขที่
                 match = re.search(r"(\d+/.*|ม\..*)", full_text)
                 if match:
                     address = match.group(0)

            # แสดงผลลัพธ์
            st.success("✅ อ่านข้อมูลสำเร็จ")
            col1, col2 = st.columns(2)
            col1.metric("Latitude", lat)
            col2.metric("Longitude", long)
            st.write(f"🏠 **ที่อยู่:** {address}")

            # แสดงแผนที่
            if lat and long:
                map_data = pd.DataFrame({'lat': [lat], 'lon': [long]})
                st.map(map_data)
                
                google_map_link = f"http://maps.google.com/?q={lat},{long}"
                st.markdown(f"[🔗 เปิดใน Google Maps]({google_map_link})")

                # --- บันทึกลง Google Sheets ---
                sheet = connect_to_gsheet()
                if sheet:
                    # เตรียมข้อมูลลงแถวใหม่
                    new_row = [
                        str(pd.Timestamp.now()), # เวลา
                        lat, 
                        long, 
                        address, 
                        google_map_link,
                        uploaded_file.name
                    ]
                    sheet.append_row(new_row)
                    st.toast("💾 บันทึกลง Google Sheets เรียบร้อย!", icon="☁️")

# --- 5. ส่วนค้นหาข้อมูล (History) ---
st.divider()
st.subheader("📂 ฐานข้อมูลที่บันทึกไว้")
if st.checkbox("แสดงข้อมูลทั้งหมด"):
    sheet = connect_to_gsheet()
    if sheet:
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        st.dataframe(df) # ตารางนี้ค้นหา (Search) ได้ในตัว