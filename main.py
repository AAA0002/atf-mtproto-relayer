import os
import time
import random
import asyncio
import logging
import urllib.parse
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Header, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    AuthKeyUnregisteredError,
    UserDeactivatedError,
    UserDeactivatedBanError,
    FloodWaitError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError
)
from telethon.tl.functions.messages import RequestWebViewRequest, RequestAppWebViewRequest
from telethon.tl.types import InputBotAppShortName

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("atf-relayer")

# Configuration
API_ID_RAW = os.getenv("TELEGRAM_API_ID", "").strip()
API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
RELAYER_SECRET = os.getenv("RELAYER_API_SECRET", "").strip()

API_ID = int(API_ID_RAW) if API_ID_RAW.isdigit() else 0
DEVICE_MODEL = os.getenv("DEVICE_MODEL", "Samsung Galaxy A30")
SYSTEM_VERSION = os.getenv("SYSTEM_VERSION", "Android 14")
APP_VERSION = os.getenv("APP_VERSION", "11.2.2")

app = FastAPI(
    title="ATF MTProto Relayer Microservice",
    description="Stateless MTProto Telethon Relayer for ATF Token Refresh & Session Management",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory client cache for multi-step authentication: phone -> dict
CLIENT_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 600  # 10 minutes

def clean_cache():
    """Removes stale clients older than CACHE_TTL."""
    now = time.time()
    stale_keys = [k for k, v in CLIENT_CACHE.items() if now - v.get("created_at", 0) > CACHE_TTL]
    for k in stale_keys:
        entry = CLIENT_CACHE.pop(k, None)
        if entry and "client" in entry:
            try:
                asyncio.create_task(entry["client"].disconnect())
            except Exception:
                pass

def extract_tg_web_app_data(url: str) -> str:
    """Extracts raw or URL-decoded tgWebAppData string from Telegram webview URL."""
    if not url:
        return ""
    # Check fragment first (#tgWebAppData=...)
    if "#" in url:
        fragment = url.split("#", 1)[1]
        params = urllib.parse.parse_qs(fragment)
        if "tgWebAppData" in params:
            return params["tgWebAppData"][0]
    # Check query string (?tgWebAppData=...)
    if "?" in url:
        query = url.split("?", 1)[1]
        params = urllib.parse.parse_qs(query)
        if "tgWebAppData" in params:
            return params["tgWebAppData"][0]
    return url

# ==============================================================================
# Pydantic Schemas
# ==============================================================================
class SendCodeRequest(BaseModel):
    phone: str = Field(..., description="Phone number with international country code (e.g. +1234567890)")

class VerifyCodeRequest(BaseModel):
    phone: str
    code: str
    phone_code_hash: str
    password: Optional[str] = None

class SessionItem(BaseModel):
    account_id: int
    session_string: str

class RefreshBatchRequest(BaseModel):
    sessions: List[SessionItem]

# ==============================================================================
# Endpoints
# ==============================================================================

@app.get("/healthz")
@app.get("/wake")
async def wake():
    """Two-step cold-boot wakeup and liveness probe."""
    return {"status": "ready", "timestamp": int(time.time()), "relayer": "atf-mtproto-relayer"}

@app.post("/auth/send-code")
async def send_code(req: SendCodeRequest):
    """Initializes client and sends Telegram SMS / App OTP code."""
    if not API_ID or not API_HASH:
        raise HTTPException(status_code=500, detail="Telegram API credentials not configured on relayer.")

    clean_cache()
    phone = req.phone.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+") and phone.isdigit():
        phone = f"+{phone}"

    # Disconnect any lingering client for this phone
    if phone in CLIENT_CACHE:
        old_client = CLIENT_CACHE.pop(phone, {}).get("client")
        if old_client:
            try:
                await old_client.disconnect()
            except Exception:
                pass

    client = TelegramClient(
        StringSession(),
        API_ID,
        API_HASH,
        device_model=DEVICE_MODEL,
        system_version=SYSTEM_VERSION,
        app_version=APP_VERSION
    )

    try:
        await client.connect()
        res = await client.send_code_request(phone)
        CLIENT_CACHE[phone] = {
            "client": client,
            "phone_code_hash": res.phone_code_hash,
            "created_at": time.time()
        }
        logger.info(f"Code sent successfully to {phone}, hash: {res.phone_code_hash}")
        return {
            "status": "code_sent",
            "phone_code_hash": res.phone_code_hash
        }
    except FloodWaitError as e:
        logger.warning(f"Telegram FloodWait for {phone}: {e.seconds}s")
        raise HTTPException(
            status_code=429,
            detail=f"Telegram rate limit: please wait {e.seconds} seconds before requesting a new code."
        )
    except Exception as e:
        logger.error(f"Error sending code to {phone}: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/auth/verify-code")
async def verify_code(req: VerifyCodeRequest):
    """Verifies OTP code and exports StringSession string."""
    if not API_ID or not API_HASH:
        raise HTTPException(status_code=500, detail="Telegram API credentials not configured on relayer.")

    phone = req.phone.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+") and phone.isdigit():
        phone = f"+{phone}"

    cached = CLIENT_CACHE.get(phone)
    client = cached.get("client") if cached else None

    if not client or not client.is_connected():
        # Recreate client if evicted or not connected
        client = TelegramClient(
            StringSession(),
            API_ID,
            API_HASH,
            device_model=DEVICE_MODEL,
            system_version=SYSTEM_VERSION,
            app_version=APP_VERSION
        )
        await client.connect()

    try:
        try:
            await client.sign_in(phone=phone, code=req.code, phone_code_hash=req.phone_code_hash)
        except SessionPasswordNeededError:
            if req.password:
                await client.sign_in(password=req.password)
            else:
                return {"status": "2fa_required", "message": "Telegram Cloud 2FA password required"}

        session_str = client.session.save()
        CLIENT_CACHE.pop(phone, None)
        await client.disconnect()
        logger.info(f"Session established and saved for {phone}")
        return {
            "status": "success",
            "session_string": session_str
        }
    except SessionPasswordNeededError:
        return {"status": "2fa_required", "message": "Telegram Cloud 2FA password required"}
    except PhoneCodeInvalidError:
        raise HTTPException(status_code=400, detail="Invalid verification code.")
    except PhoneCodeExpiredError:
        raise HTTPException(status_code=400, detail="Verification code has expired.")
    except PasswordHashInvalidError:
        raise HTTPException(status_code=400, detail="Invalid 2FA password.")
    except Exception as e:
        logger.error(f"Error verifying code for {phone}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/session/refresh-batch")
async def refresh_batch(
    req: RefreshBatchRequest,
    x_relayer_secret: Optional[str] = Header(None, alias="X-Relayer-Secret")
):
    """Batch refreshes session tokens by simulating official Telegram WebApp launches."""
    if RELAYER_SECRET and x_relayer_secret != RELAYER_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Relayer Secret")

    if not API_ID or not API_HASH:
        raise HTTPException(status_code=500, detail="Telegram API credentials not configured on relayer.")

    results = []
    target_bots = ["ATF_AIRDROP_bot", "atf_miner_bot"]
    target_url = "https://atfminers.asloni.online/miner/index.html"

    for item in req.sessions:
        # Humanized randomized jitter
        jitter = random.uniform(3.0, 7.0)
        await asyncio.sleep(jitter)

        client = TelegramClient(
            StringSession(item.session_string),
            API_ID,
            API_HASH,
            device_model=DEVICE_MODEL,
            system_version=SYSTEM_VERSION,
            app_version=APP_VERSION
        )

        try:
            await client.connect()
            if not await client.is_user_authorized():
                results.append({
                    "account_id": item.account_id,
                    "success": False,
                    "error": "session_revoked"
                })
                await client.disconnect()
                continue

            bot_entity = None
            for b_name in target_bots:
                try:
                    bot_entity = await client.get_input_entity(b_name)
                    if bot_entity:
                        break
                except Exception:
                    pass

            if not bot_entity:
                results.append({
                    "account_id": item.account_id,
                    "success": False,
                    "error": "bot_not_found"
                })
                await client.disconnect()
                continue

            url = None
            # Method 1: RequestAppWebViewRequest (Telegram Mini App standard)
            try:
                app_res = await client(RequestAppWebViewRequest(
                    peer=bot_entity,
                    app=InputBotAppShortName(bot_id=bot_entity, short_name="miner"),
                    platform="android"
                ))
                url = app_res.url
            except Exception as app_err:
                logger.debug(f"RequestAppWebViewRequest fallback for {item.account_id}: {app_err}")
                # Method 2: RequestWebViewRequest (Direct URL launch)
                try:
                    web_res = await client(RequestWebViewRequest(
                        peer=bot_entity,
                        bot=bot_entity,
                        platform="android",
                        url=target_url
                    ))
                    url = web_res.url
                except Exception as web_err:
                    logger.warning(f"RequestWebViewRequest also failed for {item.account_id}: {web_err}")

            if url:
                token = extract_tg_web_app_data(url)
                results.append({
                    "account_id": item.account_id,
                    "success": True,
                    "token": token,
                    "expires_in": 86400
                })
                logger.info(f"Refreshed token for account {item.account_id}")
            else:
                results.append({
                    "account_id": item.account_id,
                    "success": False,
                    "error": "webview_url_empty"
                })

            await client.disconnect()

        except (AuthKeyUnregisteredError, UserDeactivatedError, UserDeactivatedBanError):
            logger.warning(f"Session revoked for account {item.account_id}")
            results.append({
                "account_id": item.account_id,
                "success": False,
                "error": "session_revoked"
            })
            try:
                await client.disconnect()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Unexpected error refreshing account {item.account_id}: {e}")
            results.append({
                "account_id": item.account_id,
                "success": False,
                "error": str(e)
            })
            try:
                await client.disconnect()
            except Exception:
                pass

    return {"results": results}
