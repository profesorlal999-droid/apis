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
NVIDIA_API_KEY = "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"

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


# Добавить новые схемы для POST запросов
class GPTRequest(BaseModel):
    key: str
    model: str
    prompt: str = "Test prompt"

class GeminiRequest(BaseModel):
    key: str
    prompt: str = "Hello"

class ImageGenRequest(BaseModel):
    key: str
    prompt: str

class AgentRequest(BaseModel):
    key: str
    prompt: str = "Hello"
    stream: Optional[bool] = False
class OpenAIRequest(BaseModel):
    key: str
    prompt: str = "Hello"



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
class QwenRequest(BaseModel):
    key: str
    prompt: str = "Hello"
# --- УТИЛИТЫ ---






# --- GEMINI CONFIGURATION ---
GEMINI_AT_TOKEN = "AJvLN6NKk4QhHMw9zGozPcogazJy%3A1772682621350" # Из твоего кода

INITIAL_COOKIES_DICT = {
    '_gcl_au': '1.1.321804501.1769867681',
    '_ga': 'GA1.1.170156978.1769867682',
    'NID': '529=YMyke9XxrMc2q92HvlthDqQk_M6zUoDdXePyjqQwgrfJ813bgvddrd4vR791mao2N8ZmCRv37-XCUe9SZPQCT6OpWSURDv8xrqZfQxueunEBCZpwsHIiNJU_J2uZsALN5iTxHPpYFAJUfNPnXFtzWt02g9Al4F3_Yh6duVXiTSw7l5FXI9uCR21LsCbwQTqkptKqCSXVrFUZCLv_P7Jv0i86C1aBznt7ZSaqs-z-oPezfPyhQ4LE1NTypLIzmr1bZY9Rj67Kv_FOf6mHU461rxgKPw570zcWo5N9FOIBkKsiypzV_WSNFJRMl3VjQjWCZxZ7D-ehrFkKlsZxYS6wqB92B7Jvaj881i70HjXmscDyr8tQggZWdosBPWjuPd6IobuO3245qhH4qBOIJM8AQR2Wgja77pvRCBUQ1lO1FRlQ4tlo7IlNqWFPsuSD1q09SWXPOFrbOkuAzE65OdKU6DEkZkLfm-1fMSE9w4FKmVwBe-R_3y5FdPiYC3U6Qe9KQkmp81aakawXRWEayJG7Zq2gWi-VZLh4TjgebgXkg5Kg4wlQT7Nv-k4OIaALbfeg90uazMyxKhpHUoNeiEJHqUVqlzsxV3_e7Y7-TUYjsPjLRPJBQjlaiZnYjzGZcQhTIPOjRxtIYBc1EcjR9eYJ5AZo',
    'SID': 'g.a0007QjXi3sWVcDNUAfFZjdikxo1in0VE8DmBrUJ_H2sxrlQLKRSjFug5GCzogtfFZsIiZ-jugACgYKAdISARESFQHGX2MikOvQcZE9vdcQZOZl9Bv_5hoVAUF8yKpjvHAPnDvU5UJXjBECUooZ0076',
    '__Secure-1PSID': 'g.a0007QjXi3sWVcDNUAfFZjdikxo1in0VE8DmBrUJ_H2sxrlQLKRS1uoprkBUZ6L9W3tu5GcOjAACgYKAWoSARESFQHGX2MiKOQ4wdySAGNOHAxBVQ6OkRoVAUF8yKpXIfDRh5QtdRimBeFjD7F30076',
    '__Secure-3PSID': 'g.a0007QjXi3sWVcDNUAfFZjdikxo1in0VE8DmBrUJ_H2sxrlQLKRSBwxqp8mMvveu6rtU4d2kFAACgYKAb0SARESFQHGX2MiAyGmOzhfSypUEV9kIWY0yBoVAUF8yKovMpzbvIXhGdLaufcDGvZ30076',
    'HSID': 'APNLcQoB5WDCwGJCL',
    'SSID': 'AP6OGrifHiSMuXncN',
    'APISID': '_AzEezhk0kiiMVZG/ApiX3JUy57Ku1kVav',
    'SAPISID': 'dKxntdKtLzpTLaW4/AncyYY85xgNE-NnkE',
    '__Secure-1PAPISID': 'dKxntdKtLzpTLaW4/AncyYY85xgNE-NnkE',
    '__Secure-3PAPISID': 'dKxntdKtLzpTLaW4/AncyYY85xgNE-NnkE',
    '_ga_BF8Q35BMLM': 'GS2.1.s1772678990$o6$g1$t1772679055$j60$l0$h0',
    '_ga_WC57KJ50ZZ': 'GS2.1.s1772678987$o5$g1$t1772679061$j53$l0$h0',
    'SIDCC': 'AKEyXzVbrbu2fABTL2Nej5Y3gOuWREDs_pzpw8Hcs-0a78RtInmLrz4wxd4EittHpGq9-Zpw',
    '__Secure-1PSIDCC': 'AKEyXzXBfX5LH9eTOlo6NuiYK2z4ga8jGUss51gsbeeQuROEJNsBVWq-DODdUMfEFT9l2jpFFA',
    '__Secure-3PSIDCC': 'AKEyXzVaajFDbcraqZUQdq7f1oAh5Q6l7vsOeZdS7zkzyuX0jTeUMUdGbZxY2kTS87h673dm',
}

