import os
import random
import string
import secrets
from datetime import datetime, timedelta
from typing import List, Optional
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import requests
import re
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
        'oai-did': 'bed381d1-dbfa-43c5-870b-6a1ed45c5177',
        '_ga': 'GA1.1.414653376.1768045124',
        'oai-hlib': 'true',
        '_account_is_fedramp': 'false',
        'oai-nav-state': '0',
        '__Host-next-auth.csrf-token': '1ef796978f0e8319dc3b7db5627c5f6b01bd62312f3dfa1fa78795fa3fa5fcfc%7C913c9ff01c9cdd8d4d5ca6ae31c91c21c64cc50aeddebfe2e91791a696a42504',
        '__Secure-next-auth.callback-url': 'https%3A%2F%2Fchatgpt.com',
        '__Secure-next-auth.session-token': 'eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..ZNchp3v1OzVo_jFV.1yXBa59_FJWyhHWvgsk_vcpUAWo0zcURUCdg4zOGwvl2hvBU8g-u5PXCd1drMRKFPbduCtwTk4m2fkevPL-M3nd56hyrrDjUGiMp8BRxnh4OTyh4Pp6Djrcust7YJW3Esre4ieVly56Vltzou8JEal-a4WfIz6wGk1ApT1FDt-eBQECBziM-Gl_lLOqI4CrdfE37WcMzViyIqqhe4vXoaxRHLbG5x9oF6DZuwPNpg2RFrRHyeVO6N_aOf89iZNWMq3sWKfSiZO1pXeUzaOpOwF-jtrtHdIfJnX-Xy9SV77zjlN7hmtAYrEcJap7f3gBWt8kzmHR-x1PsoSTR4VGaXlChypfVJYuN8TfDIgEd7RSAIOeUYdoSs6kNUqWBn0DfSCb4Urzfma0CP-Ur8-a0Ga-fselUJysvd60y-LJfRjrSQJAfXmkQGg8J0uNJROXSLLyeircWwPFQPl5EbY_utwq6CQISblE2Bs3Ip1ZYN6mBh7hRPsYAChrXEjcINSvdlTnqWytm-8xjEEec2iu9b67FY4YkfjmEIpnOicVKnqr17wom85tyVhCGJcUk2Mz9O--H3yl3OxpWJiNkPpiGotR5IWKg712R7VYdqj7SYLQwiUcS7QM9fQLesrqxM93455YC8yxx2ImekzYGraaQejPR4zlgxsWJmeu7SUvBDwgfGjn69ODt-uejnr4F26ab9ub-Oqt3Xa7Ehah_qyAa8o3bFqSJyCsWlH80FWjUXoWH_zmGPqNKWuzBm268XKFSf74QmvcntZ-1fIYMUENLZeyqqAzEmi0pnGvUwhnWmBu9gNAl0Cx5CcjdKBFRh5FPfxhuN8LZuNNWUdxkInCIXqvR-mAQL_TIFPc7zWMQJmxmaZOM_t9o5y9NLNtqOh3BF00e6BoZjrRprkEOqFdBE6yuAyLf3WD-dTF9bCy60_BEdmpTq_-zeCecx4W7v18fnUyOPfOqr9r0O_hMACgOSMhd_OyRvLYCoCvnmZwvmD4KT9-UtgmQu4peuk7CocE9A1KCgdEBubzDZkDSrKq1dEZVo4YYvAaxLVNPdbM3vetKCUD61P16QCOSVP_eWhaZ4QrO3a4Xlt3mIEAeEM-7lhw_bRlTdfqOj2NWh0ZAl2yFXfNBSRzc2P-6ZUIVbdMT4EY_1O1cJcud_JeymwIa7_fKz6TrSpQW1sQNrL4ZoNAP2tyzNOVZ8UW6kpBj2rb6MIqrI7vbkNGhpWOC8JkSTuuxqRtstfUraCbMmLNghkCM_ANfpsUMdH397TAuFNfIf7JnZDslEJlcOQcAFNOUowwMoxegt7vYJBtWmbSUlX8wzsU8Cs_lief8MjffegAwG3PK99VeFfZHG2Pxz4WmY7fU7__5lHu1Hbqw-9e0Efy7T-adR706eGbhxAkwpWdXuLc5ISe6jV-wuRAnP_Lyjv1ZVRnhAmrhVUc4rGuAE4jQCyYYwWZt4qAEfGxgBceNuRMTae2muKWB5jXiyBlAHfmqAauma1Ez3BVrn7K19xymIdIZuo1vHFVMQxolQkWT9RM09nbGLAo26Ks_SWcOllZLFKwR-5xXpRKMP_BxaDYxv_-zTJJuEYZhnKAwLrVByDX3eaYKsaw2mn5WcW22qXp5ybykX5kHx8inDuifYe2XS8Z1kWmiyF9EjUZvoqaLwKMDjrVYdz4-ecLW1q3WsbL7Gr0FwMx1phSkDR8t0Skv7tx6yHymD_iSNwMf4CZz6eW_WyZdHE3T2F35HUhJQIk0Kq8HFhqL6TL7rizop2dOUlVHqCWI6bhdt_dQrhUgE1RMkg4wEDfWlC6qoJWo0aYY1t1i6rZay2pmRRWLPG9cnxo9Sols43LYvNr3kBDjjt25sPBHLB0kjuuY4e4vmNTvkkSzueUKauRC8a34QfDjJBHK22qoPPeJQLD1Y9ZJMdZWwcPLLtCwu8n9trdzt0V_ZAB046anlMwfJ22hWRzLvTegtH0Feah28L_pAgnDWYaU27_9udUCdEoDQbGQaXW8LHsnUepPP_wP2o0ZWvoObEh7_lwA0Dhybgb1fKvcjLm6fswIUl6LmdcPEZJ6GRsmGOdpxGBGl9hG1HWQ3oFgHsJyiRiGfkzv7-IwNI88JFkyhH8rw--GQK4SZIZ9mz0Y1A8GITEtxPIPoqfR0cZJJNrI7Prq-4FbHvZPKDA4wKDq8rCMPmCMtm-d2_cMfq1LBEL-g9FaaTNE5mqMrae88wgNFVLdagETQyenfflE-eMmx_YB7mQ5vP81IW_Ae3wmiHueHHr1U1V7JfDwuFBV6u25s0zxFAc5hzyyZyDmWI0LJhCuxYmtaWsqZm8kRHnptBwiB33fUBWxdW3bsk-oYI65P68EBVV8iBsIp6u3qtpRUn-jgJstlvnXaU597fdzQ2KwSPb9GIhIUycejvcZCBoVd21iKAkIJp0HVRCRjD1YP_-w2wK6nuTDfxWXTM08rBplW4TAyo_i7D6ZU4CEph9BsTNfRpyRxJSty1-Ucd_k9vWNn9F1oFIAqmp_RYPWrwwVtOaEGhU2lXMTYjQeva8IaIIiJxteoNIDkMqUiltSXej1Gnk-pO4tHok6JhahBfUrguDRncHdELutSqXlLxRhgVZD79ngjASP9Gq_YhzlislhPA7NksOVcvSmwJV6OpfS99ED4DT0ipiNoEPE8X6BYyLLLIbajTG8XnPyRG76zfWJ_lvESxk4ectoBrgUSSSqpKuTJCuIG6PT4xXRt-9kDge2V1SU9kd_ffgsThSJ178eE5swtMjoswUWqw0zIV-jtFgpIOseBWw1D7HhcWhDNH1ks_MAIr_Y4ByjdBxC-9HF-EC4WldPN5-m-w-_jpl9WzkU0qI41jux8jG2mskOL4kv5enbyvcrQk1H4hiXbojV6z9tzq2Alp-Rv9hhkp5Jl9ErBYi2AXi573tu86a2KWe1BjMvBcNmtxc0KXCQ44O4tDp9cjQWQcz_BbDtJvsLe41MqsR06T5r564uxThP_y5HZf5eFmq50ct1Ek_y6Rm69UW-KSSTY1MS_ZhlQqYBvhKO9RcfBFVN-g9gai8sWFGt7c9stDYYunBv7co-EQvH971OSZc0uiQ2ihec6_5l2CCsfsGt7kmdK4XuWeB2Wth2rPJAX3PcyuBceofpgwV3lGMDcFHpD4qho1yI15ygh--HhxiD1C5O2d4u--W5bxNyx8p82uNgR0UFiGYzqR7FOg5gqKPKd7vDc_u6cY6aKK9YrhYA6_XjUT8OROerNz-BTkkbIVzv1wxcYceex6hca8dfjV-sUzQ0ttMDD-bNuAbXABUKj4vVIDF617xtpfA8ADuGkdbjDO_jrl261lXxxdkPu6EJGzJw8O9tsyXmmFfXrxe3IL_NnMAzv1rxN-XxH1PVz7k8NE1K4EdlH15oeoGxcT8MwI1SoFP6xXsXyFsIgyBeW-zZnB5vS7nlRGsy-jduOIUBmlQOVfQeCCplcGbxtgCoHTB15JIB5hLsfv_98YgpvuJQuQwhvEVb_G9YTIimMYyShh8H9cj32A9gFnreCjyIoC0scIYjVA8j46CM4rdGkwTk0k1UgrSzP3bgC5R9Q1O7Ga_6ydCBPyZvqL7idd4SGQTc45o-Nven9utaiwVnwbKVuW5byLEt9ooJBnxcZGFS-wlI9bzN-fyGj0hOZZj0hqrc.KDY0IGIo1Gxpa5oNsxaxFg',
        '__cf_bm': 'QnOMgWgkWmguxdn2pgIKthiHi6BDQZ.RoTqPNT5bk48-1769669653-1.0.1.1-0WrR.BZQcrCqPzqQcqPJsM8p3ApZjfv010vKRimwK0JXPj3oevZpIqQWY.USL7uR0OBLmX168Z01_mojVuCRGjOwV2UjA6Hx98pb5izaJAI',
        '__cflb': '0H28vzvP5FJafnkHxihKb44bdy6fTJD3Pt5hM927szj',
        '_cfuvid': '_cx7eAewzyHLXIolRd2ZcoHTigKglp_PLvZoBRSZvGI-1769669653674-0.0.1.1-604800000',
        'cf_clearance': '0tAEh9f9iGQPDGVGvma5fLylvwtsyUigBA5EfFjcp64-1769669663-1.2.1.1-s.HIRwCGGJRTxGl.dtiI_wv5M9VT8KFpxHSzxt6G.j0ZwPSmfsPr2BMlyJk2YTCHSLJQ.CCF2DLoL8E6F3kD8wCDwHOKlHhJOb5MTwHqN2E9o7tWZUbFbR303LVE6AU4TA6i5DiM3xcbwhKiSdR70pt2a2mFuxBUYiBAXbn80gjEMuLmLZPrJOQ0YzCTO1tNNXMcB9o9eoO1kyuSImlsBoiQ3ann8H3xymuWPly5W2o',
        '_ga_9SHBSK2D9J': 'GS2.1.s1769666076$o8$g0$t1769666076$j60$l0$h0',
        'oai-sc': '0gAAAAABpewQfPxAQec-UTPyMPqWnFIDn5Xc6tIn44x-mD0mhHkMXPTVZsThQCdRI-X2KsJZqBhPoa-xUiKcRwUGfft-m8VozO0Llho2wJGylPLOWPDDbdDEbFoDAeWTf2q90ZhK7OauE2XwlCeiPkgaoF59DV8ONlnN6AXhsDn0aSGNhWN8dEkiuJmiOM96fypChnjs5q_JE3nEbv166Q4nVpbERLa65rR-6YmqH4E5dc3DYGVHXirI',
        'oai-client-auth-info': '%7B%22user%22%3A%7B%22name%22%3A%22Tima%20First%22%2C%22email%22%3A%22topovii8888%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fpr.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1769666079178%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D',
        'oai-gn': '',
        'oai-hm': 'ON_YOUR_MIND%20%7C%20GOOD_TO_SEE_YOU',
        '_dd_s': 'aid=56785c82-0c44-4651-a04a-b253a3a84c14&rum=0&expire=1769666981586&logs=1&id=d79e02ff-b93c-4e28-9164-61c5a88d1498&created=1769666072653',
    }

    headers = {
        'accept': '*/*',
        'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'authorization': 'Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjE5MzQ0ZTY1LWJiYzktNDRkMS1hOWQwLWY5NTdiMDc5YmQwZSIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS92MSJdLCJjbGllbnRfaWQiOiJhcHBfWDh6WTZ2VzJwUTl0UjNkRTduSzFqTDVnSCIsImV4cCI6MTc3MDQyMzY3OSwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9hdXRoIjp7ImNoYXRncHRfY29tcHV0ZV9yZXNpZGVuY3kiOiJub19jb25zdHJhaW50IiwiY2hhdGdwdF9kYXRhX3Jlc2lkZW5jeSI6Im5vX2NvbnN0cmFpbnQiLCJ1c2VyX2lkIjoidXNlci1CbWZLM3BFQ2YwaFhUQ1ViQjM4czkwNjIifSwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9wcm9maWxlIjp7ImVtYWlsIjoidG9wb3ZpaTg4ODhAZ21haWwuY29tIiwiZW1haWxfdmVyaWZpZWQiOnRydWV9LCJpYXQiOjE3Njk1NTk2NzgsImlzcyI6Imh0dHBzOi8vYXV0aC5vcGVuYWkuY29tIiwianRpIjoiNzcwOGY3NzMtN2JjNS00YzlkLTk5MDUtYjYzNWVlOTdhZmNhIiwibmJmIjoxNzY5NTU5Njc4LCJwd2RfYXV0aF90aW1lIjoxNzY4NTUzNzk0NzMyLCJzY3AiOlsib3BlbmlkIiwiZW1haWwiLCJwcm9maWxlIiwib2ZmbGluZV9hY2Nlc3MiLCJtb2RlbC5yZXF1ZXN0IiwibW9kZWwucmVhZCIsIm9yZ2FuaXphdGlvbi5yZWFkIiwib3JnYW5pemF0aW9uLndyaXRlIl0sInNlc3Npb25faWQiOiJhdXRoc2Vzc18yTnlwcjBmZXlEOEdzdTJMYzZtWVJMbHIiLCJzdWIiOiJnb29nbGUtb2F1dGgyfDExNTQ5MDA1MDM4NjQ0OTAzMDY0NCJ9.3Jj4W8lO2KiQsI8bBL4MOUXz8X3F4pGpZiTxSAn2G9lz8jzN8yBTcawkg3LN1_OEE7P9diQKGl-n2PG6vKUrTviGb2-8vXKlknMsYV8I43vomtKPtbtTyFasDo7XeC6aqcaEq7CuJ6mKn-MzyzIVG_Lb7MBRl6dOtuofFsHyFjwTjtDGXveSP3qo7-A0rmcjNU5hP7V8HosvzGixFvpg23ZlzTDfIe5rUY4e3tYeMFYaMAvs7-TJ2N7q_mIkCrdMiK0tEAgmUqyXFuKZiG6FQT7znXRhSpHExzl7wxmI_aGKLM2_6KSZn7ZZLqqMfa-nseGqr7CQB_5EjginGJm3CPW-GawS7nzPU5OK3vszj3tFiTSH5QaS4f4f8_puO2b9m2rNNWSL2nCC5KbZMPtFus3als_iTwSXQLypVUmDj-awVtnq7-YmM5l5pE-PakUa1ktG58LdbwlEZ9yjd0JgmU3lkPcWZSyUghGpdKwCjCeHwHVHJUQC1o8EypCtegFxG0DrcSaHaJQrJ15PhyictgjhGCR6PI9_CO7P0z40szKAaswB4u0VAjC9Xkbst5czt-3QsiAhVLsN2FqJHiX6SNj9X4GXk_4fwmGRaJS7HQPUYPi0DqVVdfU7OqcUKFnaeuHasicAUvzr3ZYovD3fJONbO144XvRGgGS1zt7bFWg',
        'content-type': 'application/json',
        'oai-client-build-number': '4308576',
        'oai-client-version': 'prod-3ea25c39fc07b9df21c8b5b7a151a3de9567273c',
        'oai-device-id': 'bed381d1-dbfa-43c5-870b-6a1ed45c5177',
        'oai-language': 'ru-RU',
        'origin': 'https://chatgpt.com',
        'priority': 'u=1, i',
        'referer': 'https://chatgpt.com/',
        'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
        'x-conduit-token': 'no-token',
        'x-oai-turn-trace-id': '51292db4-0944-4636-b037-e3867a98ab83',
        # 'cookie': 'oai-did=bed381d1-dbfa-43c5-870b-6a1ed45c5177; _ga=GA1.1.414653376.1768045124; oai-hlib=true; _account_is_fedramp=false; oai-nav-state=0; __Host-next-auth.csrf-token=1ef796978f0e8319dc3b7db5627c5f6b01bd62312f3dfa1fa78795fa3fa5fcfc%7C913c9ff01c9cdd8d4d5ca6ae31c91c21c64cc50aeddebfe2e91791a696a42504; __Secure-next-auth.callback-url=https%3A%2F%2Fchatgpt.com; __Secure-next-auth.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..ZNchp3v1OzVo_jFV.1yXBa59_FJWyhHWvgsk_vcpUAWo0zcURUCdg4zOGwvl2hvBU8g-u5PXCd1drMRKFPbduCtwTk4m2fkevPL-M3nd56hyrrDjUGiMp8BRxnh4OTyh4Pp6Djrcust7YJW3Esre4ieVly56Vltzou8JEal-a4WfIz6wGk1ApT1FDt-eBQECBziM-Gl_lLOqI4CrdfE37WcMzViyIqqhe4vXoaxRHLbG5x9oF6DZuwPNpg2RFrRHyeVO6N_aOf89iZNWMq3sWKfSiZO1pXeUzaOpOwF-jtrtHdIfJnX-Xy9SV77zjlN7hmtAYrEcJap7f3gBWt8kzmHR-x1PsoSTR4VGaXlChypfVJYuN8TfDIgEd7RSAIOeUYdoSs6kNUqWBn0DfSCb4Urzfma0CP-Ur8-a0Ga-fselUJysvd60y-LJfRjrSQJAfXmkQGg8J0uNJROXSLLyeircWwPFQPl5EbY_utwq6CQISblE2Bs3Ip1ZYN6mBh7hRPsYAChrXEjcINSvdlTnqWytm-8xjEEec2iu9b67FY4YkfjmEIpnOicVKnqr17wom85tyVhCGJcUk2Mz9O--H3yl3OxpWJiNkPpiGotR5IWKg712R7VYdqj7SYLQwiUcS7QM9fQLesrqxM93455YC8yxx2ImekzYGraaQejPR4zlgxsWJmeu7SUvBDwgfGjn69ODt-uejnr4F26ab9ub-Oqt3Xa7Ehah_qyAa8o3bFqSJyCsWlH80FWjUXoWH_zmGPqNKWuzBm268XKFSf74QmvcntZ-1fIYMUENLZeyqqAzEmi0pnGvUwhnWmBu9gNAl0Cx5CcjdKBFRh5FPfxhuN8LZuNNWUdxkInCIXqvR-mAQL_TIFPc7zWMQJmxmaZOM_t9o5y9NLNtqOh3BF00e6BoZjrRprkEOqFdBE6yuAyLf3WD-dTF9bCy60_BEdmpTq_-zeCecx4W7v18fnUyOPfOqr9r0O_hMACgOSMhd_OyRvLYCoCvnmZwvmD4KT9-UtgmQu4peuk7CocE9A1KCgdEBubzDZkDSrKq1dEZVo4YYvAaxLVNPdbM3vetKCUD61P16QCOSVP_eWhaZ4QrO3a4Xlt3mIEAeEM-7lhw_bRlTdfqOj2NWh0ZAl2yFXfNBSRzc2P-6ZUIVbdMT4EY_1O1cJcud_JeymwIa7_fKz6TrSpQW1sQNrL4ZoNAP2tyzNOVZ8UW6kpBj2rb6MIqrI7vbkNGhpWOC8JkSTuuxqRtstfUraCbMmLNghkCM_ANfpsUMdH397TAuFNfIf7JnZDslEJlcOQcAFNOUowwMoxegt7vYJBtWmbSUlX8wzsU8Cs_lief8MjffegAwG3PK99VeFfZHG2Pxz4WmY7fU7__5lHu1Hbqw-9e0Efy7T-adR706eGbhxAkwpWdXuLc5ISe6jV-wuRAnP_Lyjv1ZVRnhAmrhVUc4rGuAE4jQCyYYwWZt4qAEfGxgBceNuRMTae2muKWB5jXiyBlAHfmqAauma1Ez3BVrn7K19xymIdIZuo1vHFVMQxolQkWT9RM09nbGLAo26Ks_SWcOllZLFKwR-5xXpRKMP_BxaDYxv_-zTJJuEYZhnKAwLrVByDX3eaYKsaw2mn5WcW22qXp5ybykX5kHx8inDuifYe2XS8Z1kWmiyF9EjUZvoqaLwKMDjrVYdz4-ecLW1q3WsbL7Gr0FwMx1phSkDR8t0Skv7tx6yHymD_iSNwMf4CZz6eW_WyZdHE3T2F35HUhJQIk0Kq8HFhqL6TL7rizop2dOUlVHqCWI6bhdt_dQrhUgE1RMkg4wEDfWlC6qoJWo0aYY1t1i6rZay2pmRRWLPG9cnxo9Sols43LYvNr3kBDjjt25sPBHLB0kjuuY4e4vmNTvkkSzueUKauRC8a34QfDjJBHK22qoPPeJQLD1Y9ZJMdZWwcPLLtCwu8n9trdzt0V_ZAB046anlMwfJ22hWRzLvTegtH0Feah28L_pAgnDWYaU27_9udUCdEoDQbGQaXW8LHsnUepPP_wP2o0ZWvoObEh7_lwA0Dhybgb1fKvcjLm6fswIUl6LmdcPEZJ6GRsmGOdpxGBGl9hG1HWQ3oFgHsJyiRiGfkzv7-IwNI88JFkyhH8rw--GQK4SZIZ9mz0Y1A8GITEtxPIPoqfR0cZJJNrI7Prq-4FbHvZPKDA4wKDq8rCMPmCMtm-d2_cMfq1LBEL-g9FaaTNE5mqMrae88wgNFVLdagETQyenfflE-eMmx_YB7mQ5vP81IW_Ae3wmiHueHHr1U1V7JfDwuFBV6u25s0zxFAc5hzyyZyDmWI0LJhCuxYmtaWsqZm8kRHnptBwiB33fUBWxdW3bsk-oYI65P68EBVV8iBsIp6u3qtpRUn-jgJstlvnXaU597fdzQ2KwSPb9GIhIUycejvcZCBoVd21iKAkIJp0HVRCRjD1YP_-w2wK6nuTDfxWXTM08rBplW4TAyo_i7D6ZU4CEph9BsTNfRpyRxJSty1-Ucd_k9vWNn9F1oFIAqmp_RYPWrwwVtOaEGhU2lXMTYjQeva8IaIIiJxteoNIDkMqUiltSXej1Gnk-pO4tHok6JhahBfUrguDRncHdELutSqXlLxRhgVZD79ngjASP9Gq_YhzlislhPA7NksOVcvSmwJV6OpfS99ED4DT0ipiNoEPE8X6BYyLLLIbajTG8XnPyRG76zfWJ_lvESxk4ectoBrgUSSSqpKuTJCuIG6PT4xXRt-9kDge2V1SU9kd_ffgsThSJ178eE5swtMjoswUWqw0zIV-jtFgpIOseBWw1D7HhcWhDNH1ks_MAIr_Y4ByjdBxC-9HF-EC4WldPN5-m-w-_jpl9WzkU0qI41jux8jG2mskOL4kv5enbyvcrQk1H4hiXbojV6z9tzq2Alp-Rv9hhkp5Jl9ErBYi2AXi573tu86a2KWe1BjMvBcNmtxc0KXCQ44O4tDp9cjQWQcz_BbDtJvsLe41MqsR06T5r564uxThP_y5HZf5eFmq50ct1Ek_y6Rm69UW-KSSTY1MS_ZhlQqYBvhKO9RcfBFVN-g9gai8sWFGt7c9stDYYunBv7co-EQvH971OSZc0uiQ2ihec6_5l2CCsfsGt7kmdK4XuWeB2Wth2rPJAX3PcyuBceofpgwV3lGMDcFHpD4qho1yI15ygh--HhxiD1C5O2d4u--W5bxNyx8p82uNgR0UFiGYzqR7FOg5gqKPKd7vDc_u6cY6aKK9YrhYA6_XjUT8OROerNz-BTkkbIVzv1wxcYceex6hca8dfjV-sUzQ0ttMDD-bNuAbXABUKj4vVIDF617xtpfA8ADuGkdbjDO_jrl261lXxxdkPu6EJGzJw8O9tsyXmmFfXrxe3IL_NnMAzv1rxN-XxH1PVz7k8NE1K4EdlH15oeoGxcT8MwI1SoFP6xXsXyFsIgyBeW-zZnB5vS7nlRGsy-jduOIUBmlQOVfQeCCplcGbxtgCoHTB15JIB5hLsfv_98YgpvuJQuQwhvEVb_G9YTIimMYyShh8H9cj32A9gFnreCjyIoC0scIYjVA8j46CM4rdGkwTk0k1UgrSzP3bgC5R9Q1O7Ga_6ydCBPyZvqL7idd4SGQTc45o-Nven9utaiwVnwbKVuW5byLEt9ooJBnxcZGFS-wlI9bzN-fyGj0hOZZj0hqrc.KDY0IGIo1Gxpa5oNsxaxFg; __cf_bm=QnOMgWgkWmguxdn2pgIKthiHi6BDQZ.RoTqPNT5bk48-1769669653-1.0.1.1-0WrR.BZQcrCqPzqQcqPJsM8p3ApZjfv010vKRimwK0JXPj3oevZpIqQWY.USL7uR0OBLmX168Z01_mojVuCRGjOwV2UjA6Hx98pb5izaJAI; __cflb=0H28vzvP5FJafnkHxihKb44bdy6fTJD3Pt5hM927szj; _cfuvid=_cx7eAewzyHLXIolRd2ZcoHTigKglp_PLvZoBRSZvGI-1769669653674-0.0.1.1-604800000; cf_clearance=0tAEh9f9iGQPDGVGvma5fLylvwtsyUigBA5EfFjcp64-1769669663-1.2.1.1-s.HIRwCGGJRTxGl.dtiI_wv5M9VT8KFpxHSzxt6G.j0ZwPSmfsPr2BMlyJk2YTCHSLJQ.CCF2DLoL8E6F3kD8wCDwHOKlHhJOb5MTwHqN2E9o7tWZUbFbR303LVE6AU4TA6i5DiM3xcbwhKiSdR70pt2a2mFuxBUYiBAXbn80gjEMuLmLZPrJOQ0YzCTO1tNNXMcB9o9eoO1kyuSImlsBoiQ3ann8H3xymuWPly5W2o; _ga_9SHBSK2D9J=GS2.1.s1769666076$o8$g0$t1769666076$j60$l0$h0; oai-sc=0gAAAAABpewQfPxAQec-UTPyMPqWnFIDn5Xc6tIn44x-mD0mhHkMXPTVZsThQCdRI-X2KsJZqBhPoa-xUiKcRwUGfft-m8VozO0Llho2wJGylPLOWPDDbdDEbFoDAeWTf2q90ZhK7OauE2XwlCeiPkgaoF59DV8ONlnN6AXhsDn0aSGNhWN8dEkiuJmiOM96fypChnjs5q_JE3nEbv166Q4nVpbERLa65rR-6YmqH4E5dc3DYGVHXirI; oai-client-auth-info=%7B%22user%22%3A%7B%22name%22%3A%22Tima%20First%22%2C%22email%22%3A%22topovii8888%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fpr.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1769666079178%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D; oai-gn=; oai-hm=ON_YOUR_MIND%20%7C%20GOOD_TO_SEE_YOU; _dd_s=aid=56785c82-0c44-4651-a04a-b253a3a84c14&rum=0&expire=1769666981586&logs=1&id=d79e02ff-b93c-4e28-9164-61c5a88d1498&created=1769666072653',
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
        'oai-did': 'bed381d1-dbfa-43c5-870b-6a1ed45c5177',
        '_ga': 'GA1.1.414653376.1768045124',
        'oai-hlib': 'true',
        '_account_is_fedramp': 'false',
        'oai-nav-state': '0',
        '__Host-next-auth.csrf-token': '1ef796978f0e8319dc3b7db5627c5f6b01bd62312f3dfa1fa78795fa3fa5fcfc%7C913c9ff01c9cdd8d4d5ca6ae31c91c21c64cc50aeddebfe2e91791a696a42504',
        '__Secure-next-auth.callback-url': 'https%3A%2F%2Fchatgpt.com',
        '__Secure-next-auth.session-token': 'eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..ZNchp3v1OzVo_jFV.1yXBa59_FJWyhHWvgsk_vcpUAWo0zcURUCdg4zOGwvl2hvBU8g-u5PXCd1drMRKFPbduCtwTk4m2fkevPL-M3nd56hyrrDjUGiMp8BRxnh4OTyh4Pp6Djrcust7YJW3Esre4ieVly56Vltzou8JEal-a4WfIz6wGk1ApT1FDt-eBQECBziM-Gl_lLOqI4CrdfE37WcMzViyIqqhe4vXoaxRHLbG5x9oF6DZuwPNpg2RFrRHyeVO6N_aOf89iZNWMq3sWKfSiZO1pXeUzaOpOwF-jtrtHdIfJnX-Xy9SV77zjlN7hmtAYrEcJap7f3gBWt8kzmHR-x1PsoSTR4VGaXlChypfVJYuN8TfDIgEd7RSAIOeUYdoSs6kNUqWBn0DfSCb4Urzfma0CP-Ur8-a0Ga-fselUJysvd60y-LJfRjrSQJAfXmkQGg8J0uNJROXSLLyeircWwPFQPl5EbY_utwq6CQISblE2Bs3Ip1ZYN6mBh7hRPsYAChrXEjcINSvdlTnqWytm-8xjEEec2iu9b67FY4YkfjmEIpnOicVKnqr17wom85tyVhCGJcUk2Mz9O--H3yl3OxpWJiNkPpiGotR5IWKg712R7VYdqj7SYLQwiUcS7QM9fQLesrqxM93455YC8yxx2ImekzYGraaQejPR4zlgxsWJmeu7SUvBDwgfGjn69ODt-uejnr4F26ab9ub-Oqt3Xa7Ehah_qyAa8o3bFqSJyCsWlH80FWjUXoWH_zmGPqNKWuzBm268XKFSf74QmvcntZ-1fIYMUENLZeyqqAzEmi0pnGvUwhnWmBu9gNAl0Cx5CcjdKBFRh5FPfxhuN8LZuNNWUdxkInCIXqvR-mAQL_TIFPc7zWMQJmxmaZOM_t9o5y9NLNtqOh3BF00e6BoZjrRprkEOqFdBE6yuAyLf3WD-dTF9bCy60_BEdmpTq_-zeCecx4W7v18fnUyOPfOqr9r0O_hMACgOSMhd_OyRvLYCoCvnmZwvmD4KT9-UtgmQu4peuk7CocE9A1KCgdEBubzDZkDSrKq1dEZVo4YYvAaxLVNPdbM3vetKCUD61P16QCOSVP_eWhaZ4QrO3a4Xlt3mIEAeEM-7lhw_bRlTdfqOj2NWh0ZAl2yFXfNBSRzc2P-6ZUIVbdMT4EY_1O1cJcud_JeymwIa7_fKz6TrSpQW1sQNrL4ZoNAP2tyzNOVZ8UW6kpBj2rb6MIqrI7vbkNGhpWOC8JkSTuuxqRtstfUraCbMmLNghkCM_ANfpsUMdH397TAuFNfIf7JnZDslEJlcOQcAFNOUowwMoxegt7vYJBtWmbSUlX8wzsU8Cs_lief8MjffegAwG3PK99VeFfZHG2Pxz4WmY7fU7__5lHu1Hbqw-9e0Efy7T-adR706eGbhxAkwpWdXuLc5ISe6jV-wuRAnP_Lyjv1ZVRnhAmrhVUc4rGuAE4jQCyYYwWZt4qAEfGxgBceNuRMTae2muKWB5jXiyBlAHfmqAauma1Ez3BVrn7K19xymIdIZuo1vHFVMQxolQkWT9RM09nbGLAo26Ks_SWcOllZLFKwR-5xXpRKMP_BxaDYxv_-zTJJuEYZhnKAwLrVByDX3eaYKsaw2mn5WcW22qXp5ybykX5kHx8inDuifYe2XS8Z1kWmiyF9EjUZvoqaLwKMDjrVYdz4-ecLW1q3WsbL7Gr0FwMx1phSkDR8t0Skv7tx6yHymD_iSNwMf4CZz6eW_WyZdHE3T2F35HUhJQIk0Kq8HFhqL6TL7rizop2dOUlVHqCWI6bhdt_dQrhUgE1RMkg4wEDfWlC6qoJWo0aYY1t1i6rZay2pmRRWLPG9cnxo9Sols43LYvNr3kBDjjt25sPBHLB0kjuuY4e4vmNTvkkSzueUKauRC8a34QfDjJBHK22qoPPeJQLD1Y9ZJMdZWwcPLLtCwu8n9trdzt0V_ZAB046anlMwfJ22hWRzLvTegtH0Feah28L_pAgnDWYaU27_9udUCdEoDQbGQaXW8LHsnUepPP_wP2o0ZWvoObEh7_lwA0Dhybgb1fKvcjLm6fswIUl6LmdcPEZJ6GRsmGOdpxGBGl9hG1HWQ3oFgHsJyiRiGfkzv7-IwNI88JFkyhH8rw--GQK4SZIZ9mz0Y1A8GITEtxPIPoqfR0cZJJNrI7Prq-4FbHvZPKDA4wKDq8rCMPmCMtm-d2_cMfq1LBEL-g9FaaTNE5mqMrae88wgNFVLdagETQyenfflE-eMmx_YB7mQ5vP81IW_Ae3wmiHueHHr1U1V7JfDwuFBV6u25s0zxFAc5hzyyZyDmWI0LJhCuxYmtaWsqZm8kRHnptBwiB33fUBWxdW3bsk-oYI65P68EBVV8iBsIp6u3qtpRUn-jgJstlvnXaU597fdzQ2KwSPb9GIhIUycejvcZCBoVd21iKAkIJp0HVRCRjD1YP_-w2wK6nuTDfxWXTM08rBplW4TAyo_i7D6ZU4CEph9BsTNfRpyRxJSty1-Ucd_k9vWNn9F1oFIAqmp_RYPWrwwVtOaEGhU2lXMTYjQeva8IaIIiJxteoNIDkMqUiltSXej1Gnk-pO4tHok6JhahBfUrguDRncHdELutSqXlLxRhgVZD79ngjASP9Gq_YhzlislhPA7NksOVcvSmwJV6OpfS99ED4DT0ipiNoEPE8X6BYyLLLIbajTG8XnPyRG76zfWJ_lvESxk4ectoBrgUSSSqpKuTJCuIG6PT4xXRt-9kDge2V1SU9kd_ffgsThSJ178eE5swtMjoswUWqw0zIV-jtFgpIOseBWw1D7HhcWhDNH1ks_MAIr_Y4ByjdBxC-9HF-EC4WldPN5-m-w-_jpl9WzkU0qI41jux8jG2mskOL4kv5enbyvcrQk1H4hiXbojV6z9tzq2Alp-Rv9hhkp5Jl9ErBYi2AXi573tu86a2KWe1BjMvBcNmtxc0KXCQ44O4tDp9cjQWQcz_BbDtJvsLe41MqsR06T5r564uxThP_y5HZf5eFmq50ct1Ek_y6Rm69UW-KSSTY1MS_ZhlQqYBvhKO9RcfBFVN-g9gai8sWFGt7c9stDYYunBv7co-EQvH971OSZc0uiQ2ihec6_5l2CCsfsGt7kmdK4XuWeB2Wth2rPJAX3PcyuBceofpgwV3lGMDcFHpD4qho1yI15ygh--HhxiD1C5O2d4u--W5bxNyx8p82uNgR0UFiGYzqR7FOg5gqKPKd7vDc_u6cY6aKK9YrhYA6_XjUT8OROerNz-BTkkbIVzv1wxcYceex6hca8dfjV-sUzQ0ttMDD-bNuAbXABUKj4vVIDF617xtpfA8ADuGkdbjDO_jrl261lXxxdkPu6EJGzJw8O9tsyXmmFfXrxe3IL_NnMAzv1rxN-XxH1PVz7k8NE1K4EdlH15oeoGxcT8MwI1SoFP6xXsXyFsIgyBeW-zZnB5vS7nlRGsy-jduOIUBmlQOVfQeCCplcGbxtgCoHTB15JIB5hLsfv_98YgpvuJQuQwhvEVb_G9YTIimMYyShh8H9cj32A9gFnreCjyIoC0scIYjVA8j46CM4rdGkwTk0k1UgrSzP3bgC5R9Q1O7Ga_6ydCBPyZvqL7idd4SGQTc45o-Nven9utaiwVnwbKVuW5byLEt9ooJBnxcZGFS-wlI9bzN-fyGj0hOZZj0hqrc.KDY0IGIo1Gxpa5oNsxaxFg',
        '__cf_bm': 'QnOMgWgkWmguxdn2pgIKthiHi6BDQZ.RoTqPNT5bk48-1769669653-1.0.1.1-0WrR.BZQcrCqPzqQcqPJsM8p3ApZjfv010vKRimwK0JXPj3oevZpIqQWY.USL7uR0OBLmX168Z01_mojVuCRGjOwV2UjA6Hx98pb5izaJAI',
        '__cflb': '0H28vzvP5FJafnkHxihKb44bdy6fTJD3Pt5hM927szj',
        '_cfuvid': '_cx7eAewzyHLXIolRd2ZcoHTigKglp_PLvZoBRSZvGI-1769669653674-0.0.1.1-604800000',
        'cf_clearance': '0tAEh9f9iGQPDGVGvma5fLylvwtsyUigBA5EfFjcp64-1769669663-1.2.1.1-s.HIRwCGGJRTxGl.dtiI_wv5M9VT8KFpxHSzxt6G.j0ZwPSmfsPr2BMlyJk2YTCHSLJQ.CCF2DLoL8E6F3kD8wCDwHOKlHhJOb5MTwHqN2E9o7tWZUbFbR303LVE6AU4TA6i5DiM3xcbwhKiSdR70pt2a2mFuxBUYiBAXbn80gjEMuLmLZPrJOQ0YzCTO1tNNXMcB9o9eoO1kyuSImlsBoiQ3ann8H3xymuWPly5W2o',
        '_ga_9SHBSK2D9J': 'GS2.1.s1769666076$o8$g0$t1769666076$j60$l0$h0',
        'oai-client-auth-info': '%7B%22user%22%3A%7B%22name%22%3A%22Tima%20First%22%2C%22email%22%3A%22topovii8888%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fpr.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1769666079178%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D',
        'oai-gn': '',
        'oai-hm': 'ON_YOUR_MIND%20%7C%20GOOD_TO_SEE_YOU',
        '_dd_s': 'aid=56785c82-0c44-4651-a04a-b253a3a84c14&rum=0&expire=1769667075513&logs=1&id=d79e02ff-b93c-4e28-9164-61c5a88d1498&created=1769666072653',
        '_uasid': '"Z0FBQUFBQnBld1NHODFhNVk3cU1zWXV1bzJkMGxMQ2JYdW80OW5IMGU4eGFGdkZtSUVIQ01LYWppNUNBU0VMdnU3WFlRclhxa0VPTE5yenRlMC1zYkFRSl80VjlGX0tKcGpzMV9VcWN3WTExdDVrU2R5c1FrNjRDOTBHb1pkeml6eGszQ2V0QTlNVE5yUmNhQ3JxbEtuNVhuVmVUUXd2WkIwM2hlaENHQmRHeFhMbTJ1WnA4M2lPNkgwblp3cGRoQzhNNTF3RnFrMjJDU01DXy1HY01SRDNxbHNaczNsV3ZhSXhJX2twaW1lT0VwbUNWY2FDU0M1V3I5eGlfQnZHcGJmTlRhQzVjQVZ0WGY3VXBZQXB1MjdQQlpnMnJkVHVRX3lsRm9kamQ1QkcyWG1WRktZSkRVd3dyODhVNTVSeEZmV2xDbDROTVl3cFpQeDM0cTlOY1pXZUdQOXRGaXUxRGlRPT0="',
        '_umsid': '"Z0FBQUFBQnBld1NHRHR0enctdnVmZ2UxeFVRMHFsNDc3YTA4MEdwTWVvQmhxMnBBWHRIeE1SSGJ4UmF0c3EzVEJKc21MakRPajlrMzBHczRYTE5hZWp6azVXYnN2Z0xvQXNmUDdHSENTWFZxSEpOTW45dU80eEs3emNzSjdadVROdFJNRkpTcEhSTmRrcUpDZGdxcWlIV3VlZFBPdVNhcl9zWUpCVVVGREh2NzZDSWlGRE5jVTdMRHJuOVFtdUFJSXNPakE3cUYtR1UyYVNvbDNLU1RBaFQ5dUxaUlRtdG9wYmFma1dMclA4eVFiSDV2Mm5wS1JGTT0="',
        'oai-sc': '0gAAAAABpewSGn8EzY748jt_ZqjlmN2CLdFQgTLTqjbE8GsqxNAtJCMMt8ZOwjey23lJQt9vj0KPLdDxXgFUzd_oy8G0VxiAvDsYtK6DNL0qVH9g8j4riVLYUmEIqBzUqbu-57PHm-GyWK5URAUSuDFZNtZ5pdsssKkFaUQM4_HmeOjsVXCY31L_Wz9HT_lFE32FLwtQPCw0Q1GH9Ky02oMwK-47Faco-OYXKOqThPE4vVwZIHVH11Es',
    }

    headers = {
        'accept': '*/*',
        'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'authorization': 'Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjE5MzQ0ZTY1LWJiYzktNDRkMS1hOWQwLWY5NTdiMDc5YmQwZSIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS92MSJdLCJjbGllbnRfaWQiOiJhcHBfWDh6WTZ2VzJwUTl0UjNkRTduSzFqTDVnSCIsImV4cCI6MTc3MDQyMzY3OSwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9hdXRoIjp7ImNoYXRncHRfY29tcHV0ZV9yZXNpZGVuY3kiOiJub19jb25zdHJhaW50IiwiY2hhdGdwdF9kYXRhX3Jlc2lkZW5jeSI6Im5vX2NvbnN0cmFpbnQiLCJ1c2VyX2lkIjoidXNlci1CbWZLM3BFQ2YwaFhUQ1ViQjM4czkwNjIifSwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9wcm9maWxlIjp7ImVtYWlsIjoidG9wb3ZpaTg4ODhAZ21haWwuY29tIiwiZW1haWxfdmVyaWZpZWQiOnRydWV9LCJpYXQiOjE3Njk1NTk2NzgsImlzcyI6Imh0dHBzOi8vYXV0aC5vcGVuYWkuY29tIiwianRpIjoiNzcwOGY3NzMtN2JjNS00YzlkLTk5MDUtYjYzNWVlOTdhZmNhIiwibmJmIjoxNzY5NTU5Njc4LCJwd2RfYXV0aF90aW1lIjoxNzY4NTUzNzk0NzMyLCJzY3AiOlsib3BlbmlkIiwiZW1haWwiLCJwcm9maWxlIiwib2ZmbGluZV9hY2Nlc3MiLCJtb2RlbC5yZXF1ZXN0IiwibW9kZWwucmVhZCIsIm9yZ2FuaXphdGlvbi5yZWFkIiwib3JnYW5pemF0aW9uLndyaXRlIl0sInNlc3Npb25faWQiOiJhdXRoc2Vzc18yTnlwcjBmZXlEOEdzdTJMYzZtWVJMbHIiLCJzdWIiOiJnb29nbGUtb2F1dGgyfDExNTQ5MDA1MDM4NjQ0OTAzMDY0NCJ9.3Jj4W8lO2KiQsI8bBL4MOUXz8X3F4pGpZiTxSAn2G9lz8jzN8yBTcawkg3LN1_OEE7P9diQKGl-n2PG6vKUrTviGb2-8vXKlknMsYV8I43vomtKPtbtTyFasDo7XeC6aqcaEq7CuJ6mKn-MzyzIVG_Lb7MBRl6dOtuofFsHyFjwTjtDGXveSP3qo7-A0rmcjNU5hP7V8HosvzGixFvpg23ZlzTDfIe5rUY4e3tYeMFYaMAvs7-TJ2N7q_mIkCrdMiK0tEAgmUqyXFuKZiG6FQT7znXRhSpHExzl7wxmI_aGKLM2_6KSZn7ZZLqqMfa-nseGqr7CQB_5EjginGJm3CPW-GawS7nzPU5OK3vszj3tFiTSH5QaS4f4f8_puO2b9m2rNNWSL2nCC5KbZMPtFus3als_iTwSXQLypVUmDj-awVtnq7-YmM5l5pE-PakUa1ktG58LdbwlEZ9yjd0JgmU3lkPcWZSyUghGpdKwCjCeHwHVHJUQC1o8EypCtegFxG0DrcSaHaJQrJ15PhyictgjhGCR6PI9_CO7P0z40szKAaswB4u0VAjC9Xkbst5czt-3QsiAhVLsN2FqJHiX6SNj9X4GXk_4fwmGRaJS7HQPUYPi0DqVVdfU7OqcUKFnaeuHasicAUvzr3ZYovD3fJONbO144XvRGgGS1zt7bFWg',
        'content-type': 'application/json',
        'oai-client-build-number': '4308576',
        'oai-client-version': 'prod-3ea25c39fc07b9df21c8b5b7a151a3de9567273c',
        'oai-device-id': 'bed381d1-dbfa-43c5-870b-6a1ed45c5177',
        'oai-language': 'ru-RU',
        'origin': 'https://chatgpt.com',
        'priority': 'u=1, i',
        'referer': 'https://chatgpt.com/c/697b0483-5aac-8325-844f-1a03001bd4b0',
        'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
        # 'cookie': 'oai-did=bed381d1-dbfa-43c5-870b-6a1ed45c5177; _ga=GA1.1.414653376.1768045124; oai-hlib=true; _account_is_fedramp=false; oai-nav-state=0; __Host-next-auth.csrf-token=1ef796978f0e8319dc3b7db5627c5f6b01bd62312f3dfa1fa78795fa3fa5fcfc%7C913c9ff01c9cdd8d4d5ca6ae31c91c21c64cc50aeddebfe2e91791a696a42504; __Secure-next-auth.callback-url=https%3A%2F%2Fchatgpt.com; __Secure-next-auth.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..ZNchp3v1OzVo_jFV.1yXBa59_FJWyhHWvgsk_vcpUAWo0zcURUCdg4zOGwvl2hvBU8g-u5PXCd1drMRKFPbduCtwTk4m2fkevPL-M3nd56hyrrDjUGiMp8BRxnh4OTyh4Pp6Djrcust7YJW3Esre4ieVly56Vltzou8JEal-a4WfIz6wGk1ApT1FDt-eBQECBziM-Gl_lLOqI4CrdfE37WcMzViyIqqhe4vXoaxRHLbG5x9oF6DZuwPNpg2RFrRHyeVO6N_aOf89iZNWMq3sWKfSiZO1pXeUzaOpOwF-jtrtHdIfJnX-Xy9SV77zjlN7hmtAYrEcJap7f3gBWt8kzmHR-x1PsoSTR4VGaXlChypfVJYuN8TfDIgEd7RSAIOeUYdoSs6kNUqWBn0DfSCb4Urzfma0CP-Ur8-a0Ga-fselUJysvd60y-LJfRjrSQJAfXmkQGg8J0uNJROXSLLyeircWwPFQPl5EbY_utwq6CQISblE2Bs3Ip1ZYN6mBh7hRPsYAChrXEjcINSvdlTnqWytm-8xjEEec2iu9b67FY4YkfjmEIpnOicVKnqr17wom85tyVhCGJcUk2Mz9O--H3yl3OxpWJiNkPpiGotR5IWKg712R7VYdqj7SYLQwiUcS7QM9fQLesrqxM93455YC8yxx2ImekzYGraaQejPR4zlgxsWJmeu7SUvBDwgfGjn69ODt-uejnr4F26ab9ub-Oqt3Xa7Ehah_qyAa8o3bFqSJyCsWlH80FWjUXoWH_zmGPqNKWuzBm268XKFSf74QmvcntZ-1fIYMUENLZeyqqAzEmi0pnGvUwhnWmBu9gNAl0Cx5CcjdKBFRh5FPfxhuN8LZuNNWUdxkInCIXqvR-mAQL_TIFPc7zWMQJmxmaZOM_t9o5y9NLNtqOh3BF00e6BoZjrRprkEOqFdBE6yuAyLf3WD-dTF9bCy60_BEdmpTq_-zeCecx4W7v18fnUyOPfOqr9r0O_hMACgOSMhd_OyRvLYCoCvnmZwvmD4KT9-UtgmQu4peuk7CocE9A1KCgdEBubzDZkDSrKq1dEZVo4YYvAaxLVNPdbM3vetKCUD61P16QCOSVP_eWhaZ4QrO3a4Xlt3mIEAeEM-7lhw_bRlTdfqOj2NWh0ZAl2yFXfNBSRzc2P-6ZUIVbdMT4EY_1O1cJcud_JeymwIa7_fKz6TrSpQW1sQNrL4ZoNAP2tyzNOVZ8UW6kpBj2rb6MIqrI7vbkNGhpWOC8JkSTuuxqRtstfUraCbMmLNghkCM_ANfpsUMdH397TAuFNfIf7JnZDslEJlcOQcAFNOUowwMoxegt7vYJBtWmbSUlX8wzsU8Cs_lief8MjffegAwG3PK99VeFfZHG2Pxz4WmY7fU7__5lHu1Hbqw-9e0Efy7T-adR706eGbhxAkwpWdXuLc5ISe6jV-wuRAnP_Lyjv1ZVRnhAmrhVUc4rGuAE4jQCyYYwWZt4qAEfGxgBceNuRMTae2muKWB5jXiyBlAHfmqAauma1Ez3BVrn7K19xymIdIZuo1vHFVMQxolQkWT9RM09nbGLAo26Ks_SWcOllZLFKwR-5xXpRKMP_BxaDYxv_-zTJJuEYZhnKAwLrVByDX3eaYKsaw2mn5WcW22qXp5ybykX5kHx8inDuifYe2XS8Z1kWmiyF9EjUZvoqaLwKMDjrVYdz4-ecLW1q3WsbL7Gr0FwMx1phSkDR8t0Skv7tx6yHymD_iSNwMf4CZz6eW_WyZdHE3T2F35HUhJQIk0Kq8HFhqL6TL7rizop2dOUlVHqCWI6bhdt_dQrhUgE1RMkg4wEDfWlC6qoJWo0aYY1t1i6rZay2pmRRWLPG9cnxo9Sols43LYvNr3kBDjjt25sPBHLB0kjuuY4e4vmNTvkkSzueUKauRC8a34QfDjJBHK22qoPPeJQLD1Y9ZJMdZWwcPLLtCwu8n9trdzt0V_ZAB046anlMwfJ22hWRzLvTegtH0Feah28L_pAgnDWYaU27_9udUCdEoDQbGQaXW8LHsnUepPP_wP2o0ZWvoObEh7_lwA0Dhybgb1fKvcjLm6fswIUl6LmdcPEZJ6GRsmGOdpxGBGl9hG1HWQ3oFgHsJyiRiGfkzv7-IwNI88JFkyhH8rw--GQK4SZIZ9mz0Y1A8GITEtxPIPoqfR0cZJJNrI7Prq-4FbHvZPKDA4wKDq8rCMPmCMtm-d2_cMfq1LBEL-g9FaaTNE5mqMrae88wgNFVLdagETQyenfflE-eMmx_YB7mQ5vP81IW_Ae3wmiHueHHr1U1V7JfDwuFBV6u25s0zxFAc5hzyyZyDmWI0LJhCuxYmtaWsqZm8kRHnptBwiB33fUBWxdW3bsk-oYI65P68EBVV8iBsIp6u3qtpRUn-jgJstlvnXaU597fdzQ2KwSPb9GIhIUycejvcZCBoVd21iKAkIJp0HVRCRjD1YP_-w2wK6nuTDfxWXTM08rBplW4TAyo_i7D6ZU4CEph9BsTNfRpyRxJSty1-Ucd_k9vWNn9F1oFIAqmp_RYPWrwwVtOaEGhU2lXMTYjQeva8IaIIiJxteoNIDkMqUiltSXej1Gnk-pO4tHok6JhahBfUrguDRncHdELutSqXlLxRhgVZD79ngjASP9Gq_YhzlislhPA7NksOVcvSmwJV6OpfS99ED4DT0ipiNoEPE8X6BYyLLLIbajTG8XnPyRG76zfWJ_lvESxk4ectoBrgUSSSqpKuTJCuIG6PT4xXRt-9kDge2V1SU9kd_ffgsThSJ178eE5swtMjoswUWqw0zIV-jtFgpIOseBWw1D7HhcWhDNH1ks_MAIr_Y4ByjdBxC-9HF-EC4WldPN5-m-w-_jpl9WzkU0qI41jux8jG2mskOL4kv5enbyvcrQk1H4hiXbojV6z9tzq2Alp-Rv9hhkp5Jl9ErBYi2AXi573tu86a2KWe1BjMvBcNmtxc0KXCQ44O4tDp9cjQWQcz_BbDtJvsLe41MqsR06T5r564uxThP_y5HZf5eFmq50ct1Ek_y6Rm69UW-KSSTY1MS_ZhlQqYBvhKO9RcfBFVN-g9gai8sWFGt7c9stDYYunBv7co-EQvH971OSZc0uiQ2ihec6_5l2CCsfsGt7kmdK4XuWeB2Wth2rPJAX3PcyuBceofpgwV3lGMDcFHpD4qho1yI15ygh--HhxiD1C5O2d4u--W5bxNyx8p82uNgR0UFiGYzqR7FOg5gqKPKd7vDc_u6cY6aKK9YrhYA6_XjUT8OROerNz-BTkkbIVzv1wxcYceex6hca8dfjV-sUzQ0ttMDD-bNuAbXABUKj4vVIDF617xtpfA8ADuGkdbjDO_jrl261lXxxdkPu6EJGzJw8O9tsyXmmFfXrxe3IL_NnMAzv1rxN-XxH1PVz7k8NE1K4EdlH15oeoGxcT8MwI1SoFP6xXsXyFsIgyBeW-zZnB5vS7nlRGsy-jduOIUBmlQOVfQeCCplcGbxtgCoHTB15JIB5hLsfv_98YgpvuJQuQwhvEVb_G9YTIimMYyShh8H9cj32A9gFnreCjyIoC0scIYjVA8j46CM4rdGkwTk0k1UgrSzP3bgC5R9Q1O7Ga_6ydCBPyZvqL7idd4SGQTc45o-Nven9utaiwVnwbKVuW5byLEt9ooJBnxcZGFS-wlI9bzN-fyGj0hOZZj0hqrc.KDY0IGIo1Gxpa5oNsxaxFg; __cf_bm=QnOMgWgkWmguxdn2pgIKthiHi6BDQZ.RoTqPNT5bk48-1769669653-1.0.1.1-0WrR.BZQcrCqPzqQcqPJsM8p3ApZjfv010vKRimwK0JXPj3oevZpIqQWY.USL7uR0OBLmX168Z01_mojVuCRGjOwV2UjA6Hx98pb5izaJAI; __cflb=0H28vzvP5FJafnkHxihKb44bdy6fTJD3Pt5hM927szj; _cfuvid=_cx7eAewzyHLXIolRd2ZcoHTigKglp_PLvZoBRSZvGI-1769669653674-0.0.1.1-604800000; cf_clearance=0tAEh9f9iGQPDGVGvma5fLylvwtsyUigBA5EfFjcp64-1769669663-1.2.1.1-s.HIRwCGGJRTxGl.dtiI_wv5M9VT8KFpxHSzxt6G.j0ZwPSmfsPr2BMlyJk2YTCHSLJQ.CCF2DLoL8E6F3kD8wCDwHOKlHhJOb5MTwHqN2E9o7tWZUbFbR303LVE6AU4TA6i5DiM3xcbwhKiSdR70pt2a2mFuxBUYiBAXbn80gjEMuLmLZPrJOQ0YzCTO1tNNXMcB9o9eoO1kyuSImlsBoiQ3ann8H3xymuWPly5W2o; _ga_9SHBSK2D9J=GS2.1.s1769666076$o8$g0$t1769666076$j60$l0$h0; oai-client-auth-info=%7B%22user%22%3A%7B%22name%22%3A%22Tima%20First%22%2C%22email%22%3A%22topovii8888%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fpr.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1769666079178%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D; oai-gn=; oai-hm=ON_YOUR_MIND%20%7C%20GOOD_TO_SEE_YOU; _dd_s=aid=56785c82-0c44-4651-a04a-b253a3a84c14&rum=0&expire=1769667075513&logs=1&id=d79e02ff-b93c-4e28-9164-61c5a88d1498&created=1769666072653; _uasid="Z0FBQUFBQnBld1NHODFhNVk3cU1zWXV1bzJkMGxMQ2JYdW80OW5IMGU4eGFGdkZtSUVIQ01LYWppNUNBU0VMdnU3WFlRclhxa0VPTE5yenRlMC1zYkFRSl80VjlGX0tKcGpzMV9VcWN3WTExdDVrU2R5c1FrNjRDOTBHb1pkeml6eGszQ2V0QTlNVE5yUmNhQ3JxbEtuNVhuVmVUUXd2WkIwM2hlaENHQmRHeFhMbTJ1WnA4M2lPNkgwblp3cGRoQzhNNTF3RnFrMjJDU01DXy1HY01SRDNxbHNaczNsV3ZhSXhJX2twaW1lT0VwbUNWY2FDU0M1V3I5eGlfQnZHcGJmTlRhQzVjQVZ0WGY3VXBZQXB1MjdQQlpnMnJkVHVRX3lsRm9kamQ1QkcyWG1WRktZSkRVd3dyODhVNTVSeEZmV2xDbDROTVl3cFpQeDM0cTlOY1pXZUdQOXRGaXUxRGlRPT0="; _umsid="Z0FBQUFBQnBld1NHRHR0enctdnVmZ2UxeFVRMHFsNDc3YTA4MEdwTWVvQmhxMnBBWHRIeE1SSGJ4UmF0c3EzVEJKc21MakRPajlrMzBHczRYTE5hZWp6azVXYnN2Z0xvQXNmUDdHSENTWFZxSEpOTW45dU80eEs3emNzSjdadVROdFJNRkpTcEhSTmRrcUpDZGdxcWlIV3VlZFBPdVNhcl9zWUpCVVVGREh2NzZDSWlGRE5jVTdMRHJuOVFtdUFJSXNPakE3cUYtR1UyYVNvbDNLU1RBaFQ5dUxaUlRtdG9wYmFma1dMclA4eVFiSDV2Mm5wS1JGTT0="; oai-sc=0gAAAAABpewSGn8EzY748jt_ZqjlmN2CLdFQgTLTqjbE8GsqxNAtJCMMt8ZOwjey23lJQt9vj0KPLdDxXgFUzd_oy8G0VxiAvDsYtK6DNL0qVH9g8j4riVLYUmEIqBzUqbu-57PHm-GyWK5URAUSuDFZNtZ5pdsssKkFaUQM4_HmeOjsVXCY31L_Wz9HT_lFE32FLwtQPCw0Q1GH9Ky02oMwK-47Faco-OYXKOqThPE4vVwZIHVH11Es',
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
        'oai-did': 'bed381d1-dbfa-43c5-870b-6a1ed45c5177',
        '_ga': 'GA1.1.414653376.1768045124',
        'oai-hlib': 'true',
        '_account_is_fedramp': 'false',
        'oai-nav-state': '0',
        '__Host-next-auth.csrf-token': '1ef796978f0e8319dc3b7db5627c5f6b01bd62312f3dfa1fa78795fa3fa5fcfc%7C913c9ff01c9cdd8d4d5ca6ae31c91c21c64cc50aeddebfe2e91791a696a42504',
        '__Secure-next-auth.callback-url': 'https%3A%2F%2Fchatgpt.com',
        '__Secure-next-auth.session-token': 'eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..ZNchp3v1OzVo_jFV.1yXBa59_FJWyhHWvgsk_vcpUAWo0zcURUCdg4zOGwvl2hvBU8g-u5PXCd1drMRKFPbduCtwTk4m2fkevPL-M3nd56hyrrDjUGiMp8BRxnh4OTyh4Pp6Djrcust7YJW3Esre4ieVly56Vltzou8JEal-a4WfIz6wGk1ApT1FDt-eBQECBziM-Gl_lLOqI4CrdfE37WcMzViyIqqhe4vXoaxRHLbG5x9oF6DZuwPNpg2RFrRHyeVO6N_aOf89iZNWMq3sWKfSiZO1pXeUzaOpOwF-jtrtHdIfJnX-Xy9SV77zjlN7hmtAYrEcJap7f3gBWt8kzmHR-x1PsoSTR4VGaXlChypfVJYuN8TfDIgEd7RSAIOeUYdoSs6kNUqWBn0DfSCb4Urzfma0CP-Ur8-a0Ga-fselUJysvd60y-LJfRjrSQJAfXmkQGg8J0uNJROXSLLyeircWwPFQPl5EbY_utwq6CQISblE2Bs3Ip1ZYN6mBh7hRPsYAChrXEjcINSvdlTnqWytm-8xjEEec2iu9b67FY4YkfjmEIpnOicVKnqr17wom85tyVhCGJcUk2Mz9O--H3yl3OxpWJiNkPpiGotR5IWKg712R7VYdqj7SYLQwiUcS7QM9fQLesrqxM93455YC8yxx2ImekzYGraaQejPR4zlgxsWJmeu7SUvBDwgfGjn69ODt-uejnr4F26ab9ub-Oqt3Xa7Ehah_qyAa8o3bFqSJyCsWlH80FWjUXoWH_zmGPqNKWuzBm268XKFSf74QmvcntZ-1fIYMUENLZeyqqAzEmi0pnGvUwhnWmBu9gNAl0Cx5CcjdKBFRh5FPfxhuN8LZuNNWUdxkInCIXqvR-mAQL_TIFPc7zWMQJmxmaZOM_t9o5y9NLNtqOh3BF00e6BoZjrRprkEOqFdBE6yuAyLf3WD-dTF9bCy60_BEdmpTq_-zeCecx4W7v18fnUyOPfOqr9r0O_hMACgOSMhd_OyRvLYCoCvnmZwvmD4KT9-UtgmQu4peuk7CocE9A1KCgdEBubzDZkDSrKq1dEZVo4YYvAaxLVNPdbM3vetKCUD61P16QCOSVP_eWhaZ4QrO3a4Xlt3mIEAeEM-7lhw_bRlTdfqOj2NWh0ZAl2yFXfNBSRzc2P-6ZUIVbdMT4EY_1O1cJcud_JeymwIa7_fKz6TrSpQW1sQNrL4ZoNAP2tyzNOVZ8UW6kpBj2rb6MIqrI7vbkNGhpWOC8JkSTuuxqRtstfUraCbMmLNghkCM_ANfpsUMdH397TAuFNfIf7JnZDslEJlcOQcAFNOUowwMoxegt7vYJBtWmbSUlX8wzsU8Cs_lief8MjffegAwG3PK99VeFfZHG2Pxz4WmY7fU7__5lHu1Hbqw-9e0Efy7T-adR706eGbhxAkwpWdXuLc5ISe6jV-wuRAnP_Lyjv1ZVRnhAmrhVUc4rGuAE4jQCyYYwWZt4qAEfGxgBceNuRMTae2muKWB5jXiyBlAHfmqAauma1Ez3BVrn7K19xymIdIZuo1vHFVMQxolQkWT9RM09nbGLAo26Ks_SWcOllZLFKwR-5xXpRKMP_BxaDYxv_-zTJJuEYZhnKAwLrVByDX3eaYKsaw2mn5WcW22qXp5ybykX5kHx8inDuifYe2XS8Z1kWmiyF9EjUZvoqaLwKMDjrVYdz4-ecLW1q3WsbL7Gr0FwMx1phSkDR8t0Skv7tx6yHymD_iSNwMf4CZz6eW_WyZdHE3T2F35HUhJQIk0Kq8HFhqL6TL7rizop2dOUlVHqCWI6bhdt_dQrhUgE1RMkg4wEDfWlC6qoJWo0aYY1t1i6rZay2pmRRWLPG9cnxo9Sols43LYvNr3kBDjjt25sPBHLB0kjuuY4e4vmNTvkkSzueUKauRC8a34QfDjJBHK22qoPPeJQLD1Y9ZJMdZWwcPLLtCwu8n9trdzt0V_ZAB046anlMwfJ22hWRzLvTegtH0Feah28L_pAgnDWYaU27_9udUCdEoDQbGQaXW8LHsnUepPP_wP2o0ZWvoObEh7_lwA0Dhybgb1fKvcjLm6fswIUl6LmdcPEZJ6GRsmGOdpxGBGl9hG1HWQ3oFgHsJyiRiGfkzv7-IwNI88JFkyhH8rw--GQK4SZIZ9mz0Y1A8GITEtxPIPoqfR0cZJJNrI7Prq-4FbHvZPKDA4wKDq8rCMPmCMtm-d2_cMfq1LBEL-g9FaaTNE5mqMrae88wgNFVLdagETQyenfflE-eMmx_YB7mQ5vP81IW_Ae3wmiHueHHr1U1V7JfDwuFBV6u25s0zxFAc5hzyyZyDmWI0LJhCuxYmtaWsqZm8kRHnptBwiB33fUBWxdW3bsk-oYI65P68EBVV8iBsIp6u3qtpRUn-jgJstlvnXaU597fdzQ2KwSPb9GIhIUycejvcZCBoVd21iKAkIJp0HVRCRjD1YP_-w2wK6nuTDfxWXTM08rBplW4TAyo_i7D6ZU4CEph9BsTNfRpyRxJSty1-Ucd_k9vWNn9F1oFIAqmp_RYPWrwwVtOaEGhU2lXMTYjQeva8IaIIiJxteoNIDkMqUiltSXej1Gnk-pO4tHok6JhahBfUrguDRncHdELutSqXlLxRhgVZD79ngjASP9Gq_YhzlislhPA7NksOVcvSmwJV6OpfS99ED4DT0ipiNoEPE8X6BYyLLLIbajTG8XnPyRG76zfWJ_lvESxk4ectoBrgUSSSqpKuTJCuIG6PT4xXRt-9kDge2V1SU9kd_ffgsThSJ178eE5swtMjoswUWqw0zIV-jtFgpIOseBWw1D7HhcWhDNH1ks_MAIr_Y4ByjdBxC-9HF-EC4WldPN5-m-w-_jpl9WzkU0qI41jux8jG2mskOL4kv5enbyvcrQk1H4hiXbojV6z9tzq2Alp-Rv9hhkp5Jl9ErBYi2AXi573tu86a2KWe1BjMvBcNmtxc0KXCQ44O4tDp9cjQWQcz_BbDtJvsLe41MqsR06T5r564uxThP_y5HZf5eFmq50ct1Ek_y6Rm69UW-KSSTY1MS_ZhlQqYBvhKO9RcfBFVN-g9gai8sWFGt7c9stDYYunBv7co-EQvH971OSZc0uiQ2ihec6_5l2CCsfsGt7kmdK4XuWeB2Wth2rPJAX3PcyuBceofpgwV3lGMDcFHpD4qho1yI15ygh--HhxiD1C5O2d4u--W5bxNyx8p82uNgR0UFiGYzqR7FOg5gqKPKd7vDc_u6cY6aKK9YrhYA6_XjUT8OROerNz-BTkkbIVzv1wxcYceex6hca8dfjV-sUzQ0ttMDD-bNuAbXABUKj4vVIDF617xtpfA8ADuGkdbjDO_jrl261lXxxdkPu6EJGzJw8O9tsyXmmFfXrxe3IL_NnMAzv1rxN-XxH1PVz7k8NE1K4EdlH15oeoGxcT8MwI1SoFP6xXsXyFsIgyBeW-zZnB5vS7nlRGsy-jduOIUBmlQOVfQeCCplcGbxtgCoHTB15JIB5hLsfv_98YgpvuJQuQwhvEVb_G9YTIimMYyShh8H9cj32A9gFnreCjyIoC0scIYjVA8j46CM4rdGkwTk0k1UgrSzP3bgC5R9Q1O7Ga_6ydCBPyZvqL7idd4SGQTc45o-Nven9utaiwVnwbKVuW5byLEt9ooJBnxcZGFS-wlI9bzN-fyGj0hOZZj0hqrc.KDY0IGIo1Gxpa5oNsxaxFg',
        '__cf_bm': 'QnOMgWgkWmguxdn2pgIKthiHi6BDQZ.RoTqPNT5bk48-1769669653-1.0.1.1-0WrR.BZQcrCqPzqQcqPJsM8p3ApZjfv010vKRimwK0JXPj3oevZpIqQWY.USL7uR0OBLmX168Z01_mojVuCRGjOwV2UjA6Hx98pb5izaJAI',
        '__cflb': '0H28vzvP5FJafnkHxihKb44bdy6fTJD3Pt5hM927szj',
        '_cfuvid': '_cx7eAewzyHLXIolRd2ZcoHTigKglp_PLvZoBRSZvGI-1769669653674-0.0.1.1-604800000',
        'cf_clearance': '0tAEh9f9iGQPDGVGvma5fLylvwtsyUigBA5EfFjcp64-1769669663-1.2.1.1-s.HIRwCGGJRTxGl.dtiI_wv5M9VT8KFpxHSzxt6G.j0ZwPSmfsPr2BMlyJk2YTCHSLJQ.CCF2DLoL8E6F3kD8wCDwHOKlHhJOb5MTwHqN2E9o7tWZUbFbR303LVE6AU4TA6i5DiM3xcbwhKiSdR70pt2a2mFuxBUYiBAXbn80gjEMuLmLZPrJOQ0YzCTO1tNNXMcB9o9eoO1kyuSImlsBoiQ3ann8H3xymuWPly5W2o',
        'oai-client-auth-info': '%7B%22user%22%3A%7B%22name%22%3A%22Tima%20First%22%2C%22email%22%3A%22topovii8888%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fpr.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1769666079178%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D',
        'oai-gn': '',
        'oai-hm': 'ON_YOUR_MIND%20%7C%20GOOD_TO_SEE_YOU',
        '_uasid': '"Z0FBQUFBQnBld1NHODFhNVk3cU1zWXV1bzJkMGxMQ2JYdW80OW5IMGU4eGFGdkZtSUVIQ01LYWppNUNBU0VMdnU3WFlRclhxa0VPTE5yenRlMC1zYkFRSl80VjlGX0tKcGpzMV9VcWN3WTExdDVrU2R5c1FrNjRDOTBHb1pkeml6eGszQ2V0QTlNVE5yUmNhQ3JxbEtuNVhuVmVUUXd2WkIwM2hlaENHQmRHeFhMbTJ1WnA4M2lPNkgwblp3cGRoQzhNNTF3RnFrMjJDU01DXy1HY01SRDNxbHNaczNsV3ZhSXhJX2twaW1lT0VwbUNWY2FDU0M1V3I5eGlfQnZHcGJmTlRhQzVjQVZ0WGY3VXBZQXB1MjdQQlpnMnJkVHVRX3lsRm9kamQ1QkcyWG1WRktZSkRVd3dyODhVNTVSeEZmV2xDbDROTVl3cFpQeDM0cTlOY1pXZUdQOXRGaXUxRGlRPT0="',
        '_umsid': '"Z0FBQUFBQnBld1NHRHR0enctdnVmZ2UxeFVRMHFsNDc3YTA4MEdwTWVvQmhxMnBBWHRIeE1SSGJ4UmF0c3EzVEJKc21MakRPajlrMzBHczRYTE5hZWp6azVXYnN2Z0xvQXNmUDdHSENTWFZxSEpOTW45dU80eEs3emNzSjdadVROdFJNRkpTcEhSTmRrcUpDZGdxcWlIV3VlZFBPdVNhcl9zWUpCVVVGREh2NzZDSWlGRE5jVTdMRHJuOVFtdUFJSXNPakE3cUYtR1UyYVNvbDNLU1RBaFQ5dUxaUlRtdG9wYmFma1dMclA4eVFiSDV2Mm5wS1JGTT0="',
        'oai-sc': '0gAAAAABpewSHJbu3gEPAbHZX2UCVw9CoHCSsNH9c95-MiU4NOHiYnwJn-_-rpy5OZIFG_AJli9G_AqAbtLUmPFNaPh6TCscBS8PbAgw0bidV5RCXawTCVU074ditMB9yvKSC_LFhV1qnOsJd-sChV64h9HrZAzE8SMnAGyncn1vfYRxYSuyy_bGZo5V43tG9vrzwLKo0gndwUYubrG6SEFC6Drso-as2mLztflS98vFtOawYGoEe-WQ',
        '_ga_9SHBSK2D9J': 'GS2.1.s1769666076$o8$g1$t1769666177$j60$l0$h0',
        '_dd_s': 'aid=56785c82-0c44-4651-a04a-b253a3a84c14&rum=0&expire=1769667093062&logs=1&id=d79e02ff-b93c-4e28-9164-61c5a88d1498&created=1769666072653',
    }

    headers = {
        'accept': 'text/event-stream',
        'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'authorization': 'Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6IjE5MzQ0ZTY1LWJiYzktNDRkMS1hOWQwLWY5NTdiMDc5YmQwZSIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS92MSJdLCJjbGllbnRfaWQiOiJhcHBfWDh6WTZ2VzJwUTl0UjNkRTduSzFqTDVnSCIsImV4cCI6MTc3MDQyMzY3OSwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9hdXRoIjp7ImNoYXRncHRfY29tcHV0ZV9yZXNpZGVuY3kiOiJub19jb25zdHJhaW50IiwiY2hhdGdwdF9kYXRhX3Jlc2lkZW5jeSI6Im5vX2NvbnN0cmFpbnQiLCJ1c2VyX2lkIjoidXNlci1CbWZLM3BFQ2YwaFhUQ1ViQjM4czkwNjIifSwiaHR0cHM6Ly9hcGkub3BlbmFpLmNvbS9wcm9maWxlIjp7ImVtYWlsIjoidG9wb3ZpaTg4ODhAZ21haWwuY29tIiwiZW1haWxfdmVyaWZpZWQiOnRydWV9LCJpYXQiOjE3Njk1NTk2NzgsImlzcyI6Imh0dHBzOi8vYXV0aC5vcGVuYWkuY29tIiwianRpIjoiNzcwOGY3NzMtN2JjNS00YzlkLTk5MDUtYjYzNWVlOTdhZmNhIiwibmJmIjoxNzY5NTU5Njc4LCJwd2RfYXV0aF90aW1lIjoxNzY4NTUzNzk0NzMyLCJzY3AiOlsib3BlbmlkIiwiZW1haWwiLCJwcm9maWxlIiwib2ZmbGluZV9hY2Nlc3MiLCJtb2RlbC5yZXF1ZXN0IiwibW9kZWwucmVhZCIsIm9yZ2FuaXphdGlvbi5yZWFkIiwib3JnYW5pemF0aW9uLndyaXRlIl0sInNlc3Npb25faWQiOiJhdXRoc2Vzc18yTnlwcjBmZXlEOEdzdTJMYzZtWVJMbHIiLCJzdWIiOiJnb29nbGUtb2F1dGgyfDExNTQ5MDA1MDM4NjQ0OTAzMDY0NCJ9.3Jj4W8lO2KiQsI8bBL4MOUXz8X3F4pGpZiTxSAn2G9lz8jzN8yBTcawkg3LN1_OEE7P9diQKGl-n2PG6vKUrTviGb2-8vXKlknMsYV8I43vomtKPtbtTyFasDo7XeC6aqcaEq7CuJ6mKn-MzyzIVG_Lb7MBRl6dOtuofFsHyFjwTjtDGXveSP3qo7-A0rmcjNU5hP7V8HosvzGixFvpg23ZlzTDfIe5rUY4e3tYeMFYaMAvs7-TJ2N7q_mIkCrdMiK0tEAgmUqyXFuKZiG6FQT7znXRhSpHExzl7wxmI_aGKLM2_6KSZn7ZZLqqMfa-nseGqr7CQB_5EjginGJm3CPW-GawS7nzPU5OK3vszj3tFiTSH5QaS4f4f8_puO2b9m2rNNWSL2nCC5KbZMPtFus3als_iTwSXQLypVUmDj-awVtnq7-YmM5l5pE-PakUa1ktG58LdbwlEZ9yjd0JgmU3lkPcWZSyUghGpdKwCjCeHwHVHJUQC1o8EypCtegFxG0DrcSaHaJQrJ15PhyictgjhGCR6PI9_CO7P0z40szKAaswB4u0VAjC9Xkbst5czt-3QsiAhVLsN2FqJHiX6SNj9X4GXk_4fwmGRaJS7HQPUYPi0DqVVdfU7OqcUKFnaeuHasicAUvzr3ZYovD3fJONbO144XvRGgGS1zt7bFWg',
        'content-type': 'application/json',
        'oai-client-build-number': '4308576',
        'oai-client-version': 'prod-3ea25c39fc07b9df21c8b5b7a151a3de9567273c',
        'oai-device-id': 'bed381d1-dbfa-43c5-870b-6a1ed45c5177',
        'oai-echo-logs': '0,10683,1,11623,0,19189,1,20931,0,108964,1,112356,0,113101,1,113112,0,115923,1,117661',
        'oai-language': 'ru-RU',
        'openai-sentinel-chat-requirements-token': req,
        'openai-sentinel-proof-token': 'gAAAAABWzIxMzQsIlRodSBKYW4gMjkgMjAyNiAxMTo1NjoxNiBHTVQrMDYwMCAoR01UKzA2OjAwKSIsMjAwNzc2MDg5Niw1LCJNb3ppbGxhLzUuMCAoV2luZG93cyBOVCAxMC4wOyBXaW42NDsgeDY0KSBBcHBsZVdlYktpdC81MzcuMzYgKEtIVE1MLCBsaWtlIEdlY2tvKSBDaHJvbWUvMTQ0LjAuMC4wIFNhZmFyaS81MzcuMzYiLCJodHRwczovL3d3dy5nb29nbGV0YWdtYW5hZ2VyLmNvbS9ndGFnL2pzP2lkPUctOVNIQlNLMkQ5SiIsInByb2QtM2VhMjVjMzlmYzA3YjlkZjIxYzhiNWI3YTE1MWEzZGU5NTY3MjczYyIsInJ1LVJVIiwicnUtUlUscnUsZW4tVVMsZW4iLDEzLCJwZGZWaWV3ZXJFbmFibGVk4oiSdHJ1ZSIsIl9yZWFjdExpc3RlbmluZ2phOXl3dHdraWlsIiwib25nYW1lcGFkZGlzY29ubmVjdGVkIiwxMTQyMDQuNzAwMDAwNzYyOTQsImRkMDk3N2M4LWI2M2MtNDg2Yy1iMWZhLWZmOWMwZDBhNGM3ZCIsIiIsNCwxNzY5NjY2MDYyMzI1XQ==~S',
        'openai-sentinel-turnstile-token': 'TRAaAhgGGBYMEGxMQWsMGBQLHxoBAQwOBwcXAgIEFwEHCh0HGhAXBRgHFxYMAx8BAgQCFgMGAAwDEBQWYgN8blJnH0VhZh4JFB4MBwUcGwIUCAxycWh2ZnB8HWxzaHRldGN7UGB8b2NaXmlwXWh6YQZde2ZdcGxmcHxvY1oDemVnB0xhdHhMY1p4dmdwA3diBl17fF1wYmJgeExlcH95cmMDfVIGeEdjB1p4ZgZwamJdQXp8B3RnUmNoBWVgQnxjB0JsYVpRYxYaEB8GGAYbFgwQb1MLDwwYFAsWGgQEDA4UakVuV2BrYX9zfEBgZUUNd2d7fld1QmJSY1ZAYHp8U3hzeHVTc28AdHNZcm5nHgRsdklxVXN8R311SXZnelYNYHBJcVNwb1BnYUl2Y3NJCQsQAhYFCgANAxAUFnh2Y0F7Zk0JFB4MBw4cGQUUCAxgZHB5clFFbXVeaH5xdGNtdVF/bXJddExnXXBLcXRjb3ZeRUtwZ2doc05Ve2NdRkphYEJPclFFb3JeXWp8UUpvY2N7Y3BRc2l8TlV7ZXNWemxda2NwUXttdXdjfnVkWWxzXnBjYwZ4aWVgWXt3dFVtcE5FTHN0YEtnB3htcXRja2dBDxMWGhAbGgALDA4Uc0kJCxACFgcBAAUDEBRAREdLGBQBGhoHCgwOQkBbURoQGgwYBx8WDBBNYgdwewVGampaB2t7ZXdnZlp4WE12Dg8MGBQDGBoPCwwOFFYdTEZXZnllcEBiAVZJfWNWHQ1HdGlmYmFCcmxqb25RV1ZHeWtCemBmVmVwd0NYcWQefnFqeGJ0dkZDcWFGAHNwSX1Qc3Z+BHBWZk9lH0xkZHttUlFvV3J2RX5wUHxARHBGflNgHF9+UX9lV3dDW35RSXlQen8JCxACFgEcHA0UCAx5XGtbe2J3ExYaEB0HGAYYFgwQY3cCB2BDCw8MGBQAGBoDBgwOFGpCBXJne25iYXhyRmNCTFRlHn5XUx5icWQfXGJ6RQVUZWhMemQeflJqa1dUY2tAbmV4THdneGJxcx9YbGRFDW91H25+YB9AVGp7UGJkSX5UZB9HdGR7bnVqfEBTZx8FWGcfUHdXQlhMZR52U1ceemBneFB8Z3hcDmUeQ1dqew1SdXh6U2BrYVJgQmJwa3tAZGVmTHRWe3pxYEB+c2QeQFFgaEBmZWl6dWp4cldqSX5zZx52f2geflJqaHF7YGwMb2V4en51H1B1akVXbGVFDX5RQkBSZXgNdGp4cmVoRUBgZR5yUWUdUH5lQkxcY0IFZGVCAHVlH1BUc3hicWNCBFRlQnpSY2libmEfRAJqeQ10Y0JEAGBCDWJjRVAGZx52V2cedkBjbFt9YWh9d2d8AHtnH3pmc0kMc3VCdmN6RXJgalYFZmBrbnRzeERsUR9Ad2pARGZgHVBkZnhycWBFQGdRaAV0Y2h6d1NCBXBne35gah8NemBGRHRlQmJ1Y3tQYnVrcnpgHmJUY3hERmUeW1dwWWFucEl1cnpJQ3BzSX5UZB9HdGR7bnVqfEBXZx8Fd2hCenxndkRSZh92T2cfRGR1QnJmZB8BbnN7cnBkH0NsVmgNf1RZYnVTHVBnc0VuRWtAcgFkQGV0dEVXZnNGU3F1f2VSdnhhdGFvBHl6VlxhcFltcnNvbXJ0Rl9mc0ZMYHVZfVdzb2JjdVl2ZXBsW3JkbAVSY2hQd2pocmV6HgVUZWtmemBFYmJlQgVsamgEb2B7bmZja2JUYXxEcGBFDWFlQlBOanh9VXpZeWZzbFNwcElbUnBsRGRmH1BTZFlydGB4UFV1dmZQYWYMcGNDQAVnb35xUGl+TlcfbgZzfG5+dll9b3BGX3N6fwVgc3tTeHZGDG9kSVdxc1l1cXNWbWx2WX1VZHxHdXVJdXBkbEd3Z0llV3MfZVJheAV7ah5xbGRoBXRja2JxamhicXpFcmBqVgVmYGtudHN7dm5gRUBxY0IFV1R4XE5jeER1ZB9ud2seflJneERTUGhib2prQHFrb3V1ZR9QVHN4YnFjQgRUZUIFfGcfemFoH0RcY3tAcVcfUFJgH1BgYR1QbGAednd1QgVmanxYYmpFZnB6ewVUZWtmemBFYmJlQgVsamlEcWQfZldgVkRxYEJyVGRZfltRHHIBV3Z6QldAQA9WHHZEaHluXmhAekJodnJAekUFYGR4RHp1Hnp0YR5tVFFremVUf3JTUXZiY3pCTENoQwVeZGZ1UnZrYXp6SX1zenxTfnYfcVJnSQx9c1l+Y3NWW2xzSX1QdmxHenpJbm56VgB3c0lyf3N/fmF1b3V0Z29ybmV4TG9ne2Z0Zh52UGp7dWxqQkBSY2hYd2RWXGxnHldUZB5+U2BWWHRlH25wZ3hMUmBFbn5lH1wGZh8FV2QeRARkH0x3Z3tlU2p4cm5jawVVdWh6V2NrZlRma3J1ZR5yV2B8DVJne35xYB9EZGN/fldnHkRXZHt6cWBGRFRkHwV+ZB9ienVoWGJgVlhlZR5bb2V4en51H1B1akVXbGNFQFdnQkB3ZxxifmprfmJhHQVzZx9xdWUfUFRzeGJxY0IEVGVoBUBje35XUGhib2prQHFRQkBSZB9EYmAfWGJjaQVvah9Qd2NpfldqaHJuZENQZ3VCBWZqfFhiakVmcHp7BWBgQFBhYGgNd2EeUAVgSX5uZUJAemVFfVBmeEBzY2tAb2QebnpnbEB0YR9Qc2QfW29leHp+dR9QdWpFV2xqa3IHYx5ucWt7Ym5qZlhiY3gFYGd4UEJlHnpVZUZcbGceV1RkHn5TYFZYV2YeUE9lHwVsah5yUmd4fl5lQmJzYEVAcWhFdnRle25xUGhib2prQHF1QgVmanxYYmpFZnB6e25gYHZufmAfQFRqe1BiZENyV2RoBWZgQn5UY2h6WmUeRGRkaHpVUXhccWprBFdqew1SdXh6U2BrYVJmeEBvamtAbmNCBVVUeFxOY3hEdWQfbndrHn5SZ3hEU1BoYm9qa0Bxa295dWUfUFRzeGJxY0IEVGdCUHtjeHJeYR52dWUedmJgfAF0ZHgMUmYeZnFjf3JuakJ6V2toAW5jH3FsYB8FZGBsDUVleHYDentmTFF7U3NqQ0QAVhx6Z1BvV3ljfFdzc291CxACFgILAAIHEBQWd3NvdXdzb3V3c291d3NvdXdzb3V3c291FB4MBxgGGhYMAgAEAAUbBwEHGAMOBBoFAAUbABoQHhoDAAwOFHB/CQsQAhYBAgAFDxAUFlRgbERhA0JiZ15rY1MCbGxxcG93cF9oVmdzTXFzc2d1cVVkThQeDAwBHB0NFAgMYGRwfmVaVkxlW3xIYlpCTXJ3VUh2QXNrdV5odmZzC0pjWEJibGB0T2Nea2N2ZEVjc15wfmVaVkxlWFp6YnBdeXB3e21yWlZrYAcDT25ddGtyd1VLc3RgfGVgWnRlcUJ8Zl1oaXJRRW9zXlV7Y11GSmFgQk9yUUVoc3dFT3FwXkphXWhpclFFanxne298UUprbAZCYmN+eHZsXXBLcXRjaXVdChMWSw==',
        'origin': 'https://chatgpt.com',
        'priority': 'u=1, i',
        'referer': 'https://chatgpt.com/c/697b0483-5aac-8325-844f-1a03001bd4b0',
        'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
        'x-conduit-token': token,
        'x-oai-turn-trace-id': '466212ce-5baf-4477-b929-03bb4ecd048a',
        # 'cookie': 'oai-did=bed381d1-dbfa-43c5-870b-6a1ed45c5177; _ga=GA1.1.414653376.1768045124; oai-hlib=true; _account_is_fedramp=false; oai-nav-state=0; __Host-next-auth.csrf-token=1ef796978f0e8319dc3b7db5627c5f6b01bd62312f3dfa1fa78795fa3fa5fcfc%7C913c9ff01c9cdd8d4d5ca6ae31c91c21c64cc50aeddebfe2e91791a696a42504; __Secure-next-auth.callback-url=https%3A%2F%2Fchatgpt.com; __Secure-next-auth.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..ZNchp3v1OzVo_jFV.1yXBa59_FJWyhHWvgsk_vcpUAWo0zcURUCdg4zOGwvl2hvBU8g-u5PXCd1drMRKFPbduCtwTk4m2fkevPL-M3nd56hyrrDjUGiMp8BRxnh4OTyh4Pp6Djrcust7YJW3Esre4ieVly56Vltzou8JEal-a4WfIz6wGk1ApT1FDt-eBQECBziM-Gl_lLOqI4CrdfE37WcMzViyIqqhe4vXoaxRHLbG5x9oF6DZuwPNpg2RFrRHyeVO6N_aOf89iZNWMq3sWKfSiZO1pXeUzaOpOwF-jtrtHdIfJnX-Xy9SV77zjlN7hmtAYrEcJap7f3gBWt8kzmHR-x1PsoSTR4VGaXlChypfVJYuN8TfDIgEd7RSAIOeUYdoSs6kNUqWBn0DfSCb4Urzfma0CP-Ur8-a0Ga-fselUJysvd60y-LJfRjrSQJAfXmkQGg8J0uNJROXSLLyeircWwPFQPl5EbY_utwq6CQISblE2Bs3Ip1ZYN6mBh7hRPsYAChrXEjcINSvdlTnqWytm-8xjEEec2iu9b67FY4YkfjmEIpnOicVKnqr17wom85tyVhCGJcUk2Mz9O--H3yl3OxpWJiNkPpiGotR5IWKg712R7VYdqj7SYLQwiUcS7QM9fQLesrqxM93455YC8yxx2ImekzYGraaQejPR4zlgxsWJmeu7SUvBDwgfGjn69ODt-uejnr4F26ab9ub-Oqt3Xa7Ehah_qyAa8o3bFqSJyCsWlH80FWjUXoWH_zmGPqNKWuzBm268XKFSf74QmvcntZ-1fIYMUENLZeyqqAzEmi0pnGvUwhnWmBu9gNAl0Cx5CcjdKBFRh5FPfxhuN8LZuNNWUdxkInCIXqvR-mAQL_TIFPc7zWMQJmxmaZOM_t9o5y9NLNtqOh3BF00e6BoZjrRprkEOqFdBE6yuAyLf3WD-dTF9bCy60_BEdmpTq_-zeCecx4W7v18fnUyOPfOqr9r0O_hMACgOSMhd_OyRvLYCoCvnmZwvmD4KT9-UtgmQu4peuk7CocE9A1KCgdEBubzDZkDSrKq1dEZVo4YYvAaxLVNPdbM3vetKCUD61P16QCOSVP_eWhaZ4QrO3a4Xlt3mIEAeEM-7lhw_bRlTdfqOj2NWh0ZAl2yFXfNBSRzc2P-6ZUIVbdMT4EY_1O1cJcud_JeymwIa7_fKz6TrSpQW1sQNrL4ZoNAP2tyzNOVZ8UW6kpBj2rb6MIqrI7vbkNGhpWOC8JkSTuuxqRtstfUraCbMmLNghkCM_ANfpsUMdH397TAuFNfIf7JnZDslEJlcOQcAFNOUowwMoxegt7vYJBtWmbSUlX8wzsU8Cs_lief8MjffegAwG3PK99VeFfZHG2Pxz4WmY7fU7__5lHu1Hbqw-9e0Efy7T-adR706eGbhxAkwpWdXuLc5ISe6jV-wuRAnP_Lyjv1ZVRnhAmrhVUc4rGuAE4jQCyYYwWZt4qAEfGxgBceNuRMTae2muKWB5jXiyBlAHfmqAauma1Ez3BVrn7K19xymIdIZuo1vHFVMQxolQkWT9RM09nbGLAo26Ks_SWcOllZLFKwR-5xXpRKMP_BxaDYxv_-zTJJuEYZhnKAwLrVByDX3eaYKsaw2mn5WcW22qXp5ybykX5kHx8inDuifYe2XS8Z1kWmiyF9EjUZvoqaLwKMDjrVYdz4-ecLW1q3WsbL7Gr0FwMx1phSkDR8t0Skv7tx6yHymD_iSNwMf4CZz6eW_WyZdHE3T2F35HUhJQIk0Kq8HFhqL6TL7rizop2dOUlVHqCWI6bhdt_dQrhUgE1RMkg4wEDfWlC6qoJWo0aYY1t1i6rZay2pmRRWLPG9cnxo9Sols43LYvNr3kBDjjt25sPBHLB0kjuuY4e4vmNTvkkSzueUKauRC8a34QfDjJBHK22qoPPeJQLD1Y9ZJMdZWwcPLLtCwu8n9trdzt0V_ZAB046anlMwfJ22hWRzLvTegtH0Feah28L_pAgnDWYaU27_9udUCdEoDQbGQaXW8LHsnUepPP_wP2o0ZWvoObEh7_lwA0Dhybgb1fKvcjLm6fswIUl6LmdcPEZJ6GRsmGOdpxGBGl9hG1HWQ3oFgHsJyiRiGfkzv7-IwNI88JFkyhH8rw--GQK4SZIZ9mz0Y1A8GITEtxPIPoqfR0cZJJNrI7Prq-4FbHvZPKDA4wKDq8rCMPmCMtm-d2_cMfq1LBEL-g9FaaTNE5mqMrae88wgNFVLdagETQyenfflE-eMmx_YB7mQ5vP81IW_Ae3wmiHueHHr1U1V7JfDwuFBV6u25s0zxFAc5hzyyZyDmWI0LJhCuxYmtaWsqZm8kRHnptBwiB33fUBWxdW3bsk-oYI65P68EBVV8iBsIp6u3qtpRUn-jgJstlvnXaU597fdzQ2KwSPb9GIhIUycejvcZCBoVd21iKAkIJp0HVRCRjD1YP_-w2wK6nuTDfxWXTM08rBplW4TAyo_i7D6ZU4CEph9BsTNfRpyRxJSty1-Ucd_k9vWNn9F1oFIAqmp_RYPWrwwVtOaEGhU2lXMTYjQeva8IaIIiJxteoNIDkMqUiltSXej1Gnk-pO4tHok6JhahBfUrguDRncHdELutSqXlLxRhgVZD79ngjASP9Gq_YhzlislhPA7NksOVcvSmwJV6OpfS99ED4DT0ipiNoEPE8X6BYyLLLIbajTG8XnPyRG76zfWJ_lvESxk4ectoBrgUSSSqpKuTJCuIG6PT4xXRt-9kDge2V1SU9kd_ffgsThSJ178eE5swtMjoswUWqw0zIV-jtFgpIOseBWw1D7HhcWhDNH1ks_MAIr_Y4ByjdBxC-9HF-EC4WldPN5-m-w-_jpl9WzkU0qI41jux8jG2mskOL4kv5enbyvcrQk1H4hiXbojV6z9tzq2Alp-Rv9hhkp5Jl9ErBYi2AXi573tu86a2KWe1BjMvBcNmtxc0KXCQ44O4tDp9cjQWQcz_BbDtJvsLe41MqsR06T5r564uxThP_y5HZf5eFmq50ct1Ek_y6Rm69UW-KSSTY1MS_ZhlQqYBvhKO9RcfBFVN-g9gai8sWFGt7c9stDYYunBv7co-EQvH971OSZc0uiQ2ihec6_5l2CCsfsGt7kmdK4XuWeB2Wth2rPJAX3PcyuBceofpgwV3lGMDcFHpD4qho1yI15ygh--HhxiD1C5O2d4u--W5bxNyx8p82uNgR0UFiGYzqR7FOg5gqKPKd7vDc_u6cY6aKK9YrhYA6_XjUT8OROerNz-BTkkbIVzv1wxcYceex6hca8dfjV-sUzQ0ttMDD-bNuAbXABUKj4vVIDF617xtpfA8ADuGkdbjDO_jrl261lXxxdkPu6EJGzJw8O9tsyXmmFfXrxe3IL_NnMAzv1rxN-XxH1PVz7k8NE1K4EdlH15oeoGxcT8MwI1SoFP6xXsXyFsIgyBeW-zZnB5vS7nlRGsy-jduOIUBmlQOVfQeCCplcGbxtgCoHTB15JIB5hLsfv_98YgpvuJQuQwhvEVb_G9YTIimMYyShh8H9cj32A9gFnreCjyIoC0scIYjVA8j46CM4rdGkwTk0k1UgrSzP3bgC5R9Q1O7Ga_6ydCBPyZvqL7idd4SGQTc45o-Nven9utaiwVnwbKVuW5byLEt9ooJBnxcZGFS-wlI9bzN-fyGj0hOZZj0hqrc.KDY0IGIo1Gxpa5oNsxaxFg; __cf_bm=QnOMgWgkWmguxdn2pgIKthiHi6BDQZ.RoTqPNT5bk48-1769669653-1.0.1.1-0WrR.BZQcrCqPzqQcqPJsM8p3ApZjfv010vKRimwK0JXPj3oevZpIqQWY.USL7uR0OBLmX168Z01_mojVuCRGjOwV2UjA6Hx98pb5izaJAI; __cflb=0H28vzvP5FJafnkHxihKb44bdy6fTJD3Pt5hM927szj; _cfuvid=_cx7eAewzyHLXIolRd2ZcoHTigKglp_PLvZoBRSZvGI-1769669653674-0.0.1.1-604800000; cf_clearance=0tAEh9f9iGQPDGVGvma5fLylvwtsyUigBA5EfFjcp64-1769669663-1.2.1.1-s.HIRwCGGJRTxGl.dtiI_wv5M9VT8KFpxHSzxt6G.j0ZwPSmfsPr2BMlyJk2YTCHSLJQ.CCF2DLoL8E6F3kD8wCDwHOKlHhJOb5MTwHqN2E9o7tWZUbFbR303LVE6AU4TA6i5DiM3xcbwhKiSdR70pt2a2mFuxBUYiBAXbn80gjEMuLmLZPrJOQ0YzCTO1tNNXMcB9o9eoO1kyuSImlsBoiQ3ann8H3xymuWPly5W2o; oai-client-auth-info=%7B%22user%22%3A%7B%22name%22%3A%22Tima%20First%22%2C%22email%22%3A%22topovii8888%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fpr.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1769666079178%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D; oai-gn=; oai-hm=ON_YOUR_MIND%20%7C%20GOOD_TO_SEE_YOU; _uasid="Z0FBQUFBQnBld1NHODFhNVk3cU1zWXV1bzJkMGxMQ2JYdW80OW5IMGU4eGFGdkZtSUVIQ01LYWppNUNBU0VMdnU3WFlRclhxa0VPTE5yenRlMC1zYkFRSl80VjlGX0tKcGpzMV9VcWN3WTExdDVrU2R5c1FrNjRDOTBHb1pkeml6eGszQ2V0QTlNVE5yUmNhQ3JxbEtuNVhuVmVUUXd2WkIwM2hlaENHQmRHeFhMbTJ1WnA4M2lPNkgwblp3cGRoQzhNNTF3RnFrMjJDU01DXy1HY01SRDNxbHNaczNsV3ZhSXhJX2twaW1lT0VwbUNWY2FDU0M1V3I5eGlfQnZHcGJmTlRhQzVjQVZ0WGY3VXBZQXB1MjdQQlpnMnJkVHVRX3lsRm9kamQ1QkcyWG1WRktZSkRVd3dyODhVNTVSeEZmV2xDbDROTVl3cFpQeDM0cTlOY1pXZUdQOXRGaXUxRGlRPT0="; _umsid="Z0FBQUFBQnBld1NHRHR0enctdnVmZ2UxeFVRMHFsNDc3YTA4MEdwTWVvQmhxMnBBWHRIeE1SSGJ4UmF0c3EzVEJKc21MakRPajlrMzBHczRYTE5hZWp6azVXYnN2Z0xvQXNmUDdHSENTWFZxSEpOTW45dU80eEs3emNzSjdadVROdFJNRkpTcEhSTmRrcUpDZGdxcWlIV3VlZFBPdVNhcl9zWUpCVVVGREh2NzZDSWlGRE5jVTdMRHJuOVFtdUFJSXNPakE3cUYtR1UyYVNvbDNLU1RBaFQ5dUxaUlRtdG9wYmFma1dMclA4eVFiSDV2Mm5wS1JGTT0="; oai-sc=0gAAAAABpewSHJbu3gEPAbHZX2UCVw9CoHCSsNH9c95-MiU4NOHiYnwJn-_-rpy5OZIFG_AJli9G_AqAbtLUmPFNaPh6TCscBS8PbAgw0bidV5RCXawTCVU074ditMB9yvKSC_LFhV1qnOsJd-sChV64h9HrZAzE8SMnAGyncn1vfYRxYSuyy_bGZo5V43tG9vrzwLKo0gndwUYubrG6SEFC6Drso-as2mLztflS98vFtOawYGoEe-WQ; _ga_9SHBSK2D9J=GS2.1.s1769666076$o8$g1$t1769666177$j60$l0$h0; _dd_s=aid=56785c82-0c44-4651-a04a-b253a3a84c14&rum=0&expire=1769667093062&logs=1&id=d79e02ff-b93c-4e28-9164-61c5a88d1498&created=1769666072653',
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
