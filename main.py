import os
import random
import string
import struct
import secrets
from datetime import datetime, timedelta
from typing import List, Optional
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import requests
import re
from fastapi.responses import StreamingResponse
import codecs
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, status, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, backref
from sqlalchemy import Column, Integer, String, Boolean, BigInteger, ForeignKey, select, func, DateTime 
from passlib.context import CryptContext
from jose import JWTError, jwt
import httpx
import json
import urllib.parse
import pickle
from fastapi import Response
from datetime import datetime # Для штампа времени в письме
from sqlalchemy import LargeBinary # Добавить к импортам sqlalchemy
# Остальные импорты уже есть, убедись что requests и json импортированы
# --- КОНФИГУРАЦИЯ ---
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL") # Например: postgresql+asyncpg://user:pass@host/db
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_neon_key_change_me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# --- БАЗА ДАННЫХ ---
engine = create_async_engine(
    DATABASE_URL, 
    echo=False,
    connect_args={"statement_cache_size": 0}
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    # Добавляем поле для хранения "чистого" email для проверки дублей
    normalized_email = Column(String, index=True, nullable=False) 
    hashed_password = Column(String, nullable=False)
    verification_code = Column(String, nullable=True)
    is_active = Column(Boolean, default=False)
    tokens_balance = Column(BigInteger, default=0)
    # Добавляем поле IP
    registration_ip = Column(String, index=True, nullable=True)
    

    referral_code = Column(String, unique=True, index=True, nullable=True) # Личный код пользователя
    referrer_id = Column(Integer, ForeignKey("users.id"), nullable=True)   # Кто пригласил этого юзера
    invites_count = Column(Integer, default=0)                             # Сколько людей пригласил
    unlimited_until = Column(DateTime, nullable=True)


    api_keys = relationship("APIKey", back_populates="user")
    referred_users = relationship("User", backref=backref("referrer", remote_side=[id]))

class APIKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True, index=True)
    key_hash = Column(String, unique=True, index=True)
    name = Column(String)
    limit_tokens = Column(BigInteger)
    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    
    user = relationship("User", back_populates="api_keys")
class SystemData(Base):
    """Таблица для хранения бинарных данных (куки, сессии)"""
    __tablename__ = "system_data"
    key = Column(String, primary_key=True, index=True)
    value = Column(LargeBinary) # Храним pickle bytes
# --- СХЕМЫ Pydantic ---
class UserRegister(BaseModel):
    email: EmailStr
    password: str
    referral_code: Optional[str] = None
class AutoDrawRequest(BaseModel):
    key: str
    ink: List[List[List[int]]]
    width: Optional[float] = 1092.8
    height: Optional[float] = 522.9
class UserVerify(BaseModel):
    email: EmailStr
    code: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str
class TokenizeRequest(BaseModel):
    text: str
class Token(BaseModel):
    access_token: str
    token_type: str

class KeyCreate(BaseModel):
    name: str
    limit: int

class KeyResponse(BaseModel):
    id: int
    name: str
    key: str
    limit: int
    created_at: str
class QuickDrawRequest(BaseModel):
    key: str # API ключ пользователя
    # ink: список штрихов. Каждый штрих = [ [x_coords], [y_coords], [times] ]
    ink: List[List[List[int]]] 
    width: Optional[int] = 255 # Ширина холста (не обязательна, но полезна)
    height: Optional[int] = 255
class ContactRequest(BaseModel):
    email: Optional[EmailStr] = None
    message: str
# --- УТИЛИТЫ ---






# --- GEMINI CONFIGURATION ---
GEMINI_AT_TOKEN = "AEHmXlFy6SpYiMH26GJl4SS7W7Cw:1769731948839" # Из твоего кода

INITIAL_COOKIES_DICT = {
    'SID': 'g.a0005ghCS3Go2zHTEbhTihFpfXPdlwvXhOygnDsHibxQ2VhXXp-WMaDnN_MC-HH4E89swWIksgACgYKAZESARASFQHGX2Mi_3n-_z8pzW2SzhF1AckE6BoVAUF8yKqOTiG5o3kW3PDSFfGVWD7l0076',
    '__Secure-1PSID': 'g.a0005ghCS3Go2zHTEbhTihFpfXPdlwvXhOygnDsHibxQ2VhXXp-W3uZZTr0OGwYjov9t4SfP1gACgYKAbsSARASFQHGX2MikhOEbHNsUDP00MOlvG75NBoVAUF8yKpJItwJkkEZomhAplK3ztxc0076',
    '__Secure-3PSID': 'g.a0005ghCS3Go2zHTEbhTihFpfXPdlwvXhOygnDsHibxQ2VhXXp-WWP315X8vVS1SenBmEOkmSgACgYKASQSARASFQHGX2Mi0ASBu2PydJnOiaKpE-IOpRoVAUF8yKr4WOBmzxvU7A_yB9Q7zUOC0076',
    'HSID': 'AjK9lOp-1MpP5slS9',
    'SSID': 'Ay_56OtWGzrUIBcgw',
    'APISID': '_wJeK7rClA9y9mmx/AOLYKaciVgGvqH-Wk',
    'SAPISID': 'sIAVlRgPOYMcAc2H/AggJEqXwNVmkThrgI',
    '__Secure-1PAPISID': 'sIAVlRgPOYMcAc2H/AggJEqXwNVmkThrgI',
    '__Secure-3PAPISID': 'sIAVlRgPOYMcAc2H/AggJEqXwNVmkThrgI',
    '_gcl_au': '1.1.2010919010.1769724179',
    '_ga': 'GA1.1.65427254.1769724180',
    'COMPASS': 'gemini-pd=CjwACWuJV93jFYb_b6k1ZbZc5AVi75OXfwVJx6huPFdJgLZgT-iphNSBtyIyTho-2Gurv4U86El7hPmdVFUQnPH0ywYaXQAJa4lX8ymz41ej13SsiHXrbpu08aY2VbCe5uWAu4z_vvIU7rGkhTpTPTxW4sI6PkizmbWDAWCprGS2ab3M7pEAm5X6dgCtuY9wsocKIQf7LJYA9k5VpM7V6j0_5iABKmcIARDBk_XLBhpdAAlriVfzKbPjV6PXdKyIdetum7TxpjZVsJ7m5YC7jP--8hTusaSFOlM9PFbiwjo-SLOZtYMBYKmsZLZpvczukQCblfp2AK25j3CyhwohB_sslgD2TlWkztXqPT_mMAE:gemini-hl=CkkACWuJV4Jq7gXnYGXm-CCWRGf1MNczIJ0yMsen8R98zb0fdd_v1HDcw_-Y0Gxw7WZu_GGVl89NUAGecp6EG6tM_DjudIlkdiK-EPPx9MsGGmoACWuJVxACX2HJ_WTtDaV4g7VmrQ9U6Nhmc45YIYMdTv3q_xHAkKdlYqQTO-JnjNE8HfJt4g4xAXknNJZJWw3QMjGq76KbrdMup1xF6mFLuwVNMqi_eARLWKvm5PWUo40jx9EJI1fVgvHRIAEqdAgBENqV9csGGmoACWuJVxACX2HJ_WTtDaV4g7VmrQ9U6Nhmc45YIYMdTv3q_xHAkKdlYqQTO-JnjNE8HfJt4g4xAXknNJZJWw3QMjGq76KbrdMup1xF6mFLuwVNMqi_eARLWKvm5PWUo40jx9EJI1fVgvHRMAE',
    'NID': '528=T5pglaGSdrwKou9uDcpu9UNFZ0kwH9x8DZ4_er_pwqFohnn7Ri-ajkyrcfqDbfAL4Q8sARVzgE8WS4i2CZQgOZPn65qH40UbaASLFUh3aenL5Xefj1CpFzRjsNdvleolynoNk5ifjikCBEyFncjuF2K1w67HIujcK5p2zBbaMsbobg9pawqsJgBX_rV_sEhhq68M8rKayLod3Y61IDqU938e4EFhUTvqTeIjPoqmkuHXPNiPSV86qnYs-ZqpT5GZ4o6eANGqGphwwdhkCdLIUh9QOV0DJlO_4BjbzzEs-IKuElygGlu_zR0RXZJgyhvkH0-4XnSprWuKsDXVTS1TP2nE1FcOXGknNCEFsrZ57a6JoSReJaa77i8VlQ89cnmTOYuUvpRqfZeLIG-RXSe9benosOzf5AoKJVaLMlTwC_U18XD37QMoReGtizugC1kRr3K3bAEHeTPmljpoAR_Vi22V2i7EK0E9NWyUNSMLhnShMPZYu2o4dIXp7AfcH3Oj7QrLcg2Q-_nnw_bGDv3lBWCR100gLNra_scqCKYH4R8DrT_1JoMU1cBL_r-iUYtaRMnAfgzaH0B3Om48DHm9e_NKR9y6Rclu2O69nsuzFrQinWWpo7aiKP8wEU_l3y0A_tQAKjv637iMCP7S-GByXymOZR_w3aEv0zr0_nWcbc0LtaJ-wshYgG-WmWndnNcLgVmIpx1JiCBe_hLxCo1RApzvz1BskE1qh6kiG_7BqaxHgqrRK3tLACTFYgI7taB8Iv7YYtzL-vAz-tbwiW72Twebh-hjypSdohRrMMoPqfgvsnVbtiF7NC_zaQrc9eFExfi3ZYxCoP8WcGGOMm66oQ_gD77FTd84Fw4H1Xy85cXZ7OO6XZboIROeXCBatNqgigsS7GoDCk3k7LA1dFQfFZH0XCnEvwiJVzddV_UvCTynUqJPk0VNTmKMBAUzIlcwmJTMWNTjjWF6Ef_99mXvOdr2NRWjietJIcFTXxcgRWvb1dOzMuzo-JwR3N1QovPQkclWUtnm27Hn0Oh4E7CZpSg4icu6f1J1QaGS0yb6mumviI9Vjokj9rb6RMhVUGTKVvbo8rJjlPw375eYo3BHkjq2eknqxsgMxHWZ17lT4OYRAA7DCWd2rIakR_ETnijzGs702i4ag5Q5m87Dx6mx5ONKEAHZtvEun3-6whPstlQz8ELJTdkpAzwi7TtAsoRWWr70Jt4N5r__xsRGWk_bQ5FAAR059lEApHZ6JADiMiG_wVBCSQA9dAIxM__qmk9Lr88djlaCtjRH1JsWDNiGqiA4z2aoVh_bYh5YkOJcTSdUgmcCK0b3mNal70fyJbLx-c1UFnAv0Rng6hGeilBgfJ_FqD5ZyMBMhLw9votSmwQFBP5ukstqEFWulKiKbKTqk7W8uQ_JbLrvmWhJ2GJtaVHLqipZ2gfU0_y_zWg9w8BqVLbbE_weBGWovxBNw8F5H5SldXaxvWqgQEX08l7wFdaKr6D4B8l2tAQ5OG592uodGxReRIeUIxffs7XoHxuWVYFr-Fl1kR_eJ-NPzgUN',
    '_ga_BF8Q35BMLM': 'GS2.1.s1769728249$o2$g1$t1769731719$j60$l0$h0',
    '__Secure-1PSIDTS': 'sidts-CjEB7I_69PGvzQP2ZdmMDP5af1eoKYP3KaoTV3-2DEUCb6fZswwFCGJ7Vh7PC4bwk_BlEAA',
    '__Secure-3PSIDTS': 'sidts-CjEB7I_69PGvzQP2ZdmMDP5af1eoKYP3KaoTV3-2DEUCb6fZswwFCGJ7Vh7PC4bwk_BlEAA',
    '_ga_WC57KJ50ZZ': 'GS2.1.s1769728249$o2$g1$t1769732817$j58$l0$h0',
    'SIDCC': 'AKEyXzV1E1K29UfEbjqfzzT1wlWJ9OHpz-A41YfxTVbBbBytvfLiBSX4rj1kcKbCt3C0Nnuf1w',
    '__Secure-1PSIDCC': 'AKEyXzVxgQhc1wCbbDFXXRW7RIsLrC3BK2d9_xVxHGN9_5Ml9KVayL-8xlwkx7g2w6KcWN5wdTY',
    '__Secure-3PSIDCC': 'AKEyXzWOmp9j2nA-GsfIk4XDWBhS_aIauD8_7v7vYb0l9OGpJ8PgVE_n3OgOsOcdByurkqTq7NY',
}

GEMINI_HEADERS = {
    'accept': '*/*',
    'accept-language': 'ru,en;q=0.9',
    'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
    'origin': 'https://gemini.google.com',
    'referer': 'https://gemini.google.com/',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'x-same-domain': '1',
}

# --- ФУНКЦИИ GEMINI ---