GEMINI_HEADERS = {
    'accept': '*/*',
    'accept-language': 'ru,en;q=0.9,en-GB;q=0.8,en-US;q=0.7',
    'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
    'origin': 'https://gemini.google.com',
    'priority': 'u=1, i',
    'referer': 'https://gemini.google.com/',
    'sec-ch-ua': '"Not:A-Brand";v="99", "Chromium";v="145", "Google Chrome";v="145"',
    'sec-ch-ua-arch': '"x86"',
    'sec-ch-ua-bitness': '"64"',
    'sec-ch-ua-form-factors': '"Desktop"',
    'sec-ch-ua-full-version': '"145.0.7632.117"',
    'sec-ch-ua-full-version-list': '"Not:A-Brand";v="99.0.0.0", "Chromium";v="145.0.7632.117", "Google Chrome";v="145.0.7632.117"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-model': '""',
    'sec-ch-ua-platform': '"Windows"',
    'sec-ch-ua-platform-version': '"10.0.0"',
    'sec-ch-ua-wow64': '?0',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
    'x-browser-channel': 'stable',
    'x-browser-copyright': 'Copyright 2025 Google LLC. All rights reserved.',
    'x-browser-validation': 'cr7cHVG+SNcWXljUH9Du0os0RUg=',
    'x-browser-year': '1969',
    'x-goog-ext-525001261-jspb': '[1,null,null,null,"5bf011840784117a",null,null,0,[4],null,null,1]',
    'x-goog-ext-525005358-jspb': '["FD7BC92F-87CF-4E69-BCF3-C29FCCC9BE65",1]',
    'x-goog-ext-73010989-jspb': '[0]',
    'x-goog-ext-73010990-jspb': '[0]',
    'x-same-domain': '1',
    # 'cookie': '_gcl_au=1.1.321804501.1769867681; _ga=GA1.1.170156978.1769867682; NID=529=YMyke9XxrMc2q92HvlthDqQk_M6zUoDdXePyjqQwgrfJ813bgvddrd4vR791mao2N8ZmCRv37-XCUe9SZPQCT6OpWSURDv8xrqZfQxueunEBCZpwsHIiNJU_J2uZsALN5iTxHPpYFAJUfNPnXFtzWt02g9Al4F3_Yh6duVXiTSw7l5FXI9uCR21LsCbwQTqkptKqCSXVrFUZCLv_P7Jv0i86C1aBznt7ZSaqs-z-oPezfPyhQ4LE1NTypLIzmr1bZY9Rj67Kv_FOf6mHU461rxgKPw570zcWo5N9FOIBkKsiypzV_WSNFJRMl3VjQjWCZxZ7D-ehrFkKlsZxYS6wqB92B7Jvaj881i70HjXmscDyr8tQggZWdosBPWjuPd6IobuO3245qhH4qBOIJM8AQR2Wgja77pvRCBUQ1lO1FRlQ4tlo7IlNqWFPsuSD1q09SWXPOFrbOkuAzE65OdKU6DEkZkLfm-1fMSE9w4FKmVwBe-R_3y5FdPiYC3U6Qe9KQkmp81aakawXRWEayJG7Zq2gWi-VZLh4TjgebgXkg5Kg4wlQT7Nv-k4OIaALbfeg90uazMyxKhpHUoNeiEJHqUVqlzsxV3_e7Y7-TUYjsPjLRPJBQjlaiZnYjzGZcQhTIPOjRxtIYBc1EcjR9eYJ5AZo; SID=g.a0007QjXi3sWVcDNUAfFZjdikxo1in0VE8DmBrUJ_H2sxrlQLKRSjFug5GCzogtfFZsIiZ-jugACgYKAdISARESFQHGX2MikOvQcZE9vdcQZOZl9Bv_5hoVAUF8yKpjvHAPnDvU5UJXjBECUooZ0076; __Secure-1PSID=g.a0007QjXi3sWVcDNUAfFZjdikxo1in0VE8DmBrUJ_H2sxrlQLKRS1uoprkBUZ6L9W3tu5GcOjAACgYKAWoSARESFQHGX2MiKOQ4wdySAGNOHAxBVQ6OkRoVAUF8yKpXIfDRh5QtdRimBeFjD7F30076; __Secure-3PSID=g.a0007QjXi3sWVcDNUAfFZjdikxo1in0VE8DmBrUJ_H2sxrlQLKRSBwxqp8mMvveu6rtU4d2kFAACgYKAb0SARESFQHGX2MiAyGmOzhfSypUEV9kIWY0yBoVAUF8yKovMpzbvIXhGdLaufcDGvZ30076; HSID=APNLcQoB5WDCwGJCL; SSID=AP6OGrifHiSMuXncN; APISID=_AzEezhk0kiiMVZG/ApiX3JUy57Ku1kVav; SAPISID=dKxntdKtLzpTLaW4/AncyYY85xgNE-NnkE; __Secure-1PAPISID=dKxntdKtLzpTLaW4/AncyYY85xgNE-NnkE; __Secure-3PAPISID=dKxntdKtLzpTLaW4/AncyYY85xgNE-NnkE; _ga_BF8Q35BMLM=GS2.1.s1772678990$o6$g1$t1772679055$j60$l0$h0; _ga_WC57KJ50ZZ=GS2.1.s1772678987$o5$g1$t1772679061$j53$l0$h0; SIDCC=AKEyXzVbrbu2fABTL2Nej5Y3gOuWREDs_pzpw8Hcs-0a78RtInmLrz4wxd4EittHpGq9-Zpw; __Secure-1PSIDCC=AKEyXzXBfX5LH9eTOlo6NuiYK2z4ga8jGUss51gsbeeQuROEJNsBVWq-DODdUMfEFT9l2jpFFA; __Secure-3PSIDCC=AKEyXzVaajFDbcraqZUQdq7f1oAh5Q6l7vsOeZdS7zkzyuX0jTeUMUdGbZxY2kTS87h673dm',
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
        'bl': 'boq_assistant-bard-web-server_20260303.06_p0',
        'f.sid': '3738742133910157453',
        'hl': 'ru',
        '_reqid': '3731855',
        'rt': 'c',
    }

    try:
        response = session.post(
            'https://gemini.google.com/u/1/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate',
            params=params,
            data=data,
            timeout=3000
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
    
    # ИСПРАВЛЕНИЕ: Проверяем db_cookie.value и добавляем try-except на случай битых данных
    if db_cookie and db_cookie.value:
        try:
            cookies = pickle.loads(db_cookie.value)
        except Exception:
            # Если данные повреждены или не распикливаются, берем исходные
            cookies = INITIAL_COOKIES_DICT
    else:
        cookies = INITIAL_COOKIES_DICT

    # 2. Выполняем запрос синхронно в пуле потоков
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
IMAGEN_AT_TOKEN = "AJvLN6PwnlLftd3PUqtHb4kqOIc6%3A1772682833266"
IMAGEN_F_SID = "4101729569074572286"
IMAGEN_BL_SERVER = "boq_assistant-bard-web-server_20260303.06_p0"

IMAGEN_INITIAL_COOKIES = {
    '_gcl_au': '1.1.2010919010.1769724179',
    '_ga': 'GA1.1.65427254.1769724180',
    'SID': 'g.a0006gjhbWH_l4FkqlkWWXGcJmWWiRS8C-CrFbYRNht80Wce4p_cCYU9fqyx9ccKrR6H8C5dQgACgYKAWUSARQSFQHGX2MigNNHzj8YCYMgyZsJf6gk7BoVAUF8yKpKybfXD3O7KsN3Q9ITvE_40076',
    '__Secure-1PSID': 'g.a0006gjhbWH_l4FkqlkWWXGcJmWWiRS8C-CrFbYRNht80Wce4p_c7o2JRzDRELm_KCAbmgRfkwACgYKASMSARQSFQHGX2MilJEYzHDsTHEsCGpgy4XLsBoVAUF8yKp2IRt1RN3ddl3BxgtErdp20076',
    '__Secure-3PSID': 'g.a0006gjhbWH_l4FkqlkWWXGcJmWWiRS8C-CrFbYRNht80Wce4p_cIxHLU7GdHIrVq4IEC1jkfQACgYKAV8SARQSFQHGX2MiGl4ulyWiHLwgwgMhQW57bxoVAUF8yKqLfncTGhg-M70087-ULeKI0076',
    'HSID': 'A1_kmO0yoCn9IdfeM',
    'SSID': 'AurqrD4LQA8HGRgKr',
    'APISID': 'SKABWoA6qrqb75kG/A7t8EMxyaTIXP8Unu',
    'SAPISID': '53LMi2vZ6qBSVK42/AZUUUA2lUct1eZvWw',
    '__Secure-1PAPISID': '53LMi2vZ6qBSVK42/AZUUUA2lUct1eZvWw',
    '__Secure-3PAPISID': '53LMi2vZ6qBSVK42/AZUUUA2lUct1eZvWw',
    'NID': '529=zHVzTO48EztxQD1KNFfaDnJKHv4xA4rArFWe-fPHIbf0KCJH2JnUr51brjhgnUdR7bHy3NIZoA-hKqWRZtVMW94AjQQCUmrYyRgCz0WWzrB-XX6Oi0URyFdv5VnsIQU8TgXd4Ck9-8ILiwDMCS4sNuXk5unZdhXozxqkT6J61wmUFCY2UsK2_tOlT5bLkvdUXspm2rgMZdx0c3TYlgJ5GnrUZy1tHq_FZuNoCt8i6OACBDiEKJz5gce2izeUkPif0cSvzGnMbUZr4xU8Tdn8ENjkTLfqLQ8SvN0kgyoYAwojjNRlvRemVsip2e372rLxLTFXeaTy81i2xTsLiqpxHFTcFNLOcJ1X8Xxhw0vEWrra5vDMYAE0DzVBFIdiNaOXxVlaQ_4LxIlI2sTFTR64K_VrXsDvtM_lhIuaFhPMev68qNoqWOvdCj96FCCvYFmCJss4bMZf_aPHdJ4R6VLdYMq6eyetFsi3JyS4gZHjiffwdwb2SGfPIit-M4kYTFxJQ-MghQIgOA8E_OA-K64YkoxtffQcWncjCE3s0pxr6cUPgxFsw0UpYbnNBsQX311VJz413hDvTVB95bw7HTLEiiN_QBnFG9mkglgWJIkpoyIFfxwpqTiyQThpAdhjyBukuUIyr1g79r1vTAa11CLYn0N8XUF-OitVGm6d',
    '_ga_BF8Q35BMLM': 'GS2.1.s1772679268$o4$g0$t1772679268$j60$l0$h0',
    '__Secure-1PSIDTS': 'sidts-CjEBBj1CYoTl-H50dHa8cD9fYEgaArv8X8Lwhpwue2kExK8X-DdPFkVfLjbxaiEJRdRTEAA',
    '__Secure-3PSIDTS': 'sidts-CjEBBj1CYoTl-H50dHa8cD9fYEgaArv8X8Lwhpwue2kExK8X-DdPFkVfLjbxaiEJRdRTEAA',
    '_ga_WC57KJ50ZZ': 'GS2.1.s1772679267$o4$g0$t1772679275$j52$l0$h0',
    'SIDCC': 'AKEyXzVRT4yEIyJQc2qx4cX6V6an3M-9YxxGB9fly4LKMcMVDbAQziBHp_Pp0HELSIG_4YVf',
    '__Secure-1PSIDCC': 'AKEyXzWco6SpQoiyHtFrUABpStJTFpUCq24XdsP8yqRMXPn8fJA-X7lmMsWSsxG5hNOMba0a',
    '__Secure-3PSIDCC': 'AKEyXzVCFIYfcR0nfUyj7HtUl6DRKy96yiWqotYGuaJMPGdPGpGgq3GuTWktr3WVrk_Wsksx',
}

IMAGEN_HEADERS = {
    'accept': '*/*',
    'accept-language': 'ru,en;q=0.9',
    'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
    'origin': 'https://gemini.google.com',
    'priority': 'u=1, i',
    'referer': 'https://gemini.google.com/',
    'sec-ch-ua': '"Not_A Brand";v="99", "Chromium";v="142"',
    'sec-ch-ua-arch': '"x86"',
    'sec-ch-ua-bitness': '"64"',
    'sec-ch-ua-form-factors': '"Desktop"',
    'sec-ch-ua-full-version': '"142.0.7444.265"',
    'sec-ch-ua-full-version-list': '"Not_A Brand";v="99.0.0.0", "Chromium";v="142.0.7444.693"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-model': '""',
    'sec-ch-ua-platform': '"Windows"',
    'sec-ch-ua-platform-version': '"10.0.0"',
    'sec-ch-ua-wow64': '?0',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'x-goog-ext-525001261-jspb': '[1,null,null,null,"5bf011840784117a",null,null,0,[4],null,null,1]',
    'x-goog-ext-525005358-jspb': '["E5EF1229-E0C5-4F72-AB8A-6C60ED824E66",1]',
    'x-goog-ext-73010989-jspb': '[0]',
    'x-goog-ext-73010990-jspb': '[0]',
    'x-same-domain': '1',
    # 'cookie': '_gcl_au=1.1.2010919010.1769724179; _ga=GA1.1.65427254.1769724180; SID=g.a0006gjhbWH_l4FkqlkWWXGcJmWWiRS8C-CrFbYRNht80Wce4p_cCYU9fqyx9ccKrR6H8C5dQgACgYKAWUSARQSFQHGX2MigNNHzj8YCYMgyZsJf6gk7BoVAUF8yKpKybfXD3O7KsN3Q9ITvE_40076; __Secure-1PSID=g.a0006gjhbWH_l4FkqlkWWXGcJmWWiRS8C-CrFbYRNht80Wce4p_c7o2JRzDRELm_KCAbmgRfkwACgYKASMSARQSFQHGX2MilJEYzHDsTHEsCGpgy4XLsBoVAUF8yKp2IRt1RN3ddl3BxgtErdp20076; __Secure-3PSID=g.a0006gjhbWH_l4FkqlkWWXGcJmWWiRS8C-CrFbYRNht80Wce4p_cIxHLU7GdHIrVq4IEC1jkfQACgYKAV8SARQSFQHGX2MiGl4ulyWiHLwgwgMhQW57bxoVAUF8yKqLfncTGhg-M70087-ULeKI0076; HSID=A1_kmO0yoCn9IdfeM; SSID=AurqrD4LQA8HGRgKr; APISID=SKABWoA6qrqb75kG/A7t8EMxyaTIXP8Unu; SAPISID=53LMi2vZ6qBSVK42/AZUUUA2lUct1eZvWw; __Secure-1PAPISID=53LMi2vZ6qBSVK42/AZUUUA2lUct1eZvWw; __Secure-3PAPISID=53LMi2vZ6qBSVK42/AZUUUA2lUct1eZvWw; NID=529=zHVzTO48EztxQD1KNFfaDnJKHv4xA4rArFWe-fPHIbf0KCJH2JnUr51brjhgnUdR7bHy3NIZoA-hKqWRZtVMW94AjQQCUmrYyRgCz0WWzrB-XX6Oi0URyFdv5VnsIQU8TgXd4Ck9-8ILiwDMCS4sNuXk5unZdhXozxqkT6J61wmUFCY2UsK2_tOlT5bLkvdUXspm2rgMZdx0c3TYlgJ5GnrUZy1tHq_FZuNoCt8i6OACBDiEKJz5gce2izeUkPif0cSvzGnMbUZr4xU8Tdn8ENjkTLfqLQ8SvN0kgyoYAwojjNRlvRemVsip2e372rLxLTFXeaTy81i2xTsLiqpxHFTcFNLOcJ1X8Xxhw0vEWrra5vDMYAE0DzVBFIdiNaOXxVlaQ_4LxIlI2sTFTR64K_VrXsDvtM_lhIuaFhPMev68qNoqWOvdCj96FCCvYFmCJss4bMZf_aPHdJ4R6VLdYMq6eyetFsi3JyS4gZHjiffwdwb2SGfPIit-M4kYTFxJQ-MghQIgOA8E_OA-K64YkoxtffQcWncjCE3s0pxr6cUPgxFsw0UpYbnNBsQX311VJz413hDvTVB95bw7HTLEiiN_QBnFG9mkglgWJIkpoyIFfxwpqTiyQThpAdhjyBukuUIyr1g79r1vTAa11CLYn0N8XUF-OitVGm6d; _ga_BF8Q35BMLM=GS2.1.s1772679268$o4$g0$t1772679268$j60$l0$h0; __Secure-1PSIDTS=sidts-CjEBBj1CYoTl-H50dHa8cD9fYEgaArv8X8Lwhpwue2kExK8X-DdPFkVfLjbxaiEJRdRTEAA; __Secure-3PSIDTS=sidts-CjEBBj1CYoTl-H50dHa8cD9fYEgaArv8X8Lwhpwue2kExK8X-DdPFkVfLjbxaiEJRdRTEAA; _ga_WC57KJ50ZZ=GS2.1.s1772679267$o4$g0$t1772679275$j52$l0$h0; SIDCC=AKEyXzVRT4yEIyJQc2qx4cX6V6an3M-9YxxGB9fly4LKMcMVDbAQziBHp_Pp0HELSIG_4YVf; __Secure-1PSIDCC=AKEyXzWco6SpQoiyHtFrUABpStJTFpUCq24XdsP8yqRMXPn8fJA-X7lmMsWSsxG5hNOMba0a; __Secure-3PSIDCC=AKEyXzVCFIYfcR0nfUyj7HtUl6DRKy96yiWqotYGuaJMPGdPGpGgq3GuTWktr3WVrk_Wsksx',
}


def _sync_gemini_image_request(prompt: str, cookies: dict):
    """
    Исправленная синхронная функция:
    Использует тот же метод формирования пакета, что и работающий текстовый чат.
    """
    session = requests.Session()
    session.headers.update(IMAGEN_HEADERS)
    session.cookies.update(cookies)
    
    # 1. Формируем запрос так же, как в текстовом чате
    # Добавляем префикс, чтобы триггернуть генерацию картинки
    full_prompt = f"Generate image: {prompt}"
    encoded_message = urllib.parse.quote(full_prompt)
    
    # ВАЖНО: Используем те же магические строки, что и в рабочем текстовом запросе.
    # Google требует этот контекст (!Q0Cl...), иначе возвращает 400.
    data_prefix = 'f.req=%5Bnull%2C%22%5B%5B%5C%22'
    data_suffix = '%5C%22%2C0%2Cnull%2Cnull%2Cnull%2Cnull%2C0%5D%2C%5B%5C%22ru%5C%22%5D%2C%5B%5C%22%5C%22%2C%5C%22%5C%22%2C%5C%22%5C%22%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5C%22%5C%22%5D%2C%5C%22!Q0ClQBjNAAZeabWMfmlCpnSCL5dFYOU7ADQBEArZ1Gc_anREocG2B2DF4zwI3mMyyuxLwB3yyOX-obdTN4OSl3a3tQxZZe63PRBHz5MpAgAAAZBSAAAAH2gBB34AQTHSxFe9uWaOZl9ZAQEUAZFB1PeSf5unoZqC5vOsTi1aMUiGGFfitq8c3XWaxLuoNi8p7lVS2y7a3qFwk_8X05YZmQMsSOzmJgrf0uyeL43D9vsDgTX10K9qD33A6c4jvTo03DSm4r0cy4wGl5inCOqH-PPyy55-U_yq2FJrGDCDV2unoVxdigUOrKDZNUiq81iK_kxQ36QGKL3pm8xbV1Rrf-1s-yk8rT82kecMfkaq_a-ugAWljDoxPz4e7URmws0yXqWHmBwkDrHHjdkoUfX9swDb0rJ_cuPt7oVbTb7ZEWwI6ZPx7Zg_AjM7z1iQSqYouQ5dDu3uiw_mtE3o1E9pnbGPCjUH5UXjaZhOT3wBmgbptjUbeEdKOz3qyZJe0kYJEGGQ1uaLltqrad2xC_4dImSFLP_9fxt5qZxSDOXMkgdrNQBBBTROv_WMyz7YZqkLhFy6UVSAhKx4-uN3tUjp-Q3yogjb6jrnhh2Uw6MFTQU8o_D8evKQgyb2uKokDYes1QRRUagKlCy9-W9RtUGaKGbqHK35ssJ_R6HXLGgYcWwkVyQDolevtmZUCH3hhKhZyHbBjCXHbs8uvPkd1tmPmbewcdrmBG-_Dfj3jodamuqVD4eVX0ltvi0UjbD6hxTUGdZBmQWsJnmIL4boG4jnGdz-qBUUtir5ycfP3P65QqaOQdCxCio5HiYFG4DAPkiwXZDUG_5KO68J4cph1zK9bOqMjlIvWwiKqIgIeEpdke8UJrq0aZ6RHfXu0I5lquyFiqlPwlmBMEo9DvNWI0DoFtWNPfTFHXrvCVyHL8E61k3Ti8ops_mj75HR670AzA160nowkWNHP6HS3QMZEJSXb-ybbfypWE0JnT1NGOMQdieDXq5-xVfg888XvDZov1qaLaNzU8XfCtmrEOjeTyV27yLFyNzN0fQfEK6Zq4_svnOsZer37EQPKMKi10FBkawsKgSb3bn74i3DTZOEc0M8zDZUcunyPjoCAvcmjsy_JvMQFNaj8y4lbSQ_Wf5lFJD79tE8jYdJuGUzKsyYKfP86W5t7guwQyia53Y2n0OFiGafu0tPhhEaIewYHu8UX0K96C1nCR4pWLkumW3490j7yN5iuOwe0VZVgeL3th_j8tiloh6kuKSbDys4utjQ57JU_Gd6H7HDYDzMptO8pFU%5C%22%2C%5C%2262806c58061d7d812a36fc661042319b%5C%22%2Cnull%2C%5B0%5D%2C1%2Cnull%2Cnull%2C1%2C0%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B%5B0%5D%5D%2C0%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C1%2Cnull%2Cnull%2C%5B4%5D%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B1%5D%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C0%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5C%221670C968-EBC3-4DC2-953A-E02A6ADDC428%5C%22%2Cnull%2C%5B%5D%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B%5D%2Cnull%2C1%5D%22%5D'
    
    # Собираем сырую строку данных, как в текстовом чате
    data = f"{data_prefix}{encoded_message}{data_suffix}&at={IMAGEN_AT_TOKEN}&"

    req_id = int(random.random() * 10000000)
    
    params = {
        'bl': IMAGEN_BL_SERVER,
        'f.sid': IMAGEN_F_SID,
        'hl': 'ru',
        '_reqid': str(req_id),
        'rt': 'c',
    }

    try:
        # 2. Отправляем запрос
        response = session.post(
            'https://gemini.google.com/u/1/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate',
            params=params,
            data=data, # Отправляем как строку (body), а не как словарь (form-data)
            timeout=3000
        )
        
        if response.status_code != 200:
            return {"error": f"Gemini Error: {response.status_code}"}
            
        raw_response = response.text
        
        # 3. Поиск ссылки
        pattern = r'https://lh3\.googleusercontent\.com/gg-dl/[^"]+'
        found_urls = re.findall(pattern, raw_response)
        
        if not found_urls:
            return {"error": "No image URL found. The prompt might be refused by safety filters."}
            
        image_url = found_urls[0]
        image_url = image_url.replace('\\', '')
        
        # 4. Скачивание
        img_resp = session.get(image_url, timeout=30)
        
        if img_resp.status_code == 200:
            return {
                "image_data": img_resp.content,
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
    
    if db_cookie and db_cookie.value:
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

    prompt = prompt
    user_agent = "Mozilla/5.0 (Linux; Android 8.1.0; ZTE Blade A3 2019) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.101 Mobile Safari/537.36"
    oai_did = "8sa86d4as4sa54das6asd68aw4"
    
    cookies = {
        '_ga': 'GA1.1.1230440378.1770357436',
        'oai-hlib': 'true',
        '_account_is_fedramp': 'false',
        'oai-nav-state': '1',
        '__Host-next-auth.csrf-token': '1841f67ee8819e499985488cfe4ffcc996e8d8e62596109a15fbf19642ff01f1%7C75e3347202b03399abc4f868800d3bfbdb75a9d6e65e721a5a3d43c9fa4db8b2',
        'oai-client-auth-info': '%7B%22user%22%3A%7B%22name%22%3A%22SATANA%22%2C%22email%22%3A%22topovii6666%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fsa.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770605128437%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D',
        '__Secure-next-auth.callback-url': 'https%3A%2F%2Fchatgpt.com%2F',
        'oai-asli': '38249160-cf03-4c4b-b93c-a20b90d03feb',
        'oai-did': oai_did,
        '__cflb': '04dTofELUVCxHqRn2Xc7KZnrejaJmSyE3MBwTMPKU7',
        '_cfuvid': '2o9yGvp1QLr2yS6DT7Ad4HwuuNVRuqg8ZA4rsJtpHm4-1770612328.3379626-1.0.1.1-H5L0Ad4ojzk1aCXNSzVQ9YdUSXHmm6F.GLSQCC7LOmI',
        '_ga_9SHBSK2D9J': 'GS2.1.s1770611115$o4$g0$t1770611115$j60$l0$h0',
        'cf_clearance': 'WLH8ypSu33SNH5lv.yShiIyKVDxux_78AhA51.cZg5I-1770614703-1.2.1.1-ojuyJ8.07y0Ou8hBo78eCJzsKJSU3FCRhS4sqDff0nKJSo.0kRSmo7AIAD8ySfK9WuEl9XyikAzKzZ0_t3TxoxQPgMBiY3zFZpUB23gLxim.iVsAjprXclq_C20nI3eCFXQlyXdVgsSol8ShEq_f.FV0oFY8CJZDdbgSYSmqWivYvVCU7vf5uKqs5Dr3mZTRpl6d.JAjHs5NIxc7_zejvGV8iW.V3AtUvkID6nJ79GM',
        '_dd_s': 'aid=6480ac49-2a58-4ff5-a131-b496cba04f9d&rum=0&expire=1770612020533&logs=1&id=22d2e26f-b969-4c76-9c5a-59300ea7bfdc&created=1770610994037',
        'oai-sc': '0gAAAAABpiW-wEDNBcW1Ktd0vYOKzkegTAr81yEfGfks4EJKenIxf7ckkI1puibCN9xSCBEI4XHM66lIF-x9lDNNVw0GpzA7S0oJqUPrFK0WV-n_tIm99ydAA6kyBXWWKphFDdwJSoc_y-X-HiAxnoLSgB_ELri2juDAdb-iDQPfINogzHgRXTEuOyYu1R9AyCGhqeLuC6RVzOZs53PqMVrYG86bytGPV1LpJsRIuq3Flr1i4ybiyw6s',
        'oai-hm': 'ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE',
        '__cf_bm': 'S8KkcRiNSZjk3I_lMFml2RTKjKKsv.t3bdKMuI.5hgU-1770614705.3191593-1.0.1.1-cs3ln3yF2wuksUqnVEJp5oV7BnTCb6bbBOCa29pQH1Sb52DBqHq.o1TyNTHvwCD.F5L..vQ3ixFpRAGu9gwx_SfZ_WscB8_mAgyhU.RiN1vIRdS9AFnVt6jaLQy39GYN',
    }
    
    headers = {
        'accept': '*/*',
        'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'content-type': 'application/json',
        'oai-client-build-number': '4480993',
        'oai-client-version': 'prod-7c2e8d83df2cf0b6eaa11ba7b37f1605384da182',
        'oai-device-id': oai_did,
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
        'user-agent': user_agent,
        # 'cookie': '_ga=GA1.1.1230440378.1770357436; oai-hlib=true; _account_is_fedramp=false; oai-nav-state=1; __Host-next-auth.csrf-token=1841f67ee8819e499985488cfe4ffcc996e8d8e62596109a15fbf19642ff01f1%7C75e3347202b03399abc4f868800d3bfbdb75a9d6e65e721a5a3d43c9fa4db8b2; oai-client-auth-info=%7B%22user%22%3A%7B%22name%22%3A%22SATANA%22%2C%22email%22%3A%22topovii6666%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fsa.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770605128437%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D; __Secure-next-auth.callback-url=https%3A%2F%2Fchatgpt.com%2F; oai-asli=38249160-cf03-4c4b-b93c-a20b90d03feb; oai-did=8sa86d4as4sa54das6asd68aw4; __cflb=04dTofELUVCxHqRn2Xc7KZnrejaJmSyE3MBwTMPKU7; _cfuvid=2o9yGvp1QLr2yS6DT7Ad4HwuuNVRuqg8ZA4rsJtpHm4-1770612328.3379626-1.0.1.1-H5L0Ad4ojzk1aCXNSzVQ9YdUSXHmm6F.GLSQCC7LOmI; _ga_9SHBSK2D9J=GS2.1.s1770611115$o4$g0$t1770611115$j60$l0$h0; cf_clearance=WLH8ypSu33SNH5lv.yShiIyKVDxux_78AhA51.cZg5I-1770614703-1.2.1.1-ojuyJ8.07y0Ou8hBo78eCJzsKJSU3FCRhS4sqDff0nKJSo.0kRSmo7AIAD8ySfK9WuEl9XyikAzKzZ0_t3TxoxQPgMBiY3zFZpUB23gLxim.iVsAjprXclq_C20nI3eCFXQlyXdVgsSol8ShEq_f.FV0oFY8CJZDdbgSYSmqWivYvVCU7vf5uKqs5Dr3mZTRpl6d.JAjHs5NIxc7_zejvGV8iW.V3AtUvkID6nJ79GM; _dd_s=aid=6480ac49-2a58-4ff5-a131-b496cba04f9d&rum=0&expire=1770612020533&logs=1&id=22d2e26f-b969-4c76-9c5a-59300ea7bfdc&created=1770610994037; oai-sc=0gAAAAABpiW-wEDNBcW1Ktd0vYOKzkegTAr81yEfGfks4EJKenIxf7ckkI1puibCN9xSCBEI4XHM66lIF-x9lDNNVw0GpzA7S0oJqUPrFK0WV-n_tIm99ydAA6kyBXWWKphFDdwJSoc_y-X-HiAxnoLSgB_ELri2juDAdb-iDQPfINogzHgRXTEuOyYu1R9AyCGhqeLuC6RVzOZs53PqMVrYG86bytGPV1LpJsRIuq3Flr1i4ybiyw6s; oai-hm=ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE; __cf_bm=S8KkcRiNSZjk3I_lMFml2RTKjKKsv.t3bdKMuI.5hgU-1770614705.3191593-1.0.1.1-cs3ln3yF2wuksUqnVEJp5oV7BnTCb6bbBOCa29pQH1Sb52DBqHq.o1TyNTHvwCD.F5L..vQ3ixFpRAGu9gwx_SfZ_WscB8_mAgyhU.RiN1vIRdS9AFnVt6jaLQy39GYN',
    }
    
    json_data = {
        'prepare_token': 'gAAAAABpiW-wbm0F2N6a-wv0GRwBvGPhL2dUscBPYzXCo6C6d_4fDgH9otx3FW6kl7QpPPT8u1jDILL22_LbOk0ch79-QkbSRhuU1gYh7K0XCiCUMWrmuh0yW9OaU2iW64TuJBoCW9CB4926jVCC5YnPKj__FD-xHz55Fr5v4vGdXKVwhlre-AuyqXzdJxBFNysAlhOU_D1RaCwfKWzF74Z_QjxAqhgdw3GbQj77ShfsrufNXcqVYfLI_1liAJnJ6mKVDoQqahyldpFizS5Iv6b2e5w2fCqPnlvXdm-dJBLz2kCYN_85Uj1aogI5OH8iS9L9ZdHPHdNwp0x33gr_BIpyu9FAb0L_Oogdgy4ZkZJLyPiF0KNfoaviaEa8-Ym7ekpE3MWS2Yg94oJe1RUDg74vl7QBoX_t1kJXracinF3Cm05XqZmuc4vwxDdSkzBcaZKx6KiurLkFePBOOTDdK_E-enqHtpuayVWmayxK8wIdrsmsCDvXFf-wmMdkcAqnJSFAueQ2SOZ645yhWvagP3MWGjii05Di7hXJbmDBBawOjQUJ1KrI-gOmj8tEkv-BeSR0SzqN-hdHJYrInRG0ObrYukWeTM5OPZ5ADqcJBfS-ztKD6YjAvQO0-qYWH384PvsAOuQzN4aA9chYZZ58H4dZB8c4V3eOv7PkfF5NEpzvXFEglxUo0rRivwkFE84RnIbvMeJLR-cdtW6KUQg19Bd7lTEzK_hOblQXTfNGa5KeptTYp3Y6v6HlgExyKr6GZi-iouRgNXw54nw9w7A6S2nUL2q-TrOZrTkQhE1KXR2lerqgyvBG0tCEJCCYqlmyd7QrzPSAMrxgvPK-a-9kzO_4DRMunAlJeyAnpIoB0uf7SC_-xAdEQ4-dwWq7fW6Yf5rVBC-t4AJpmabXdwUIUAY3SeRMzxdEqbBQhGWtHT-VkluKxn_JvJ7S1u_xi2FVtG-XhcAPnhAOOaCh1Fg1pATdo-oakVO4huXv-AZefExmlglBAvkr344K-eh55J5MwDWFi9Zj5m2N1s1Kxk7jAE9VHfTV6hVgmzL8e-q7ue7df_lcP7ouvC7hJczehWykQJqjbqJlCE7PeZoC5Pe4nMip43yjmbMVx2CJlnxRQlpaCxDvXEg1SQfp7AukqeTY3eh8UXcSz7tA6QCj0iYrhj3vX3MuUyf_-6FLm52UPYYE8KP3OjNQn4wFSf-6jieG41UzJXPa0Kn_KT-kysD4bI1rdqwIJE0m3ZO8WPyKa2ARQlQoiXVo7Abmj_DccdKlH5L9czIGFDTmDiQz6U2iD7sfyh9r110p0y0QfTtl4xxXZhQuS9X3BAbnP4ocI6xajpbcvjaAq6lDM2UHuzSzmbv-pk_pGUI7foRNp7QMFw9vLxBSlvFtnSpOFCSB5YQOSzr0Vpe17ew8I4FPOnuoTwc_QSpjrCT3lDTvumrvQCbzN1xpHKFpbJDAPsTYL4lw5wKHMV_AR1KgZRNy72f-19g9EEAZs6GMvMmMpfO4eW7zkbm0ioSCIcYCjW_lhuj3jLaiuNJ8EnzA9-9RMcUkiZ9Q4kxZhPAtIJQm4ET5lgScDT9CWTcSmOYhcreQ6p8Tgty6GesMkBV5eRO12QXgGeAJfEpuy1coJ9zKtiZbxox9IbTRWNFBgHF-B3KwfNnkO_JX9rQ_FHdw0mfzs_KUkdoWwAJACkua5cPTK1_jHGfDZCCqKIznFZhYJcWSnSoQDreiBoP1MsyfE9EK8TzjR6ZtNGtrn9WJYcslJ1ZSbwPMUAEzuTCPegCt6mOikbEu7tnJJHJHCOI1aKXh2VNXxaJjv3pLEIF3hlwK0rUQQTmHXbFjW0Cg1zPOZTiL-vnhCbjWBKB2-Q1s1vmBIwF-LiQjhjHBc3Z89KPLWWLSpAOip2Ixk1UvK3zodlcp4rsqL6Yib8LXV_JuMtbI1EtXSWBlOJl41274X3lIRuqJReVah6FuZeCllqf49PMV4GnArODZ8WcWfsywfQxJGPhm2DyohMIM-9RhRwTepfmGYJAHrNKhfosdHx7Z5nAmGUAS3_yjOAr1U1t8LfxJbk2AcnFq8OBz0yDKz3tq_SajtfwblAGI91ioq-nT76z1Hkw5oRX5DSoMu0ywtfBfyln6V9KrAVREAH9xSxCCPame0Tg1NmO3BPq1UhRsSzEVlgfOUUfeht4j9rfVJ-aCOsbXc5NX4ucLMFg7oY20dQx7THiA7rQ3DUhkNl3Rx9z1kFsWtT9z3bgiKB1AhiYdSLJAik_KqE5pNS5MzF7FHRqARG2NatDoCnBg52LrFx_z9u24krHZ0azX8uMzCF5tztVQPKJ0xhd6rE3x0b64iWsHEInCeCBuDBLEDf0GtHUOzqQjLDtumYC2XYb-6Nasv_Ko24JzPFHQD29ChbczO5xvry3LcIA4_ZF8P1Aw5cqUYQbuWExpxA04NwhtLiUqzhntabOWMJIa3ai5_ojUBFpHpKVUCzTxBfOYT5JKu1vbCzykqBnQJwADAKefpmA-iOD4AaLIb6JKVrSVvXpwVyalSAes_Dt7zpZLF--6d8n_v8dUuj9HDyq_7l7q0V_xipulbuVf6pldz2azoZIJ-OD8l75Zpj5PKF2ksOUUO70nk7nyOFC7JgV7XEYSk3RG7EcvkstDTdymphMbol9VOYVxvo_Yyxm_aEb5186MKF9IUxfe5iAFM9chXkszfvCyS3CcYRBXusSVHtEu_iF0LBPyopf8eH7tJIEaY2zu_8nGCmS8VRp7PtzfQEeGwpuuQQuO8l_Rjf8LCT0H3HLWKp0zE8A2mTeqE9nIEh8BX41T-KGGRtG9LwH7Dv6jE3z2YFf1uJPYYAU3lHN5zCPka1UDmbHYs6WqqJE5hKvLaOem5F1i4fUqZxFaebLpAOrUJiVyqqcKB-B_GrWLrOODXdoqnyxgktVGIAICNL-TXYF5C9OJnwPg5Cdf0YIs_wRwhB-sZOxbiZFKztN7f9QPxrCRmd31TpArT5dn4CVE_fMr2nUfhJCwSeyYLPAMWEtUeRiEaxyeMmt4zgqpkWQaQMP9R2-pYuqXKILl3dkBYkUxYoWNPB-e6DWow9mmVRyE6Q8VjYuONOdzO8dBxPyXip_C3SiQMZ3gTbxcI7_05wwMcEaXk85A9JJRlPQU0JuUCGMAJiTci7bTeo_G0TJbpMCH6S8k_yG1vFnmqlGqx4s-eQ---fx3wSoS9zEwkEGjM6gHoy2HXZJ-CGtwLb8e9hdkOyVBtgZQC_yUQugRd8gqq6QXZ_kPeTsf4rdVKyeAq11VuezFyKpYeZr-2Tuq1OoaG6ycB5Ebks2pcowX1iRmu_ZJEY7Uf1WAwqJ4w4jcj-lY3wAlMBJKDO8a_w==',
        'proofofwork': 'gAAAAABWzIxMzQsIk1vbiBGZWIgMDkgMjAyNiAxMDoyNToyMiBHTVQrMDYwMCAoR01UKzA2OjAwKSIsMjAwNzc2MDg5NiwzNiwiTW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTAuMDsgV2luNjQ7IHg2NCkgQXBwbGVXZWJLaXQvNTM3LjM2IChLSFRNTCwgbGlrZSBHZWNrbykgQ2hyb21lLzE0NC4wLjAuMCBTYWZhcmkvNTM3LjM2IiwiaHR0cHM6Ly93d3cuZ29vZ2xldGFnbWFuYWdlci5jb20vZ3RhZy9qcz9pZD1HLTlTSEJTSzJEOUoiLCJwcm9kLTdjMmU4ZDgzZGYyY2YwYjZlYWExMWJhN2IzN2YxNjA1Mzg0ZGExODIiLCJydS1SVSIsInJ1LVJVLHJ1LGVuLVVTLGVuIiw4LCJzZXJ2aWNlV29ya2Vy4oiSW29iamVjdCBTZXJ2aWNlV29ya2VyQ29udGFpbmVyXSIsIl9fcmVhY3RDb250YWluZXIkNGQxOHlwZTNjY2EiLCJvbmlucHV0Iiw3NzY1LjUsImIwOGQ3OGEzLWJjMDktNDIyNC05Y2E0LTgwODY5N2NiMDdmOSIsIiIsNCwxNzcwNjExMTE0NDUzLjEsMF0=~S',
        'turnstile': '131',
    }
    
    response1 = requests.post(
        'https://chatgpt.com/backend-anon/sentinel/chat-requirements/finalize',
        cookies=cookies,
        headers=headers,
        json=json_data,
    ).json()['token']
    
    
    
    cookies = {
        '_ga': 'GA1.1.1230440378.1770357436',
        'oai-hlib': 'true',
        '_account_is_fedramp': 'false',
        'oai-nav-state': '1',
        '__Host-next-auth.csrf-token': '1841f67ee8819e499985488cfe4ffcc996e8d8e62596109a15fbf19642ff01f1%7C75e3347202b03399abc4f868800d3bfbdb75a9d6e65e721a5a3d43c9fa4db8b2',
        'oai-client-auth-info': '%7B%22user%22%3A%7B%22name%22%3A%22SATANA%22%2C%22email%22%3A%22topovii6666%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fsa.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770605128437%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D',
        '__Secure-next-auth.callback-url': 'https%3A%2F%2Fchatgpt.com%2F',
        'oai-asli': '38249160-cf03-4c4b-b93c-a20b90d03feb',
        'oai-did': oai_did,
        '__cflb': '04dTofELUVCxHqRn2Xc7KZnrejaJmSyE3MBwTMPKU7',
        '_cfuvid': '2o9yGvp1QLr2yS6DT7Ad4HwuuNVRuqg8ZA4rsJtpHm4-1770612328.3379626-1.0.1.1-H5L0Ad4ojzk1aCXNSzVQ9YdUSXHmm6F.GLSQCC7LOmI',
        '_ga_9SHBSK2D9J': 'GS2.1.s1770611115$o4$g0$t1770611115$j60$l0$h0',
        'cf_clearance': 'WLH8ypSu33SNH5lv.yShiIyKVDxux_78AhA51.cZg5I-1770614703-1.2.1.1-ojuyJ8.07y0Ou8hBo78eCJzsKJSU3FCRhS4sqDff0nKJSo.0kRSmo7AIAD8ySfK9WuEl9XyikAzKzZ0_t3TxoxQPgMBiY3zFZpUB23gLxim.iVsAjprXclq_C20nI3eCFXQlyXdVgsSol8ShEq_f.FV0oFY8CJZDdbgSYSmqWivYvVCU7vf5uKqs5Dr3mZTRpl6d.JAjHs5NIxc7_zejvGV8iW.V3AtUvkID6nJ79GM',
        'oai-hm': 'ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE',
        'oai-sc': '0gAAAAABpiW-yMn7QBIaA8qd_j16CF-pTmRhM0W20WO5YEW35AIOAxM3e_kXFHCw0wnMWGWGpoeRNFZlN7HlW76LR69sAcIGjgmMLRKom9KUsbtTlF2U60Lgya1uEdAjx2kAcmU5z3ce18XDjTfQejDEpVYXq9yur9-wYzM84epO2shXdt4dSXDzjbUqvbLsYkA7CmQ1Nq6jfMXb0tJ7R3eDFInn6OBy7VmGDi-95RzxXfkVV0gvaljE',
        '_dd_s': 'aid=6480ac49-2a58-4ff5-a131-b496cba04f9d&rum=0&expire=1770612024010&logs=1&id=22d2e26f-b969-4c76-9c5a-59300ea7bfdc&created=1770610994037',
        '__cf_bm': 'RMpEeNkjb_.gwE04TG8N_Nv06gGRrAFjxkQOUA65BrQ-1770614704.9578319-1.0.1.1-zyEXnVjyvDOIgQynAwgJkNjvksKYXW9wWuY8FLAPnzyhWPNNUcoHwrB46lVsHhEA8j368yVeLAofJyHtqFcca2Ac.rJ0TfYG287_IWpPnd5Rzs6kRjcpXSRV76d5Av.C',
    }
    
    headers = {
        'accept': '*/*',
        'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'content-type': 'application/json',
        'oai-client-build-number': '4480993',
        'oai-client-version': 'prod-7c2e8d83df2cf0b6eaa11ba7b37f1605384da182',
        'oai-device-id': oai_did,
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
        'user-agent': user_agent,
        'x-conduit-token': 'no-token',
        'x-oai-turn-trace-id': '8592e0d2-71b7-4883-a1df-9640ffa44bbc',
        # 'cookie': '_ga=GA1.1.1230440378.1770357436; oai-hlib=true; _account_is_fedramp=false; oai-nav-state=1; __Host-next-auth.csrf-token=1841f67ee8819e499985488cfe4ffcc996e8d8e62596109a15fbf19642ff01f1%7C75e3347202b03399abc4f868800d3bfbdb75a9d6e65e721a5a3d43c9fa4db8b2; oai-client-auth-info=%7B%22user%22%3A%7B%22name%22%3A%22SATANA%22%2C%22email%22%3A%22topovii6666%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fsa.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770605128437%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D; __Secure-next-auth.callback-url=https%3A%2F%2Fchatgpt.com%2F; oai-asli=38249160-cf03-4c4b-b93c-a20b90d03feb; oai-did=8sa86d4as4sa54das6asd68aw4; __cflb=04dTofELUVCxHqRn2Xc7KZnrejaJmSyE3MBwTMPKU7; _cfuvid=2o9yGvp1QLr2yS6DT7Ad4HwuuNVRuqg8ZA4rsJtpHm4-1770612328.3379626-1.0.1.1-H5L0Ad4ojzk1aCXNSzVQ9YdUSXHmm6F.GLSQCC7LOmI; _ga_9SHBSK2D9J=GS2.1.s1770611115$o4$g0$t1770611115$j60$l0$h0; cf_clearance=WLH8ypSu33SNH5lv.yShiIyKVDxux_78AhA51.cZg5I-1770614703-1.2.1.1-ojuyJ8.07y0Ou8hBo78eCJzsKJSU3FCRhS4sqDff0nKJSo.0kRSmo7AIAD8ySfK9WuEl9XyikAzKzZ0_t3TxoxQPgMBiY3zFZpUB23gLxim.iVsAjprXclq_C20nI3eCFXQlyXdVgsSol8ShEq_f.FV0oFY8CJZDdbgSYSmqWivYvVCU7vf5uKqs5Dr3mZTRpl6d.JAjHs5NIxc7_zejvGV8iW.V3AtUvkID6nJ79GM; oai-hm=ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE; oai-sc=0gAAAAABpiW-yMn7QBIaA8qd_j16CF-pTmRhM0W20WO5YEW35AIOAxM3e_kXFHCw0wnMWGWGpoeRNFZlN7HlW76LR69sAcIGjgmMLRKom9KUsbtTlF2U60Lgya1uEdAjx2kAcmU5z3ce18XDjTfQejDEpVYXq9yur9-wYzM84epO2shXdt4dSXDzjbUqvbLsYkA7CmQ1Nq6jfMXb0tJ7R3eDFInn6OBy7VmGDi-95RzxXfkVV0gvaljE; _dd_s=aid=6480ac49-2a58-4ff5-a131-b496cba04f9d&rum=0&expire=1770612024010&logs=1&id=22d2e26f-b969-4c76-9c5a-59300ea7bfdc&created=1770610994037; __cf_bm=RMpEeNkjb_.gwE04TG8N_Nv06gGRrAFjxkQOUA65BrQ-1770614704.9578319-1.0.1.1-zyEXnVjyvDOIgQynAwgJkNjvksKYXW9wWuY8FLAPnzyhWPNNUcoHwrB46lVsHhEA8j368yVeLAofJyHtqFcca2Ac.rJ0TfYG287_IWpPnd5Rzs6kRjcpXSRV76d5Av.C',
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
            'id': 'd9add13b-fada-4486-b7f8-f67d02f28d61',
            'author': {
                'role': 'user',
            },
            'content': {
                'content_type': 'text',
                'parts': [
                    'Hi',
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
    
    response2 = requests.post(
        'https://chatgpt.com/backend-anon/f/conversation/prepare',
        cookies=cookies,
        headers=headers,
        json=json_data,
    ).json()['conduit_token']
    
    
    cookies = {
        '_ga': 'GA1.1.1230440378.1770357436',
        'oai-hlib': 'true',
        '_account_is_fedramp': 'false',
        'oai-nav-state': '1',
        '__Host-next-auth.csrf-token': '1841f67ee8819e499985488cfe4ffcc996e8d8e62596109a15fbf19642ff01f1%7C75e3347202b03399abc4f868800d3bfbdb75a9d6e65e721a5a3d43c9fa4db8b2',
        'oai-client-auth-info': '%7B%22user%22%3A%7B%22name%22%3A%22SATANA%22%2C%22email%22%3A%22topovii6666%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fsa.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770605128437%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D',
        '__Secure-next-auth.callback-url': 'https%3A%2F%2Fchatgpt.com%2F',
        'oai-asli': '38249160-cf03-4c4b-b93c-a20b90d03feb',
        'oai-did': oai_did,
        '__cflb': '04dTofELUVCxHqRn2Xc7KZnrejaJmSyE3MBwTMPKU7',
        '_cfuvid': '2o9yGvp1QLr2yS6DT7Ad4HwuuNVRuqg8ZA4rsJtpHm4-1770612328.3379626-1.0.1.1-H5L0Ad4ojzk1aCXNSzVQ9YdUSXHmm6F.GLSQCC7LOmI',
        'cf_clearance': 'WLH8ypSu33SNH5lv.yShiIyKVDxux_78AhA51.cZg5I-1770614703-1.2.1.1-ojuyJ8.07y0Ou8hBo78eCJzsKJSU3FCRhS4sqDff0nKJSo.0kRSmo7AIAD8ySfK9WuEl9XyikAzKzZ0_t3TxoxQPgMBiY3zFZpUB23gLxim.iVsAjprXclq_C20nI3eCFXQlyXdVgsSol8ShEq_f.FV0oFY8CJZDdbgSYSmqWivYvVCU7vf5uKqs5Dr3mZTRpl6d.JAjHs5NIxc7_zejvGV8iW.V3AtUvkID6nJ79GM',
        'oai-hm': 'ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE',
        'oai-sc': '0gAAAAABpiW-yMn7QBIaA8qd_j16CF-pTmRhM0W20WO5YEW35AIOAxM3e_kXFHCw0wnMWGWGpoeRNFZlN7HlW76LR69sAcIGjgmMLRKom9KUsbtTlF2U60Lgya1uEdAjx2kAcmU5z3ce18XDjTfQejDEpVYXq9yur9-wYzM84epO2shXdt4dSXDzjbUqvbLsYkA7CmQ1Nq6jfMXb0tJ7R3eDFInn6OBy7VmGDi-95RzxXfkVV0gvaljE',
        '_ga_9SHBSK2D9J': 'GS2.1.s1770611115$o4$g1$t1770611126$j49$l0$h0',
        '__cf_bm': 'friyxHkVcM7rTGelRwMyudxFEQhC0MlVYfU0iqE6rZA-1770614712.7833295-1.0.1.1-HOwPukMnomCxTwul9oDeXblSZw9b2EkFssPGWc_GXj08cnsXfiaX30XK5rbRCuNeecCL2VWGWVEWbhNRU.FdY7RlFld5Lp1ofk2SnyC8HpGhHHMlBCxySojmDiBIP6ih',
        '_dd_s': 'aid=6480ac49-2a58-4ff5-a131-b496cba04f9d&rum=0&expire=1770612030745&logs=1&id=22d2e26f-b969-4c76-9c5a-59300ea7bfdc&created=1770610994037',
    }
    
    headers = {
        'accept': 'text/event-stream',
        'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'content-type': 'application/json',
        'oai-client-build-number': '4480993',
        'oai-client-version': 'prod-7c2e8d83df2cf0b6eaa11ba7b37f1605384da182',
        'oai-device-id': oai_did,
        'oai-echo-logs': '0,7132',
        'oai-language': 'ru-RU',
        'openai-sentinel-chat-requirements-token': response1,
        'openai-sentinel-proof-token': 'gAAAAABWzIxMzQsIk1vbiBGZWIgMDkgMjAyNiAxMDoyNToyMiBHTVQrMDYwMCAoR01UKzA2OjAwKSIsMjAwNzc2MDg5NiwzNiwiTW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTAuMDsgV2luNjQ7IHg2NCkgQXBwbGVXZWJLaXQvNTM3LjM2IChLSFRNTCwgbGlrZSBHZWNrbykgQ2hyb21lLzE0NC4wLjAuMCBTYWZhcmkvNTM3LjM2IiwiaHR0cHM6Ly93d3cuZ29vZ2xldGFnbWFuYWdlci5jb20vZ3RhZy9qcz9pZD1HLTlTSEJTSzJEOUoiLCJwcm9kLTdjMmU4ZDgzZGYyY2YwYjZlYWExMWJhN2IzN2YxNjA1Mzg0ZGExODIiLCJydS1SVSIsInJ1LVJVLHJ1LGVuLVVTLGVuIiw4LCJzZXJ2aWNlV29ya2Vy4oiSW29iamVjdCBTZXJ2aWNlV29ya2VyQ29udGFpbmVyXSIsIl9fcmVhY3RDb250YWluZXIkNGQxOHlwZTNjY2EiLCJvbmlucHV0Iiw3NzY1LjUsImIwOGQ3OGEzLWJjMDktNDIyNC05Y2E0LTgwODY5N2NiMDdmOSIsIiIsNCwxNzcwNjExMTE0NDUzLjEsMF0=~S',
        'openai-sentinel-turnstile-token': '131',
        'origin': 'https://chatgpt.com',
        'priority': 'u=1, i',
        'referer': 'https://chatgpt.com/',
        'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': user_agent,
        'x-conduit-token': response2,
        'x-oai-turn-trace-id': '8592e0d2-71b7-4883-a1df-9640ffa44bbc',
        # 'cookie': '_ga=GA1.1.1230440378.1770357436; oai-hlib=true; _account_is_fedramp=false; oai-nav-state=1; __Host-next-auth.csrf-token=1841f67ee8819e499985488cfe4ffcc996e8d8e62596109a15fbf19642ff01f1%7C75e3347202b03399abc4f868800d3bfbdb75a9d6e65e721a5a3d43c9fa4db8b2; oai-client-auth-info=%7B%22user%22%3A%7B%22name%22%3A%22SATANA%22%2C%22email%22%3A%22topovii6666%40gmail.com%22%2C%22picture%22%3A%22https%3A%2F%2Fcdn.auth0.com%2Favatars%2Fsa.png%22%2C%22connectionType%22%3A2%2C%22timestamp%22%3A1770605128437%7D%2C%22loggedInWithGoogleOneTap%22%3Afalse%2C%22isOptedOut%22%3Afalse%7D; __Secure-next-auth.callback-url=https%3A%2F%2Fchatgpt.com%2F; oai-asli=38249160-cf03-4c4b-b93c-a20b90d03feb; oai-did=8sa86d4as4sa54das6asd68aw4; __cflb=04dTofELUVCxHqRn2Xc7KZnrejaJmSyE3MBwTMPKU7; _cfuvid=2o9yGvp1QLr2yS6DT7Ad4HwuuNVRuqg8ZA4rsJtpHm4-1770612328.3379626-1.0.1.1-H5L0Ad4ojzk1aCXNSzVQ9YdUSXHmm6F.GLSQCC7LOmI; cf_clearance=WLH8ypSu33SNH5lv.yShiIyKVDxux_78AhA51.cZg5I-1770614703-1.2.1.1-ojuyJ8.07y0Ou8hBo78eCJzsKJSU3FCRhS4sqDff0nKJSo.0kRSmo7AIAD8ySfK9WuEl9XyikAzKzZ0_t3TxoxQPgMBiY3zFZpUB23gLxim.iVsAjprXclq_C20nI3eCFXQlyXdVgsSol8ShEq_f.FV0oFY8CJZDdbgSYSmqWivYvVCU7vf5uKqs5Dr3mZTRpl6d.JAjHs5NIxc7_zejvGV8iW.V3AtUvkID6nJ79GM; oai-hm=ON_YOUR_MIND%20%7C%20READY_WHEN_YOU_ARE; oai-sc=0gAAAAABpiW-yMn7QBIaA8qd_j16CF-pTmRhM0W20WO5YEW35AIOAxM3e_kXFHCw0wnMWGWGpoeRNFZlN7HlW76LR69sAcIGjgmMLRKom9KUsbtTlF2U60Lgya1uEdAjx2kAcmU5z3ce18XDjTfQejDEpVYXq9yur9-wYzM84epO2shXdt4dSXDzjbUqvbLsYkA7CmQ1Nq6jfMXb0tJ7R3eDFInn6OBy7VmGDi-95RzxXfkVV0gvaljE; _ga_9SHBSK2D9J=GS2.1.s1770611115$o4$g1$t1770611126$j49$l0$h0; __cf_bm=friyxHkVcM7rTGelRwMyudxFEQhC0MlVYfU0iqE6rZA-1770614712.7833295-1.0.1.1-HOwPukMnomCxTwul9oDeXblSZw9b2EkFssPGWc_GXj08cnsXfiaX30XK5rbRCuNeecCL2VWGWVEWbhNRU.FdY7RlFld5Lp1ofk2SnyC8HpGhHHMlBCxySojmDiBIP6ih; _dd_s=aid=6480ac49-2a58-4ff5-a131-b496cba04f9d&rum=0&expire=1770612030745&logs=1&id=22d2e26f-b969-4c76-9c5a-59300ea7bfdc&created=1770610994037',
    }
    
    json_data = {
        'action': 'next',
        'messages': [
            {
                'id': '000e88d2-dc13-4b37-8a39-492c39fb0c1b',
                'author': {
                    'role': 'user',
                },
                'create_time': 1770611130.757,
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
        'parent_message_id': 'client-created-root',
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
            'is_dark_mode': True,
            'time_since_loaded': 16,
            'page_height': 641,
            'page_width': 886,
            'pixel_ratio': 1,
            'screen_height': 768,
            'screen_width': 1366,
            'app_name': 'chatgpt.com',
        },
        'paragen_cot_summary_display_override': 'allow',
        'force_parallel_switch': 'auto',
    }
    
    data = requests.post('https://chatgpt.com/backend-anon/f/conversation', cookies=cookies, headers=headers, json=json_data).text
    print(data)



    pattern = r'(?:\"p\":\s*\"/message/content/parts/0\".*?\"v\":\s*|data:\s*\{\"v\":\s*|\"role\":\s*\"assistant\".*?\"parts\":\s*\[)\"(?P<content>(?:[^"\\]|\\.)*)\"'
    raw_text = "".join(re.findall(pattern, data))
    print(raw_text)

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

# --- GPT LOGIC & HANDLERS ---
async def process_gpt(key: str, model: str, prompt: str, db: AsyncSession):
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

    if not has_unlimited and user.tokens_balance <= 0:
        raise HTTPException(status_code=402, detail="INSUFFICIENT GLOBAL BALANCE")
    
    if api_key_obj.limit_tokens <= 0:
        raise HTTPException(status_code=402, detail="API KEY LIMIT EXCEEDED")

    # 3. Генерация
    ai_response = await chatgpt(model=model, prompt=prompt)

    # 4. Подсчет и списание
    input_tokens = await get_token_count(prompt)
    output_tokens = await get_token_count(ai_response)
    total_cost = input_tokens["tokenCount"] + output_tokens["tokenCount"]

    if not has_unlimited:
        user.tokens_balance -= total_cost 
    api_key_obj.limit_tokens -= total_cost 
    
    await db.commit()
    return ai_response

@app.get("/api/run/gpt")
async def run_gpt_get(key: str, model: str, prompt: str = "Test prompt", db: AsyncSession = Depends(get_db)):
    return await process_gpt(key, model, prompt, db)

@app.post("/api/run/gpt")
async def run_gpt_post(req: GPTRequest, db: AsyncSession = Depends(get_db)):
    return await process_gpt(req.key, req.model, req.prompt, db)
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
# --- GEMINI LOGIC & HANDLERS ---
async def process_gemini(key: str, prompt: str, db: AsyncSession):
    stmt = select(APIKey).where(APIKey.key_hash == key)
    result = await db.execute(stmt)
    api_key_obj = result.scalar_one_or_none()

    if not api_key_obj:
        raise HTTPException(status_code=403, detail="INVALID API KEY")

    user_result = await db.execute(select(User).where(User.id == api_key_obj.user_id))
    user = user_result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=402, detail="USER NOT FOUND")
        
    has_unlimited = user.unlimited_until and user.unlimited_until > datetime.utcnow()
    
    if not has_unlimited and user.tokens_balance <= 0:
        raise HTTPException(status_code=402, detail="INSUFFICIENT FUNDS")
        
    if api_key_obj.limit_tokens <= 0:
        raise HTTPException(status_code=402, detail="API KEY LIMIT EXCEEDED")

    ai_response = await gemini_chat(prompt, db)
    
    input_tokens = await get_token_count(prompt)
    output_tokens = await get_token_count(ai_response)
    COST = input_tokens['tokenCount'] + output_tokens['tokenCount']

    if not has_unlimited:
        user.tokens_balance -= COST
    api_key_obj.limit_tokens -= COST
    await db.commit()

    return ai_response

@app.get("/api/run/gemini")
async def run_gemini_get(key: str, prompt: str = "Hello", db: AsyncSession = Depends(get_db)):
    return await process_gemini(key, prompt, db)

@app.post("/api/run/gemini")
async def run_gemini_post(req: GeminiRequest, db: AsyncSession = Depends(get_db)):
    return await process_gemini(req.key, req.prompt, db)


# --- IMAGE LOGIC & HANDLERS ---
async def process_image(key: str, prompt: str, db: AsyncSession):
    COST = 500 
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

    result_data = await generate_gemini_image_async(prompt, db)
    
    if "error" in result_data:
        raise HTTPException(status_code=500, detail=result_data["error"])

    if not has_unlimited:
        user.tokens_balance -= COST
    api_key_obj.limit_tokens -= COST
    await db.commit()

    return Response(content=result_data["image"], media_type="image/jpeg")

@app.get("/api/run/image")
async def run_image_get(key: str, prompt: str, db: AsyncSession = Depends(get_db)):
    return await process_image(key, prompt, db)

@app.post("/api/run/image")
async def run_image_post(req: ImageGenRequest, db: AsyncSession = Depends(get_db)):
    return await process_image(req.key, req.prompt, db)
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
    'accept': '*/*',
    'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'authorization': 'Bearer eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJ1c2VyLWNlbnRlciIsImV4cCI6MTc3MzcyMzM3OCwiaWF0IjoxNzcxMTMxMzc4LCJqdGkiOiJkNjhsM3NodG9vbWRrNWs2MXYwMCIsInR5cCI6ImFjY2VzcyIsImFwcF9pZCI6ImtpbWkiLCJzdWIiOiJkNjg5bGtmazY3ZWN2dTMybzc2MCIsInNwYWNlX2lkIjoiZDY4OWxrZms2N2VjdnUzMm81aWciLCJhYnN0cmFjdF91c2VyX2lkIjoiZDY4OWxrZms2N2VjdnUzMm81aTAiLCJzc2lkIjoiMTczMTU0MTY5Nzc3NjUzOTY5NSIsImRldmljZV9pZCI6Ijc2MDYxOTY2MjUxODYzODEwNjgiLCJyZWdpb24iOiJvdmVyc2VhcyIsIm1lbWJlcnNoaXAiOnsibGV2ZWwiOjEwfX0.oZPCy7wrOm0ihqrf70uWZGMOsjo7kf_q7pW4UkfpGcsAtvgcxlIueXfMqEKt6ys6O0MWk14FyLY5P32YXKLstQ',
    'connect-protocol-version': '1',
    'content-type': 'application/connect+json',
    'origin': 'https://www.kimi.com',
    'priority': 'u=1, i',
    'r-timezone': 'Etc/GMT-6',
    'referer': 'https://www.kimi.com/',
    'sec-ch-ua': '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
    'x-language': 'en-US',
    'x-msh-device-id': '7606196625186381068',
    'x-msh-platform': 'web',
    'x-msh-session-id': '1731541697776539695',
    'x-msh-version': '1.0.0',
    'x-traffic-id': 'd689lkfk67ecvu32o760',
    # 'cookie': '_ga=GA1.1.205527601.1770952074; theme=dark; __snaker__id=HTFeLzJVE0QpxltU; _gcl_au=1.1.1381313197.1770952074.38843557.1771001468.1771001468; kimi-auth=eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJ1c2VyLWNlbnRlciIsImV4cCI6MTc3MzcyMzM3OCwiaWF0IjoxNzcxMTMxMzc4LCJqdGkiOiJkNjhsM3NodG9vbWRrNWs2MXYwMCIsInR5cCI6ImFjY2VzcyIsImFwcF9pZCI6ImtpbWkiLCJzdWIiOiJkNjg5bGtmazY3ZWN2dTMybzc2MCIsInNwYWNlX2lkIjoiZDY4OWxrZms2N2VjdnUzMm81aWciLCJhYnN0cmFjdF91c2VyX2lkIjoiZDY4OWxrZms2N2VjdnUzMm81aTAiLCJzc2lkIjoiMTczMTU0MTY5Nzc3NjUzOTY5NSIsImRldmljZV9pZCI6Ijc2MDYxOTY2MjUxODYzODEwNjgiLCJyZWdpb24iOiJvdmVyc2VhcyIsIm1lbWJlcnNoaXAiOnsibGV2ZWwiOjEwfX0.oZPCy7wrOm0ihqrf70uWZGMOsjo7kf_q7pW4UkfpGcsAtvgcxlIueXfMqEKt6ys6O0MWk14FyLY5P32YXKLstQ; gdxidpyhxdE=ZDeDqH6uSWt%2BKvWxqGfxonr9cMqK7u%5CtPOQ%2FqdkZwmrU0WDVwPGU7QxxXD2NZ6I%2FLmsNq1wxaxt5%5CrDwoWilK1zVVHKYhETSN7m7MKk504q4xegluS1YPTvhiYmDd0KJr5%2BGslGa%2F%2BcLrQz44yUq%2BWlD%2BnnqkziOMk%2FAQloab4HlVLiy%3A1771129508572; _ga_YXD8W70SZP=GS2.1.s1772770956$o14$g0$t1772770956$j60$l0$h0; Hm_lvt_358cae4815e85d48f7e8ab7f3680a74b=1771067704,1771120805,1772628154,1772770957; Hm_lpvt_358cae4815e85d48f7e8ab7f3680a74b=1772770957; HMACCOUNT=24FA09A586DC2CEA',
}

KIMI_COOKIES = {
    '_ga': 'GA1.1.205527601.1770952074',
    'theme': 'dark',
    '__snaker__id': 'HTFeLzJVE0QpxltU',
    '_gcl_au': '1.1.1381313197.1770952074.38843557.1771001468.1771001468',
    'kimi-auth': 'eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJ1c2VyLWNlbnRlciIsImV4cCI6MTc3MzcyMzM3OCwiaWF0IjoxNzcxMTMxMzc4LCJqdGkiOiJkNjhsM3NodG9vbWRrNWs2MXYwMCIsInR5cCI6ImFjY2VzcyIsImFwcF9pZCI6ImtpbWkiLCJzdWIiOiJkNjg5bGtmazY3ZWN2dTMybzc2MCIsInNwYWNlX2lkIjoiZDY4OWxrZms2N2VjdnUzMm81aWciLCJhYnN0cmFjdF91c2VyX2lkIjoiZDY4OWxrZms2N2VjdnUzMm81aTAiLCJzc2lkIjoiMTczMTU0MTY5Nzc3NjUzOTY5NSIsImRldmljZV9pZCI6Ijc2MDYxOTY2MjUxODYzODEwNjgiLCJyZWdpb24iOiJvdmVyc2VhcyIsIm1lbWJlcnNoaXAiOnsibGV2ZWwiOjEwfX0.oZPCy7wrOm0ihqrf70uWZGMOsjo7kf_q7pW4UkfpGcsAtvgcxlIueXfMqEKt6ys6O0MWk14FyLY5P32YXKLstQ',
    'gdxidpyhxdE': 'ZDeDqH6uSWt%2BKvWxqGfxonr9cMqK7u%5CtPOQ%2FqdkZwmrU0WDVwPGU7QxxXD2NZ6I%2FLmsNq1wxaxt5%5CrDwoWilK1zVVHKYhETSN7m7MKk504q4xegluS1YPTvhiYmDd0KJr5%2BGslGa%2F%2BcLrQz44yUq%2BWlD%2BnnqkziOMk%2FAQloab4HlVLiy%3A1771129508572',
    '_ga_YXD8W70SZP': 'GS2.1.s1772770956$o14$g0$t1772770956$j60$l0$h0',
    'Hm_lvt_358cae4815e85d48f7e8ab7f3680a74b': '1771067704,1771120805,1772628154,1772770957',
    'Hm_lpvt_358cae4815e85d48f7e8ab7f3680a74b': '1772770957',
    'HMACCOUNT': '24FA09A586DC2CEA',
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

# --- AGENT LOGIC & HANDLERS ---
async def process_agent(key: str, prompt: str, stream: bool, db: AsyncSession):
    COST = 1500
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

    if not has_unlimited:
        user.tokens_balance -= COST
    api_key_obj.limit_tokens -= COST
    await db.commit()

    generator = kimi_stream_generator(prompt)

    if stream:
        return StreamingResponse(generator, media_type="text/plain")
    else:
        full_text = ""
        try:
            async for chunk in generator:
                full_text += chunk
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
        return full_text

@app.get("/api/run/agent")
async def run_agent_get(key: str, prompt: str = "Hello", stream: bool = False, db: AsyncSession = Depends(get_db)):
    return await process_agent(key, prompt, stream, db)

@app.post("/api/run/agent")
async def run_agent_post(req: AgentRequest, db: AsyncSession = Depends(get_db)):
    # Обратите внимание: Pydantic модель AgentRequest уже содержит stream
    return await process_agent(req.key, req.prompt, req.stream, db)





# --- OPENAI GPT-OSS-120B LOGIC (NVIDIA API) ---

async def openai_chat_nvidia(prompt: str) -> str:
    """Запрос к OpenAI GPT-OSS-120B через NVIDIA API"""

    # Лучше вынести в .env, но оставляем как в оригинале
    

    headers = {
        'Authorization': f'Bearer {NVIDIA_API_KEY}',
        'Content-Type': 'application/json',
    }

    json_data = {
        'model': 'openai/gpt-oss-120b',
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.7, # Чуть убавил для стабильности, можно вернуть 1
        'top_p': 1,
        'stream': False,
        #'reasoning_effort': 'high'
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                'https://integrate.api.nvidia.com/v1/chat/completions',
                headers=headers,
                json=json_data,
                timeout=120.0
            )

            if response.status_code == 200:
                data = response.json()
                # Возвращаем только контент
                return data['choices'][0]['message']['content']
            else:
                # ВАЖНО: Вызываем ошибку, чтобы не возвращать текст ошибки как результат генерации
                # и не списывать за это токены
                raise HTTPException(status_code=502, detail=f"Nvidia API Error: {response.status_code}")
        except HTTPException as he:
            raise he
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Nvidia Execution Error: {str(e)}")

async def process_openai_nvidia(key: str, prompt: str, db: AsyncSession):
    """Обработка запроса к OpenAI через NVIDIA API с точным подсчетом токенов"""

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

    # 2. Проверка баланса (как в GPT/Gemini - просто должен быть положительным)
    if not has_unlimited and user.tokens_balance <= 0:
        raise HTTPException(status_code=402, detail="INSUFFICIENT GLOBAL BALANCE")

    if api_key_obj.limit_tokens <= 0:
        raise HTTPException(status_code=402, detail="API KEY LIMIT EXCEEDED")

    # 3. Генерация ответа (здесь может вылететь ошибка, если API недоступен, и баланс не спишется)
    ai_response = await openai_chat_nvidia(prompt)

    # 4. Подсчет токенов (Input + Output)
    # Используем ту же функцию get_token_count, что и для GPT
    input_tokens_data = await get_token_count(prompt)
    output_tokens_data = await get_token_count(ai_response)
    
    total_cost = input_tokens_data["tokenCount"] + output_tokens_data["tokenCount"]

    # 5. Списание средств
    if not has_unlimited:
        user.tokens_balance -= total_cost 
    
    api_key_obj.limit_tokens -= total_cost 

    await db.commit()
    
    return ai_response

@app.get("/api/run/openai")
async def run_openai_get(key: str, prompt: str = "Hello", db: AsyncSession = Depends(get_db)):
    """GET endpoint для OpenAI GPT-OSS-120B"""
    return await process_openai_nvidia(key, prompt, db)

@app.post("/api/run/openai")
async def run_openai_post(req: OpenAIRequest, db: AsyncSession = Depends(get_db)):
    """POST endpoint для OpenAI GPT-OSS-120B"""
    return await process_openai_nvidia(req.key, req.prompt, db)


async def qwen_chat_nvidia(prompt: str) -> str:
    """Запрос к Qwen 3.5 397B через NVIDIA API"""
    
    headers = {
        'Authorization': f'Bearer {NVIDIA_API_KEY}',
        'Content-Type': 'application/json',
    }

    # Параметры из вашего примера
    json_data = {
        'model': 'qwen/qwen3.5-397b-a17b',
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 1,
        'top_p': 0.95,
        'top_k': 20,
        'presence_penalty': 0,
        'repetition_penalty': 1,
        'stream': False, # Используем False для простоты интеграции с текущим фронтом
        'chat_template_kwargs': {"enable_thinking": True} # Включаем Thinking
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                'https://integrate.api.nvidia.com/v1/chat/completions',
                headers=headers,
                json=json_data,
                timeout=120.0
            )

            if response.status_code == 200:
                data = response.json()
                return data['choices'][0]['message']['content']
            else:
                raise HTTPException(status_code=502, detail=f"Nvidia/Qwen API Error: {response.status_code} - {response.text}")
        except HTTPException as he:
            raise he
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Qwen Execution Error: {str(e)}")

async def process_qwen_nvidia(key: str, prompt: str, db: AsyncSession):
    """Обработка запроса к Qwen с подсчетом токенов"""
    
    # 1. Проверка ключа
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

    if not has_unlimited and user.tokens_balance <= 0:
        raise HTTPException(status_code=402, detail="INSUFFICIENT GLOBAL BALANCE")

    if api_key_obj.limit_tokens <= 0:
        raise HTTPException(status_code=402, detail="API KEY LIMIT EXCEEDED")

    # 2. Генерация
    ai_response = await qwen_chat_nvidia(prompt)

    # 3. Подсчет токенов
    input_tokens_data = await get_token_count(prompt)
    output_tokens_data = await get_token_count(ai_response)
    
    total_cost = input_tokens_data["tokenCount"] + output_tokens_data["tokenCount"]

    # 4. Списание
    if not has_unlimited:
        user.tokens_balance -= total_cost 
    
    api_key_obj.limit_tokens -= total_cost 
    await db.commit()
    
    return ai_response

@app.get("/api/run/qwen")
async def run_qwen_get(key: str, prompt: str = "Hello", db: AsyncSession = Depends(get_db)):
    """GET endpoint для Qwen 3.5"""
    return await process_qwen_nvidia(key, prompt, db)

@app.post("/api/run/qwen")
async def run_qwen_post(req: QwenRequest, db: AsyncSession = Depends(get_db)):
    """POST endpoint для Qwen 3.5"""
    return await process_qwen_nvidia(req.key, req.prompt, db)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)



