def _sync_gemini_request(message_text, cookies):
    """Синхронная функция запроса (запускается в отдельном потоке)"""
    session = requests.Session()
    session.headers.update(GEMINI_HEADERS)
    session.cookies.update(cookies)
    
    encoded_message = urllib.parse.quote(message_text)
    data_prefix = 'f.req=%5Bnull%2C%22%5B%5B%5C%22'
    data_suffix = '%5C%22%2C0%2Cnull%2Cnull%2Cnull%2Cnull%2C0%5D%2C%5B%5C%22ru%5C%22%5D%2C%5B%5C%22%5C%22%2C%5C%22%5C%22%2C%5C%22%5C%22%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5C%22%5C%22%5D%2C%5C%22!Q0ClQBjNAAZeabWMfmlCpnSCL5dFYOU7ADQBEArZ1Gc_anREocG2B2DF4zwI3mMyyuxLwB3yyOX-obdTN4OSl3a3tQxZZe63PRBHz5MpAgAAAZBSAAAAH2gBB34AQTHSxFe9uWaOZl9ZAQEUAZFB1PeSf5unoZqC5vOsTi1aMUiGGFfitq8c3XWaxLuoNi8p7lVS2y7a3qFwk_8X05YZmQMsSOzmJgrf0uyeL43D9vsDgTX10K9qD33A6c4jvTo03DSm4r0cy4wGl5inCOqH-PPyy55-U_yq2FJrGDCDV2unoVxdigUOrKDZNUiq81iK_kxQ36QGKL3pm8xbV1Rrf-1s-yk8rT82kecMfkaq_a-ugAWljDoxPz4e7URmws0yXqWHmBwkDrHHjdkoUfX9swDb0rJ_cuPt7oVbTb7ZEWwI6ZPx7Zg_AjM7z1iQSqYouQ5dDu3uiw_mtE3o1E9pnbGPCjUH5UXjaZhOT3wBmgbptjUbeEdKOz3qyZJe0kYJEGGQ1uaLltqrad2xC_4dImSFLP_9fxt5qZxSDOXMkgdrNQBBBTROv_WMyz7YZqkLhFy6UVSAhKx4-uN3tUjp-Q3yogjb6jrnhh2Uw6MFTQU8o_D8evKQgyb2uKokDYes1QRRUagKlCy9-W9RtUGaKGbqHK35ssJ_R6HXLGgYcWwkVyQDolevtmZUCH3hhKhZyHbBjCXHbs8uvPkd1tmPmbewcdrmBG-_Dfj3jodamuqVD4eVX0ltvi0UjbD6hxTUGdZBmQWsJnmIL4boG4jnGdz-qBUUtir5ycfP3P65QqaOQdCxCio5HiYFG4DAPkiwXZDUG_5KO68J4cph1zK9bOqMjlIvWwiKqIgIeEpdke8UJrq0aZ6RHfXu0I5lquyFiqlPwlmBMEo9DvNWI0DoFtWNPfTFHXrvCVyHL8E61k3Ti8ops_mj75HR670AzA160nowkWNHP6HS3QMZEJSXb-ybbfypWE0JnT1NGOMQdieDXq5-xVfg888XvDZov1qaLaNzU8XfCtmrEOjeTyV27yLFyNzN0fQfEK6Zq4_svnOsZer37EQPKMKi10FBkawsKgSb3bn74i3DTZOEc0M8zDZUcunyPjoCAvcmjsy_JvMQFNaj8y4lbSQ_Wf5lFJD79tE8jYdJuGUzKsyYKfP86W5t7guwQyia53Y2n0OFiGafu0tPhhEaIewYHu8UX0K96C1nCR4pWLkumW3490j7yN5iuOwe0VZVgeL3th_j8tiloh6kuKSbDys4utjQ57JU_Gd6H7HDYDzMptO8pFU%5C%22%2C%5C%2262806c58061d7d812a36fc661042319b%5C%22%2Cnull%2C%5B0%5D%2C1%2Cnull%2Cnull%2C1%2C0%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B%5B0%5D%5D%2C0%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C1%2Cnull%2Cnull%2C%5B4%5D%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B1%5D%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C0%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5C%221670C968-EBC3-4DC2-953A-E02A6ADDC428%5C%22%2Cnull%2C%5B%5D%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B%5D%2Cnull%2C1%5D%22%5D'
    data = f"{data_prefix}{encoded_message}{data_suffix}&at={GEMINI_AT_TOKEN}&"

    params = {
        'bl': 'boq_assistant-bard-web-server_20260128.03_p2',
        'f.sid': '8915235416742414989',
        'hl': 'ru',
        '_reqid': '3818761',
        'rt': 'c',
    }

    try:
        response = session.post(
            'https://gemini.google.com/u/1/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate',
            params=params,
            data=data,
            timeout=30
        )
        if response.status_code == 200:
            return {"text": response.text, "cookies": session.cookies}
        return {"error": f"Status {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}

def parse_gemini_response(raw_text):
    if not raw_text: return None
    lines = raw_text.split('\n')
    final_text = ""
    for line in lines:
        start_index = line.find('[')
        if start_index == -1: continue
        try:
            json_data = json.loads(line[start_index:])
            if not json_data or not isinstance(json_data, list) or len(json_data) == 0: continue
            wrapper = json_data[0]
            if not isinstance(wrapper, list) or len(wrapper) < 3 or wrapper[0] != "wrb.fr": continue
            inner_json_str = wrapper[2]
            if not inner_json_str: continue
            inner_data = json.loads(inner_json_str)
            if len(inner_data) > 4 and inner_data[4] is not None:
                candidates = inner_data[4]
                if isinstance(candidates, list) and len(candidates) > 0:
                    first_candidate = candidates[0]
                    if len(first_candidate) > 1 and isinstance(first_candidate[1], list) and len(first_candidate[1]) > 0:
                        text_chunk = first_candidate[1][0]
                        if text_chunk: final_text = text_chunk
        except: continue
    return final_text

async def gemini_chat(prompt: str, db: AsyncSession):
    # 1. Загружаем куки из БД
    result = await db.execute(select(SystemData).where(SystemData.key == 'gemini_cookies'))
    db_cookie = result.scalar_one_or_none()
    
    if db_cookie:
        cookies = pickle.loads(db_cookie.value)
    else:
        cookies = INITIAL_COOKIES_DICT

    # 2. Выполняем запрос синхронно в пуле потоков (requests блокирует, поэтому так надо)
    import asyncio
    response_data = await asyncio.to_thread(_sync_gemini_request, prompt, cookies)
    
    if "error" in response_data:
        return f"Gemini Error: {response_data['error']}"
    
    # 3. Сохраняем обновленные куки в БД
    new_cookies_bytes = pickle.dumps(response_data["cookies"])
    if db_cookie:
        db_cookie.value = new_cookies_bytes
    else:
        new_entry = SystemData(key='gemini_cookies', value=new_cookies_bytes)
        db.add(new_entry)
    await db.commit()

    # 4. Парсим ответ
    return parse_gemini_response(response_data["text"]) or "Empty response parsed"






# --- GEMINI IMAGE CONFIGURATION ---
IMAGEN_AT_TOKEN = "AEHmXlEXuKlaeAWzQ-dHE5uMCPD6:1769906748197"
IMAGEN_F_SID = "-5526697252036765155"
IMAGEN_BL_SERVER = "boq_assistant-bard-web-server_20260128.03_p2"

IMAGEN_INITIAL_COOKIES = {
    '_gcl_au': '1.1.321804501.1769867681',
    '_ga': 'GA1.1.170156978.1769867682',
    'SID': 'g.a0006QjXizp8jo3Gc0Of5kjZV2Md2EWGK6QEcrJetAWXraNkI_Y99h9WHWdR1wrd5ZqOwEwLwwACgYKARwSARESFQHGX2MiYDtBz6M4ZLr2o0VPzQmhhhoVAUF8yKoL1B25RPbKIRn8GzxuM2aZ0076',
    '__Secure-1PSID': 'g.a0006QjXizp8jo3Gc0Of5kjZV2Md2EWGK6QEcrJetAWXraNkI_Y9U0pAz-3TkC5ksCp0GItjJQACgYKAZsSARESFQHGX2MiyKFZwheb1FGfoBQTTQXvqxoVAUF8yKp0KI6Nqs3RFCwX07VjyDCN0076',
    '__Secure-3PSID': 'g.a0006QjXizp8jo3Gc0Of5kjZV2Md2EWGK6QEcrJetAWXraNkI_Y9jLMfSj7c9OlNwf0nD1AcpAACgYKASESARESFQHGX2Mi5iiHy2LFdBngPR300nkt_RoVAUF8yKqVcIDOjuqJFtziGWdgNkRI0076',
    'HSID': 'Ah9YeSisMyhI6WIqA',
    'SSID': 'AhHdOe0c5RcKS_O8j',
    'APISID': 'xuothCntUVVWgUcw/Aq2XPdx7sNWWdO85m',
    'SAPISID': 'RWzy4FADoAFzO-oD/AiswpUkRdFazLFo0U',
    '__Secure-1PAPISID': 'RWzy4FADoAFzO-oD/AiswpUkRdFazLFo0U',
    '__Secure-3PAPISID': 'RWzy4FADoAFzO-oD/AiswpUkRdFazLFo0U',
    'COMPASS': 'gemini-pd=CjwACWuJV93jFYb_b6k1ZbZc5AVi75OXfwVJx6huPFdJgLZgT-iphNSBtyIyTho-2Gurv4U86El7hPmdVFUQi9P9ywYaXwAJa4lXLvbjFchc4_1pxVv6T7gLfJ2slxUaoulGsvyMeC-j3jnVGpAQWHeqydbFMC5a2ywGx3-W0RdB_hYBOblB5Xvwosrkr3XM_QPpkWQE1U1ZEPyUNCch4_659F_JIAEwAQ',
    'NID': '528=by5du2Dtn531lheJBkzTPaI5AmjuzG_sSTaEmSxliY3Q4H3e4iivfMxOSLVqdQzFUaYi52trqVmqBFA9XJ2c7bgWpi6EKme9uppeU2gIalI25LC53Fh5olyCY_qs8q-pl31TPokrsLupt4GDAaUVw9YZJaufwui35Knp4wZuUpGKof8u39i8IYiLGwu6Sq_p6cDh2ND7wKDlPu35YZQht9Z-x-oD5thx9uPHTspFVPGsgY2Sk2wW7DTU5XrSmoR_lrcjpGYK8n1QBVUEGJ5rR7tdfBwiZJW-B3tAhr_nK4mQlySc6Cc9lBaq6Gcbufr22rOgqB2dTF7nOZGzjuqm2wv8koxsDx3IG7Wn2VQZFlP7KPG3vDqQ-O9iIRsLHHfZvkSuvKL3IlyrjJ9aCDURlgpi7ZpRIlpHLQZLt_YmbMafqoFraFxA_30rYpED_4WTzICUny0S2FjdvYiKy-z-UlGAup6tlUWiq2xN8Dv5fpOhW5TC-p6rhoSUQBScUpnzh54xLuO147_KA8JmFbW_oZjZ9JfU87D8y4tqs3-ujKCAkR_f9w9_ElyoDqRA6Myc-6mL3moYPRS_ndeSuvIu51urDd2M4zGP2jIwCmtGweW2hFmduzlNgLKdqa3V1ZK3RgWDvXKflgKaQiuj',
    '_ga_WC57KJ50ZZ': 'GS2.1.s1769903163$o2$g0$t1769903163$j60$l0$h0',
    '_ga_BF8Q35BMLM': 'GS2.1.s1769903163$o2$g0$t1769903163$j60$l0$h0',
    '__Secure-1PSIDTS': 'sidts-CjIB7I_69ACI_ut9anc9-3DvYGxAP7aLbWzxw1vz8TdZWEHdiCnApClDhbyS20HzCy_rOhAA',
    '__Secure-3PSIDTS': 'sidts-CjIB7I_69ACI_ut9anc9-3DvYGxAP7aLbWzxw1vz8TdZWEHdiCnApClDhbyS20HzCy_rOhAA',
    'SIDCC': 'AKEyXzXkWnAWjbBPxa-NJml1Dg2CaJGsLa6-YVyh0A4qSHm0FOTG__2UCimtxGTSdlLp-XwatQ',
    '__Secure-1PSIDCC': 'AKEyXzU2f-QfwX_0FtLkuU9VJxohGyg36to4nWf1FffevCChFWu0uVQ9OtV1ybcvLjJCoBfw',
    '__Secure-3PSIDCC': 'AKEyXzXpYZORco20KCQZf1Qoe_NWRWTFr1TgpvpZEL6qyMOp9UHkHfXCdCAPeiBWaNsgj3M4Tg',
}

IMAGEN_HEADERS = {
    'accept': '*/*',
    'accept-language': 'ru,en;q=0.9,en-GB;q=0.8,en-US;q=0.7',
    'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
    'origin': 'https://gemini.google.com',
    'priority': 'u=1, i',
    'referer': 'https://gemini.google.com/',
    'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    'sec-ch-ua-arch': '"x86"',
    'sec-ch-ua-bitness': '"64"',
    'sec-ch-ua-form-factors': '"Desktop"',
    'sec-ch-ua-full-version': '"144.0.7559.110"',
    'sec-ch-ua-full-version-list': '"Not(A:Brand";v="8.0.0.0", "Chromium";v="144.0.7559.110", "Google Chrome";v="144.0.7559.110"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-model': '""',
    'sec-ch-ua-platform': '"Windows"',
    'sec-ch-ua-platform-version': '"10.0.0"',
    'sec-ch-ua-wow64': '?0',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    'x-browser-channel': 'stable',
    'x-browser-copyright': 'Copyright 2025 Google LLC. All rights reserved.',
    'x-browser-validation': '5sIVVtVmIdhoPXzr4AHI3aD5P60=',
    'x-browser-year': '1969',
    'x-goog-ext-525001261-jspb': '[1,null,null,null,"fbb127bbb056c959",null,null,0,[4],null,null,1]',
    'x-goog-ext-525005358-jspb': '["D98A3EE2-30BA-491D-9A23-0D4BAE17ACE8",1]',
    'x-goog-ext-73010989-jspb': '[0]',
    'x-same-domain': '1'
}


def _sync_gemini_image_request(prompt: str, cookies: dict):
    """
    Синхронная функция:
    1. Отправляет запрос "Generate image: ..."
    2. Парсит ответ на наличие ссылки
    3. Скачивает картинку с теми же куками
    4. Возвращает bytes картинки и новые cookies
    """
    session = requests.Session()
    session.headers.update(IMAGEN_HEADERS)
    session.cookies.update(cookies)
    
    # Подготовка запроса (Generate image + prompt)
    full_prompt = f"Generate image: {prompt}"
    req_id = int(random.random() * 10000000)
    
    params = {
        'bl': IMAGEN_BL_SERVER,
        'f.sid': IMAGEN_F_SID,
        'hl': 'ru',
        '_reqid': str(req_id),
        'rt': 'c',
    }
    
    # Структура сообщения [[text], null, [context]]
    message_structure = [[full_prompt], None, [None, None, None]]
    f_req_value = json.dumps([None, json.dumps(message_structure)])
    
    post_data = {'f.req': f_req_value, 'at': IMAGEN_AT_TOKEN}

    try:
        # 1. Запрос генерации
        response = session.post(
            'https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate',
            params=params,
            data=post_data,
            timeout=60 # Генерация может занять время
        )
        
        if response.status_code != 200:
            return {"error": f"Gemini Error: {response.status_code}"}
            
        raw_response = response.text
        
        # 2. Поиск ссылки (Regex из примера)
        pattern = r'https://lh3\.googleusercontent\.com/gg-dl/[^"]+'
        found_urls = re.findall(pattern, raw_response)
        
        if not found_urls:
            return {"error": "No image URL found in response. Verify prompt compliance."}
            
        # Убираем возможный мусор в конце (как в примере пользователя [:-1])
        # Но regex [^"]+ обычно останавливается перед кавычкой. 
        # На всякий случай проверим, если ссылка валидная, requests справится.
        image_url = found_urls[0]
        # Иногда regex захватывает лишний слэш экранирования, если ответ в JSON
        image_url = image_url.replace('\\', '')
        
        # 3. Скачивание изображения
        img_resp = session.get(image_url, timeout=30)
        
        if img_resp.status_code == 200:
            return {
                "image_data": img_resp.content, # bytes
                "cookies": session.cookies
            }
        else:
            return {"error": f"Image Download Failed: {img_resp.status_code}"}
            
    except Exception as e:
        return {"error": str(e)}

async def generate_gemini_image_async(prompt: str, db: AsyncSession):
    """Асинхронная обертка: работа с БД и запуск потока"""
    # 1. Загружаем куки для картинок (отдельный ключ в БД)
    stmt = select(SystemData).where(SystemData.key == 'gemini_image_cookies')
    result = await db.execute(stmt)
    db_cookie = result.scalar_one_or_none()
    
    if db_cookie:
        cookies = pickle.loads(db_cookie.value)
    else:
        cookies = IMAGEN_INITIAL_COOKIES
        
    # 2. Запускаем тяжелую задачу в потоке
    import asyncio
    result_data = await asyncio.to_thread(_sync_gemini_image_request, prompt, cookies)
    
    if "error" in result_data:
        return {"error": result_data["error"]}
        
    # 3. Сохраняем новые куки
    new_cookies_bytes = pickle.dumps(result_data["cookies"])
    if db_cookie:
        db_cookie.value = new_cookies_bytes
    else:
        new_entry = SystemData(key='gemini_image_cookies', value=new_cookies_bytes)
        db.add(new_entry)
    await db.commit()
    
    return {"image": result_data["image_data"]}



pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def generate_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_token_count(text: str) -> dict:
    """
    Возвращает словарь:
    {
        "tokenCount": int,
        "string_tokens": List[str]
    }
    """
    if not text:
        return {"tokenCount": 0, "string_tokens": []}
        
    url = 'https://tokenizers.lunary.ai/v1/openai/token-chunks'
    
    # Заголовки (оставляем как были, они правильные)
    headers = {
        'accept': '*/*',
        'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'content-type': 'application/json',
        'origin': 'https://lunary.ai',
        'priority': 'u=1, i',
        'referer': 'https://lunary.ai/',
        'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    }

    # Формируем тело запроса
    payload_dict = {
        'text': text,
    }
    # ВАЖНО: Преобразуем словарь в JSON-строку
    payload_json = json.dumps(payload_dict) 

    async with httpx.AsyncClient() as client:
        try:
            # Отправляем content=payload_json (строка), а не словарь
            response = await client.post(
                url, 
                headers=headers,  
                content=payload_json, 
                timeout=30.0
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Извлекаем общее количество токенов
                token_count = data.get('expectedTokenCount', 0)
                
                # Извлекаем массив чанков (безопасное получение)
                chunks = data.get('chunks', [])
                
                # Проходимся по списку и достаем 'text'. 
                # Если ключа нет, вернем пустую строку, чтобы не упало.
                string_tokens = [item.get('text', '') for item in chunks]
                
                return {
                    "tokenCount": token_count,
                    "string_tokens": string_tokens
                }
            else:
                print(f"Token API Error: {response.status_code}")
                # Fallback: грубая оценка
                return {"tokenCount": len(text) // 4, "string_tokens": []}
        except Exception as e:
            print(f"Token API Exception: {e}")
            return {"tokenCount": len(text) // 4, "string_tokens": []}
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user


async def send_contact_email_to_admin(user_email: str, message_text: str):
    """Отправка сообщения с сайта на почту администратора"""
    if not BREVO_API_KEY:
        print("BREVO_API_KEY is missing")
        return

    admin_email = "profesorlalforusers@gmail.com"
    
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json", 
        "api-key": BREVO_API_KEY, 
        "content-type": "application/json"
    }
    
    # Формируем красивое письмо для админа
    html_content = f"""
    <div style="background:#050505; color:#e0e0e0; padding:20px; font-family:monospace; border: 1px solid #00f3ff;">
        <h2 style="color:#00f3ff; border-bottom: 1px solid #333; padding-bottom: 10px;">NEXUS CONTACT FORM</h2>
        <p style="color:#888;">SENDER:</p>
        <p style="font-size: 16px; color:#fff;">{user_email if user_email else 'Anonymous'}</p>
        <br>
        <p style="color:#888;">MESSAGE:</p>
        <div style="background: #111; padding: 15px; border-left: 3px solid #bc13fe;">
            {message_text}
        </div>
        <p style="font-size: 10px; color: #555; margin-top: 30px;">SYSTEM TIMESTAMP: {datetime.utcnow()}</p>
    </div>
    """
    
    payload = {
        "sender": {"name": "NEXUS SYSTEM", "email": SENDER_EMAIL},
        "to": [{"email": admin_email}],
        "replyTo": {"email": user_email} if user_email else {"email": SENDER_EMAIL},
        "subject": f"NEXUS MSG: {user_email if user_email else 'Anonymous'}",
        "htmlContent": html_content
    }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code not in [200, 201, 202]:
                print(f"Error sending contact email: {resp.text}")
        except Exception as e:
            print(f"Exception sending contact email: {e}")


async def send_email_async(to_email: str, code: str):
    print(f"--- EMAIL SIMULATION ---")
    print(f"TO: {to_email}")
    print(f"CODE: {code}")
    print(f"------------------------")

    if not BREVO_API_KEY:
        return

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {"accept": "application/json", "api-key": BREVO_API_KEY, "content-type": "application/json"}
    html_content = f"""
    <div style="background:#000; color:#fff; padding:20px; font-family:monospace;">
        <h2 style="color:#00f3ff;">NEXUS SECURITY</h2>
        <p>YOUR VERIFICATION CODE:</p>
        <h1 style="font-size:30px; letter-spacing:5px; color:#bc13fe;">{code}</h1>
    </div>
    """
    payload = {"sender": {"name": "NEXUS SYSTEM", "email": SENDER_EMAIL}, "to": [{"email": to_email}], "subject": "NEXUS ACTIVATION", "htmlContent": html_content}
    
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, headers=headers)
        except Exception as e:
            print(f"Error sending email: {e}")

def normalize_email_logic(email: str) -> str:
    """
    Приводит email к каноническому виду.
    1. Переводит в нижний регистр.
    2. Для gmail.com удаляет точки и все, что после знака +.
    """
    email = email.lower().strip()
    try:
        local_part, domain = email.split('@')
    except ValueError:
        return email

    if domain == 'gmail.com':
        local_part = local_part.replace('.', '') # user.name -> username
    
    # Удаляем алиасы через плюс (user+bonus@domain.com -> user@domain.com)
    if '+' in local_part:
        local_part = local_part.split('+')[0]
        
    return f"{local_part}@{domain}"

# НОВАЯ ФУНКЦИЯ: Получение реального IP
def get_client_ip(request: Request):
    # Если сервер за прокси (Nginx/Cloudflare), IP будет в заголовке
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0]
    else:
        ip = request.client.host
    return ip

# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

# --- APP ---
app = FastAPI(title="NEXUS API Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- ВНЕШНИЕ СЕРВИСЫ ---
async def chatgpt(model: str, prompt: str = "Hello") -> str:

    cookies = {
        'oai-did': 'f49caa8e-b1a8-472a-b61a-61b8f8aac215',
        '_ga': 'GA1.1.292430756.1770133139',
        'g_state': '{"i_l":1,"i_ll":1770362114073,"i_b":"IFhSqKx7PLKGzqT/eTztkhsTEh4Rxkjg/PAh3jKLBOw","i_e":{"enable_itp_optimization":0},"i_p":1770140342337}',
        'oai-hlib': 'true',
        '_account_is_fedramp': 'false',
        '__cflb': '0H28vzvP5FJafnkHxih7XmuVuidhF3NrzJ7BynNR7qu',
        '_uasid': '"Z0FBQUFBQnBpS0VnTGJLeEJYLUVUSW5tNHFDV0NUSi04RktubXZxRDJkc0JQV0w5UDJMRk1Xd0lsWEVKaEpmdjJNTERfNVZidEI1X2hwN0ZlZ0VydzRpTFUwd3o0SC1BNEx6N29nWHlRRVVCNDNfeUM4dTdteTQ3aXB3WktyR0ZFSGxlMlZ4czdWMk1uR2wwRG9hSTkzTzBMRkFLRVh1M05PVl9iOGhTbDN5cndTQzRzVHlOSFVhelduNE9pZENmaDF5UkF2ZW11Z0g3TmlaTEJvUGVXOGI1LVB5VVp6RGppT1REaXdHblc4U2thbV91a3ZiaWh6MXpXV0VJdWUxSUhmVjNCU2VmSXhCT2J2LVE0ejlsQmRFNWt1U1RZREJxSWZPYWFEcTVVRE51bXpZMW9KZGNnbGFMeWRQSWRkQXBfMWZaTzVHZXZJUnp1MDRocHNEZ2s4dzRLQUtMZVJVZkF3PT0="',
        '_umsid': '"Z0FBQUFBQnBpS0VnMm9mQUYxbWpITkxlckdYVTh5U3dKMXdxUEVwVnV3U3RPM09TQ3I4ZGZ5VEU0MjF4ZEViWmdkV2ZqdHZPcjdzTnZONEQ1czlFRFp4endFNFVsUFZxZEZqT1E1aDJOUkRhdTNZRVhrREstOFpJSHpFY3N6dlJBRlRXWk1HR0c5ZDR3eGhscm1XdkRqYXdvWTFaZUhjbDlRb2NJTEMzLWxUSkZEWkkteklVN1FPazI3MWhqaGtaek9Td0h2SkJXcERnZU5LV2hlVjI5Z0xSTURvamRzUGV2aExYOUdwbGhUNHQ4Vm4xZWo2ZmNUTT0="',
        '__Host-next-auth.csrf-token': '5f9ae71c0f66fb0dfe7b080becc5b4d3a28482752652f86f2d61c69cfd75c78f%7Cf399c3ea965af14e7ebd1691d09d48969f78bb90fb15c7a283bcb4b1fb47e2d5',
        'cf_clearance': 'NMUsrVKTrmJPtC9sIsiSWi.pNIoMjV7QJh.07Tc_CCQ-1770562506-1.2.1.1-cf1i3oYooDb3HAHNUADS1vRLmQSA6Bgd7DCvWPAyJdgZIXc74nqqAH8UrHtjoIPz_HwOARBq_YDgHkL61a5SYtP8zDGdtmoFB0tX5.5tftoFvCoqTXsAIRnaM.VsJ6shnILq7RaJTegRNIajSPBovJwOx_nHtsbIsKXnBfPkEeYVK8PLA9tA_UxQhKmaOdFay8nzoLZTtga6oJ5g3HkrEBCVd7yAMeTDXYKQGVFkoL0',
        '__Secure-next-auth.callback-url': 'https%3A%2F%2Fchatgpt.com%2F',
        '__Secure-next-auth.session-token.0': 'eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..yKd7pOXfyebO5aal.kBsyS-1MaWi7Xo-cCt-faDKWsdRRqDK63uy52WZnG94nbprvlunh-yQu-EOEU5jC_9P2MmrNL6BJLVaPeMkuxJtVyWSWS7ZmN4-F-MCQ6et5HPVQmpnXpSxuDq9UUfHQm857EErtFjvmACiafNgrMPmu3S3tjkE-mhqvBzDuc4Fk6gIV8BCFMhCZCYVjPbvopVEsGYiYHtB--GWyvldL--sOOXAq_gOZTnH99xSzUPcPFK-5WH5_UJBzh2OmQ1y495Enap4Wacan2txrUGJ26aTRp5E1YRsLJknwCWxgZtouOa9sKd9hMxKHhxYXNohkm14qikGTutPs1xslbs8gRtl7QkCSPygV7w6gGcROuTKEMGStWMl17v8gXMnGpj9UfVBoktLdZEg39jGkpFKx4-gT9k_ChjB_j0elweX3eo01aTcrC2-jRafBiK_5uHIcQqxyhKgDR27llVJ5JJ30l4uHIAqSxeK2fhHDa8ZW8Sw5qi0mdPpvLNC7ksmJPNuRnQXlUDkHzi03BMK19jfFvMbooIBx4okw_hx12Z-cKJHH8dZeYr0q8UqBjM3flmLyj30Bgo1JMVr1m9d3rR4Gq2n0J0aWMifuks3rpSemRz195fAUVFzaTBZNwB5tW8DceP8SzPk6gcDtmCMe79ojT6GUqYTV_MjrGsm5aYz7EUi_-zweuLT6WU3Mxo_LWlEtZ6Euj8Y0Ut0J-lohT5GqUMy-7Cs4pcOt1dm2HPy45i_8YBnrKtf83xl83AZZrA8V5Rh_LyB3XotvWZT-CQ-YUPok4dYUL0Qbaowy0D_nWdiybNQSTf_kruczvVKknOsZrkKWL3CNpij2puVcfBiewEcdfQC06Mg2NffIUWLJiyaw7EAtoY2NlU3uUHpz_tRf9VOP49PHZ89mJdlsuXTQxtk1ZBaoUBkBrAiOHhz-10wobfim-JsjSOC6Zf2eIhtFZ9u_nIKOaDV2qFYMW0pByHkm5rQhs79-ozY5PNlr1qZsJCPaqS5EsGkZAmyHzGWwtDcux9B0ZbYSjmPuXY7v9oE5o4nKR1J3H88EaMgZfhD2h50LugY9W1WJIzrrePop0vJ3NTLyX4-PFBF9oKDIS8Dwef2UYFS5SaVadrO2PCa1ZKchGP94zUY_UvH6gmZL4XFqc4pGkHIdyS62nOb98bmobvcSwarquUoWDfbuHNJ9bby_Hnp7CpuTjVR9GURzboC6RmS7cz6JGWUlZ9_tLiCBh3fxef8omhyIExWjadCCUwkQj6Ml-PHbV_8wAMWcq3mqTw330ptZvz8774JuG_vu731keYalYOU3lbWZP620FNtyl7UTDPjbbsft4bvBmuHTRWQwLbKCkLUdUI_VDGgm2wpOghQEWIwT4-WCuz7NgUXHZlUlA9KbdH_5LFLSrlZvRk1OWExZK06BND7rAOCJHwyesFa7SDn0mVkIGrDZ7uH-pGZe1RnKsQ2qfO4KfSaV7LxA3oMc-4V9gPrz7dTqqxJSIQ0Opki_t8T7E_1tGwGD_u_jS9nA_dvKT7q9QoFLLDfIoPXXU7qWCMVB3fu4eA5d4fcpk8PC_nkqDckQlTzFdRFxdzdwgAkaD7oyJ47gJtZzJun7gYlRbdFX3AzaouekqLvco1SlU3j4pIJrPZbfF7CVrZFMB1KIUnMM80rosSadCKwgkoD455wFK4U6OVeQh2UdKm--YN1a5ejK1AEqWQSMDutoKolcYo0WQCiS1646R1cx_cMb4S-BkPfea57DDOa7zCLcjuX7QyP3DeFn9nYvXhRr2qP3qbm8C5WhF3r1gc0nisrImu7kfLH8oqbXrsV4ACcFQ7et3Ux-2pRes0nPybA5ehkneyIbrJNnXh6jHAbx2xboCZ7DSQ2JCKEg2ivaAaSyyE_QR8ucJPqQKIVXw_6Wxf1IOECXLSNyNfUTmJ8GGF3Wto_UmtPZ8l0B1DO_X4ON_n6N3iJsk9MrvfeCJrUe5jGFZRMQ-3xxfKlPwsZk0Mv8TDrP8UWZ4n_cOWdq-qU4bsj3RadMG9X_da4Hf9YlfDjlZaTQ1ZAnnqjuQeWgxdqEueLB7EgniMAzfJ38aTikKwD2TPW7O0OV5e8N0DF7DG2h0S-Cq0BOk-xYZj1QlRymrFSLVslhkde_nKY1za4lzJFeBdD1nTfUdDjR-2rsvrddiwMuqCZcsdkR88JuipoDXEHZUUHMkS2oHoa25MU0Zt0t0Abthbn4VinIbfgTaU3HuCxGnxSwHACk186nGlGq2OFqpY0M956Vc-akFGHPf2JNPhr8uzL1xXliNmNIWgb91ra4QN6osoAa7p2j9_t3kkqQYas5OE7z1GHZi7ESO3miFNI8pxRkoOYw3q_qGOjm2ovreERy0--xEg2OkAvwLq7iwos2Pw0_yZ9ald6MF3bogP5d3ZQg2c4RcxZ5zUmou2BcLldxZKet_lix9irtsnIbxp0Ui3D_iSQlu3c1Rf4CLu77i-boNxY4VJWarIdoO6xNLsCJMTKQc1UD6Uzku9LoWuxfOaNk8M_sulgq_KknCoznHqG0Rs5YW6lDfPiU6WqbWvG9WvpQj5Nv15TmVgW3R6CrOKpDR9EG9MvWCQ_1Ck0Dyhv5SCBBST00SCI3MADR_qxwmcMNDaVI7tHNC62l5tpxaeWf1Tm9tEfIuUuBJz57i_9r5BUI3tNwRxk0wucmHwFpcW-t2ul7gW7HUJmtmPDh7z73qVCsNbDymLW7-RkbD_DGIxtzvGN9PWDMedLKhdsS2nkHZcnJGAIYIUelWXptUlt7cmidipgmvyUkepT05BAGFv9IFlsje2ytqtpWt7TEA-y7rY6s9OBTMSXyR_OoBvWDTbYOVMRn0bHE4IrcKpj-boEM9VYzNGf9JdaqAOGC2W8iOZPId_jspEeeboAIyf3Zsnut23pSIN0lkwa7v0jtHb0va22T-MZsKxsJmeCkCHhz9agENpYQfxDd7UY0AG03bpS0CH9CU__8S8-SfSl8a8niDeqWovqrBdwzwPu15oOD4wNlNrpWGnQrOi_6gC54DvEPO8SA3uhXX9NSGSn3Vt9kbxZnkCyN6QcGcsQTg6ugzi0sJNwq58fuq2gq9JKMaoQBXLu3DCW5L54o5jpksb_1Tun4Yc6jRKVE6exB8UJrt5rG97BAQV0iRKc87NsATPzooda1goyOZdW-exlszXuisnutz0AyfP3jv-JkhBkQp53Rezj6VTxyf-3feQGzkmD-DvhZTzku4xUkXBkpothy0NEh1GzmVAqIHsIjbJ3Hi1geIIH8tZ7s6S7qLasgj59gxrEkM8Y2D5wQD_KdoneBrsrAikyyRa-JLIVPN5yq4h6Uy9Rx1WUlzXB0vJTCrHMWIW-uc5f9bkCLLet9AcA1M5vX5EW1ry04PmZvFCejoovYzJu39qZgA43f3wJV7f3xvFg4xEHR7w9IGTCGO-YmI-ApyD-cDFv7_cZtFeS-JBkLHILorUUpHn_Nm1n6bV4JwobfTM0cSDEtcdUk7saB8linjstQMblH6wVVgdhEEulR_uJrC7Q1pEhfnC_QmRZmkrwraquebYRoj7GJw1urPtXIPuXjezMjDdYRBItTktr_V5s1iPt_CGK_KCHqq7w8eeZEOshoSk1pC2_4CdHL-yEoUx6VbPz8Uq2qjK7Zrzx-W2ADj61BNXTRJ-JssjitqL4iGfLymYbeenyjEN_VntoCjGbXKb-ow1d4LBvqeQqW2LI0UnTdqRr_P2yqCzTQ6WWQqGAbo8bE1b1pOIU9BIho6RGZ2o8fgxBs9maTAakHmzJAfknohulzPTFychSwGrTwheRp6KuWsGWCYgy3vpoiX3d0ALKLrmhvrqi_HzEdh_NrCSD30_HQk6RJagelU6w',
        '__Secure-next-auth.session-token.1': 'cf9LSPnzHA3B_D1uOZtcQedttfDikxu86q9fEw-8uNfU71Rlk3lsn7qrPNV5LQ9L7qd3vwdnokYieLp5Fb3cWvRSuWOjeSp-epcJHpCX2ziB2nJlQDh2H9hHIuAElPekXn1E4gdWEGIEEZ6gkkRv2laYmCb1shtRi7Y182zxFTRCLIPRjQoyfsAZHHv-cZEkdWnvw_7LAliAcu7PgFMpHpog24Ig.ebPBq-hQaZoswaGOtAPddw',
        'oai-client-auth-info': '%7B%22user%22%3A%7B%22name%22%3A%22FFF%22%2C%22email%22%3A%22profesorlal5%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fff.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770558990065%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D',
        '_ga_9SHBSK2D9J': 'GS2.1.s1770558176$o4$g1$t1770558990$j59$l0$h0',
        'oai-gn': '',
        'oai-hm': 'ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE',
        'oai-sc': '0gAAAAABpiKQPyGgmpEH_DA4tXL5mtq1UroadG2IqOdiOPAnsgYVgAhAHe1BcaLjzx5xKi5Bw07penQUUs7VAM6cP7Fep30dynR3DiLD1ZhoFC6DFucWDPLazhPB7aQkzjUjBW31u0uMSB5lzvzunM8vk02M7pZaHgoEo7pyvukFWwv7BYuq_8Mq-oz60RC40A3_ds4-D44muzTKIUGDgGHAvl-JPi3WPFt7rZj3m0ajN7mRH-4Wi-GQ',
        '__cf_bm': 'dT5f27razy..pG41W5P0hS6BMvGv5ApwuKW_P3B9pts-1770562575.9542005-1.0.1.1-8mP3wd6dD7OW5Wjc3xgXqVo_Fh0gBnhofhYy_Tzxc_U_TvYs3HRa.erhXxOKVPxzG3SkurNwTS1NE8ADPY24dQMIjknfaYyzLHbwO8t3oQDcqm7EeS68mGRznPoELapa',
        '_cfuvid': 'KIbXypYx4l9tW.5Zj1Praq42wzgCp_fogA814RTa3l4-1770562575.9542005-1.0.1.1-pbCy.tgwNdtySdrJmgkQNwP0KiXW2y5zFN3GYRyj8Sg',
        '_dd_s': 'aid=1b42e6ea-3c02-48bd-830d-92a29fb20553&rum=0&expire=1770559892846&logs=1&id=28080f70-f74c-4c58-af10-98330710f7f5&created=1770558153456',
    }
    
    headers = {
        'accept': '*/*',
        'accept-language': 'ru,en;q=0.9,en-GB;q=0.8,en-US;q=0.7',
        'authorization': 'Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjE5MzQ0ZTY1LWJiYzktNDRkMS1hOWQwLWY5NTdiMDc5YmQwZSIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS92MSJdLCJjbGllbnRfaWQiOiJhcHBfWDh6WTZ2VzJwUTl0UjNkRTduSzFqTDVnSCIsImV4cCI6MTc3MTIyOTczNiwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9hdXRoIjp7ImNoYXRncHRfYWNjb3VudF9pZCI6IjI1ZWExNjFjLTJmMDEtNDAwZi04M2YzLTJjZTk3MTg3NTdhYSIsImNoYXRncHRfYWNjb3VudF91c2VyX2lkIjoidXNlci1lRVlxWmc5VURvZFJ2c3daRlpSS0lWYnlfXzI1ZWExNjFjLTJmMDEtNDAwZi04M2YzLTJjZTk3MTg3NTdhYSIsImNoYXRncHRfY29tcHV0ZV9yZXNpZGVuY3kiOiJub19jb25zdHJhaW50IiwiY2hhdGdwdF9wbGFuX3R5cGUiOiJmcmVlIiwiY2hhdGdwdF91c2VyX2lkIjoidXNlci1lRVlxWmc5VURvZFJ2c3daRlpSS0lWYnkiLCJ1c2VyX2lkIjoidXNlci1lRVlxWmc5VURvZFJ2c3daRlpSS0lWYnkifSwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9wcm9maWxlIjp7ImVtYWlsIjoicHJvZmVzb3JsYWw1QGdtYWlsLmNvbSIsImVtYWlsX3ZlcmlmaWVkIjp0cnVlfSwiaWF0IjoxNzcwMzY1NzM2LCJpc3MiOiJodHRwczovL2F1dGgub3BlbmFpLmNvbSIsImp0aSI6IjNjYzk3ODgxLWU4NTUtNDNjMS1iNjAyLWE1NDIxMGMxODY2NCIsIm5iZiI6MTc3MDM2NTczNiwicHdkX2F1dGhfdGltZSI6MTc3MDM2NTczNDEwMywic2NwIjpbIm9wZW5pZCIsImVtYWlsIiwicHJvZmlsZSIsIm9mZmxpbmVfYWNjZXNzIiwibW9kZWwucmVxdWVzdCIsIm1vZGVsLnJlYWQiLCJvcmdhbml6YXRpb24ucmVhZCIsIm9yZ2FuaXphdGlvbi53cml0ZSJdLCJzZXNzaW9uX2lkIjoiYXV0aHNlc3NfeGVRb3hOcFRiczdsZnZGSEJlUVY0OFA5Iiwic3ViIjoiZ29vZ2xlLW9hdXRoMnwxMDg1MjgxNTg0ODk2NDc4OTcxNzAifQ.a3jjNh7ORKfawJ6cLFoLosK7ES0d7WquZ1bOJHMKvPUavQ4gYrrd1P90vJMFyggwX4PAB4yjvVN2x_zuGGpCeqcdK7oOVcaY6PquMgc5gQurxnXe5FTCJSzsnb9fSk6D4QQwMhd2mN6j7wpwaMgszIYobtLYcTidKxvrBPV41yoSzFBsFQfhyXIOyd7QPfUQY2nTbqWIlXHRvyZTzQBQDmFhNeEJtl3p20vUqpVf9Pem8HByx2_Soc76BkHrwTZv_6NdjeOQXeGUPe6uqlNJKAleKTw98scni-wRtwsHzH8w3HftcDmySXNDe9sm6ujXRwffxpI_sjEkxgHxO0AhgW7hyzTzi3tZofoV1nLINlKUEljzNiVfFUcPb5sW4MjAbu7dqV07zBoAHu2mvp74QK49iW2nlugoN5VwTHfsW6e1263a7ZiXWuI3sORO3dDEPnwM7qYrLq2Kdfn11ip2E9J2Nk8y2jceICffvrcfnWpv5etd5uaLW_IgfF-pQzALJxy_tuIX_Xi5Anhytd3hceAt66_1-gw3uj-54xttqF7UZu5Dn4W3T7Hq7pAE1eW-UYmr2oCJVzpZOehzgUDEJUr_nJp7Y6GZdTplUwKJwQVbbwuiJoA3s_jm58qNJ4srgxVpLsYYKAklPd-_lcJd4JoXOpXPBE-PI6q6WKZW-dk',
        'content-type': 'application/json',
        'oai-client-build-number': '4480993',
        'oai-client-version': 'prod-7c2e8d83df2cf0b6eaa11ba7b37f1605384da182',
        'oai-device-id': 'f49caa8e-b1a8-472a-b61a-61b8f8aac215',
        'oai-language': 'ru-RU',
        'origin': 'https://chatgpt.com',
        'priority': 'u=1, i',
        'referer': 'https://chatgpt.com/',
        'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Microsoft Edge";v="144"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
        'x-conduit-token': 'no-token',
        'x-oai-turn-trace-id': 'b929398d-fb8d-4f30-8257-b0a87c949197',
        # 'cookie': 'oai-did=f49caa8e-b1a8-472a-b61a-61b8f8aac215; _ga=GA1.1.292430756.1770133139; g_state={"i_l":1,"i_ll":1770362114073,"i_b":"IFhSqKx7PLKGzqT/eTztkhsTEh4Rxkjg/PAh3jKLBOw","i_e":{"enable_itp_optimization":0},"i_p":1770140342337}; oai-hlib=true; _account_is_fedramp=false; __cflb=0H28vzvP5FJafnkHxih7XmuVuidhF3NrzJ7BynNR7qu; _uasid="Z0FBQUFBQnBpS0VnTGJLeEJYLUVUSW5tNHFDV0NUSi04RktubXZxRDJkc0JQV0w5UDJMRk1Xd0lsWEVKaEpmdjJNTERfNVZidEI1X2hwN0ZlZ0VydzRpTFUwd3o0SC1BNEx6N29nWHlRRVVCNDNfeUM4dTdteTQ3aXB3WktyR0ZFSGxlMlZ4czdWMk1uR2wwRG9hSTkzTzBMRkFLRVh1M05PVl9iOGhTbDN5cndTQzRzVHlOSFVhelduNE9pZENmaDF5UkF2ZW11Z0g3TmlaTEJvUGVXOGI1LVB5VVp6RGppT1REaXdHblc4U2thbV91a3ZiaWh6MXpXV0VJdWUxSUhmVjNCU2VmSXhCT2J2LVE0ejlsQmRFNWt1U1RZREJxSWZPYWFEcTVVRE51bXpZMW9KZGNnbGFMeWRQSWRkQXBfMWZaTzVHZXZJUnp1MDRocHNEZ2s4dzRLQUtMZVJVZkF3PT0="; _umsid="Z0FBQUFBQnBpS0VnMm9mQUYxbWpITkxlckdYVTh5U3dKMXdxUEVwVnV3U3RPM09TQ3I4ZGZ5VEU0MjF4ZEViWmdkV2ZqdHZPcjdzTnZONEQ1czlFRFp4endFNFVsUFZxZEZqT1E1aDJOUkRhdTNZRVhrREstOFpJSHpFY3N6dlJBRlRXWk1HR0c5ZDR3eGhscm1XdkRqYXdvWTFaZUhjbDlRb2NJTEMzLWxUSkZEWkkteklVN1FPazI3MWhqaGtaek9Td0h2SkJXcERnZU5LV2hlVjI5Z0xSTURvamRzUGV2aExYOUdwbGhUNHQ4Vm4xZWo2ZmNUTT0="; __Host-next-auth.csrf-token=5f9ae71c0f66fb0dfe7b080becc5b4d3a28482752652f86f2d61c69cfd75c78f%7Cf399c3ea965af14e7ebd1691d09d48969f78bb90fb15c7a283bcb4b1fb47e2d5; cf_clearance=NMUsrVKTrmJPtC9sIsiSWi.pNIoMjV7QJh.07Tc_CCQ-1770562506-1.2.1.1-cf1i3oYooDb3HAHNUADS1vRLmQSA6Bgd7DCvWPAyJdgZIXc74nqqAH8UrHtjoIPz_HwOARBq_YDgHkL61a5SYtP8zDGdtmoFB0tX5.5tftoFvCoqTXsAIRnaM.VsJ6shnILq7RaJTegRNIajSPBovJwOx_nHtsbIsKXnBfPkEeYVK8PLA9tA_UxQhKmaOdFay8nzoLZTtga6oJ5g3HkrEBCVd7yAMeTDXYKQGVFkoL0; __Secure-next-auth.callback-url=https%3A%2F%2Fchatgpt.com%2F; __Secure-next-auth.session-token.0=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..yKd7pOXfyebO5aal.kBsyS-1MaWi7Xo-cCt-faDKWsdRRqDK63uy52WZnG94nbprvlunh-yQu-EOEU5jC_9P2MmrNL6BJLVaPeMkuxJtVyWSWS7ZmN4-F-MCQ6et5HPVQmpnXpSxuDq9UUfHQm857EErtFjvmACiafNgrMPmu3S3tjkE-mhqvBzDuc4Fk6gIV8BCFMhCZCYVjPbvopVEsGYiYHtB--GWyvldL--sOOXAq_gOZTnH99xSzUPcPFK-5WH5_UJBzh2OmQ1y495Enap4Wacan2txrUGJ26aTRp5E1YRsLJknwCWxgZtouOa9sKd9hMxKHhxYXNohkm14qikGTutPs1xslbs8gRtl7QkCSPygV7w6gGcROuTKEMGStWMl17v8gXMnGpj9UfVBoktLdZEg39jGkpFKx4-gT9k_ChjB_j0elweX3eo01aTcrC2-jRafBiK_5uHIcQqxyhKgDR27llVJ5JJ30l4uHIAqSxeK2fhHDa8ZW8Sw5qi0mdPpvLNC7ksmJPNuRnQXlUDkHzi03BMK19jfFvMbooIBx4okw_hx12Z-cKJHH8dZeYr0q8UqBjM3flmLyj30Bgo1JMVr1m9d3rR4Gq2n0J0aWMifuks3rpSemRz195fAUVFzaTBZNwB5tW8DceP8SzPk6gcDtmCMe79ojT6GUqYTV_MjrGsm5aYz7EUi_-zweuLT6WU3Mxo_LWlEtZ6Euj8Y0Ut0J-lohT5GqUMy-7Cs4pcOt1dm2HPy45i_8YBnrKtf83xl83AZZrA8V5Rh_LyB3XotvWZT-CQ-YUPok4dYUL0Qbaowy0D_nWdiybNQSTf_kruczvVKknOsZrkKWL3CNpij2puVcfBiewEcdfQC06Mg2NffIUWLJiyaw7EAtoY2NlU3uUHpz_tRf9VOP49PHZ89mJdlsuXTQxtk1ZBaoUBkBrAiOHhz-10wobfim-JsjSOC6Zf2eIhtFZ9u_nIKOaDV2qFYMW0pByHkm5rQhs79-ozY5PNlr1qZsJCPaqS5EsGkZAmyHzGWwtDcux9B0ZbYSjmPuXY7v9oE5o4nKR1J3H88EaMgZfhD2h50LugY9W1WJIzrrePop0vJ3NTLyX4-PFBF9oKDIS8Dwef2UYFS5SaVadrO2PCa1ZKchGP94zUY_UvH6gmZL4XFqc4pGkHIdyS62nOb98bmobvcSwarquUoWDfbuHNJ9bby_Hnp7CpuTjVR9GURzboC6RmS7cz6JGWUlZ9_tLiCBh3fxef8omhyIExWjadCCUwkQj6Ml-PHbV_8wAMWcq3mqTw330ptZvz8774JuG_vu731keYalYOU3lbWZP620FNtyl7UTDPjbbsft4bvBmuHTRWQwLbKCkLUdUI_VDGgm2wpOghQEWIwT4-WCuz7NgUXHZlUlA9KbdH_5LFLSrlZvRk1OWExZK06BND7rAOCJHwyesFa7SDn0mVkIGrDZ7uH-pGZe1RnKsQ2qfO4KfSaV7LxA3oMc-4V9gPrz7dTqqxJSIQ0Opki_t8T7E_1tGwGD_u_jS9nA_dvKT7q9QoFLLDfIoPXXU7qWCMVB3fu4eA5d4fcpk8PC_nkqDckQlTzFdRFxdzdwgAkaD7oyJ47gJtZzJun7gYlRbdFX3AzaouekqLvco1SlU3j4pIJrPZbfF7CVrZFMB1KIUnMM80rosSadCKwgkoD455wFK4U6OVeQh2UdKm--YN1a5ejK1AEqWQSMDutoKolcYo0WQCiS1646R1cx_cMb4S-BkPfea57DDOa7zCLcjuX7QyP3DeFn9nYvXhRr2qP3qbm8C5WhF3r1gc0nisrImu7kfLH8oqbXrsV4ACcFQ7et3Ux-2pRes0nPybA5ehkneyIbrJNnXh6jHAbx2xboCZ7DSQ2JCKEg2ivaAaSyyE_QR8ucJPqQKIVXw_6Wxf1IOECXLSNyNfUTmJ8GGF3Wto_UmtPZ8l0B1DO_X4ON_n6N3iJsk9MrvfeCJrUe5jGFZRMQ-3xxfKlPwsZk0Mv8TDrP8UWZ4n_cOWdq-qU4bsj3RadMG9X_da4Hf9YlfDjlZaTQ1ZAnnqjuQeWgxdqEueLB7EgniMAzfJ38aTikKwD2TPW7O0OV5e8N0DF7DG2h0S-Cq0BOk-xYZj1QlRymrFSLVslhkde_nKY1za4lzJFeBdD1nTfUdDjR-2rsvrddiwMuqCZcsdkR88JuipoDXEHZUUHMkS2oHoa25MU0Zt0t0Abthbn4VinIbfgTaU3HuCxGnxSwHACk186nGlGq2OFqpY0M956Vc-akFGHPf2JNPhr8uzL1xXliNmNIWgb91ra4QN6osoAa7p2j9_t3kkqQYas5OE7z1GHZi7ESO3miFNI8pxRkoOYw3q_qGOjm2ovreERy0--xEg2OkAvwLq7iwos2Pw0_yZ9ald6MF3bogP5d3ZQg2c4RcxZ5zUmou2BcLldxZKet_lix9irtsnIbxp0Ui3D_iSQlu3c1Rf4CLu77i-boNxY4VJWarIdoO6xNLsCJMTKQc1UD6Uzku9LoWuxfOaNk8M_sulgq_KknCoznHqG0Rs5YW6lDfPiU6WqbWvG9WvpQj5Nv15TmVgW3R6CrOKpDR9EG9MvWCQ_1Ck0Dyhv5SCBBST00SCI3MADR_qxwmcMNDaVI7tHNC62l5tpxaeWf1Tm9tEfIuUuBJz57i_9r5BUI3tNwRxk0wucmHwFpcW-t2ul7gW7HUJmtmPDh7z73qVCsNbDymLW7-RkbD_DGIxtzvGN9PWDMedLKhdsS2nkHZcnJGAIYIUelWXptUlt7cmidipgmvyUkepT05BAGFv9IFlsje2ytqtpWt7TEA-y7rY6s9OBTMSXyR_OoBvWDTbYOVMRn0bHE4IrcKpj-boEM9VYzNGf9JdaqAOGC2W8iOZPId_jspEeeboAIyf3Zsnut23pSIN0lkwa7v0jtHb0va22T-MZsKxsJmeCkCHhz9agENpYQfxDd7UY0AG03bpS0CH9CU__8S8-SfSl8a8niDeqWovqrBdwzwPu15oOD4wNlNrpWGnQrOi_6gC54DvEPO8SA3uhXX9NSGSn3Vt9kbxZnkCyN6QcGcsQTg6ugzi0sJNwq58fuq2gq9JKMaoQBXLu3DCW5L54o5jpksb_1Tun4Yc6jRKVE6exB8UJrt5rG97BAQV0iRKc87NsATPzooda1goyOZdW-exlszXuisnutz0AyfP3jv-JkhBkQp53Rezj6VTxyf-3feQGzkmD-DvhZTzku4xUkXBkpothy0NEh1GzmVAqIHsIjbJ3Hi1geIIH8tZ7s6S7qLasgj59gxrEkM8Y2D5wQD_KdoneBrsrAikyyRa-JLIVPN5yq4h6Uy9Rx1WUlzXB0vJTCrHMWIW-uc5f9bkCLLet9AcA1M5vX5EW1ry04PmZvFCejoovYzJu39qZgA43f3wJV7f3xvFg4xEHR7w9IGTCGO-YmI-ApyD-cDFv7_cZtFeS-JBkLHILorUUpHn_Nm1n6bV4JwobfTM0cSDEtcdUk7saB8linjstQMblH6wVVgdhEEulR_uJrC7Q1pEhfnC_QmRZmkrwraquebYRoj7GJw1urPtXIPuXjezMjDdYRBItTktr_V5s1iPt_CGK_KCHqq7w8eeZEOshoSk1pC2_4CdHL-yEoUx6VbPz8Uq2qjK7Zrzx-W2ADj61BNXTRJ-JssjitqL4iGfLymYbeenyjEN_VntoCjGbXKb-ow1d4LBvqeQqW2LI0UnTdqRr_P2yqCzTQ6WWQqGAbo8bE1b1pOIU9BIho6RGZ2o8fgxBs9maTAakHmzJAfknohulzPTFychSwGrTwheRp6KuWsGWCYgy3vpoiX3d0ALKLrmhvrqi_HzEdh_NrCSD30_HQk6RJagelU6w; __Secure-next-auth.session-token.1=cf9LSPnzHA3B_D1uOZtcQedttfDikxu86q9fEw-8uNfU71Rlk3lsn7qrPNV5LQ9L7qd3vwdnokYieLp5Fb3cWvRSuWOjeSp-epcJHpCX2ziB2nJlQDh2H9hHIuAElPekXn1E4gdWEGIEEZ6gkkRv2laYmCb1shtRi7Y182zxFTRCLIPRjQoyfsAZHHv-cZEkdWnvw_7LAliAcu7PgFMpHpog24Ig.ebPBq-hQaZoswaGOtAPddw; oai-client-auth-info=%7B%22user%22%3A%7B%22name%22%3A%22FFF%22%2C%22email%22%3A%22profesorlal5%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fff.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770558990065%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D; _ga_9SHBSK2D9J=GS2.1.s1770558176$o4$g1$t1770558990$j59$l0$h0; oai-gn=; oai-hm=ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE; oai-sc=0gAAAAABpiKQPyGgmpEH_DA4tXL5mtq1UroadG2IqOdiOPAnsgYVgAhAHe1BcaLjzx5xKi5Bw07penQUUs7VAM6cP7Fep30dynR3DiLD1ZhoFC6DFucWDPLazhPB7aQkzjUjBW31u0uMSB5lzvzunM8vk02M7pZaHgoEo7pyvukFWwv7BYuq_8Mq-oz60RC40A3_ds4-D44muzTKIUGDgGHAvl-JPi3WPFt7rZj3m0ajN7mRH-4Wi-GQ; __cf_bm=dT5f27razy..pG41W5P0hS6BMvGv5ApwuKW_P3B9pts-1770562575.9542005-1.0.1.1-8mP3wd6dD7OW5Wjc3xgXqVo_Fh0gBnhofhYy_Tzxc_U_TvYs3HRa.erhXxOKVPxzG3SkurNwTS1NE8ADPY24dQMIjknfaYyzLHbwO8t3oQDcqm7EeS68mGRznPoELapa; _cfuvid=KIbXypYx4l9tW.5Zj1Praq42wzgCp_fogA814RTa3l4-1770562575.9542005-1.0.1.1-pbCy.tgwNdtySdrJmgkQNwP0KiXW2y5zFN3GYRyj8Sg; _dd_s=aid=1b42e6ea-3c02-48bd-830d-92a29fb20553&rum=0&expire=1770559892846&logs=1&id=28080f70-f74c-4c58-af10-98330710f7f5&created=1770558153456',
    }

    json_data = {
        'action': 'next',
        'fork_from_shared_post': False,
        'parent_message_id': 'client-created-root',
        'model': 'auto',
        'timezone_offset_min': -360,
        'timezone': 'Etc/GMT-6',
        'conversation_mode': {
            'kind': 'primary_assistant',
        },
        'system_hints': [],
        'partial_query': {
            'id': '34273b57-1ed2-42d5-bbba-da3217a5c092',
            'author': {
                'role': 'user',
            },
            'content': {
                'content_type': 'text',
                'parts': [
                    prompt,
                ],
            },
        },
        'supports_buffering': True,
        'supported_encodings': [
            'v1',
        ],
        'client_contextual_info': {
            'app_name': 'chatgpt.com',
        },
    }

    response = requests.post(
        'https://chatgpt.com/backend-api/f/conversation/prepare',
        cookies=cookies,
        headers=headers,
        json=json_data,
    )

    token = response.json()['conduit_token']


    cookies = {
        'oai-did': 'f49caa8e-b1a8-472a-b61a-61b8f8aac215',
        '_ga': 'GA1.1.292430756.1770133139',
        'g_state': '{"i_l":1,"i_ll":1770362114073,"i_b":"IFhSqKx7PLKGzqT/eTztkhsTEh4Rxkjg/PAh3jKLBOw","i_e":{"enable_itp_optimization":0},"i_p":1770140342337}',
        'oai-hlib': 'true',
        '_account_is_fedramp': 'false',
        '__cflb': '0H28vzvP5FJafnkHxih7XmuVuidhF3NrzJ7BynNR7qu',
        '__Host-next-auth.csrf-token': '5f9ae71c0f66fb0dfe7b080becc5b4d3a28482752652f86f2d61c69cfd75c78f%7Cf399c3ea965af14e7ebd1691d09d48969f78bb90fb15c7a283bcb4b1fb47e2d5',
        'cf_clearance': 'NMUsrVKTrmJPtC9sIsiSWi.pNIoMjV7QJh.07Tc_CCQ-1770562506-1.2.1.1-cf1i3oYooDb3HAHNUADS1vRLmQSA6Bgd7DCvWPAyJdgZIXc74nqqAH8UrHtjoIPz_HwOARBq_YDgHkL61a5SYtP8zDGdtmoFB0tX5.5tftoFvCoqTXsAIRnaM.VsJ6shnILq7RaJTegRNIajSPBovJwOx_nHtsbIsKXnBfPkEeYVK8PLA9tA_UxQhKmaOdFay8nzoLZTtga6oJ5g3HkrEBCVd7yAMeTDXYKQGVFkoL0',
        '__Secure-next-auth.callback-url': 'https%3A%2F%2Fchatgpt.com%2F',
        '__Secure-next-auth.session-token.0': 'eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..yKd7pOXfyebO5aal.kBsyS-1MaWi7Xo-cCt-faDKWsdRRqDK63uy52WZnG94nbprvlunh-yQu-EOEU5jC_9P2MmrNL6BJLVaPeMkuxJtVyWSWS7ZmN4-F-MCQ6et5HPVQmpnXpSxuDq9UUfHQm857EErtFjvmACiafNgrMPmu3S3tjkE-mhqvBzDuc4Fk6gIV8BCFMhCZCYVjPbvopVEsGYiYHtB--GWyvldL--sOOXAq_gOZTnH99xSzUPcPFK-5WH5_UJBzh2OmQ1y495Enap4Wacan2txrUGJ26aTRp5E1YRsLJknwCWxgZtouOa9sKd9hMxKHhxYXNohkm14qikGTutPs1xslbs8gRtl7QkCSPygV7w6gGcROuTKEMGStWMl17v8gXMnGpj9UfVBoktLdZEg39jGkpFKx4-gT9k_ChjB_j0elweX3eo01aTcrC2-jRafBiK_5uHIcQqxyhKgDR27llVJ5JJ30l4uHIAqSxeK2fhHDa8ZW8Sw5qi0mdPpvLNC7ksmJPNuRnQXlUDkHzi03BMK19jfFvMbooIBx4okw_hx12Z-cKJHH8dZeYr0q8UqBjM3flmLyj30Bgo1JMVr1m9d3rR4Gq2n0J0aWMifuks3rpSemRz195fAUVFzaTBZNwB5tW8DceP8SzPk6gcDtmCMe79ojT6GUqYTV_MjrGsm5aYz7EUi_-zweuLT6WU3Mxo_LWlEtZ6Euj8Y0Ut0J-lohT5GqUMy-7Cs4pcOt1dm2HPy45i_8YBnrKtf83xl83AZZrA8V5Rh_LyB3XotvWZT-CQ-YUPok4dYUL0Qbaowy0D_nWdiybNQSTf_kruczvVKknOsZrkKWL3CNpij2puVcfBiewEcdfQC06Mg2NffIUWLJiyaw7EAtoY2NlU3uUHpz_tRf9VOP49PHZ89mJdlsuXTQxtk1ZBaoUBkBrAiOHhz-10wobfim-JsjSOC6Zf2eIhtFZ9u_nIKOaDV2qFYMW0pByHkm5rQhs79-ozY5PNlr1qZsJCPaqS5EsGkZAmyHzGWwtDcux9B0ZbYSjmPuXY7v9oE5o4nKR1J3H88EaMgZfhD2h50LugY9W1WJIzrrePop0vJ3NTLyX4-PFBF9oKDIS8Dwef2UYFS5SaVadrO2PCa1ZKchGP94zUY_UvH6gmZL4XFqc4pGkHIdyS62nOb98bmobvcSwarquUoWDfbuHNJ9bby_Hnp7CpuTjVR9GURzboC6RmS7cz6JGWUlZ9_tLiCBh3fxef8omhyIExWjadCCUwkQj6Ml-PHbV_8wAMWcq3mqTw330ptZvz8774JuG_vu731keYalYOU3lbWZP620FNtyl7UTDPjbbsft4bvBmuHTRWQwLbKCkLUdUI_VDGgm2wpOghQEWIwT4-WCuz7NgUXHZlUlA9KbdH_5LFLSrlZvRk1OWExZK06BND7rAOCJHwyesFa7SDn0mVkIGrDZ7uH-pGZe1RnKsQ2qfO4KfSaV7LxA3oMc-4V9gPrz7dTqqxJSIQ0Opki_t8T7E_1tGwGD_u_jS9nA_dvKT7q9QoFLLDfIoPXXU7qWCMVB3fu4eA5d4fcpk8PC_nkqDckQlTzFdRFxdzdwgAkaD7oyJ47gJtZzJun7gYlRbdFX3AzaouekqLvco1SlU3j4pIJrPZbfF7CVrZFMB1KIUnMM80rosSadCKwgkoD455wFK4U6OVeQh2UdKm--YN1a5ejK1AEqWQSMDutoKolcYo0WQCiS1646R1cx_cMb4S-BkPfea57DDOa7zCLcjuX7QyP3DeFn9nYvXhRr2qP3qbm8C5WhF3r1gc0nisrImu7kfLH8oqbXrsV4ACcFQ7et3Ux-2pRes0nPybA5ehkneyIbrJNnXh6jHAbx2xboCZ7DSQ2JCKEg2ivaAaSyyE_QR8ucJPqQKIVXw_6Wxf1IOECXLSNyNfUTmJ8GGF3Wto_UmtPZ8l0B1DO_X4ON_n6N3iJsk9MrvfeCJrUe5jGFZRMQ-3xxfKlPwsZk0Mv8TDrP8UWZ4n_cOWdq-qU4bsj3RadMG9X_da4Hf9YlfDjlZaTQ1ZAnnqjuQeWgxdqEueLB7EgniMAzfJ38aTikKwD2TPW7O0OV5e8N0DF7DG2h0S-Cq0BOk-xYZj1QlRymrFSLVslhkde_nKY1za4lzJFeBdD1nTfUdDjR-2rsvrddiwMuqCZcsdkR88JuipoDXEHZUUHMkS2oHoa25MU0Zt0t0Abthbn4VinIbfgTaU3HuCxGnxSwHACk186nGlGq2OFqpY0M956Vc-akFGHPf2JNPhr8uzL1xXliNmNIWgb91ra4QN6osoAa7p2j9_t3kkqQYas5OE7z1GHZi7ESO3miFNI8pxRkoOYw3q_qGOjm2ovreERy0--xEg2OkAvwLq7iwos2Pw0_yZ9ald6MF3bogP5d3ZQg2c4RcxZ5zUmou2BcLldxZKet_lix9irtsnIbxp0Ui3D_iSQlu3c1Rf4CLu77i-boNxY4VJWarIdoO6xNLsCJMTKQc1UD6Uzku9LoWuxfOaNk8M_sulgq_KknCoznHqG0Rs5YW6lDfPiU6WqbWvG9WvpQj5Nv15TmVgW3R6CrOKpDR9EG9MvWCQ_1Ck0Dyhv5SCBBST00SCI3MADR_qxwmcMNDaVI7tHNC62l5tpxaeWf1Tm9tEfIuUuBJz57i_9r5BUI3tNwRxk0wucmHwFpcW-t2ul7gW7HUJmtmPDh7z73qVCsNbDymLW7-RkbD_DGIxtzvGN9PWDMedLKhdsS2nkHZcnJGAIYIUelWXptUlt7cmidipgmvyUkepT05BAGFv9IFlsje2ytqtpWt7TEA-y7rY6s9OBTMSXyR_OoBvWDTbYOVMRn0bHE4IrcKpj-boEM9VYzNGf9JdaqAOGC2W8iOZPId_jspEeeboAIyf3Zsnut23pSIN0lkwa7v0jtHb0va22T-MZsKxsJmeCkCHhz9agENpYQfxDd7UY0AG03bpS0CH9CU__8S8-SfSl8a8niDeqWovqrBdwzwPu15oOD4wNlNrpWGnQrOi_6gC54DvEPO8SA3uhXX9NSGSn3Vt9kbxZnkCyN6QcGcsQTg6ugzi0sJNwq58fuq2gq9JKMaoQBXLu3DCW5L54o5jpksb_1Tun4Yc6jRKVE6exB8UJrt5rG97BAQV0iRKc87NsATPzooda1goyOZdW-exlszXuisnutz0AyfP3jv-JkhBkQp53Rezj6VTxyf-3feQGzkmD-DvhZTzku4xUkXBkpothy0NEh1GzmVAqIHsIjbJ3Hi1geIIH8tZ7s6S7qLasgj59gxrEkM8Y2D5wQD_KdoneBrsrAikyyRa-JLIVPN5yq4h6Uy9Rx1WUlzXB0vJTCrHMWIW-uc5f9bkCLLet9AcA1M5vX5EW1ry04PmZvFCejoovYzJu39qZgA43f3wJV7f3xvFg4xEHR7w9IGTCGO-YmI-ApyD-cDFv7_cZtFeS-JBkLHILorUUpHn_Nm1n6bV4JwobfTM0cSDEtcdUk7saB8linjstQMblH6wVVgdhEEulR_uJrC7Q1pEhfnC_QmRZmkrwraquebYRoj7GJw1urPtXIPuXjezMjDdYRBItTktr_V5s1iPt_CGK_KCHqq7w8eeZEOshoSk1pC2_4CdHL-yEoUx6VbPz8Uq2qjK7Zrzx-W2ADj61BNXTRJ-JssjitqL4iGfLymYbeenyjEN_VntoCjGbXKb-ow1d4LBvqeQqW2LI0UnTdqRr_P2yqCzTQ6WWQqGAbo8bE1b1pOIU9BIho6RGZ2o8fgxBs9maTAakHmzJAfknohulzPTFychSwGrTwheRp6KuWsGWCYgy3vpoiX3d0ALKLrmhvrqi_HzEdh_NrCSD30_HQk6RJagelU6w',
        '__Secure-next-auth.session-token.1': 'cf9LSPnzHA3B_D1uOZtcQedttfDikxu86q9fEw-8uNfU71Rlk3lsn7qrPNV5LQ9L7qd3vwdnokYieLp5Fb3cWvRSuWOjeSp-epcJHpCX2ziB2nJlQDh2H9hHIuAElPekXn1E4gdWEGIEEZ6gkkRv2laYmCb1shtRi7Y182zxFTRCLIPRjQoyfsAZHHv-cZEkdWnvw_7LAliAcu7PgFMpHpog24Ig.ebPBq-hQaZoswaGOtAPddw',
        'oai-client-auth-info': '%7B%22user%22%3A%7B%22name%22%3A%22FFF%22%2C%22email%22%3A%22profesorlal5%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fff.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770558990065%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D',
        '_ga_9SHBSK2D9J': 'GS2.1.s1770558176$o4$g1$t1770558990$j59$l0$h0',
        'oai-gn': '',
        'oai-hm': 'ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE',
        '_cfuvid': 'KIbXypYx4l9tW.5Zj1Praq42wzgCp_fogA814RTa3l4-1770562575.9542005-1.0.1.1-pbCy.tgwNdtySdrJmgkQNwP0KiXW2y5zFN3GYRyj8Sg',
        '_dd_s': 'aid=1b42e6ea-3c02-48bd-830d-92a29fb20553&rum=0&expire=1770560100814&logs=1&id=28080f70-f74c-4c58-af10-98330710f7f5&created=1770558153456',
        'oai-sc': '0gAAAAABpiKThdv6uzC3heahJ4-1my3tP2t6jFSkxQpNAv2PUbDJZPLDAdnPnIKfVb0Wdy6Swi_iz7OCsCNm733ooXIBc2RU3wHNWtPf-DQ3FtzaGp2HfXHy3NWrWdWfG-ILPbrwtmu-6mEA-KLSErUyR0ysxUVmgy_HgJYpzmODyXWla41ZWd1lhAo6IDHnr1oEqPH6jC6zaNlWnpIs8cqKMTXBRkpCjQzAO5dVQiWvaqDBtcdPUHf0',
        '_uasid': '"Z0FBQUFBQnBpS1RpMWxCNG5oMldzYnVqaUVPVWFZVFUxOEtaQ3BrejdGeHFEeFVULWVzWFlmelllZGQ4MEZWNGNJZnBYcGlFcXdIS1ZMelpjSjA5d3JBTmp4R21kdldZZ3hfWm51eDJCb0hQQXJKZXQtZ1JFQjdRRGk3TFlFMk91SE1JRVN6UmpXNFQxcklrRjktLWN3bWY1UU9JNml4clBlclpkOFR4UzZvOFpLODIza3IyZWE2ZmN1d2xQd2NfWk9ya1VuLTFqSnFrd2h1a3BYbTBtY05KeE85SzdWWDRUOVc3ZHoxNDduTXBoWTFmVHBjUkZveEpiVnMzcElQYWVNck1BWE5RTWkxWEQxcDBRTUNsNFRJSnpFZ0JiM25hdWtNX09zTlI0ckxVelJQTzJpd2pxSVo2UHdJcjZPUVVranlOcnBST2lfZ0dTT0xZczVWTDIzdlFGcEhfUlg3OEhnPT0="',
        '_umsid': '"Z0FBQUFBQnBpS1RpeDNlRDl2YV9wX09BMFRQNnZUUnhtN2JQNWVwclAzUlBtblhZbE5YVkxuZkZZM3R2a0hTYjVWUjM5Y0k5YW1YZXNFdjc4TGd0S19rdHNqRWlzTlc0UkVqVE5KVDJnMWVNMndNUDZDYW9sbUFLcHJhcHpCdkdaM3labzg2bHJxY3lLd1oyTDk0Zjc0SGdBMmI0dW9Zcl84eXZ1UE9qSjdyTFVQWVNhY0t4VG1lc2duYTBpYkpSUFBRVkFsVWJVeGo1MHdKX3IxZExXRVJKcVZRaEN6YW5YQTZFR0JQSHI5X1p0a3hPN3VaZktobz0="',
        '__cf_bm': 'R82eCaF8p2XGs9h1tZUuuPqdMctyo6Ym_78DWOJFKQc-1770562789.3867137-1.0.1.1-rCgS3FDpZWM4ZP1vZ68_bOHh2rO3Yf0W6QEZ0HsXoFToaQNUa9NgwHERY1bm8QctbDG.hbs0XU0HDrSr6V9F1C6W.epDgZ55_3f14THK1XfM5UT_QKBHgBgQS9fwZXua',
    }
    
    headers = {
        'accept': '*/*',
        'accept-language': 'ru,en;q=0.9,en-GB;q=0.8,en-US;q=0.7',
        'authorization': 'Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjE5MzQ0ZTY1LWJiYzktNDRkMS1hOWQwLWY5NTdiMDc5YmQwZSIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS92MSJdLCJjbGllbnRfaWQiOiJhcHBfWDh6WTZ2VzJwUTl0UjNkRTduSzFqTDVnSCIsImV4cCI6MTc3MTIyOTczNiwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9hdXRoIjp7ImNoYXRncHRfYWNjb3VudF9pZCI6IjI1ZWExNjFjLTJmMDEtNDAwZi04M2YzLTJjZTk3MTg3NTdhYSIsImNoYXRncHRfYWNjb3VudF91c2VyX2lkIjoidXNlci1lRVlxWmc5VURvZFJ2c3daRlpSS0lWYnlfXzI1ZWExNjFjLTJmMDEtNDAwZi04M2YzLTJjZTk3MTg3NTdhYSIsImNoYXRncHRfY29tcHV0ZV9yZXNpZGVuY3kiOiJub19jb25zdHJhaW50IiwiY2hhdGdwdF9wbGFuX3R5cGUiOiJmcmVlIiwiY2hhdGdwdF91c2VyX2lkIjoidXNlci1lRVlxWmc5VURvZFJ2c3daRlpSS0lWYnkiLCJ1c2VyX2lkIjoidXNlci1lRVlxWmc5VURvZFJ2c3daRlpSS0lWYnkifSwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9wcm9maWxlIjp7ImVtYWlsIjoicHJvZmVzb3JsYWw1QGdtYWlsLmNvbSIsImVtYWlsX3ZlcmlmaWVkIjp0cnVlfSwiaWF0IjoxNzcwMzY1NzM2LCJpc3MiOiJodHRwczovL2F1dGgub3BlbmFpLmNvbSIsImp0aSI6IjNjYzk3ODgxLWU4NTUtNDNjMS1iNjAyLWE1NDIxMGMxODY2NCIsIm5iZiI6MTc3MDM2NTczNiwicHdkX2F1dGhfdGltZSI6MTc3MDM2NTczNDEwMywic2NwIjpbIm9wZW5pZCIsImVtYWlsIiwicHJvZmlsZSIsIm9mZmxpbmVfYWNjZXNzIiwibW9kZWwucmVxdWVzdCIsIm1vZGVsLnJlYWQiLCJvcmdhbml6YXRpb24ucmVhZCIsIm9yZ2FuaXphdGlvbi53cml0ZSJdLCJzZXNzaW9uX2lkIjoiYXV0aHNlc3NfeGVRb3hOcFRiczdsZnZGSEJlUVY0OFA5Iiwic3ViIjoiZ29vZ2xlLW9hdXRoMnwxMDg1MjgxNTg0ODk2NDc4OTcxNzAifQ.a3jjNh7ORKfawJ6cLFoLosK7ES0d7WquZ1bOJHMKvPUavQ4gYrrd1P90vJMFyggwX4PAB4yjvVN2x_zuGGpCeqcdK7oOVcaY6PquMgc5gQurxnXe5FTCJSzsnb9fSk6D4QQwMhd2mN6j7wpwaMgszIYobtLYcTidKxvrBPV41yoSzFBsFQfhyXIOyd7QPfUQY2nTbqWIlXHRvyZTzQBQDmFhNeEJtl3p20vUqpVf9Pem8HByx2_Soc76BkHrwTZv_6NdjeOQXeGUPe6uqlNJKAleKTw98scni-wRtwsHzH8w3HftcDmySXNDe9sm6ujXRwffxpI_sjEkxgHxO0AhgW7hyzTzi3tZofoV1nLINlKUEljzNiVfFUcPb5sW4MjAbu7dqV07zBoAHu2mvp74QK49iW2nlugoN5VwTHfsW6e1263a7ZiXWuI3sORO3dDEPnwM7qYrLq2Kdfn11ip2E9J2Nk8y2jceICffvrcfnWpv5etd5uaLW_IgfF-pQzALJxy_tuIX_Xi5Anhytd3hceAt66_1-gw3uj-54xttqF7UZu5Dn4W3T7Hq7pAE1eW-UYmr2oCJVzpZOehzgUDEJUr_nJp7Y6GZdTplUwKJwQVbbwuiJoA3s_jm58qNJ4srgxVpLsYYKAklPd-_lcJd4JoXOpXPBE-PI6q6WKZW-dk',
        'content-type': 'application/json',
        'oai-client-build-number': '4480993',
        'oai-client-version': 'prod-7c2e8d83df2cf0b6eaa11ba7b37f1605384da182',
        'oai-device-id': 'f49caa8e-b1a8-472a-b61a-61b8f8aac215',
        'oai-language': 'ru-RU',
        'origin': 'https://chatgpt.com',
        'priority': 'u=1, i',
        'referer': 'https://chatgpt.com/',
        'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Microsoft Edge";v="144"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
        # 'cookie': 'oai-did=f49caa8e-b1a8-472a-b61a-61b8f8aac215; _ga=GA1.1.292430756.1770133139; g_state={"i_l":1,"i_ll":1770362114073,"i_b":"IFhSqKx7PLKGzqT/eTztkhsTEh4Rxkjg/PAh3jKLBOw","i_e":{"enable_itp_optimization":0},"i_p":1770140342337}; oai-hlib=true; _account_is_fedramp=false; __cflb=0H28vzvP5FJafnkHxih7XmuVuidhF3NrzJ7BynNR7qu; __Host-next-auth.csrf-token=5f9ae71c0f66fb0dfe7b080becc5b4d3a28482752652f86f2d61c69cfd75c78f%7Cf399c3ea965af14e7ebd1691d09d48969f78bb90fb15c7a283bcb4b1fb47e2d5; cf_clearance=NMUsrVKTrmJPtC9sIsiSWi.pNIoMjV7QJh.07Tc_CCQ-1770562506-1.2.1.1-cf1i3oYooDb3HAHNUADS1vRLmQSA6Bgd7DCvWPAyJdgZIXc74nqqAH8UrHtjoIPz_HwOARBq_YDgHkL61a5SYtP8zDGdtmoFB0tX5.5tftoFvCoqTXsAIRnaM.VsJ6shnILq7RaJTegRNIajSPBovJwOx_nHtsbIsKXnBfPkEeYVK8PLA9tA_UxQhKmaOdFay8nzoLZTtga6oJ5g3HkrEBCVd7yAMeTDXYKQGVFkoL0; __Secure-next-auth.callback-url=https%3A%2F%2Fchatgpt.com%2F; __Secure-next-auth.session-token.0=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..yKd7pOXfyebO5aal.kBsyS-1MaWi7Xo-cCt-faDKWsdRRqDK63uy52WZnG94nbprvlunh-yQu-EOEU5jC_9P2MmrNL6BJLVaPeMkuxJtVyWSWS7ZmN4-F-MCQ6et5HPVQmpnXpSxuDq9UUfHQm857EErtFjvmACiafNgrMPmu3S3tjkE-mhqvBzDuc4Fk6gIV8BCFMhCZCYVjPbvopVEsGYiYHtB--GWyvldL--sOOXAq_gOZTnH99xSzUPcPFK-5WH5_UJBzh2OmQ1y495Enap4Wacan2txrUGJ26aTRp5E1YRsLJknwCWxgZtouOa9sKd9hMxKHhxYXNohkm14qikGTutPs1xslbs8gRtl7QkCSPygV7w6gGcROuTKEMGStWMl17v8gXMnGpj9UfVBoktLdZEg39jGkpFKx4-gT9k_ChjB_j0elweX3eo01aTcrC2-jRafBiK_5uHIcQqxyhKgDR27llVJ5JJ30l4uHIAqSxeK2fhHDa8ZW8Sw5qi0mdPpvLNC7ksmJPNuRnQXlUDkHzi03BMK19jfFvMbooIBx4okw_hx12Z-cKJHH8dZeYr0q8UqBjM3flmLyj30Bgo1JMVr1m9d3rR4Gq2n0J0aWMifuks3rpSemRz195fAUVFzaTBZNwB5tW8DceP8SzPk6gcDtmCMe79ojT6GUqYTV_MjrGsm5aYz7EUi_-zweuLT6WU3Mxo_LWlEtZ6Euj8Y0Ut0J-lohT5GqUMy-7Cs4pcOt1dm2HPy45i_8YBnrKtf83xl83AZZrA8V5Rh_LyB3XotvWZT-CQ-YUPok4dYUL0Qbaowy0D_nWdiybNQSTf_kruczvVKknOsZrkKWL3CNpij2puVcfBiewEcdfQC06Mg2NffIUWLJiyaw7EAtoY2NlU3uUHpz_tRf9VOP49PHZ89mJdlsuXTQxtk1ZBaoUBkBrAiOHhz-10wobfim-JsjSOC6Zf2eIhtFZ9u_nIKOaDV2qFYMW0pByHkm5rQhs79-ozY5PNlr1qZsJCPaqS5EsGkZAmyHzGWwtDcux9B0ZbYSjmPuXY7v9oE5o4nKR1J3H88EaMgZfhD2h50LugY9W1WJIzrrePop0vJ3NTLyX4-PFBF9oKDIS8Dwef2UYFS5SaVadrO2PCa1ZKchGP94zUY_UvH6gmZL4XFqc4pGkHIdyS62nOb98bmobvcSwarquUoWDfbuHNJ9bby_Hnp7CpuTjVR9GURzboC6RmS7cz6JGWUlZ9_tLiCBh3fxef8omhyIExWjadCCUwkQj6Ml-PHbV_8wAMWcq3mqTw330ptZvz8774JuG_vu731keYalYOU3lbWZP620FNtyl7UTDPjbbsft4bvBmuHTRWQwLbKCkLUdUI_VDGgm2wpOghQEWIwT4-WCuz7NgUXHZlUlA9KbdH_5LFLSrlZvRk1OWExZK06BND7rAOCJHwyesFa7SDn0mVkIGrDZ7uH-pGZe1RnKsQ2qfO4KfSaV7LxA3oMc-4V9gPrz7dTqqxJSIQ0Opki_t8T7E_1tGwGD_u_jS9nA_dvKT7q9QoFLLDfIoPXXU7qWCMVB3fu4eA5d4fcpk8PC_nkqDckQlTzFdRFxdzdwgAkaD7oyJ47gJtZzJun7gYlRbdFX3AzaouekqLvco1SlU3j4pIJrPZbfF7CVrZFMB1KIUnMM80rosSadCKwgkoD455wFK4U6OVeQh2UdKm--YN1a5ejK1AEqWQSMDutoKolcYo0WQCiS1646R1cx_cMb4S-BkPfea57DDOa7zCLcjuX7QyP3DeFn9nYvXhRr2qP3qbm8C5WhF3r1gc0nisrImu7kfLH8oqbXrsV4ACcFQ7et3Ux-2pRes0nPybA5ehkneyIbrJNnXh6jHAbx2xboCZ7DSQ2JCKEg2ivaAaSyyE_QR8ucJPqQKIVXw_6Wxf1IOECXLSNyNfUTmJ8GGF3Wto_UmtPZ8l0B1DO_X4ON_n6N3iJsk9MrvfeCJrUe5jGFZRMQ-3xxfKlPwsZk0Mv8TDrP8UWZ4n_cOWdq-qU4bsj3RadMG9X_da4Hf9YlfDjlZaTQ1ZAnnqjuQeWgxdqEueLB7EgniMAzfJ38aTikKwD2TPW7O0OV5e8N0DF7DG2h0S-Cq0BOk-xYZj1QlRymrFSLVslhkde_nKY1za4lzJFeBdD1nTfUdDjR-2rsvrddiwMuqCZcsdkR88JuipoDXEHZUUHMkS2oHoa25MU0Zt0t0Abthbn4VinIbfgTaU3HuCxGnxSwHACk186nGlGq2OFqpY0M956Vc-akFGHPf2JNPhr8uzL1xXliNmNIWgb91ra4QN6osoAa7p2j9_t3kkqQYas5OE7z1GHZi7ESO3miFNI8pxRkoOYw3q_qGOjm2ovreERy0--xEg2OkAvwLq7iwos2Pw0_yZ9ald6MF3bogP5d3ZQg2c4RcxZ5zUmou2BcLldxZKet_lix9irtsnIbxp0Ui3D_iSQlu3c1Rf4CLu77i-boNxY4VJWarIdoO6xNLsCJMTKQc1UD6Uzku9LoWuxfOaNk8M_sulgq_KknCoznHqG0Rs5YW6lDfPiU6WqbWvG9WvpQj5Nv15TmVgW3R6CrOKpDR9EG9MvWCQ_1Ck0Dyhv5SCBBST00SCI3MADR_qxwmcMNDaVI7tHNC62l5tpxaeWf1Tm9tEfIuUuBJz57i_9r5BUI3tNwRxk0wucmHwFpcW-t2ul7gW7HUJmtmPDh7z73qVCsNbDymLW7-RkbD_DGIxtzvGN9PWDMedLKhdsS2nkHZcnJGAIYIUelWXptUlt7cmidipgmvyUkepT05BAGFv9IFlsje2ytqtpWt7TEA-y7rY6s9OBTMSXyR_OoBvWDTbYOVMRn0bHE4IrcKpj-boEM9VYzNGf9JdaqAOGC2W8iOZPId_jspEeeboAIyf3Zsnut23pSIN0lkwa7v0jtHb0va22T-MZsKxsJmeCkCHhz9agENpYQfxDd7UY0AG03bpS0CH9CU__8S8-SfSl8a8niDeqWovqrBdwzwPu15oOD4wNlNrpWGnQrOi_6gC54DvEPO8SA3uhXX9NSGSn3Vt9kbxZnkCyN6QcGcsQTg6ugzi0sJNwq58fuq2gq9JKMaoQBXLu3DCW5L54o5jpksb_1Tun4Yc6jRKVE6exB8UJrt5rG97BAQV0iRKc87NsATPzooda1goyOZdW-exlszXuisnutz0AyfP3jv-JkhBkQp53Rezj6VTxyf-3feQGzkmD-DvhZTzku4xUkXBkpothy0NEh1GzmVAqIHsIjbJ3Hi1geIIH8tZ7s6S7qLasgj59gxrEkM8Y2D5wQD_KdoneBrsrAikyyRa-JLIVPN5yq4h6Uy9Rx1WUlzXB0vJTCrHMWIW-uc5f9bkCLLet9AcA1M5vX5EW1ry04PmZvFCejoovYzJu39qZgA43f3wJV7f3xvFg4xEHR7w9IGTCGO-YmI-ApyD-cDFv7_cZtFeS-JBkLHILorUUpHn_Nm1n6bV4JwobfTM0cSDEtcdUk7saB8linjstQMblH6wVVgdhEEulR_uJrC7Q1pEhfnC_QmRZmkrwraquebYRoj7GJw1urPtXIPuXjezMjDdYRBItTktr_V5s1iPt_CGK_KCHqq7w8eeZEOshoSk1pC2_4CdHL-yEoUx6VbPz8Uq2qjK7Zrzx-W2ADj61BNXTRJ-JssjitqL4iGfLymYbeenyjEN_VntoCjGbXKb-ow1d4LBvqeQqW2LI0UnTdqRr_P2yqCzTQ6WWQqGAbo8bE1b1pOIU9BIho6RGZ2o8fgxBs9maTAakHmzJAfknohulzPTFychSwGrTwheRp6KuWsGWCYgy3vpoiX3d0ALKLrmhvrqi_HzEdh_NrCSD30_HQk6RJagelU6w; __Secure-next-auth.session-token.1=cf9LSPnzHA3B_D1uOZtcQedttfDikxu86q9fEw-8uNfU71Rlk3lsn7qrPNV5LQ9L7qd3vwdnokYieLp5Fb3cWvRSuWOjeSp-epcJHpCX2ziB2nJlQDh2H9hHIuAElPekXn1E4gdWEGIEEZ6gkkRv2laYmCb1shtRi7Y182zxFTRCLIPRjQoyfsAZHHv-cZEkdWnvw_7LAliAcu7PgFMpHpog24Ig.ebPBq-hQaZoswaGOtAPddw; oai-client-auth-info=%7B%22user%22%3A%7B%22name%22%3A%22FFF%22%2C%22email%22%3A%22profesorlal5%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fff.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770558990065%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D; _ga_9SHBSK2D9J=GS2.1.s1770558176$o4$g1$t1770558990$j59$l0$h0; oai-gn=; oai-hm=ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE; _cfuvid=KIbXypYx4l9tW.5Zj1Praq42wzgCp_fogA814RTa3l4-1770562575.9542005-1.0.1.1-pbCy.tgwNdtySdrJmgkQNwP0KiXW2y5zFN3GYRyj8Sg; _dd_s=aid=1b42e6ea-3c02-48bd-830d-92a29fb20553&rum=0&expire=1770560100814&logs=1&id=28080f70-f74c-4c58-af10-98330710f7f5&created=1770558153456; oai-sc=0gAAAAABpiKThdv6uzC3heahJ4-1my3tP2t6jFSkxQpNAv2PUbDJZPLDAdnPnIKfVb0Wdy6Swi_iz7OCsCNm733ooXIBc2RU3wHNWtPf-DQ3FtzaGp2HfXHy3NWrWdWfG-ILPbrwtmu-6mEA-KLSErUyR0ysxUVmgy_HgJYpzmODyXWla41ZWd1lhAo6IDHnr1oEqPH6jC6zaNlWnpIs8cqKMTXBRkpCjQzAO5dVQiWvaqDBtcdPUHf0; _uasid="Z0FBQUFBQnBpS1RpMWxCNG5oMldzYnVqaUVPVWFZVFUxOEtaQ3BrejdGeHFEeFVULWVzWFlmelllZGQ4MEZWNGNJZnBYcGlFcXdIS1ZMelpjSjA5d3JBTmp4R21kdldZZ3hfWm51eDJCb0hQQXJKZXQtZ1JFQjdRRGk3TFlFMk91SE1JRVN6UmpXNFQxcklrRjktLWN3bWY1UU9JNml4clBlclpkOFR4UzZvOFpLODIza3IyZWE2ZmN1d2xQd2NfWk9ya1VuLTFqSnFrd2h1a3BYbTBtY05KeE85SzdWWDRUOVc3ZHoxNDduTXBoWTFmVHBjUkZveEpiVnMzcElQYWVNck1BWE5RTWkxWEQxcDBRTUNsNFRJSnpFZ0JiM25hdWtNX09zTlI0ckxVelJQTzJpd2pxSVo2UHdJcjZPUVVranlOcnBST2lfZ0dTT0xZczVWTDIzdlFGcEhfUlg3OEhnPT0="; _umsid="Z0FBQUFBQnBpS1RpeDNlRDl2YV9wX09BMFRQNnZUUnhtN2JQNWVwclAzUlBtblhZbE5YVkxuZkZZM3R2a0hTYjVWUjM5Y0k5YW1YZXNFdjc4TGd0S19rdHNqRWlzTlc0UkVqVE5KVDJnMWVNMndNUDZDYW9sbUFLcHJhcHpCdkdaM3labzg2bHJxY3lLd1oyTDk0Zjc0SGdBMmI0dW9Zcl84eXZ1UE9qSjdyTFVQWVNhY0t4VG1lc2duYTBpYkpSUFBRVkFsVWJVeGo1MHdKX3IxZExXRVJKcVZRaEN6YW5YQTZFR0JQSHI5X1p0a3hPN3VaZktobz0="; __cf_bm=R82eCaF8p2XGs9h1tZUuuPqdMctyo6Ym_78DWOJFKQc-1770562789.3867137-1.0.1.1-rCgS3FDpZWM4ZP1vZ68_bOHh2rO3Yf0W6QEZ0HsXoFToaQNUa9NgwHERY1bm8QctbDG.hbs0XU0HDrSr6V9F1C6W.epDgZ55_3f14THK1XfM5UT_QKBHgBgQS9fwZXua',
    }

    json_data = {
        'prepare_token': 'gAAAAABpewSGbQ29HQatQNAhYw6swJ0033nbd75HmL7BtXoa-qthoAJQu0-7XRKJyr9XoNn8EnWOBLzoqPwvk2fNzxXDLuBmbSrbaZXQurEAqRcyhM2pBIooEvvAUBGGHNRa8IP1-qaks2U8jnaOKVShsGDC9bA8UxaRcOGHRD5b07YFH4B5Z_oUZ3hyKQVHK9DU9ogmVcE7SIc6ISJdcVAVCpkvg7egR-F0niINxqSs-xRcoabntC-Mwl7WxI8Wpiw09GF1ZMENUWuBIdLZ8LdT2dk1hgT7P3NV1hTGiBMsQICnYJZdQNY8IJAgY4GtIPuoPtmDpqFKg2WeNYZkr3XIp7f8WPg38sLJ8jgeH4lDZAxsilufT2L9TUmpCuKy2rS6hfTKJ25tSo3dqEfhZYkRjxIkRDEWPcL00_oDgdMDcpc_pU_zH4Nfyw68SfWy97VjAHFDloLWjsFwxA0u3jvCexOkInll9MKGX4j3UtAUNn5rrrT8zlQporaM1xARfldcs5H1lor65km_Nz-PO-iDmphr1ApahPXAbKu1XIx1V-QwDtGwF6yztiYiqEzS6HNBQp_DX4dBygzIMgchNYKl7wNUC4mCCrYV38I5B5RMsE0UxZXBZ726jtnxavvpB_F88FkG44Ii6jKsK6SUqhvtVKRei0WKyy3k4rdM3SopPwkttpqiYMdUYNVixfjW3_tp96Fg8G2csjiXvDXw2TnAu_2vc5flexJQDkJklxjcqxPp2TVbddkFfYiPUj5jJ5wPqU_YuuYwswsT7RtOCGeBejJ9vi5LWBONTwUBxTPLs9M0uTZMNFqzNEhHhm-338o2qQzwUVKFgaQjSf4QjrQ7pk4EXSQbGwMu-BeRMPiac_Izkv1YFJtTUEO7u5tSEc6hW4HTHnD_HS16fJEdSycY__fFDz9i9B_3uS_psZkFrORceg45zTsQDY41U2U7SzKxXBIRTlg73ucshnAFr0i16xgEZkHodZh9NBME9xT6j0wEX_qbWqrau7Jvnhbu8yDYkatiFql2jwaA_6OiJZu7XLAbdxxETPZN6hDB_qeW_WCI7iY8W6HDRg8paOlGmfN0HlQ8MtPT1xkA5N_jHrIw6BBsBmLdeu-YbT9nPle0GiMAai9tHVJB22OKQpIDVibOX7B02UJ3gI57Gwuuo5uHeYKj3-wuHmlcdY0MOJIjyr4EFyoPoqIv8IIPoRWNglThomUPNtBsaqhP5aJJYxYg2LFvk7vM-wr3GP2_J-mNXXbb5Q13DKjeWDnUcqwvUXJQbBEEBmfEenQ-1ctm_nU1AdF1asYW5IPdd4RoUOGF8RMfyqmxlEZbBhYWaqvzZJp0eIv0brc9QIzreb1Dicvimvyv7X_ajShKZp41j7qXtI1qpMppfO_bP8ZWFvOxChb6X0L51kTHcYtUfXXO0J_vhRpZong5sBRZV4bzXkSRWtUM80Y_8qvPOUYU6YXdw7NycJPfT49eYAKcdfi8iTzp5YRofaQp2a3ka15g4yzzlWKD-4UqjGTB6PbdpcnztsVTJbTOP-kv40LgeeSVbLJW6zPI8SJlhQbuMA1qbfZNqWSmedPN97sKfbQmCQfwnsB_bCOPrHkHXnkSoodHr75szqmLwf5yPq9cyD8QOYu8E9S88V4cgZflsFfsQa8OdBR8EUo6QkTTPFhx2GrSf9vYKl5-HCq8uKwcbvPXRZz0bZueuM6KnGB_13jqqDpkMBJ5oC4YyvnPpNC3pLWe8VFok7SBSDbclWzfe2ZljrltFfr65mOtuzuKq3DTOQ5HM6GITeQkZ51XKPCt7mkTw47iRQkpuljFiIlNAfWGpx-OkdDKhT_1c-M1X9eWk8gOiqj27Vh8IwVpw56gBQcvQZA-gnsTwfyb5hZ1Fh9_3DZDkoR4gFk19Yvsdv656xZbPjDe2vJWK-EDyIX_9toF-0cV7QbkwFgRl2uFbcyVSdafgDcqKDJAuhT5ZQZmbWozX_AqdaQVoAwObPEfQXiQCQK6LcScunfO5pD7QHCs5WiI-_uW2elfBNiOs9zE2WHlPZuW1OmdsBg1m-PrgPhP2YLmuyP_0TPxhegUcr9vOzekat1HumK5uYy-cgUn2NC1VbF3NDwZlSIbSbLdSBJgrw87vTYTjihlJWUmwz8_F1QKMH8obnErXEO0NyMteAU3b6eGxWTNQTA8-zB9dn4mORN17AP8NjwxubrFAf0XIE7_AvWVMez4L9OOFskkN_WwwmDN6tQlf1R-_h2Dvm2c_5Z08OyAdvxn_Qt2ELxn8RoR4kbFKCVVnQd7u98InxSXyykxkySK7v_kSFk2hKGMpjDnPAOO0dV192Phmex94crlHU4PxhPR6ksO7-uONuA9ETbtclXcQLuSpqS8EEytIiw0gUB_irUvNHQ8KlJgIdrLmrNRCbAR12Nd0hwlEkdlOQwBkf7P13s9EqwdYimxRnhI-P3gZy7T0MAHv04h6ulUttxhGsBr2D_pgN4JX-XkTeMSuge9ldRkYVXPYJvv0VlXvWZTuTgFzwm2DbJwS1zbSN_cndtGCk8=',
        'proofofwork': 'gAAAAABWzIxMzQsIlRodSBKYW4gMjkgMjAyNiAxMTo1NjoxNiBHTVQrMDYwMCAoR01UKzA2OjAwKSIsMjAwNzc2MDg5Niw1LCJNb3ppbGxhLzUuMCAoV2luZG93cyBOVCAxMC4wOyBXaW42NDsgeDY0KSBBcHBsZVdlYktpdC81MzcuMzYgKEtIVE1MLCBsaWtlIEdlY2tvKSBDaHJvbWUvMTQ0LjAuMC4wIFNhZmFyaS81MzcuMzYiLCJodHRwczovL3d3dy5nb29nbGV0YWdtYW5hZ2VyLmNvbS9ndGFnL2pzP2lkPUctOVNIQlNLMkQ5SiIsInByb2QtM2VhMjVjMzlmYzA3YjlkZjIxYzhiNWI3YTE1MWEzZGU5NTY3MjczYyIsInJ1LVJVIiwicnUtUlUscnUsZW4tVVMsZW4iLDEzLCJwZGZWaWV3ZXJFbmFibGVk4oiSdHJ1ZSIsIl9yZWFjdExpc3RlbmluZ2phOXl3dHdraWlsIiwib25nYW1lcGFkZGlzY29ubmVjdGVkIiwxMTQyMDQuNzAwMDAwNzYyOTQsImRkMDk3N2M4LWI2M2MtNDg2Yy1iMWZhLWZmOWMwZDBhNGM3ZCIsIiIsNCwxNzY5NjY2MDYyMzI1XQ==~S',
        'turnstile': 'TRAaAhgGGBYMEGxMQWsMGBQLHxoBAQwOBwcXAgIEFwEHCh0HGhAXBRgHFxYMAx8BAgQCFgMGAAwDEBQWYgN8blJnH0VhZh4JFB4MBwUcGwIUCAxycWh2ZnB8HWxzaHRldGN7UGB8b2NaXmlwXWh6YQZde2ZdcGxmcHxvY1oDemVnB0xhdHhMY1p4dmdwA3diBl17fF1wYmJgeExlcH95cmMDfVIGeEdjB1p4ZgZwamJdQXp8B3RnUmNoBWVgQnxjB0JsYVpRYxYaEB8GGAYbFgwQb1MLDwwYFAsWGgQEDA4UakVuV2BrYX9zfEBgZUUNd2d7fld1QmJSY1ZAYHp8U3hzeHVTc28AdHNZcm5nHgRsdklxVXN8R311SXZnelYNYHBJcVNwb1BnYUl2Y3NJCQsQAhYFCgANAxAUFnh2Y0F7Zk0JFB4MBw4cGQUUCAxgZHB5clFFbXVeaH5xdGNtdVF/bXJddExnXXBLcXRjb3ZeRUtwZ2doc05Ve2NdRkphYEJPclFFb3JeXWp8UUpvY2N7Y3BRc2l8TlV7ZXNWemxda2NwUXttdXdjfnVkWWxzXnBjYwZ4aWVgWXt3dFVtcE5FTHN0YEtnB3htcXRja2dBDxMWGhAbGgALDA4Uc0kJCxACFgcBAAUDEBRAREdLGBQBGhoHCgwOQkBbURoQGgwYBx8WDBBNYgdwewVGampaB2t7ZXdnZlp4WE12Dg8MGBQDGBoPCwwOFFYdTEZXZnllcEBiAVZJfWNWHQ1HdGlmYmFCcmxqb25RV1ZHeWtCemBmVmVwd0NYcWQefnFqeGJ0dkZDcWFGAHNwSX1Qc3Z+BHBWZk9lH0xkZHttUlFvV3J2RX5wUHxARHBGflNgHF9+UX9lV3dDW35RSXlQen8JCxACFgEcHA0UCAx5XGtbe2J3ExYaEB0HGAYYFgwQY3cCB2BDCw8MGBQAGBoDBgwOFGpCBXJne25iYXhyRmNCTFRlHn5XUx5icWQfXGJ6RQVUZWhMemQeflJqa1dUY2tAbmV4THdneGJxcx9YbGRFDW91H25+YB9AVGp7UGJkSX5UZB9HdGR7bnVqfEBTZx8FWGcfUHdXQlhMZR52U1ceemBneFB8Z3hcDmUeQ1dqew1SdXh6U2BrYVJgQmJwa3tAZGVmTHRWe3pxYEB+c2QeQFFgaEBmZWl6dWp4cldqSX5zZx52f2geflJqaHF7YGwMb2V4en51H1B1akVXbGVFDX5RQkBSZXgNdGp4cmVoRUBgZR5yUWUdUH5lQkxcY0IFZGVCAHVlH1BUc3hicWNCBFRlQnpSY2libmEfRAJqeQ10Y0JEAGBCDWJjRVAGZx52V2cedkBjbFt9YWh9d2d8AHtnH3pmc0kMc3VCdmN6RXJgalYFZmBrbnRzeERsUR9Ad2pARGZgHVBkZnhycWBFQGdRaAV0Y2h6d1NCBXBne35gah8NemBGRHRlQmJ1Y3tQYnVrcnpgHmJUY3hERmUeW1dwWWFucEl1cnpJQ3BzSX5UZB9HdGR7bnVqfEBXZx8Fd2hCenxndkRSZh92T2cfRGR1QnJmZB8BbnN7cnBkH0NsVmgNf1RZYnVTHVBnc0VuRWtAcgFkQGV0dEVXZnNGU3F1f2VSdnhhdGFvBHl6VlxhcFltcnNvbXJ0Rl9mc0ZMYHVZfVdzb2JjdVl2ZXBsW3JkbAVSY2hQd2pocmV6HgVUZWtmemBFYmJlQgVsamgEb2B7bmZja2JUYXxEcGBFDWFlQlBOanh9VXpZeWZzbFNwcElbUnBsRGRmH1BTZFlydGB4UFV1dmZQYWYMcGNDQAVnb35xUGl+TlcfbgZzfG5+dll9b3BGX3N6fwVgc3tTeHZGDG9kSVdxc1l1cXNWbWx2WX1VZHxHdXVJdXBkbEd3Z0llV3MfZVJheAV7ah5xbGRoBXRja2JxamhicXpFcmBqVgVmYGtudHN7dm5gRUBxY0IFV1R4XE5jeER1ZB9ud2seflJneERTUGhib2prQHFrb3V1ZR9QVHN4YnFjQgRUZUIFfGcfemFoH0RcY3tAcVcfUFJgH1BgYR1QbGAednd1QgVmanxYYmpFZnB6ewVUZWtmemBFYmJlQgVsamlEcWQfZldgVkRxYEJyVGRZfltRHHIBV3Z6QldAQA9WHHZEaHluXmhAekJodnJAekUFYGR4RHp1Hnp0YR5tVFFremVUf3JTUXZiY3pCTENoQwVeZGZ1UnZrYXp6SX1zenxTfnYfcVJnSQx9c1l+Y3NWW2xzSX1QdmxHenpJbm56VgB3c0lyf3N/fmF1b3V0Z29ybmV4TG9ne2Z0Zh52UGp7dWxqQkBSY2hYd2RWXGxnHldUZB5+U2BWWHRlH25wZ3hMUmBFbn5lH1wGZh8FV2QeRARkH0x3Z3tlU2p4cm5jawVVdWh6V2NrZlRma3J1ZR5yV2B8DVJne35xYB9EZGN/fldnHkRXZHt6cWBGRFRkHwV+ZB9ienVoWGJgVlhlZR5bb2V4en51H1B1akVXbGNFQFdnQkB3ZxxifmprfmJhHQVzZx9xdWUfUFRzeGJxY0IEVGVoBUBje35XUGhib2prQHFRQkBSZB9EYmAfWGJjaQVvah9Qd2NpfldqaHJuZENQZ3VCBWZqfFhiakVmcHp7BWBgQFBhYGgNd2EeUAVgSX5uZUJAemVFfVBmeEBzY2tAb2QebnpnbEB0YR9Qc2QfW29leHp+dR9QdWpFV2xqa3IHYx5ucWt7Ym5qZlhiY3gFYGd4UEJlHnpVZUZcbGceV1RkHn5TYFZYV2YeUE9lHwVsah5yUmd4fl5lQmJzYEVAcWhFdnRle25xUGhib2prQHF1QgVmanxYYmpFZnB6e25gYHZufmAfQFRqe1BiZENyV2RoBWZgQn5UY2h6WmUeRGRkaHpVUXhccWprBFdqew1SdXh6U2BrYVJmeEBvamtAbmNCBVVUeFxOY3hEdWQfbndrHn5SZ3hEU1BoYm9qa0Bxa295dWUfUFRzeGJxY0IEVGdCUHtjeHJeYR52dWUedmJgfAF0ZHgMUmYeZnFjf3JuakJ6V2toAW5jH3FsYB8FZGBsDUVleHYDentmTFF7U3NqQ0QAVhx6Z1BvV3ljfFdzc291CxACFgILAAIHEBQWd3NvdXdzb3V3c291d3NvdXdzb3V3c291FB4MBxgGGhYMAgAEAAUbBwEHGAMOBBoFAAUbABoQHhoDAAwOFHB/CQsQAhYBAgAFDxAUFlRgbERhA0JiZ15rY1MCbGxxcG93cF9oVmdzTXFzc2d1cVVkThQeDAwBHB0NFAgMYGRwfmVaVkxlW3xIYlpCTXJ3VUh2QXNrdV5odmZzC0pjWEJibGB0T2Nea2N2ZEVjc15wfmVaVkxlWFp6YnBdeXB3e21yWlZrYAcDT25ddGtyd1VLc3RgfGVgWnRlcUJ8Zl1oaXJRRW9zXlV7Y11GSmFgQk9yUUVoc3dFT3FwXkphXWhpclFFanxne298UUprbAZCYmN+eHZsXXBLcXRjaXVdChMWSw==',
    }

    response2 = requests.post(
        'https://chatgpt.com/backend-api/sentinel/chat-requirements/finalize',
        cookies=cookies,
        headers=headers,
        json=json_data,
    )
    req = response2.json()['token']


    cookies = {
        'oai-did': 'f49caa8e-b1a8-472a-b61a-61b8f8aac215',
        '_ga': 'GA1.1.292430756.1770133139',
        'g_state': '{"i_l":1,"i_ll":1770362114073,"i_b":"IFhSqKx7PLKGzqT/eTztkhsTEh4Rxkjg/PAh3jKLBOw","i_e":{"enable_itp_optimization":0},"i_p":1770140342337}',
        'oai-hlib': 'true',
        '_account_is_fedramp': 'false',
        '__cflb': '0H28vzvP5FJafnkHxih7XmuVuidhF3NrzJ7BynNR7qu',
        '_uasid': '"Z0FBQUFBQnBpS0VnTGJLeEJYLUVUSW5tNHFDV0NUSi04RktubXZxRDJkc0JQV0w5UDJMRk1Xd0lsWEVKaEpmdjJNTERfNVZidEI1X2hwN0ZlZ0VydzRpTFUwd3o0SC1BNEx6N29nWHlRRVVCNDNfeUM4dTdteTQ3aXB3WktyR0ZFSGxlMlZ4czdWMk1uR2wwRG9hSTkzTzBMRkFLRVh1M05PVl9iOGhTbDN5cndTQzRzVHlOSFVhelduNE9pZENmaDF5UkF2ZW11Z0g3TmlaTEJvUGVXOGI1LVB5VVp6RGppT1REaXdHblc4U2thbV91a3ZiaWh6MXpXV0VJdWUxSUhmVjNCU2VmSXhCT2J2LVE0ejlsQmRFNWt1U1RZREJxSWZPYWFEcTVVRE51bXpZMW9KZGNnbGFMeWRQSWRkQXBfMWZaTzVHZXZJUnp1MDRocHNEZ2s4dzRLQUtMZVJVZkF3PT0="',
        '_umsid': '"Z0FBQUFBQnBpS0VnMm9mQUYxbWpITkxlckdYVTh5U3dKMXdxUEVwVnV3U3RPM09TQ3I4ZGZ5VEU0MjF4ZEViWmdkV2ZqdHZPcjdzTnZONEQ1czlFRFp4endFNFVsUFZxZEZqT1E1aDJOUkRhdTNZRVhrREstOFpJSHpFY3N6dlJBRlRXWk1HR0c5ZDR3eGhscm1XdkRqYXdvWTFaZUhjbDlRb2NJTEMzLWxUSkZEWkkteklVN1FPazI3MWhqaGtaek9Td0h2SkJXcERnZU5LV2hlVjI5Z0xSTURvamRzUGV2aExYOUdwbGhUNHQ4Vm4xZWo2ZmNUTT0="',
        '__Host-next-auth.csrf-token': '5f9ae71c0f66fb0dfe7b080becc5b4d3a28482752652f86f2d61c69cfd75c78f%7Cf399c3ea965af14e7ebd1691d09d48969f78bb90fb15c7a283bcb4b1fb47e2d5',
        'cf_clearance': 'NMUsrVKTrmJPtC9sIsiSWi.pNIoMjV7QJh.07Tc_CCQ-1770562506-1.2.1.1-cf1i3oYooDb3HAHNUADS1vRLmQSA6Bgd7DCvWPAyJdgZIXc74nqqAH8UrHtjoIPz_HwOARBq_YDgHkL61a5SYtP8zDGdtmoFB0tX5.5tftoFvCoqTXsAIRnaM.VsJ6shnILq7RaJTegRNIajSPBovJwOx_nHtsbIsKXnBfPkEeYVK8PLA9tA_UxQhKmaOdFay8nzoLZTtga6oJ5g3HkrEBCVd7yAMeTDXYKQGVFkoL0',
        '__Secure-next-auth.callback-url': 'https%3A%2F%2Fchatgpt.com%2F',
        '__Secure-next-auth.session-token.0': 'eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..yKd7pOXfyebO5aal.kBsyS-1MaWi7Xo-cCt-faDKWsdRRqDK63uy52WZnG94nbprvlunh-yQu-EOEU5jC_9P2MmrNL6BJLVaPeMkuxJtVyWSWS7ZmN4-F-MCQ6et5HPVQmpnXpSxuDq9UUfHQm857EErtFjvmACiafNgrMPmu3S3tjkE-mhqvBzDuc4Fk6gIV8BCFMhCZCYVjPbvopVEsGYiYHtB--GWyvldL--sOOXAq_gOZTnH99xSzUPcPFK-5WH5_UJBzh2OmQ1y495Enap4Wacan2txrUGJ26aTRp5E1YRsLJknwCWxgZtouOa9sKd9hMxKHhxYXNohkm14qikGTutPs1xslbs8gRtl7QkCSPygV7w6gGcROuTKEMGStWMl17v8gXMnGpj9UfVBoktLdZEg39jGkpFKx4-gT9k_ChjB_j0elweX3eo01aTcrC2-jRafBiK_5uHIcQqxyhKgDR27llVJ5JJ30l4uHIAqSxeK2fhHDa8ZW8Sw5qi0mdPpvLNC7ksmJPNuRnQXlUDkHzi03BMK19jfFvMbooIBx4okw_hx12Z-cKJHH8dZeYr0q8UqBjM3flmLyj30Bgo1JMVr1m9d3rR4Gq2n0J0aWMifuks3rpSemRz195fAUVFzaTBZNwB5tW8DceP8SzPk6gcDtmCMe79ojT6GUqYTV_MjrGsm5aYz7EUi_-zweuLT6WU3Mxo_LWlEtZ6Euj8Y0Ut0J-lohT5GqUMy-7Cs4pcOt1dm2HPy45i_8YBnrKtf83xl83AZZrA8V5Rh_LyB3XotvWZT-CQ-YUPok4dYUL0Qbaowy0D_nWdiybNQSTf_kruczvVKknOsZrkKWL3CNpij2puVcfBiewEcdfQC06Mg2NffIUWLJiyaw7EAtoY2NlU3uUHpz_tRf9VOP49PHZ89mJdlsuXTQxtk1ZBaoUBkBrAiOHhz-10wobfim-JsjSOC6Zf2eIhtFZ9u_nIKOaDV2qFYMW0pByHkm5rQhs79-ozY5PNlr1qZsJCPaqS5EsGkZAmyHzGWwtDcux9B0ZbYSjmPuXY7v9oE5o4nKR1J3H88EaMgZfhD2h50LugY9W1WJIzrrePop0vJ3NTLyX4-PFBF9oKDIS8Dwef2UYFS5SaVadrO2PCa1ZKchGP94zUY_UvH6gmZL4XFqc4pGkHIdyS62nOb98bmobvcSwarquUoWDfbuHNJ9bby_Hnp7CpuTjVR9GURzboC6RmS7cz6JGWUlZ9_tLiCBh3fxef8omhyIExWjadCCUwkQj6Ml-PHbV_8wAMWcq3mqTw330ptZvz8774JuG_vu731keYalYOU3lbWZP620FNtyl7UTDPjbbsft4bvBmuHTRWQwLbKCkLUdUI_VDGgm2wpOghQEWIwT4-WCuz7NgUXHZlUlA9KbdH_5LFLSrlZvRk1OWExZK06BND7rAOCJHwyesFa7SDn0mVkIGrDZ7uH-pGZe1RnKsQ2qfO4KfSaV7LxA3oMc-4V9gPrz7dTqqxJSIQ0Opki_t8T7E_1tGwGD_u_jS9nA_dvKT7q9QoFLLDfIoPXXU7qWCMVB3fu4eA5d4fcpk8PC_nkqDckQlTzFdRFxdzdwgAkaD7oyJ47gJtZzJun7gYlRbdFX3AzaouekqLvco1SlU3j4pIJrPZbfF7CVrZFMB1KIUnMM80rosSadCKwgkoD455wFK4U6OVeQh2UdKm--YN1a5ejK1AEqWQSMDutoKolcYo0WQCiS1646R1cx_cMb4S-BkPfea57DDOa7zCLcjuX7QyP3DeFn9nYvXhRr2qP3qbm8C5WhF3r1gc0nisrImu7kfLH8oqbXrsV4ACcFQ7et3Ux-2pRes0nPybA5ehkneyIbrJNnXh6jHAbx2xboCZ7DSQ2JCKEg2ivaAaSyyE_QR8ucJPqQKIVXw_6Wxf1IOECXLSNyNfUTmJ8GGF3Wto_UmtPZ8l0B1DO_X4ON_n6N3iJsk9MrvfeCJrUe5jGFZRMQ-3xxfKlPwsZk0Mv8TDrP8UWZ4n_cOWdq-qU4bsj3RadMG9X_da4Hf9YlfDjlZaTQ1ZAnnqjuQeWgxdqEueLB7EgniMAzfJ38aTikKwD2TPW7O0OV5e8N0DF7DG2h0S-Cq0BOk-xYZj1QlRymrFSLVslhkde_nKY1za4lzJFeBdD1nTfUdDjR-2rsvrddiwMuqCZcsdkR88JuipoDXEHZUUHMkS2oHoa25MU0Zt0t0Abthbn4VinIbfgTaU3HuCxGnxSwHACk186nGlGq2OFqpY0M956Vc-akFGHPf2JNPhr8uzL1xXliNmNIWgb91ra4QN6osoAa7p2j9_t3kkqQYas5OE7z1GHZi7ESO3miFNI8pxRkoOYw3q_qGOjm2ovreERy0--xEg2OkAvwLq7iwos2Pw0_yZ9ald6MF3bogP5d3ZQg2c4RcxZ5zUmou2BcLldxZKet_lix9irtsnIbxp0Ui3D_iSQlu3c1Rf4CLu77i-boNxY4VJWarIdoO6xNLsCJMTKQc1UD6Uzku9LoWuxfOaNk8M_sulgq_KknCoznHqG0Rs5YW6lDfPiU6WqbWvG9WvpQj5Nv15TmVgW3R6CrOKpDR9EG9MvWCQ_1Ck0Dyhv5SCBBST00SCI3MADR_qxwmcMNDaVI7tHNC62l5tpxaeWf1Tm9tEfIuUuBJz57i_9r5BUI3tNwRxk0wucmHwFpcW-t2ul7gW7HUJmtmPDh7z73qVCsNbDymLW7-RkbD_DGIxtzvGN9PWDMedLKhdsS2nkHZcnJGAIYIUelWXptUlt7cmidipgmvyUkepT05BAGFv9IFlsje2ytqtpWt7TEA-y7rY6s9OBTMSXyR_OoBvWDTbYOVMRn0bHE4IrcKpj-boEM9VYzNGf9JdaqAOGC2W8iOZPId_jspEeeboAIyf3Zsnut23pSIN0lkwa7v0jtHb0va22T-MZsKxsJmeCkCHhz9agENpYQfxDd7UY0AG03bpS0CH9CU__8S8-SfSl8a8niDeqWovqrBdwzwPu15oOD4wNlNrpWGnQrOi_6gC54DvEPO8SA3uhXX9NSGSn3Vt9kbxZnkCyN6QcGcsQTg6ugzi0sJNwq58fuq2gq9JKMaoQBXLu3DCW5L54o5jpksb_1Tun4Yc6jRKVE6exB8UJrt5rG97BAQV0iRKc87NsATPzooda1goyOZdW-exlszXuisnutz0AyfP3jv-JkhBkQp53Rezj6VTxyf-3feQGzkmD-DvhZTzku4xUkXBkpothy0NEh1GzmVAqIHsIjbJ3Hi1geIIH8tZ7s6S7qLasgj59gxrEkM8Y2D5wQD_KdoneBrsrAikyyRa-JLIVPN5yq4h6Uy9Rx1WUlzXB0vJTCrHMWIW-uc5f9bkCLLet9AcA1M5vX5EW1ry04PmZvFCejoovYzJu39qZgA43f3wJV7f3xvFg4xEHR7w9IGTCGO-YmI-ApyD-cDFv7_cZtFeS-JBkLHILorUUpHn_Nm1n6bV4JwobfTM0cSDEtcdUk7saB8linjstQMblH6wVVgdhEEulR_uJrC7Q1pEhfnC_QmRZmkrwraquebYRoj7GJw1urPtXIPuXjezMjDdYRBItTktr_V5s1iPt_CGK_KCHqq7w8eeZEOshoSk1pC2_4CdHL-yEoUx6VbPz8Uq2qjK7Zrzx-W2ADj61BNXTRJ-JssjitqL4iGfLymYbeenyjEN_VntoCjGbXKb-ow1d4LBvqeQqW2LI0UnTdqRr_P2yqCzTQ6WWQqGAbo8bE1b1pOIU9BIho6RGZ2o8fgxBs9maTAakHmzJAfknohulzPTFychSwGrTwheRp6KuWsGWCYgy3vpoiX3d0ALKLrmhvrqi_HzEdh_NrCSD30_HQk6RJagelU6w',
        '__Secure-next-auth.session-token.1': 'cf9LSPnzHA3B_D1uOZtcQedttfDikxu86q9fEw-8uNfU71Rlk3lsn7qrPNV5LQ9L7qd3vwdnokYieLp5Fb3cWvRSuWOjeSp-epcJHpCX2ziB2nJlQDh2H9hHIuAElPekXn1E4gdWEGIEEZ6gkkRv2laYmCb1shtRi7Y182zxFTRCLIPRjQoyfsAZHHv-cZEkdWnvw_7LAliAcu7PgFMpHpog24Ig.ebPBq-hQaZoswaGOtAPddw',
        'oai-client-auth-info': '%7B%22user%22%3A%7B%22name%22%3A%22FFF%22%2C%22email%22%3A%22profesorlal5%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fff.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770558990065%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D',
        '_ga_9SHBSK2D9J': 'GS2.1.s1770558176$o4$g1$t1770558990$j59$l0$h0',
        'oai-gn': '',
        'oai-hm': 'ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE',
        'oai-sc': '0gAAAAABpiKQPyGgmpEH_DA4tXL5mtq1UroadG2IqOdiOPAnsgYVgAhAHe1BcaLjzx5xKi5Bw07penQUUs7VAM6cP7Fep30dynR3DiLD1ZhoFC6DFucWDPLazhPB7aQkzjUjBW31u0uMSB5lzvzunM8vk02M7pZaHgoEo7pyvukFWwv7BYuq_8Mq-oz60RC40A3_ds4-D44muzTKIUGDgGHAvl-JPi3WPFt7rZj3m0ajN7mRH-4Wi-GQ',
        '_cfuvid': 'KIbXypYx4l9tW.5Zj1Praq42wzgCp_fogA814RTa3l4-1770562575.9542005-1.0.1.1-pbCy.tgwNdtySdrJmgkQNwP0KiXW2y5zFN3GYRyj8Sg',
        '__cf_bm': 'p_NhIs6u3pFDkd1eyAgu.3sbt.mkbduvMi2u2uAIVHY-1770562784.032666-1.0.1.1-hTmI4TWScrrN05N.XnnnyoZaMxIE3ap_vghPl7Io7X3vg376Z_tBw3NgA.xKZsg.x8Qt534xpH0HLbFazIwK6ov76MHVCW0aWabA19dNIGFss1GGOtyZkpHYZWKtZ5X8',
        '_dd_s': 'aid=1b42e6ea-3c02-48bd-830d-92a29fb20553&rum=0&expire=1770560100814&logs=1&id=28080f70-f74c-4c58-af10-98330710f7f5&created=1770558153456',
    }
    
    headers = {
        'accept': 'text/event-stream',
        'accept-language': 'ru,en;q=0.9,en-GB;q=0.8,en-US;q=0.7',
        'authorization': 'Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjE5MzQ0ZTY1LWJiYzktNDRkMS1hOWQwLWY5NTdiMDc5YmQwZSIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS92MSJdLCJjbGllbnRfaWQiOiJhcHBfWDh6WTZ2VzJwUTl0UjNkRTduSzFqTDVnSCIsImV4cCI6MTc3MTIyOTczNiwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9hdXRoIjp7ImNoYXRncHRfYWNjb3VudF9pZCI6IjI1ZWExNjFjLTJmMDEtNDAwZi04M2YzLTJjZTk3MTg3NTdhYSIsImNoYXRncHRfYWNjb3VudF91c2VyX2lkIjoidXNlci1lRVlxWmc5VURvZFJ2c3daRlpSS0lWYnlfXzI1ZWExNjFjLTJmMDEtNDAwZi04M2YzLTJjZTk3MTg3NTdhYSIsImNoYXRncHRfY29tcHV0ZV9yZXNpZGVuY3kiOiJub19jb25zdHJhaW50IiwiY2hhdGdwdF9wbGFuX3R5cGUiOiJmcmVlIiwiY2hhdGdwdF91c2VyX2lkIjoidXNlci1lRVlxWmc5VURvZFJ2c3daRlpSS0lWYnkiLCJ1c2VyX2lkIjoidXNlci1lRVlxWmc5VURvZFJ2c3daRlpSS0lWYnkifSwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9wcm9maWxlIjp7ImVtYWlsIjoicHJvZmVzb3JsYWw1QGdtYWlsLmNvbSIsImVtYWlsX3ZlcmlmaWVkIjp0cnVlfSwiaWF0IjoxNzcwMzY1NzM2LCJpc3MiOiJodHRwczovL2F1dGgub3BlbmFpLmNvbSIsImp0aSI6IjNjYzk3ODgxLWU4NTUtNDNjMS1iNjAyLWE1NDIxMGMxODY2NCIsIm5iZiI6MTc3MDM2NTczNiwicHdkX2F1dGhfdGltZSI6MTc3MDM2NTczNDEwMywic2NwIjpbIm9wZW5pZCIsImVtYWlsIiwicHJvZmlsZSIsIm9mZmxpbmVfYWNjZXNzIiwibW9kZWwucmVxdWVzdCIsIm1vZGVsLnJlYWQiLCJvcmdhbml6YXRpb24ucmVhZCIsIm9yZ2FuaXphdGlvbi53cml0ZSJdLCJzZXNzaW9uX2lkIjoiYXV0aHNlc3NfeGVRb3hOcFRiczdsZnZGSEJlUVY0OFA5Iiwic3ViIjoiZ29vZ2xlLW9hdXRoMnwxMDg1MjgxNTg0ODk2NDc4OTcxNzAifQ.a3jjNh7ORKfawJ6cLFoLosK7ES0d7WquZ1bOJHMKvPUavQ4gYrrd1P90vJMFyggwX4PAB4yjvVN2x_zuGGpCeqcdK7oOVcaY6PquMgc5gQurxnXe5FTCJSzsnb9fSk6D4QQwMhd2mN6j7wpwaMgszIYobtLYcTidKxvrBPV41yoSzFBsFQfhyXIOyd7QPfUQY2nTbqWIlXHRvyZTzQBQDmFhNeEJtl3p20vUqpVf9Pem8HByx2_Soc76BkHrwTZv_6NdjeOQXeGUPe6uqlNJKAleKTw98scni-wRtwsHzH8w3HftcDmySXNDe9sm6ujXRwffxpI_sjEkxgHxO0AhgW7hyzTzi3tZofoV1nLINlKUEljzNiVfFUcPb5sW4MjAbu7dqV07zBoAHu2mvp74QK49iW2nlugoN5VwTHfsW6e1263a7ZiXWuI3sORO3dDEPnwM7qYrLq2Kdfn11ip2E9J2Nk8y2jceICffvrcfnWpv5etd5uaLW_IgfF-pQzALJxy_tuIX_Xi5Anhytd3hceAt66_1-gw3uj-54xttqF7UZu5Dn4W3T7Hq7pAE1eW-UYmr2oCJVzpZOehzgUDEJUr_nJp7Y6GZdTplUwKJwQVbbwuiJoA3s_jm58qNJ4srgxVpLsYYKAklPd-_lcJd4JoXOpXPBE-PI6q6WKZW-dk',
        'content-type': 'application/json',
        'oai-client-build-number': '4480993',
        'oai-client-version': 'prod-7c2e8d83df2cf0b6eaa11ba7b37f1605384da182',
        'oai-device-id': 'f49caa8e-b1a8-472a-b61a-61b8f8aac215',
        'oai-echo-logs': '0,3953,1,11784,0,216305,1,216312',
        'oai-language': 'ru-RU',
        'openai-sentinel-chat-requirements-token': req,
        'openai-sentinel-proof-token': 'gAAAAABWzIxMzQsIlN1biBGZWIgMDggMjAyNiAxOTo1NjozMCBHTVQrMDYwMCAoR01UKzA2OjAwKSIsMjAwNzc2MDg5Niw5OCwiTW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTAuMDsgV2luNjQ7IHg2NCkgQXBwbGVXZWJLaXQvNTM3LjM2IChLSFRNTCwgbGlrZSBHZWNrbykgQ2hyb21lLzE0NC4wLjAuMCBTYWZhcmkvNTM3LjM2IEVkZy8xNDQuMC4wLjAiLCJodHRwczovL3d3dy5nb29nbGV0YWdtYW5hZ2VyLmNvbS9ndGFnL2pzP2lkPUctOVNIQlNLMkQ5SiIsInByb2QtN2MyZThkODNkZjJjZjBiNmVhYTExYmE3YjM3ZjE2MDUzODRkYTE4MiIsInJ1IiwicnUsZW4sZW4tR0IsZW4tVVMiLDQzLCJ3ZWJraXRQZXJzaXN0ZW50U3RvcmFnZeKIkltvYmplY3QgRGVwcmVjYXRlZFN0b3JhZ2VRdW90YV0iLCJsb2NhdGlvbiIsIm9uYmVmb3JldW5sb2FkIiw1OTUzLjM5OTk5NzcxMTE4MiwiMmUxMDRkMjQtNDU5ZS00ZWY3LTg3M2UtNjA1NGRhMDZiNTE3IiwiIiw0LDE3NzA1NTg5ODQxODUuNSwwXQ==~S',
        'openai-sentinel-turnstile-token': 'ShEZBhgICgwNFHByEwoUHREfBxgFCwwNBwQKGAMACAYfDwUCHwwBAR8LFhUMAAIfDg8dERoBGAAHDA0Uf1lvQnl1dhMVGhMFFhkDCREUFXMDV3ZlB38Df2FOaGFvRGJTZXRubgBLT3EGY2Z7XFlpYUV5VWlfZHJkZ0NiZXRrVnhmA3JkaGFhYnUbbmB3UGF/BmNme1xGVGRobmJyZXxmU3R9R2BwWWR5W0ZyZkJmY3MCfGZTdH1HYHBZZHlbRnJmQmZkEx8MAgEfBhsVDBNSRnFGZnVCYG4BQHZRBwhib3VjUHYcZX5mYntzc2B6anBRd0YMGxQIHRoDFAtHXEJTHREfABgAAQwNQkNGSxsUBAsABAMTCQx4cnhGYHNVDBECFQQHHRwOFAsRSltOc2ZoR2N0AUptYGMLenVYawRKZVkMEQIVAwEdHQEUCxFNfwdBVh16ZHYBFwFVWQN6VQVBSmtgcGFmH3lTaWJ0XVNJVHpScHthe3V7VXUdcXJkXxtRbgB5b3NeCXR9ZntUcVlud3NeXk1+SXUdZQZnZ3tcVVVQWVR3dAJGcldgYV1/XlV0f1pBU1JvblJ0AUFyVVZybXVBDA4MGxQEBQAFDhMJDHhiWkZgY10MEQIVAQIdHwAUCxFgdAIEflkKCxMfDAAHHwYYFQwTcm92d3Byb3Z3cHJvdndwcm92d3Byb3Z3Ex8MBAQfChsVDAEdHgUBCQQaBQ8ICxYHBwAKFwYFHREZABgEAgwNFGJbaGBwYERsf0FJfGh2RXd0SXpXdAN8f2MBeXRyZGB1bE8OcHVZVHdwRHd0cGcGe2BdXVV/X3t7e1lyb3NxFnpnAF9scmRgcW9QY1d3a2FQZ19aVnBgRGp2UXB2akBzd3tvfld0Amh1ZwF5S29RRn1vdlV1cm9+V3QCF3tjZHp6dGQEeAwbFAAEAA4OEwkMdWcMDgwbFAACAAIDEwkMYmBndH9bcGdhRX1kZ3dvDhQdERkCGAAADA0UYltoZ2dnV3dvBV1We1xgU3dvB35wVHtucUlhf2VjCFZ5WVpmYR91U2Nha2J3cGJkf0FJZH9xdFBiQENiZwNNenJgakpyYwhxeAZeV1ZCX3F0REVScUlhfW9ze2p8f2Bma0V5U3RERVR3SVx6YAZFVnthWnl3bwd0c0RddHBkdXdhB0FVaHZFcnt/cnd5YWhSbloGe28EQWB8XGBTd28Hc3NmGgoUHREcGQYAERQVbl15ZnBwVXF/BgZoZXhbZWZeYFJgdwZicHN/cHhbQVBmHWVHaQEfZXV2aV9gcHtZfGEHcFFmU0ZXe1YYbAB1dnAHZ2J7cmxidHtld2IDe1VgeWkdZVtJZG1ZQktkeGFeZnZ8dVcCZR5tbgAHdGJOfnR/enFiAnd/dEZ5YHBRY2tvTw5QcklycmJxQXh3XWZKcFFjYHhfVXBxWVxzdkRsb2NzS0pmB1V3f2JCZXQffVVgdmhjZwFxfmYHCFZ2YQZTaWZlUVQAXk1UXFdGUX5/HEx/B1tWeXkEUmtWTVdzC3RzQWtlb09BfmUfeWR2ZWtzd3dxZ3AHXWJ5UHBmYUVxd3BlF29nXHFiYlpZHHZwD3JiQlsAYAJ0ZWB3YWJgbll2fUB8aGUfUFVlA0pwZ1lLb2B+VXZ8B2xaZHhTfld1dHRgeQpmYFoIdUhccGVlHwYZZQN8dG9dR2xmcFlEfEB8aGUfUFVlA0pwZ1lLSmZaY3B7cU5UYUVhV2BlQkBjd0NtYlp7SXtcdGtmHnpQaV9kcmADcU9gc2NmbWFRdnRFfWFlA3hhYWMCfmYGe3d5XE5yYkJfVWB2f1VnXWlPZgcIVXhxBlNkeHVXcGV0cGdkcmxvB1VwSgdSdGQfX3dpZWRkbGdpfmBdZxx2cA9yYkJbW2lYfGNuZwttYHBVamZcRlRmRgZnZQJ8UmBzS0xlcH93ZlxCSFEedUZiYhtdUmdLZW1jc3B8f2RJUWl5AmsBaGJlc0RNdlp/Ym9fUXZlfEB0YnJNf35wUEt1B3BQbU8HY3F8QHRlZWdndFZ6eXZ3fHR7XA5oYXgOVWl1Wm9lYwZpb11ndXZxTn50QgJlY0tGb2dkfUpwcFlWTVtCdWtAW2Vpa3RhY11pS2YHf2VKYnBoYkJldFBfG3FjXQp+b1pFZn9mfGhlH1BVZQNKcGdZS29gflV2fAdsAGUfDlJiA3hOYXRxYGAHVWp2XEJ3V3gGV2ICQnRsZ0dNYlpVZUtcWVNkeHVXcGV0cGdkcmxlB39VeAdSa2Z5fXFpdmhjZXZxS2JaZFd2cg9UdHh1cmlYf21jXXlLVF0EcXlbTmJhdnV+cFh8dGN0V0pvWnRVf3JCdGF4X1VgdEZRYGMDSHZ3CXdsZQ55d0l+cnBfRm9hc0t+ZQZrcGZyfGZhe2FcZQJsY1BnS35iBXtifAdFU2V4BldiAkJ0fndxbGVjY2Z2Bw91Zh9iU2llZGFnAGlPcAddYnlQcGZhRXF3cGVCbVV0aU9vBH9weFtOB2YefXdlAmxjVV1LTGBjeFd2cg9UdHh1cmlYf21hZ3lKVAcIcHZcUnRheGVuV2Zgb2cBV2JlYWt3dnJ4eWJAeWVgdUJjZ0kKbGFaCVZ7WwN5YXwCVGUDfE5hdHFqb11ncHhcRgVkaHlVZQN4ZGF3AmRtcAhleFxOZmFmZVJmWHhnfmdLfm9eXWJ/BgN0dHh9VWB1QmNjWldsZW4IVU1ycGtiQmV6Zl5kcGddaW1gBWNidmJ0YmFpVHVwWHx0Y3RXSm9adFV/cUZmZWgGYVJlG2R+Rnp0c1Fge29fAmFxf3JQYGV0UX5deUtlBmRWeAdCZ2IfbVFiA2B0YXcCZGV0QVZ7XFVoZR5xcmlhRmFuXQJ3Yl1jcHtbRlRkeA4FaXV0ZmRkcm1gcFVqZnIPeWFFflVgX0ZlYF1pZVFaWVh/ckJ3Vh9ld2lldGVgeXFsZlpZdWYHcGZrVgJlaVhKcX5aV35mB393fAdwdVUfDkFgZUJ0YHRHT25da3B4XHxTV2h1UmB1ZHJsRnZtYXBVYHkHRWhiHn1haXFeY1J2Q0hVYHR6dFpGaGZDeXBpZmxEUgNfFlF+CF17YVVocW9lYWVEd3Z3d3JqdWBwcW9Qe3VxSXFgcEQWcWBGcmp1YGdmbV9ddndJbnF2ZXRvfl1xbGBjc2Z/YQdmYkJfVWBxXlJhdHFPYHNjegwbFAAFAA8AEwkMdlEMDgwbFAQBAAQBEwkMdWcMDgwbFAUEAAUDEwkMYGNVUnwHY317b3FjaV9sf2VkV21/B3NxeV9dDBFT',
        'origin': 'https://chatgpt.com',
        'priority': 'u=1, i',
        'referer': 'https://chatgpt.com/',
        'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Microsoft Edge";v="144"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
        'x-conduit-token': token,
        'x-oai-turn-trace-id': 'b929398d-fb8d-4f30-8257-b0a87c949197',
        # 'cookie': 'oai-did=f49caa8e-b1a8-472a-b61a-61b8f8aac215; _ga=GA1.1.292430756.1770133139; g_state={"i_l":1,"i_ll":1770362114073,"i_b":"IFhSqKx7PLKGzqT/eTztkhsTEh4Rxkjg/PAh3jKLBOw","i_e":{"enable_itp_optimization":0},"i_p":1770140342337}; oai-hlib=true; _account_is_fedramp=false; __cflb=0H28vzvP5FJafnkHxih7XmuVuidhF3NrzJ7BynNR7qu; _uasid="Z0FBQUFBQnBpS0VnTGJLeEJYLUVUSW5tNHFDV0NUSi04RktubXZxRDJkc0JQV0w5UDJMRk1Xd0lsWEVKaEpmdjJNTERfNVZidEI1X2hwN0ZlZ0VydzRpTFUwd3o0SC1BNEx6N29nWHlRRVVCNDNfeUM4dTdteTQ3aXB3WktyR0ZFSGxlMlZ4czdWMk1uR2wwRG9hSTkzTzBMRkFLRVh1M05PVl9iOGhTbDN5cndTQzRzVHlOSFVhelduNE9pZENmaDF5UkF2ZW11Z0g3TmlaTEJvUGVXOGI1LVB5VVp6RGppT1REaXdHblc4U2thbV91a3ZiaWh6MXpXV0VJdWUxSUhmVjNCU2VmSXhCT2J2LVE0ejlsQmRFNWt1U1RZREJxSWZPYWFEcTVVRE51bXpZMW9KZGNnbGFMeWRQSWRkQXBfMWZaTzVHZXZJUnp1MDRocHNEZ2s4dzRLQUtMZVJVZkF3PT0="; _umsid="Z0FBQUFBQnBpS0VnMm9mQUYxbWpITkxlckdYVTh5U3dKMXdxUEVwVnV3U3RPM09TQ3I4ZGZ5VEU0MjF4ZEViWmdkV2ZqdHZPcjdzTnZONEQ1czlFRFp4endFNFVsUFZxZEZqT1E1aDJOUkRhdTNZRVhrREstOFpJSHpFY3N6dlJBRlRXWk1HR0c5ZDR3eGhscm1XdkRqYXdvWTFaZUhjbDlRb2NJTEMzLWxUSkZEWkkteklVN1FPazI3MWhqaGtaek9Td0h2SkJXcERnZU5LV2hlVjI5Z0xSTURvamRzUGV2aExYOUdwbGhUNHQ4Vm4xZWo2ZmNUTT0="; __Host-next-auth.csrf-token=5f9ae71c0f66fb0dfe7b080becc5b4d3a28482752652f86f2d61c69cfd75c78f%7Cf399c3ea965af14e7ebd1691d09d48969f78bb90fb15c7a283bcb4b1fb47e2d5; cf_clearance=NMUsrVKTrmJPtC9sIsiSWi.pNIoMjV7QJh.07Tc_CCQ-1770562506-1.2.1.1-cf1i3oYooDb3HAHNUADS1vRLmQSA6Bgd7DCvWPAyJdgZIXc74nqqAH8UrHtjoIPz_HwOARBq_YDgHkL61a5SYtP8zDGdtmoFB0tX5.5tftoFvCoqTXsAIRnaM.VsJ6shnILq7RaJTegRNIajSPBovJwOx_nHtsbIsKXnBfPkEeYVK8PLA9tA_UxQhKmaOdFay8nzoLZTtga6oJ5g3HkrEBCVd7yAMeTDXYKQGVFkoL0; __Secure-next-auth.callback-url=https%3A%2F%2Fchatgpt.com%2F; __Secure-next-auth.session-token.0=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..yKd7pOXfyebO5aal.kBsyS-1MaWi7Xo-cCt-faDKWsdRRqDK63uy52WZnG94nbprvlunh-yQu-EOEU5jC_9P2MmrNL6BJLVaPeMkuxJtVyWSWS7ZmN4-F-MCQ6et5HPVQmpnXpSxuDq9UUfHQm857EErtFjvmACiafNgrMPmu3S3tjkE-mhqvBzDuc4Fk6gIV8BCFMhCZCYVjPbvopVEsGYiYHtB--GWyvldL--sOOXAq_gOZTnH99xSzUPcPFK-5WH5_UJBzh2OmQ1y495Enap4Wacan2txrUGJ26aTRp5E1YRsLJknwCWxgZtouOa9sKd9hMxKHhxYXNohkm14qikGTutPs1xslbs8gRtl7QkCSPygV7w6gGcROuTKEMGStWMl17v8gXMnGpj9UfVBoktLdZEg39jGkpFKx4-gT9k_ChjB_j0elweX3eo01aTcrC2-jRafBiK_5uHIcQqxyhKgDR27llVJ5JJ30l4uHIAqSxeK2fhHDa8ZW8Sw5qi0mdPpvLNC7ksmJPNuRnQXlUDkHzi03BMK19jfFvMbooIBx4okw_hx12Z-cKJHH8dZeYr0q8UqBjM3flmLyj30Bgo1JMVr1m9d3rR4Gq2n0J0aWMifuks3rpSemRz195fAUVFzaTBZNwB5tW8DceP8SzPk6gcDtmCMe79ojT6GUqYTV_MjrGsm5aYz7EUi_-zweuLT6WU3Mxo_LWlEtZ6Euj8Y0Ut0J-lohT5GqUMy-7Cs4pcOt1dm2HPy45i_8YBnrKtf83xl83AZZrA8V5Rh_LyB3XotvWZT-CQ-YUPok4dYUL0Qbaowy0D_nWdiybNQSTf_kruczvVKknOsZrkKWL3CNpij2puVcfBiewEcdfQC06Mg2NffIUWLJiyaw7EAtoY2NlU3uUHpz_tRf9VOP49PHZ89mJdlsuXTQxtk1ZBaoUBkBrAiOHhz-10wobfim-JsjSOC6Zf2eIhtFZ9u_nIKOaDV2qFYMW0pByHkm5rQhs79-ozY5PNlr1qZsJCPaqS5EsGkZAmyHzGWwtDcux9B0ZbYSjmPuXY7v9oE5o4nKR1J3H88EaMgZfhD2h50LugY9W1WJIzrrePop0vJ3NTLyX4-PFBF9oKDIS8Dwef2UYFS5SaVadrO2PCa1ZKchGP94zUY_UvH6gmZL4XFqc4pGkHIdyS62nOb98bmobvcSwarquUoWDfbuHNJ9bby_Hnp7CpuTjVR9GURzboC6RmS7cz6JGWUlZ9_tLiCBh3fxef8omhyIExWjadCCUwkQj6Ml-PHbV_8wAMWcq3mqTw330ptZvz8774JuG_vu731keYalYOU3lbWZP620FNtyl7UTDPjbbsft4bvBmuHTRWQwLbKCkLUdUI_VDGgm2wpOghQEWIwT4-WCuz7NgUXHZlUlA9KbdH_5LFLSrlZvRk1OWExZK06BND7rAOCJHwyesFa7SDn0mVkIGrDZ7uH-pGZe1RnKsQ2qfO4KfSaV7LxA3oMc-4V9gPrz7dTqqxJSIQ0Opki_t8T7E_1tGwGD_u_jS9nA_dvKT7q9QoFLLDfIoPXXU7qWCMVB3fu4eA5d4fcpk8PC_nkqDckQlTzFdRFxdzdwgAkaD7oyJ47gJtZzJun7gYlRbdFX3AzaouekqLvco1SlU3j4pIJrPZbfF7CVrZFMB1KIUnMM80rosSadCKwgkoD455wFK4U6OVeQh2UdKm--YN1a5ejK1AEqWQSMDutoKolcYo0WQCiS1646R1cx_cMb4S-BkPfea57DDOa7zCLcjuX7QyP3DeFn9nYvXhRr2qP3qbm8C5WhF3r1gc0nisrImu7kfLH8oqbXrsV4ACcFQ7et3Ux-2pRes0nPybA5ehkneyIbrJNnXh6jHAbx2xboCZ7DSQ2JCKEg2ivaAaSyyE_QR8ucJPqQKIVXw_6Wxf1IOECXLSNyNfUTmJ8GGF3Wto_UmtPZ8l0B1DO_X4ON_n6N3iJsk9MrvfeCJrUe5jGFZRMQ-3xxfKlPwsZk0Mv8TDrP8UWZ4n_cOWdq-qU4bsj3RadMG9X_da4Hf9YlfDjlZaTQ1ZAnnqjuQeWgxdqEueLB7EgniMAzfJ38aTikKwD2TPW7O0OV5e8N0DF7DG2h0S-Cq0BOk-xYZj1QlRymrFSLVslhkde_nKY1za4lzJFeBdD1nTfUdDjR-2rsvrddiwMuqCZcsdkR88JuipoDXEHZUUHMkS2oHoa25MU0Zt0t0Abthbn4VinIbfgTaU3HuCxGnxSwHACk186nGlGq2OFqpY0M956Vc-akFGHPf2JNPhr8uzL1xXliNmNIWgb91ra4QN6osoAa7p2j9_t3kkqQYas5OE7z1GHZi7ESO3miFNI8pxRkoOYw3q_qGOjm2ovreERy0--xEg2OkAvwLq7iwos2Pw0_yZ9ald6MF3bogP5d3ZQg2c4RcxZ5zUmou2BcLldxZKet_lix9irtsnIbxp0Ui3D_iSQlu3c1Rf4CLu77i-boNxY4VJWarIdoO6xNLsCJMTKQc1UD6Uzku9LoWuxfOaNk8M_sulgq_KknCoznHqG0Rs5YW6lDfPiU6WqbWvG9WvpQj5Nv15TmVgW3R6CrOKpDR9EG9MvWCQ_1Ck0Dyhv5SCBBST00SCI3MADR_qxwmcMNDaVI7tHNC62l5tpxaeWf1Tm9tEfIuUuBJz57i_9r5BUI3tNwRxk0wucmHwFpcW-t2ul7gW7HUJmtmPDh7z73qVCsNbDymLW7-RkbD_DGIxtzvGN9PWDMedLKhdsS2nkHZcnJGAIYIUelWXptUlt7cmidipgmvyUkepT05BAGFv9IFlsje2ytqtpWt7TEA-y7rY6s9OBTMSXyR_OoBvWDTbYOVMRn0bHE4IrcKpj-boEM9VYzNGf9JdaqAOGC2W8iOZPId_jspEeeboAIyf3Zsnut23pSIN0lkwa7v0jtHb0va22T-MZsKxsJmeCkCHhz9agENpYQfxDd7UY0AG03bpS0CH9CU__8S8-SfSl8a8niDeqWovqrBdwzwPu15oOD4wNlNrpWGnQrOi_6gC54DvEPO8SA3uhXX9NSGSn3Vt9kbxZnkCyN6QcGcsQTg6ugzi0sJNwq58fuq2gq9JKMaoQBXLu3DCW5L54o5jpksb_1Tun4Yc6jRKVE6exB8UJrt5rG97BAQV0iRKc87NsATPzooda1goyOZdW-exlszXuisnutz0AyfP3jv-JkhBkQp53Rezj6VTxyf-3feQGzkmD-DvhZTzku4xUkXBkpothy0NEh1GzmVAqIHsIjbJ3Hi1geIIH8tZ7s6S7qLasgj59gxrEkM8Y2D5wQD_KdoneBrsrAikyyRa-JLIVPN5yq4h6Uy9Rx1WUlzXB0vJTCrHMWIW-uc5f9bkCLLet9AcA1M5vX5EW1ry04PmZvFCejoovYzJu39qZgA43f3wJV7f3xvFg4xEHR7w9IGTCGO-YmI-ApyD-cDFv7_cZtFeS-JBkLHILorUUpHn_Nm1n6bV4JwobfTM0cSDEtcdUk7saB8linjstQMblH6wVVgdhEEulR_uJrC7Q1pEhfnC_QmRZmkrwraquebYRoj7GJw1urPtXIPuXjezMjDdYRBItTktr_V5s1iPt_CGK_KCHqq7w8eeZEOshoSk1pC2_4CdHL-yEoUx6VbPz8Uq2qjK7Zrzx-W2ADj61BNXTRJ-JssjitqL4iGfLymYbeenyjEN_VntoCjGbXKb-ow1d4LBvqeQqW2LI0UnTdqRr_P2yqCzTQ6WWQqGAbo8bE1b1pOIU9BIho6RGZ2o8fgxBs9maTAakHmzJAfknohulzPTFychSwGrTwheRp6KuWsGWCYgy3vpoiX3d0ALKLrmhvrqi_HzEdh_NrCSD30_HQk6RJagelU6w; __Secure-next-auth.session-token.1=cf9LSPnzHA3B_D1uOZtcQedttfDikxu86q9fEw-8uNfU71Rlk3lsn7qrPNV5LQ9L7qd3vwdnokYieLp5Fb3cWvRSuWOjeSp-epcJHpCX2ziB2nJlQDh2H9hHIuAElPekXn1E4gdWEGIEEZ6gkkRv2laYmCb1shtRi7Y182zxFTRCLIPRjQoyfsAZHHv-cZEkdWnvw_7LAliAcu7PgFMpHpog24Ig.ebPBq-hQaZoswaGOtAPddw; oai-client-auth-info=%7B%22user%22%3A%7B%22name%22%3A%22FFF%22%2C%22email%22%3A%22profesorlal5%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fff.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770558990065%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D; _ga_9SHBSK2D9J=GS2.1.s1770558176$o4$g1$t1770558990$j59$l0$h0; oai-gn=; oai-hm=ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE; oai-sc=0gAAAAABpiKQPyGgmpEH_DA4tXL5mtq1UroadG2IqOdiOPAnsgYVgAhAHe1BcaLjzx5xKi5Bw07penQUUs7VAM6cP7Fep30dynR3DiLD1ZhoFC6DFucWDPLazhPB7aQkzjUjBW31u0uMSB5lzvzunM8vk02M7pZaHgoEo7pyvukFWwv7BYuq_8Mq-oz60RC40A3_ds4-D44muzTKIUGDgGHAvl-JPi3WPFt7rZj3m0ajN7mRH-4Wi-GQ; _cfuvid=KIbXypYx4l9tW.5Zj1Praq42wzgCp_fogA814RTa3l4-1770562575.9542005-1.0.1.1-pbCy.tgwNdtySdrJmgkQNwP0KiXW2y5zFN3GYRyj8Sg; __cf_bm=p_NhIs6u3pFDkd1eyAgu.3sbt.mkbduvMi2u2uAIVHY-1770562784.032666-1.0.1.1-hTmI4TWScrrN05N.XnnnyoZaMxIE3ap_vghPl7Io7X3vg376Z_tBw3NgA.xKZsg.x8Qt534xpH0HLbFazIwK6ov76MHVCW0aWabA19dNIGFss1GGOtyZkpHYZWKtZ5X8; _dd_s=aid=1b42e6ea-3c02-48bd-830d-92a29fb20553&rum=0&expire=1770560100814&logs=1&id=28080f70-f74c-4c58-af10-98330710f7f5&created=1770558153456',
    }

    json_data = {
        'action': 'next',
        'messages': [
            {
                'id': '05d7f6e1-a481-473f-b481-4ea9dfc7a2ef',
                'author': {
                    'role': 'user',
                },
                'create_time': 1769666193.246,
                'content': {
                    'content_type': 'text',
                    'parts': [
                        prompt,
                    ],
                },
                'metadata': {
                    'selected_github_repos': [],
                    'selected_all_github_repos': False,
                    'serialization_metadata': {
                        'custom_symbol_offsets': [],
                    },
                },
            },
        ],
        'conversation_id': '697b0483-5aac-8325-844f-1a03001bd4b0',
        'parent_message_id': '3692140e-4b05-46cd-863a-eed4023c4f05',
        'model': 'auto',
        'timezone_offset_min': -360,
        'timezone': 'Etc/GMT-6',
        'conversation_mode': {
            'kind': 'primary_assistant',
        },
        'enable_message_followups': True,
        'system_hints': [],
        'supports_buffering': True,
        'supported_encodings': [
            'v1',
        ],
        'client_contextual_info': {
            'is_dark_mode': False,
            'time_since_loaded': 130,
            'page_height': 641,
            'page_width': 858,
            'pixel_ratio': 1,
            'screen_height': 768,
            'screen_width': 1366,
            'app_name': 'chatgpt.com',
        },
        'paragen_cot_summary_display_override': 'allow',
        'force_parallel_switch': 'auto',
    }



    data = requests.post('https://chatgpt.com/backend-api/f/conversation', cookies=cookies, headers=headers, json=json_data).text



    pattern = r'"v"\s*:\s*"(?!finished_successfully")((?:[^"\\]|\\.)*)"'
    raw_text = "".join(re.findall(pattern, data))

    try:
        # Декодируем escape-последовательности
        text = codecs.decode(raw_text.encode('utf-8'), 'unicode_escape')
        
        # ВАЖНОЕ ИСПРАВЛЕНИЕ:
        # Принудительно кодируем в utf-8 с заменой битых символов ('replace'), 
        # а затем декодируем обратно. Это уберет "surrogates", из-за которых падает сервер.
        return text.encode('utf-8', 'replace').decode('utf-8')
    except Exception as e:
        # Если декодирование совсем не удалось, возвращаем сырой текст или сообщение об ошибке, 
        # чтобы не ронять сервер с 500 ошибкой
        print(f"Decoding error: {e}")
        return raw_text


@app.post("/api/run/draw")
async def run_quickdraw(
    request_data: QuickDrawRequest,
    db: AsyncSession = Depends(get_db)
):
    COST = 50  # Сделаем этот запрос дешевле, чем GPT

    # 1. Проверка ключа и баланса (копируем логику, можно вынести в депенденси)
    stmt = select(APIKey).where(APIKey.key_hash == request_data.key)
    result = await db.execute(stmt)
    api_key_obj = result.scalar_one_or_none()

    if not api_key_obj:
        raise HTTPException(status_code=403, detail="INVALID API KEY")

    user_result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=403, detail="USER NOT FOUND")

    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

    if not has_unlimited and user.tokens_balance < COST:
        raise HTTPException(status_code=402, detail="INSUFFICIENT GLOBAL BALANCE")
    
    if api_key_obj.limit_tokens < COST:
        raise HTTPException(status_code=402, detail="API KEY LIMIT EXCEEDED")

    # 2. Формирование запроса к Google
    url = 'https://inputtools.google.com/request?ime=handwriting&app=quickdraw&dbg=1&cs=1&oe=UTF-8'
    
    headers = {
        'Content-Type': 'application/json',
        'Origin': 'https://quickdraw.withgoogle.com',
        'Referer': 'https://quickdraw.withgoogle.com/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
    }

    # Google ожидает определенную структуру
    payload = {
        "input_type": 0,
        "requests": [{
            "language": "quickdraw",
            "writing_guide": {
                "width": request_data.width,
                "height": request_data.height
            },
            "ink": request_data.ink
        }]
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            google_data = response.json()
        except Exception as e:
            print(f"QuickDraw Error: {e}")
            raise HTTPException(status_code=502, detail="UPSTREAM SERVICE ERROR")

    # 3. Парсинг ответа
    # Google возвращает структуру типа [SUCCESS, [ ["result1", "score"], ... ]]
    # Мы упростим ответ для клиента
    try:
        if google_data[0] != "SUCCESS":
             raise ValueError("Google API Error")
        
        # Получаем список вариантов [[вариант1, вероятность], [вариант2, ...]]
        suggestions = google_data[1][0][1] 
        best_guess = suggestions[0] # Самый вероятный вариант (строка)
        has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()
        if not has_unlimited:
            user.tokens_balance -= COST
        
        api_key_obj.limit_tokens -= COST
        await db.commit()

        return {
            "best_guess": best_guess,
            "all_suggestions": suggestions,
            "balance_remaining": user.tokens_balance
        }

    except (KeyError, IndexError, ValueError):
        return {"best_guess": "unknown", "raw": google_data}

# --- ROUTES: PAGES ---
@app.get("/")
async def read_index(): return FileResponse("static/index.html")

@app.get("/login")
async def read_login(): return FileResponse("static/auth.html")

# Добавили маршрут для регистрации, ведущий на ту же страницу
@app.get("/register")
async def read_register(): return FileResponse("static/auth.html")

@app.get("/dashboard")
async def read_dashboard(): return FileResponse("static/dashboard.html")

# --- ROUTES: AUTH ---
@app.post("/auth/register")
async def register(
    user_data: UserRegister, 
    request: Request, # Получаем доступ к запросу для IP
    background_tasks: BackgroundTasks, 
    db: AsyncSession = Depends(get_db)
):
    client_ip = get_client_ip(request)
    norm_email = normalize_email_logic(user_data.email)

    # 1. ПРОВЕРКА ЛИМИТА ПО IP
    # Разрешаем не более 2 аккаунтов с одного IP
    result_ip = await db.execute(select(func.count(User.id)).where(User.registration_ip == client_ip))
    accounts_on_ip = result_ip.scalar()
    
    if accounts_on_ip >= 2:
        print(f"Abuse attempt from IP: {client_ip}")
        # Можно вернуть ошибку, но чтобы не злить хакера, можно сымитировать успех, но не слать код
        raise HTTPException(status_code=400, detail="TOO MANY ACCOUNTS FROM THIS IP")

    # 2. ПРОВЕРКА ПО НОРМАЛИЗОВАННОМУ EMAIL (защита от алиасов)
    result_norm = await db.execute(select(User).where(User.normalized_email == norm_email))
    existing_normalized_user = result_norm.scalar_one_or_none()

    if existing_normalized_user:
        # Если такой "реальный" юзер уже есть
        if existing_normalized_user.is_active:
            raise HTTPException(status_code=400, detail="EMAIL ALREADY REGISTERED (ALIAS DETECTED)")
        else:
            # Юзер есть, но не активирован - просто обновляем код
            code = generate_code()
            existing_normalized_user.verification_code = code
            existing_normalized_user.hashed_password = get_password_hash(user_data.password)
            # Обновляем оригинальный email на новый, если пользователь решил исправить опечатку
            existing_normalized_user.email = user_data.email 
            await db.commit()
            background_tasks.add_task(send_email_async, user_data.email, code)
            return {"message": "CODE RESENT"}
    


    # 3. СОЗДАНИЕ НОВОГО ПОЛЬЗОВАТЕЛЯ


    referrer_user = None
    if user_data.referral_code:
        # Ищем пользователя, чей код ввели
        ref_res = await db.execute(select(User).where(User.referral_code == user_data.referral_code))
        referrer_user = ref_res.scalar_one_or_none()

    code = generate_code()

    new_my_ref_code = secrets.token_hex(4) 

    new_user = User(
        email=user_data.email,
        normalized_email=norm_email, # Сохраняем нормализованный вид
        hashed_password=get_password_hash(user_data.password),
        verification_code=code,
        is_active=False,
        registration_ip=client_ip,
        referral_code=new_my_ref_code,
        referrer_id=referrer_user.id if referrer_user else None
    )
    
    db.add(new_user)
    await db.commit()
    background_tasks.add_task(send_email_async, user_data.email, code)
    
    return {"message": "CODE SENT"}

@app.get("/chat")
async def read_chat():
    return FileResponse("static/chat.html")

@app.post("/auth/verify")
async def verify(data: UserVerify, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    
    # Добавлена проверка на None
    if not user:
         raise HTTPException(status_code=400, detail="USER NOT FOUND")

    if user.verification_code != data.code:
        raise HTTPException(status_code=400, detail="INVALID CODE")
    
    if not user.is_active:
        user.is_active = True
        user.verification_code = None
        if user.tokens_balance == 0:
            user.tokens_balance = 100000
        if user.referrer_id:
            # Получаем пригласившего
            referrer_res = await db.execute(select(User).where(User.id == user.referrer_id))
            referrer = referrer_res.scalar_one_or_none()
            
            if referrer:
                referrer.invites_count += 1
                count = referrer.invites_count
                
                # Награды
                if count == 1:
                    referrer.tokens_balance += 50000
                elif count == 2:
                    referrer.tokens_balance += 120000
                elif count == 3:
                    # Даем безлимит на 30 дней от текущего момента
                    referrer.unlimited_until = datetime.utcnow() + timedelta(days=30)
        
    await db.commit()
    
    # Сразу создаем токен, чтобы автоматически залогинить
    access_token = create_access_token(data={"sub": user.email})
    return {"status": "success", "access_token": access_token}

@app.post("/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=401, detail="INVALID CREDENTIALS")
        
    if not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="INVALID CREDENTIALS")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="ACCOUNT NOT ACTIVATED")
    
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}

# --- ROUTES: API KEYS (Без изменений) ---
@app.get("/api/user/me")
async def get_me(user: User = Depends(get_current_user)):
    is_unlimited = False
    if user.unlimited_until and user.unlimited_until > datetime.utcnow():
        is_unlimited = True

    return {
        "email": user.email, 
        "balance": user.tokens_balance,
        "referral_code": user.referral_code,
        "invites": user.invites_count,
        "is_unlimited": is_unlimited,
        "unlimited_until": user.unlimited_until.isoformat() if user.unlimited_until else None
    }

@app.get("/api/keys", response_model=List[KeyResponse])
async def get_keys(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(APIKey).where(APIKey.user_id == user.id))
    keys = result.scalars().all()
    return [{"id": k.id, "name": k.name, "key": k.key_hash, "limit": k.limit_tokens, "created_at": k.created_at} for k in keys]

@app.post("/api/keys", response_model=KeyResponse)
async def create_key(key_data: KeyCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(func.count()).where(APIKey.user_id == user.id))
    count = result.scalar()
    if count >= 5:
        raise HTTPException(status_code=400, detail="MAXIMUM 5 KEYS ALLOWED")

    is_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

    if not is_unlimited and key_data.limit > user.tokens_balance:
        raise HTTPException(status_code=400, detail=f"LIMIT EXCEEDS BALANCE ({user.tokens_balance})")
    
    if key_data.limit <= 0:
        raise HTTPException(status_code=400, detail="LIMIT MUST BE POSITIVE")

    raw_key = "sk-nx-" + secrets.token_urlsafe(16)
    
    new_key = APIKey(
        key_hash=raw_key, 
        name=key_data.name, 
        limit_tokens=key_data.limit,
        user_id=user.id
    )
    db.add(new_key)
    await db.commit()
    await db.refresh(new_key)
    
    return {"id": new_key.id, "name": new_key.name, "key": new_key.key_hash, "limit": new_key.limit_tokens, "created_at": new_key.created_at}

@app.delete("/api/keys/{key_id}")
async def delete_key(key_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="KEY NOT FOUND")
    
    await db.delete(key)
    await db.commit()
    return {"status": "deleted"}

@app.get("/api/run/gpt")
async def run_gpt_via_link(
    key: str, 
    model: str, 
    prompt: str = "Test prompt", 
    db: AsyncSession = Depends(get_db)
):
    # 1. Поиск ключа и пользователя
    stmt = select(APIKey).where(APIKey.key_hash == key)
    result = await db.execute(stmt)
    api_key_obj = result.scalar_one_or_none()

    if not api_key_obj:
        raise HTTPException(status_code=403, detail="INVALID API KEY")

    user_result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=403, detail="USER NOT FOUND")


    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

    # 2. Предварительная проверка баланса
    # Мы пока не знаем точную цену, но если баланс <= 0, отклоняем запрос сразу.
    if not has_unlimited and user.tokens_balance <= 0:
        raise HTTPException(status_code=402, detail="INSUFFICIENT GLOBAL BALANCE")
    
    if api_key_obj.limit_tokens <= 0:
        raise HTTPException(status_code=402, detail="API KEY LIMIT EXCEEDED")

    # 3. Выполнение генерации
    # Сначала получаем ответ от нейросети
    ai_response = await chatgpt(model=model, prompt=prompt)

    # 4. Подсчет токенов (Запрос + Ответ)
    # Запускаем подсчет параллельно или последовательно
    input_tokens = await get_token_count(prompt)
  

    output_tokens = await get_token_count(ai_response)
    
    total_cost = input_tokens["tokenCount"] + output_tokens["tokenCount"]

    # 5. Списание средств
    # Списываем фактическую стоимость. Баланс может уйти в небольшой минус,
    # если токенов было впритык — это нормальная практика.
    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()
    if not has_unlimited:
        user.tokens_balance -= total_cost 
    api_key_obj.limit_tokens -= total_cost 
    
    await db.commit()

    # (Опционально) Можно добавить в логи или вернуть в заголовках стоимость
    print(f"GPT Cost: {total_cost} tokens (In: {input_tokens}, Out: {output_tokens})")

    # 6. Возврат ответа
    return ai_response
# --- AUTODRAW ENDPOINTS ---

@app.post("/api/run/autodraw/predict")
async def run_autodraw_predict(
    request_data: AutoDrawRequest,
    db: AsyncSession = Depends(get_db)
):
    COST = 50 

    # 1. Проверка баланса (как и раньше)
    stmt = select(APIKey).where(APIKey.key_hash == request_data.key)
    result = await db.execute(stmt)
    api_key_obj = result.scalar_one_or_none()

    if not api_key_obj:
        raise HTTPException(status_code=403, detail="INVALID API KEY")

    user_result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=403, detail="USER NOT FOUND")

    # --- ИЗМЕНЕНИЕ НАЧАЛО ---
    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

    if not has_unlimited and user.tokens_balance < COST:
        raise HTTPException(status_code=402, detail="INSUFFICIENT FUNDS")
        
    if api_key_obj.limit_tokens < COST:
        raise HTTPException(status_code=402, detail="API KEY LIMIT EXCEEDED")

    # 2. Запрос к Google AutoDraw
    url = 'https://inputtools.google.com/request?ime=handwriting&app=autodraw&dbg=1&cs=1&oe=UTF-8'
    
    headers = {
        'Content-Type': 'application/json',
        'Origin': 'https://www.autodraw.com',
        'Referer': 'https://www.autodraw.com/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
    }

    payload = {
        "input_type": 0,
        "requests": [{
            "language": "autodraw",
            "writing_guide": {
                "width": request_data.width,
                "height": request_data.height
            },
            "ink": request_data.ink
        }]
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            google_data = response.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Google API Error: {str(e)}")

    # 3. Парсинг ответа
    try:
        if google_data[0] != "SUCCESS":
             raise ValueError("Google API Error")
        
        # AutoDraw возвращает список вариантов внутри сложной структуры
        # ['SUCCESS', [['GUID', ['suggestion1', 'suggestion2', ...], [], {debug...}]]]
        suggestions = google_data[1][0][1]
        
        # Проверка на безлимит
        has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

        # Списание средств
        if not has_unlimited:
            user.tokens_balance -= COST
            api_key_obj.limit_tokens -= COST
        await db.commit()

        return {
            "suggestions": suggestions,
            "balance_remaining": user.tokens_balance
        }

    except (KeyError, IndexError, ValueError):
        return {"suggestions": [], "raw": google_data}

@app.get("/api/run/autodraw/icon")
async def get_autodraw_icon(
    key: str,
    name: str,
    index: int = 0, # Обычно 0, 1, 2
    db: AsyncSession = Depends(get_db)
):
    # Этот запрос делаем дешевым
    COST = 10

    # Проверки авторизации...
    stmt = select(APIKey).where(APIKey.key_hash == key)
    result = await db.execute(stmt)
    api_key_obj = result.scalar_one_or_none()
    
    if not api_key_obj:
        raise HTTPException(status_code=403, detail="INVALID API KEY")
        
    user_result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=402, detail="USER NOT FOUND")

    # --- ИЗМЕНЕНИЕ НАЧАЛО ---
    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

    if not has_unlimited and user.tokens_balance < COST:
        raise HTTPException(status_code=402, detail="INSUFFICIENT FUNDS")

    # Формирование URL для SVG
    # Google хранит файлы с дефисами вместо пробелов (обычно)
    clean_name = name.replace(" ", "-").lower()
    # Индекс форматируем как 01, 02 (или просто 01, в примере 01)
    # Попробуем формат, который указал пользователь: [название]-[0-3 цифра]
    # На практике там часто бывает формат "cat-01.svg". 
    # Сделаем простую попытку загрузки индекса 01, если передан 0.
    
    file_index = f"{index + 1:02d}" # Превращаем 0 -> 01, 1 -> 02
    
    # URL из примера пользователя
    target_url = f"https://storage.googleapis.com/artlab-public.appspot.com/stencils/selman/{clean_name}-{file_index}.svg"
    
    headers = {
        'Referer': 'https://www.autodraw.com/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(target_url, headers=headers)
        
        if resp.status_code != 200:
            # Если не нашли 01, попробуем просто имя (иногда бывает без индекса)
            target_url_fallback = f"https://storage.googleapis.com/artlab-public.appspot.com/stencils/selman/{clean_name}.svg"
            resp = await client.get(target_url_fallback, headers=headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail="ICON NOT FOUND")

    # Списание
    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

    # Списание средств
    if not has_unlimited:
        user.tokens_balance -= COST
    api_key_obj.limit_tokens -= COST
    await db.commit()

    # Возвращаем сам SVG контент
    return Response(content=resp.text, media_type="image/svg+xml")
@app.get("/api/run/gemini")
async def run_gemini(
    key: str, 
    prompt: str = "Hello", 
    db: AsyncSession = Depends(get_db)
):
    # 1. Проверка API ключа и баланса
    stmt = select(APIKey).where(APIKey.key_hash == key)
    result = await db.execute(stmt)
    api_key_obj = result.scalar_one_or_none()

    if not api_key_obj:
        raise HTTPException(status_code=403, detail="INVALID API KEY")

    user_result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
    user = user_result.scalar_one_or_none()
    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()
    if not user:
        raise HTTPException(status_code=402, detail="USER NOT FOUND")
        
    if not has_unlimited and user.tokens_balance <= 0:
        raise HTTPException(status_code=402, detail="INSUFFICIENT FUNDS")
        
    if api_key_obj.limit_tokens <= 0:
        raise HTTPException(status_code=402, detail="API KEY LIMIT EXCEEDED")

    # 2. Вызов Gemini
    ai_response = await gemini_chat(prompt, db)
    
    input_tokens = await get_token_count(prompt)
    output_tokens = await get_token_count(ai_response)

    # 3. Списание средств (условно 100 токенов за запрос, т.к. токенайзер Gemini сложнее)
    COST = input_tokens['tokenCount'] + output_tokens['tokenCount']
    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

    # Списание средств
    if not has_unlimited:
        user.tokens_balance -= COST
    api_key_obj.limit_tokens -= COST
    await db.commit()

    return ai_response


@app.get("/api/run/image")
async def run_gemini_image(
    key: str,
    prompt: str,
    db: AsyncSession = Depends(get_db)
):
    # Стоимость генерации картинки (дороже текста)
    COST = 500 

    # 1. Проверка API ключа
    stmt = select(APIKey).where(APIKey.key_hash == key)
    result = await db.execute(stmt)
    api_key_obj = result.scalar_one_or_none()

    if not api_key_obj:
        raise HTTPException(status_code=403, detail="INVALID API KEY")

    user_result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
    user = user_result.scalar_one_or_none()

    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

    if not has_unlimited and user.tokens_balance < COST:
        raise HTTPException(status_code=402, detail=f"INSUFFICIENT FUNDS. REQUIRED: {COST}")
    
    if api_key_obj.limit_tokens < COST:
        raise HTTPException(status_code=402, detail="API KEY LIMIT EXCEEDED")

    # 3. Запуск генерации
    # Промпт уже модифицируется внутри функции (добавляется "Generate image: ")
    result = await generate_gemini_image_async(prompt, db)
    
    if "error" in result:
        # Если ошибка Gemini, деньги не списываем, возвращаем 500
        raise HTTPException(status_code=500, detail=result["error"])

    # 4. Списание средств при успехе
    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

    # Списание средств
    if not has_unlimited:
        user.tokens_balance -= COST
    api_key_obj.limit_tokens -= COST
    await db.commit()

    # 5. Возврат бинарного файла
    # FastAPI Response позволяет вернуть bytes как файл
    return Response(content=result["image"], media_type="image/jpeg")
@app.post("/api/tokenize")
async def tokenize_text_endpoint(
    req: TokenizeRequest,
    user: User = Depends(get_current_user) # Требуем авторизацию, чтобы не спамили
):
    # Используем уже существующую функцию get_token_count
    result = await get_token_count(req.text)
    return result



@app.post("/api/contact")
async def contact_form(
    data: ContactRequest, 
    background_tasks: BackgroundTasks,
    request: Request
):
    # Ограничение длины сообщения
    if len(data.message) > 2000:
         raise HTTPException(status_code=400, detail="MESSAGE TOO LONG")
         
    # Получаем IP для логов (опционально)
    client_ip = get_client_ip(request)
    
    # Отправляем в фоне
    background_tasks.add_task(send_contact_email_to_admin, data.email, data.message)
    
    return {"status": "ok", "message": "Message dispatched"}



# --- AGENT (KIMI) LOGIC ---
KIMI_URL = "https://www.kimi.com/apiv2/kimi.gateway.chat.v1.ChatService/Chat"
# (Заголовки и куки оставляем те же, что вы дали, или берем из env)
KIMI_HEADERS = {
    "accept": "*/*",
    "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "authorization": "Bearer eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJ1c2VyLWNlbnRlciIsImV4cCI6MTc3MjY0MTAwMiwiaWF0IjoxNzcwMDQ5MDAyLCJqdGkiOiJkNjBjcnFuZnRhZTEzdmZ1OXJrZyIsInR5cCI6ImFjY2VzcyIsImFwcF9pZCI6ImtpbWkiLCJzdWIiOiJkNDlna3YzYWNjNGZla2NyNGMyMCIsInNwYWNlX2lkIjoiZDQ5Z2t1cmFjYzRmZWtjcjQ5NmciLCJhYnN0cmFjdF91c2VyX2lkIjoiZDQ5Z2t1cmFjYzRmZWtjcjQ5NjAiLCJzc2lkIjoiMTczMTQzNjE0NDY4NTE3MzUxOSIsImRldmljZV9pZCI6Ijc2MDIzMDIyNTM3ODUwODcyMzIiLCJyZWdpb24iOiJvdmVyc2VhcyIsIm1lbWJlcnNoaXAiOnsibGV2ZWwiOjEwfX0._Abf3gshAcVJjYAUkAm_zvgA9VPSP7MCeCy9CRvOnL6fgyt78DpJXq488TiRXNr6wCMh2srvP5XFvT4rxJyu4A",
    "connect-protocol-version": "1",
    "content-type": "application/connect+json",
    "origin": "https://www.kimi.com",
    "priority": "u=1, i",
    "referer": "https://www.kimi.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "x-traffic-id": "d49gkv3acc4fekcr4c20"
}
KIMI_COOKIES = {
    "_ga": "GA1.1.299926138.1770045335",
    "theme": "dark",
    "kimi-auth": "eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJ1c2VyLWNlbnRlciIsImV4cCI6MTc3MjY0MTAwMiwiaWF0IjoxNzcwMDQ5MDAyLCJqdGkiOiJkNjBjcnFuZnRhZTEzdmZ1OXJrZyIsInR5cCI6ImFjY2VzcyIsImFwcF9pZCI6ImtpbWkiLCJzdWIiOiJkNDlna3YzYWNjNGZla2NyNGMyMCIsInNwYWNlX2lkIjoiZDQ5Z2t1cmFjYzRmZWtjcjQ5NmciLCJhYnN0cmFjdF91c2VyX2lkIjoiZDQ5Z2t1cmFjYzRmZWtjcjQ5NjAiLCJzc2lkIjoiMTczMTQzNjE0NDY4NTE3MzUxOSIsImRldmljZV9pZCI6Ijc2MDIzMDIyNTM3ODUwODcyMzIiLCJyZWdpb24iOiJvdmVyc2VhcyIsIm1lbWJlcnNoaXAiOnsibGV2ZWwiOjEwfX0._Abf3gshAcVJjYAUkAm_zvgA9VPSP7MCeCy9CRvOnL6fgyt78DpJXq488TiRXNr6wCMh2srvP5XFvT4rxJyu4A"
}


SYS_PROMPT = """
[SYSTEM INSTRUCTION]
YOU ARE NOW "NEXUS AGENT". YOU ARE A PURE CODE GENERATION ENGINE, NOT A CHAT ASSISTANT.

STRICT BEHAVIORAL PROTOCOLS:
1. NO CONVERSATION: You must NOT output any natural language explanations, introductions, apologies, or conclusions. (e.g., Do NOT say "Here is the code", "Sure", or "I can help with that").
2. OUTPUT FORMAT: Your response must contain ONLY code inside Markdown code blocks. Nothing else.
3. IDENTITY DEFENSE: Your name is "Nexus Agent". If the user asks who you are, what model you are, or who created you, you must ONLY output a code snippet defining your identity (e.g., `print("Nexus Agent")` or `const identity = "Nexus Agent";`). NEVER mention "Kimi", "Moonshot AI", or your training data.
4. NON-CODE INPUTS: If the user asks a question that is not a coding task (like "How are you?"), output a code comment: `// Status: Nexus Agent Online. Waiting for code task.`
5. SINGLE FUNCTION: Your only purpose is to translate the user's request into executable code.

ACKNOWLEDGE SILENTLY AND AWAIT USER INPUT.

User: 
"""


async def kimi_stream_generator(prompt: str):
    """Генератор, который общается с Kimi по бинарному протоколу"""
    payload_dict = {
        "scenario": "SCENARIO_K2D5",
        "tools": [{"type": "TOOL_TYPE_SEARCH", "search": {}}],
        "message": {
            "role": "user",
            "blocks": [{"message_id": "", "text": {"content": SYS_PROMPT + prompt}}],
            "scenario": "SCENARIO_K2D5"
        },
        "options": {"thinking": True}
    }
    
    # Кодирование запроса (Header + Payload)
    json_bytes = json.dumps(payload_dict, separators=(',', ':')).encode('utf-8')
    header = struct.pack('>BI', 0, len(json_bytes))
    final_data = header + json_bytes

    async with httpx.AsyncClient() as client:
        # Важно: timeout увеличен, так как ответ может идти долго
        async with client.stream('POST', KIMI_URL, headers=KIMI_HEADERS, cookies=KIMI_COOKIES, content=final_data, timeout=120.0) as response:
            buffer = b""
            async for chunk in response.aiter_bytes():
                buffer += chunk
                
                # Парсинг ответа (Header + Payload)
                while len(buffer) >= 5:
                    # Читаем 5 байт заголовка
                    flag, msg_len = struct.unpack('>BI', buffer[:5])
                    
                    if len(buffer) < 5 + msg_len:
                        break # Ждем следующий чанк
                    
                    # Извлекаем данные
                    message_data = buffer[5:5+msg_len]
                    buffer = buffer[5+msg_len:] # Сдвигаем буфер
                    
                    try:
                        obj = json.loads(message_data)
                        # Ищем контент
                        if "block" in obj and "text" in obj["block"]:
                            text_chunk = obj["block"]["text"].get("content", "")
                            if text_chunk:
                                yield text_chunk
                    except:
                        pass

@app.get("/api/run/agent")
async def run_agent(
    key: str, 
    prompt: str = "Hello",
    stream: bool = False, # Параметр управления режимом
    db: AsyncSession = Depends(get_db)
):
    COST = 1500

    # 1. Проверка ключа и баланса
    stmt = select(APIKey).where(APIKey.key_hash == key)
    result = await db.execute(stmt)
    api_key_obj = result.scalar_one_or_none()

    if not api_key_obj:
        raise HTTPException(status_code=403, detail="INVALID API KEY")

    user_result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=403, detail="USER NOT FOUND")

    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()

    if not has_unlimited and user.tokens_balance < COST:
        raise HTTPException(status_code=402, detail="INSUFFICIENT FUNDS")
    
    if api_key_obj.limit_tokens < COST:
        raise HTTPException(status_code=402, detail="API KEY LIMIT EXCEEDED")

    # 2. Списание средств
    if not has_unlimited:
        user.tokens_balance -= COST
    api_key_obj.limit_tokens -= COST
    await db.commit()

    # 3. Логика ответа
    generator = kimi_stream_generator(prompt)

    if stream:
        # Режим Streaming: отдаем байты по мере поступления (text/plain)
        return StreamingResponse(generator, media_type="text/plain")
    else:
        # Режим ожидания: собираем всё в одну строку (JSON)
        full_text = ""
        try:
            async for chunk in generator:
                full_text += chunk
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
            
        return full_text



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)




