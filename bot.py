from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
import requests
from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


def env_file_encoding() -> str:
    """Detect common Windows .env encodings without exposing any setting value."""
    try:
        prefix = ENV_PATH.read_bytes()[:4]
    except OSError:
        return "utf-8-sig"
    if prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    return "utf-8-sig"


try:
    load_dotenv(ENV_PATH, encoding=env_file_encoding())
except (OSError, UnicodeError) as exc:
    # Keep the bot startable so an admin can inspect the setup and logs.
    logging.getLogger("lucaxshop").warning("Could not parse .env (%s)", type(exc).__name__)


def env_int(name: str, default: int = 0, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip() or default)
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = env_int("DISCORD_GUILD_ID", 0)
SELLER_KEY = os.getenv("KEYAUTH_SELLER_KEY", "").strip()


def get_seller_key() -> str:
    """Read the Seller Key from this project's .env, supporting common encodings."""
    global SELLER_KEY
    values: dict[str, Any] = {}
    if ENV_PATH.is_file():
        try:
            values = dict(dotenv_values(ENV_PATH, encoding=env_file_encoding()))
        except Exception as exc:
            logger.warning("Could not read .env (%s)", type(exc).__name__)
    candidates = (
        values.get("KEYAUTH_SELLER_KEY"), os.getenv("KEYAUTH_SELLER_KEY"),
        values.get("SELLER_KEY"), os.getenv("SELLER_KEY"),
        values.get("KEYAUTH_SECRET"), os.getenv("KEYAUTH_SECRET"),
    )
    key = next((str(v).strip().strip('"').strip("'") for v in candidates if v and str(v).strip()), "")
    SELLER_KEY = key
    return key


def safe_error_text(error: Any, limit: int = 750) -> str:
    """Mask secrets safely. Never call str.replace with an empty secret (it inserts between every character)."""
    message = str(error)
    secrets = {
        get_seller_key(),
        os.getenv("KEYAUTH_SELLER_KEY", "").strip(),
        os.getenv("SELLER_KEY", "").strip(),
        os.getenv("KEYAUTH_SECRET", "").strip(),
        BOT_TOKEN,
    }
    for secret in secrets:
        if secret and len(secret) >= 6:
            message = message.replace(secret, "[ซ่อน]")
    return message[:limit]

LIFETIME_DAYS = env_int("LIFETIME_DAYS", 36500, minimum=365, maximum=36500)
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).expanduser()
if not DATA_DIR.is_absolute():
    DATA_DIR = (BASE_DIR / DATA_DIR).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "lucaxshop.sqlite3"
DB_EMPTY_SENTINEL = "__LUCAX_EMPTY__"

DEFAULT_SETTINGS: dict[str, str] = {
    "KEY_CHANNEL_ID": os.getenv("KEY_CHANNEL_ID", "").strip(),
    "REDEEM_CHANNEL_ID": os.getenv("REDEEM_CHANNEL_ID", "").strip(),
    "LOG_CHANNEL_ID": os.getenv("LOG_CHANNEL_ID", "").strip(),
    "KEYGEN_ROLE_IDS": os.getenv("KEYGEN_ROLE_IDS", "").strip(),
    "ADMIN_USER_IDS": os.getenv("ADMIN_USER_IDS", "").strip(),
    "SUPERADMIN_ROLE_IDS": os.getenv("SUPERADMIN_ROLE_IDS", "").strip(),
    "SUPERADMIN_USER_IDS": os.getenv("SUPERADMIN_USER_IDS", "").strip(),
    "LEVEL_ROLE_MAP": os.getenv("LEVEL_ROLE_MAP", "").strip(),
    "PANEL_LOGO_URL": os.getenv("PANEL_LOGO_URL", "").strip(),
    "PANEL_BANNER_URL": os.getenv("PANEL_BANNER_URL", os.getenv("PANEL_IMAGE_URL", "")).strip(),
    "PANEL_EXTRA_IMAGE_URL": os.getenv("PANEL_EXTRA_IMAGE_URL", "").strip(),
    "KEYAUTH_LEVEL": os.getenv("KEYAUTH_LEVEL", "1").strip() or "1",
    "KEYAUTH_MASK": os.getenv("KEYAUTH_MASK", "******-******-******-{days}D").strip() or "******-******-******-{days}D",
    "KEYAUTH_CHARACTER": os.getenv("KEYAUTH_CHARACTER", "1").strip() or "1",
    "MAX_KEYS_PER_REQUEST": os.getenv("MAX_KEYS_PER_REQUEST", "100").strip() or "100",
    "KEYAUTH_TIMEOUT_SECONDS": os.getenv("KEYAUTH_TIMEOUT_SECONDS", "35").strip() or "35",
    "KEYAUTH_CONCURRENCY": os.getenv("KEYAUTH_CONCURRENCY", "3").strip() or "3",
    "DEFAULT_PROGRAM_NAME": os.getenv("DEFAULT_PROGRAM_NAME", "").strip(),
}
RUNTIME_CONFIG: dict[str, str] = dict(DEFAULT_SETTINGS)

logger = logging.getLogger("lucaxshop")
logger.setLevel(logging.INFO)
logger.handlers.clear()
formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
try:
    file_handler = RotatingFileHandler(DATA_DIR / "bot.log", maxBytes=2_000_000, backupCount=4, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
except OSError:
    logger.warning("Cannot open file log; console logging remains active")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
_KEYGEN_LOCK: asyncio.Lock | None = None
COMMANDS_SYNCED = False
PANELS_READY = False


def keygen_lock() -> asyncio.Lock:
    global _KEYGEN_LOCK
    if _KEYGEN_LOCK is None:
        _KEYGEN_LOCK = asyncio.Lock()
    return _KEYGEN_LOCK


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _ClosingConnection(sqlite3.Connection):
    """`with db_connect()` commits and then really closes the connection."""

    def __exit__(self, *exc_info: Any) -> bool | None:
        try:
            return super().__exit__(*exc_info)
        finally:
            self.close()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, factory=_ClosingConnection)
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            guild_id TEXT,
            actor_id TEXT NOT NULL,
            actor_name TEXT NOT NULL,
            action TEXT NOT NULL,
            outcome TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT ''
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp)")
        conn.execute("CREATE TABLE IF NOT EXISTS bot_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS redeemed_keys (
            key_hash TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role_id TEXT NOT NULL,
            level TEXT NOT NULL,
            key_hint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )""")
        rows = conn.execute("SELECT setting_key, setting_value FROM bot_settings").fetchall()
        conn.commit()
    for name, value in rows:
        name, value = str(name), str(value)
        if value == DB_EMPTY_SENTINEL:
            # A prior blank DB setting must not hide a value newly added to .env.
            # To explicitly clear it, clear the corresponding .env value as well.
            RUNTIME_CONFIG[name] = str(DEFAULT_SETTINGS.get(name, "")).strip()
        elif value.strip():
            RUNTIME_CONFIG[name] = value
        elif name not in DEFAULT_SETTINGS or not DEFAULT_SETTINGS.get(name, "").strip():
            # Older versions stored blank defaults in SQLite; don't let these hide new .env values.
            RUNTIME_CONFIG[name] = ""


def setting_value(name: str, default: str = "") -> str:
    return str(RUNTIME_CONFIG.get(name, default)).strip()


def setting_int(name: str, default: int = 0, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(setting_value(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def persist_setting(name: str, value: str) -> None:
    clean = str(value).strip()
    stored = clean if clean else DB_EMPTY_SENTINEL
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO bot_settings(setting_key, setting_value) VALUES(?,?) "
            "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
            (name, stored),
        )
        conn.commit()
    RUNTIME_CONFIG[name] = clean


def parse_ids(name: str) -> set[int]:
    ids: set[int] = set()
    for item in setting_value(name).split(","):
        value = item.strip()
        if value.isdigit():
            ids.add(int(value))
    return ids


def level_role_map() -> dict[str, int]:
    result: dict[str, int] = {}
    # Format: level:role_id,level:role_id
    for pair in setting_value("LEVEL_ROLE_MAP").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        level, role_id = pair.split(":", 1)
        level, role_id = level.strip(), role_id.strip()
        if level and role_id.isdigit():
            result[level] = int(role_id)
    return result


def member_role_ids(member: discord.Member) -> set[int]:
    return {role.id for role in member.roles}


def has_superadmin_access(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    users, roles = parse_ids("SUPERADMIN_USER_IDS"), parse_ids("SUPERADMIN_ROLE_IDS")
    if users or roles:
        return interaction.user.id in users or bool(member_role_ids(interaction.user) & roles)
    # First-run bootstrap only. Once Superadmin IDs/roles are saved, only those entries apply.
    return interaction.user.guild_permissions.administrator


def can_operate_keys(interaction: discord.Interaction) -> bool:
    if has_superadmin_access(interaction):
        return True
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    return interaction.user.id in parse_ids("ADMIN_USER_IDS") or bool(member_role_ids(interaction.user) & parse_ids("KEYGEN_ROLE_IDS"))


async def require_operator(interaction: discord.Interaction) -> bool:
    if interaction.guild and can_operate_keys(interaction):
        return True
    await reply_private(interaction, "ไม่มีสิทธิ์")
    return False


async def require_superadmin(interaction: discord.Interaction) -> bool:
    if interaction.guild and has_superadmin_access(interaction):
        return True
    await reply_private(interaction, "ไม่มีสิทธิ์")
    return False


async def reply_private(interaction: discord.Interaction, content: str, **kwargs: Any) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True, **kwargs)
    else:
        await interaction.response.send_message(content, ephemeral=True, **kwargs)


def key_hint(key: str) -> str:
    value = key.strip()
    return f"••••••{value[-4:]}" if len(value) > 4 else "••••"


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.strip().upper().encode("utf-8")).hexdigest()


def split_keys(raw: str, limit: int = 25) -> list[str]:
    values = [part.strip().strip("`\"'") for part in re.split(r"[\r\n,;]+", raw) if part.strip().strip("`\"'")]
    unique = list(dict.fromkeys(values))
    if not unique:
        raise ValueError("ใส่คีย์ก่อน")
    if len(unique) > limit:
        raise ValueError(f"ได้ไม่เกิน {limit} คีย์ต่อครั้ง")
    if any(len(key) > 200 or len(key) < 3 for key in unique):
        raise ValueError("มีคีย์ที่รูปแบบไม่ถูกต้อง")
    return unique


# unit → (seconds_per_unit, label_th)
DURATION_TABLE: dict[str, tuple[int, str]] = {
    "min": (60, "นาที"), "mins": (60, "นาที"), "minute": (60, "นาที"), "minutes": (60, "นาที"), "นาที": (60, "นาที"),
    "h": (3600, "ชั่วโมง"), "hr": (3600, "ชั่วโมง"), "hrs": (3600, "ชั่วโมง"), "hour": (3600, "ชั่วโมง"), "hours": (3600, "ชั่วโมง"), "ชั่วโมง": (3600, "ชั่วโมง"),
    "d": (86400, "วัน"), "day": (86400, "วัน"), "days": (86400, "วัน"), "วัน": (86400, "วัน"),
    "w": (604800, "สัปดาห์"), "week": (604800, "สัปดาห์"), "weeks": (604800, "สัปดาห์"), "สัปดาห์": (604800, "สัปดาห์"), "อาทิตย์": (604800, "สัปดาห์"),
    "m": (2592000, "เดือน"), "mo": (2592000, "เดือน"), "month": (2592000, "เดือน"), "months": (2592000, "เดือน"), "เดือน": (2592000, "เดือน"),
    "y": (31536000, "ปี"), "year": (31536000, "ปี"), "years": (31536000, "ปี"), "ปี": (31536000, "ปี"),
}
# KeyAuth minimum expiry is 1 second; practical max is 36,500 days
_MAX_SECONDS = 36500 * 86400


def parse_duration(value: str) -> tuple[int, str, int]:
    """Return (expiry_days_for_add_api, label, total_seconds).
    total_seconds is used to call the extend API for sub-day precision."""
    raw = value.strip().lower().replace(" ", "")
    if raw in {"lifetime", "life", "ตลอดชีพ", "ถาวร"}:
        return LIFETIME_DAYS, f"Lifetime* ({LIFETIME_DAYS:,} วัน)", LIFETIME_DAYS * 86400
    match = re.fullmatch(r"(\d{1,6})([a-zก-๙]+)?", raw)
    if not match or (match.group(2) and match.group(2) not in DURATION_TABLE):
        raise ValueError("เช่น 45 (วัน), 3h, 90min, 2w, 3m, 1y, lifetime")
    count = int(match.group(1))
    secs_per, unit_label = DURATION_TABLE[match.group(2) or "d"]
    total_secs = count * secs_per
    if count < 1 or total_secs > _MAX_SECONDS:
        raise ValueError("อายุคีย์ต้องไม่เกิน 36,500 วัน")
    # How many whole days for the initial add (minimum 1)
    days = max(1, total_secs // 86400)
    # Human-readable label
    if secs_per < 3600:
        label = f"{count} {unit_label} (~{total_secs//60} นาที)"
    elif secs_per < 86400:
        mins = total_secs // 60
        label = f"{count} {unit_label} ({mins//60}h {mins%60}m)"
    else:
        label = f"{count} {unit_label}"
    return int(days), label, total_secs


def build_key_mask(style: str, program: str, expiry_days: int) -> tuple[str, str, str]:
    suffix = f"{expiry_days}D"
    style = style.strip().lower()
    if style == "random":
        mask = setting_value("KEYAUTH_MASK", "******-******-******-{days}D").replace("{days}", str(expiry_days))
        label, clean_program = "Random", ""
    elif style == "brand":
        mask, label, clean_program = f"LUCAXSHOP-******-{suffix}", "Luca X Shop", ""
    elif style == "program":
        clean_program = re.sub(r"[^A-Za-z0-9]", "", program).upper()[:14]
        if not clean_program:
            raise ValueError("ใส่ชื่อโปรแกรมก่อน")
        mask, label = f"LUCAXSHOP-{clean_program}-******-{suffix}", "Luca X Shop + Program"
    else:
        raise ValueError("เลือกรูปแบบคีย์ไม่ถูกต้อง")
    if len(mask) > 64 or not re.fullmatch(r"[A-Za-z0-9*-]+", mask) or "*" not in mask:
        raise ValueError("รูปแบบคีย์ไม่ถูกต้อง • ตรวจ Key Settings")
    return mask, label, clean_program


def request_keyauth(params: dict[str, str]) -> dict[str, Any]:
    seller_key = get_seller_key()
    if not seller_key:
        env_state = "พบไฟล์ .env" if ENV_PATH.is_file() else "ไม่พบไฟล์ .env"
        raise RuntimeError(
            f"ไม่พบ Seller Key ({env_state}) • ใส่ KEYAUTH_SELLER_KEY=... "
            "ในไฟล์ .env โฟลเดอร์เดียวกับ bot.py แล้วบันทึกและเปิดบอทใหม่"
        )
    action = params.get("type", "").lower()
    readonly = action in {"info", "verify", "fetchallkeys", "fetchallusers", "getkey", "userdata"}
    attempts = 2 if readonly else 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(
                "https://keyauth.win/api/seller/",
                params={"sellerkey": seller_key, "format": "json", **params},
                timeout=(8, setting_int("KEYAUTH_TIMEOUT_SECONDS", 35, minimum=8, maximum=90)),
            )
        except requests.Timeout as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.7)
                continue
            if not readonly:
                raise RuntimeError("KeyAuth ตอบช้าเกินไป ผลอาจยังไม่ชัดเจน • ตรวจหน้า KeyAuth ก่อนกดซ้ำ") from exc
            raise RuntimeError("KeyAuth ตอบช้าเกินไป • ลองใหม่ภายหลัง") from exc
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.7)
                continue
            if not readonly:
                raise RuntimeError("การเชื่อมต่อขาดระหว่างทำรายการ • ตรวจหน้า KeyAuth ก่อนกดซ้ำ") from exc
            raise RuntimeError("เชื่อมต่อ KeyAuth ไม่สำเร็จ") from exc

        if readonly and response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
            time.sleep(0.7)
            continue
        try:
            data = response.json()
        except ValueError as exc:
            if not readonly:
                raise RuntimeError(f"KeyAuth ตอบ HTTP {response.status_code} แต่บอทอ่านผลไม่ได้ • ตรวจ Dashboard ก่อนกดซ้ำ") from exc
            raise RuntimeError(f"KeyAuth ส่งคำตอบที่อ่านไม่ได้ (HTTP {response.status_code})") from exc
        if not isinstance(data, dict):
            raise RuntimeError("รูปแบบคำตอบจาก KeyAuth ไม่ถูกต้อง")
        if response.status_code >= 500:
            msg = str(data.get("message", "Server error"))[:160]
            if not readonly:
                raise RuntimeError(f"KeyAuth ขัดข้อง: {msg} • ตรวจ Dashboard ก่อนกดซ้ำ")
            if attempt + 1 < attempts:
                time.sleep(0.7)
                continue
        if response.status_code >= 400:
            raise RuntimeError(f"KeyAuth: {str(data.get('message', 'API error'))[:180]}")
        success = data.get("success")
        if success is True or success == 1 or (isinstance(success, str) and success.lower() in {"true", "1"}):
            return data
        raise RuntimeError(f"KeyAuth: {str(data.get('message', 'คำสั่งไม่สำเร็จ'))[:220]}")
    raise RuntimeError("KeyAuth ไม่ตอบกลับ") from last_error


def seller_action(action: str, **kwargs: str) -> dict[str, Any]:
    params = {"type": action, **kwargs}
    return request_keyauth(params)


def parse_key_response(data: dict[str, Any]) -> list[str]:
    raw = data.get("key", data.get("keys", data.get("licenses", [])))
    if isinstance(raw, list):
        keys: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                value = item.get("key", item.get("license", ""))
                if value:
                    keys.append(str(value).strip())
            elif item:
                keys.append(str(item).strip())
        return list(dict.fromkeys(key for key in keys if key))
    if isinstance(raw, str):
        parsed = raw.strip()
        if parsed.startswith("["):
            try:
                decoded = json.loads(parsed)
                if isinstance(decoded, list):
                    return parse_key_response({"keys": decoded})
            except json.JSONDecodeError:
                pass
        result = [item.strip().strip("`\"'") for item in re.split(r"[\r\n,;]+", parsed) if item.strip().strip("`\"'")]
        return list(dict.fromkeys(result))
    return []


def generate_keys(amount: int, expiry_days: int, mask: str, exact_seconds: int | None = None) -> list[str]:
    response = seller_action(
        "add", expiry=str(expiry_days), mask=mask,
        level=setting_value("KEYAUTH_LEVEL", "1") or "1", amount=str(amount),
        owner="", character=setting_value("KEYAUTH_CHARACTER", "1") or "1",
        note="Luca X Shop | Discord", displayToken="0",
    )
    keys = parse_key_response(response)
    if not keys:
        raise RuntimeError("KeyAuth ตอบกลับแต่ไม่พบคีย์ • ตรวจหน้า Licenses ก่อนสร้างซ้ำ")
    if len(keys) != amount:
        raise RuntimeError(f"KeyAuth ส่งกลับ {len(keys)} จาก {amount} คีย์ • ตรวจหน้า Licenses ก่อนสร้างซ้ำ")
    # Sub-day precision: extend each key to the exact duration
    # KeyAuth extend API accepts expiry in seconds when unit=seconds
    if exact_seconds is not None and exact_seconds != expiry_days * 86400:
        failed: list[str] = []
        for key in keys:
            try:
                seller_action("extend", key=key, expiry=str(exact_seconds), unit="seconds", activeOnly="0")
            except Exception as exc:
                logger.warning("extend failed for %s: %s", key_hint(key), exc)
                failed.append(key)
        if failed:
            raise RuntimeError(
                f"สร้างคีย์สำเร็จ แต่ตั้งอายุละเอียดไม่สำเร็จสำหรับ {len(failed)} คีย์ • "
                "ตรวจสอบและแก้อายุในหน้า KeyAuth Dashboard"
            )
    return keys


def build_exports(keys: list[str]) -> dict[str, bytes]:
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    # TXT and PDF are intentionally clean: one key per line and no sequence numbers.
    txt = ("\n".join(keys) + "\n").encode("utf-8")
    csv_out = io.StringIO(newline="")
    writer = csv.writer(csv_out)
    writer.writerow(["Key"])
    writer.writerows([[key] for key in keys])
    csv_bytes = csv_out.getvalue().encode("utf-8-sig")

    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    page_size = 45
    pages = [keys[i:i + page_size] for i in range(0, len(keys), page_size)] or [[]]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    page_ids: list[int] = []
    for page_no, page_keys in enumerate(pages, 1):
        page_id = 5 + (page_no - 1) * 2
        stream_id = page_id + 1
        page_ids.append(page_id)
        ops: list[str] = []
        y = 805
        for key in page_keys:
            ops.append(f"0.09 0.13 0.18 rg BT /F1 9 Tf 40 {y} Td ({esc(key)}) Tj ET")
            y -= 16
        ops.append("0.25 0.30 0.36 rg BT /F2 8 Tf 40 22 Td (Luca X Shop | Generate Key) Tj ET")
        stream = ("\n".join(ops) + "\n").encode("ascii", "replace")
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {stream_id} 0 R >>"
        ).encode("ascii")
        objects[stream_id] = f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream"
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")
    header = b"%PDF-1.4\n%LucaXShop\n"
    chunks = [header]
    offsets = [0] * (max(objects) + 1)
    pos = len(header)
    for obj_id in sorted(objects):
        offsets[obj_id] = pos
        part = f"{obj_id} 0 obj\n".encode("ascii") + objects[obj_id] + b"\nendobj\n"
        chunks.append(part)
        pos += len(part)
    xref_pos = pos
    size = max(objects) + 1
    xref = [f"xref\n0 {size}\n".encode("ascii"), b"0000000000 65535 f \n"]
    for obj_id in range(1, size):
        xref.append(f"{offsets[obj_id]:010d} 00000 n \n".encode("ascii"))
    trailer = f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    pdf = b"".join(chunks) + b"".join(xref) + trailer
    return {
        f"lucaxshop-keys-{stamp}.txt": txt,
        f"lucaxshop-keys-{stamp}.csv": csv_bytes,
        f"lucaxshop-keys-{stamp}.pdf": pdf,
    }


def make_files(file_data: dict[str, bytes], exts: set[str] | None = None) -> list[discord.File]:
    selected = [(name, data) for name, data in file_data.items() if exts is None or Path(name).suffix.lower().lstrip(".") in exts]
    return [discord.File(io.BytesIO(data), filename=name) for name, data in selected]


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or raw.lower() in {"n/a", "na", "none", "null", "unused", "not used", "0"}:
        return None
    if raw.isdigit():
        try:
            number = int(raw)
            if number > 10**12:
                number //= 1000
            if number > 10**9:
                return datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass
    iso = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_duration_days(value: Any) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return None
    days = int(match.group(0))
    return days if 0 <= days <= 36500 else None


def format_date(value: Any) -> str:
    parsed = parse_datetime(value)
    return parsed.strftime("%Y-%m-%d %H:%M UTC") if parsed else (str(value).strip()[:80] if value else "-")


def key_expiry_datetime(data: dict[str, Any]) -> datetime | None:
    for field in ("expiration", "expires", "expiry", "expirationdate", "expirydate"):
        parsed = parse_datetime(data.get(field))
        if parsed:
            return parsed
    used_on = parse_datetime(data.get("usedon"))
    duration_days = parse_duration_days(data.get("duration"))
    if used_on and duration_days is not None:
        return used_on + timedelta(days=duration_days)
    return None


def key_expiration_info(data: dict[str, Any]) -> tuple[str, str]:
    expiry = key_expiry_datetime(data)
    if expiry:
        left = (expiry - utc_now()).total_seconds()
        days_left = max(0, int(left // 86400))
        state = f"หมดอายุแล้ว ({format_date(expiry)})" if left < 0 else f"{format_date(expiry)} • เหลือ {days_left} วัน"
        return state, format_date(expiry)
    status = str(data.get("status", "")).lower()
    if any(word in status for word in ("unused", "not used", "un-used")) or not data.get("usedon"):
        return "ยังไม่เริ่มนับอายุ / API ไม่ส่งวันหมดอายุ", "-"
    return "API ไม่ส่งวันหมดอายุที่อ่านได้", "-"


def key_info_text(key: str, data: dict[str, Any]) -> str:
    expiry_label, _ = key_expiration_info(data)
    return "\n".join([
        f"คีย์: `{key_hint(key)}`",
        f"สถานะ: {str(data.get('status', '-'))[:80] or '-'}",
        f"Level: {str(data.get('level', '-'))[:30] or '-'}",
        f"อายุ: {str(data.get('duration', '-'))[:50] or '-'}",
        f"สร้างเมื่อ: {format_date(data.get('creationdate'))}",
        f"เริ่มใช้: {format_date(data.get('usedon'))}",
        f"หมดอายุ: {expiry_label[:140]}",
        f"ผู้สร้าง: {str(data.get('createdby', '-'))[:80] or '-'}",
        f"ผู้ใช้: {str(data.get('usedby', '-'))[:80] or '-'}",
    ])


async def fetch_channel(channel_id: int) -> discord.abc.Messageable | None:
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        return channel
    try:
        fetched = await bot.fetch_channel(channel_id)
        if isinstance(fetched, (discord.TextChannel, discord.Thread)):
            return fetched
    except (discord.HTTPException, discord.NotFound, discord.Forbidden):
        logger.warning("Channel unavailable: %s", channel_id)
    return None


def db_audit(guild_id: int | None, actor_id: int, actor_name: str, action: str, outcome: str, details: str) -> None:
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO audit_logs(timestamp,guild_id,actor_id,actor_name,action,outcome,details) VALUES(?,?,?,?,?,?,?)",
            (utc_now().isoformat(), str(guild_id or ""), str(actor_id), actor_name[:100], action[:80], outcome[:20], details[:900]),
        )
        conn.commit()


async def audit_event(guild_id: int | None, actor_id: int, actor_name: str, action: str, outcome: str, details: str = "") -> None:
    try:
        await asyncio.to_thread(db_audit, guild_id, actor_id, actor_name, action, outcome, details)
    except Exception:
        logger.exception("Could not write audit record")
    log_channel_id = setting_int("LOG_CHANNEL_ID", 0)
    if not log_channel_id or not guild_id:
        return
    channel = await fetch_channel(log_channel_id)
    if channel is None:
        return
    color = discord.Color.green() if outcome == "success" else discord.Color.orange()
    embed = discord.Embed(title="Luca X Shop | Log", color=color, timestamp=utc_now())
    embed.add_field(name="คนทำ", value=f"<@{actor_id}> (`{actor_id}`)", inline=False)
    embed.add_field(name="รายการ", value=action[:120], inline=True)
    embed.add_field(name="ผล", value="สำเร็จ" if outcome == "success" else "ไม่สำเร็จ", inline=True)
    if details:
        embed.add_field(name="รายละเอียด", value=details[:900], inline=False)
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        logger.warning("Could not send audit message")


async def audit(interaction: discord.Interaction, action: str, outcome: str, details: str = "") -> None:
    await audit_event(
        interaction.guild.id if interaction.guild else None,
        interaction.user.id,
        str(interaction.user), action, outcome, details,
    )


async def send_failure(interaction: discord.Interaction, action: str, exc: Exception) -> None:
    message = safe_error_text(exc, 750)
    await audit(interaction, action, "failure", message)
    content = f"{action} ไม่สำเร็จ\n{message}"
    await reply_private(interaction, content)


def build_panel_embeds(kind: str) -> list[discord.Embed]:
    logo = setting_value("PANEL_LOGO_URL")
    banner = setting_value("PANEL_BANNER_URL")
    extra = setting_value("PANEL_EXTRA_IMAGE_URL")
    if kind == "redeem":
        embed = discord.Embed(title="Luca X Shop", description="Redeem Key • รับยศ", color=discord.Color.from_rgb(17, 87, 145))
    else:
        embed = discord.Embed(title="Luca X Shop", description="Generate Key • Reset HWID • Delete Key • Ban / Unban • Key Info", color=discord.Color.from_rgb(17, 87, 145))
    if logo.startswith(("https://", "http://")):
        embed.set_thumbnail(url=logo)
        embed.set_footer(text="Luca X Shop", icon_url=logo)
    elif bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        embed.set_footer(text="Luca X Shop", icon_url=bot.user.display_avatar.url)
    else:
        embed.set_footer(text="Luca X Shop")
    if banner.startswith(("https://", "http://")):
        embed.set_image(url=banner)
    embeds = [embed]
    if extra.startswith(("https://", "http://")):
        extra_embed = discord.Embed(color=discord.Color.from_rgb(17, 87, 145))
        extra_embed.set_image(url=extra)
        embeds.append(extra_embed)
    return embeds


def panel_setting_key(kind: str, suffix: str, guild_id: int) -> str:
    return f"{kind.upper()}_PANEL_{suffix}_{guild_id}"


async def ensure_one_panel(kind: str, channel_id: int) -> str:
    label = "ห้องจัดการคีย์" if kind == "key" else "ห้อง Redeem"
    if not channel_id:
        logger.warning("%s ยังไม่ได้ตั้งค่า", label)
        return f"{label}: ยังไม่ได้ตั้งค่า Channel ID"
    channel = await fetch_channel(channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        logger.warning("%s ใช้ไม่ได้หรือบอทมองไม่เห็นห้อง (ID %s)", label, channel_id)
        return f"{label}: ไม่พบห้องหรือบอทมองไม่เห็น (ID {channel_id})"

    if bot.user and channel.guild.me:
        permissions = channel.permissions_for(channel.guild.me)
        missing = []
        if not permissions.view_channel:
            missing.append("View Channel")
        if not permissions.send_messages and not isinstance(channel, discord.Thread):
            missing.append("Send Messages")
        if isinstance(channel, discord.Thread) and not permissions.send_messages_in_threads:
            missing.append("Send Messages in Threads")
        if not permissions.embed_links:
            missing.append("Embed Links")
        if missing:
            logger.warning("Missing permissions in channel %s: %s", channel_id, ", ".join(missing))
            return f"{label}: บอทขาดสิทธิ์ {', '.join(missing)}"

    guild_id = channel.guild.id
    msg_key = panel_setting_key(kind, "MESSAGE_ID", guild_id)
    chan_key = panel_setting_key(kind, "CHANNEL_ID", guild_id)
    saved_message_id = setting_int(msg_key, 0)
    saved_channel_id = setting_int(chan_key, 0)
    view: discord.ui.View = RedeemPanelView() if kind == "redeem" else OperationsPanelView()
    embeds = build_panel_embeds(kind)

    if saved_message_id and saved_channel_id == channel_id:
        try:
            message = await channel.fetch_message(saved_message_id)
            await message.edit(embeds=embeds, view=view, content=None, allowed_mentions=discord.AllowedMentions.none())
            return f"{label}: อัปเดตแล้วใน <#{channel_id}>"
        except discord.NotFound:
            logger.info("Stored %s panel missing; will repost it", kind)
        except discord.Forbidden:
            logger.warning("Cannot edit stored %s panel in channel %s; will try posting a replacement", kind, channel_id)
        except discord.HTTPException as exc:
            logger.exception("Could not edit stored %s panel", kind)
            return f"{label}: แก้แผงเดิมไม่สำเร็จ (HTTP {getattr(exc, 'status', '?')}) • ลองใหม่อีกครั้ง"
    elif saved_message_id and saved_channel_id and saved_channel_id != channel_id:
        old_channel = await fetch_channel(saved_channel_id)
        if isinstance(old_channel, (discord.TextChannel, discord.Thread)):
            try:
                old_message = await old_channel.fetch_message(saved_message_id)
                await old_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    try:
        message = await channel.send(embeds=embeds, view=view, allowed_mentions=discord.AllowedMentions.none())
        persist_setting(msg_key, str(message.id))
        persist_setting(chan_key, str(channel_id))
        logger.info("Posted/updated %s panel in channel %s", kind, channel_id)
        return f"{label}: ส่งแผงแล้วที่ <#{channel_id}>"
    except discord.Forbidden:
        logger.exception("Discord denied posting the %s panel to channel %s", kind, channel_id)
        return f"{label}: ส่งไม่ได้ • ตรวจ View Channel, Send Messages, Embed Links และสิทธิ์ของบอท"
    except discord.HTTPException as exc:
        logger.exception("Could not post %s panel", kind)
        return f"{label}: Discord ปฏิเสธการส่ง (HTTP {getattr(exc, 'status', '?')})"


async def refresh_panels() -> list[str]:
    key_status = await ensure_one_panel("key", setting_int("KEY_CHANNEL_ID", 0))
    redeem_status = await ensure_one_panel("redeem", setting_int("REDEEM_CHANNEL_ID", 0))
    return [key_status, redeem_status]


async def delete_message_safely(message: discord.Message) -> None:
    try:
        await message.delete()
    except (discord.HTTPException, discord.NotFound, discord.Forbidden):
        pass


class GenerateOptionsView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.style = "brand"
        self.expiry = "30d"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await reply_private(interaction, "เมนูนี้ไม่ใช่ของคุณ")
            return False
        if not can_operate_keys(interaction):
            await reply_private(interaction, "ไม่มีสิทธิ์")
            return False
        return True

    def summary(self) -> str:
        styles = {"random": "Random", "brand": "Luca X Shop", "program": "Luca X Shop + Program"}
        expiry_labels = {"7d": "7 วัน", "30d": "30 วัน", "1m": "1 เดือน", "3m": "3 เดือน", "6m": "6 เดือน", "1y": "1 ปี", "lifetime": "Lifetime*", "custom": "กำหนดเอง (นาที/ชั่วโมง/วัน)"}
        return f"**Generate Key**\nแบบ: {styles[self.style]}\nอายุ: {expiry_labels[self.expiry]}"

    @discord.ui.select(placeholder="Key format", row=0, options=[
        discord.SelectOption(label="Random", value="random"),
        discord.SelectOption(label="Luca X Shop", value="brand", default=True),
        discord.SelectOption(label="Luca X Shop + Program", value="program"),
    ])
    async def choose_style(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        self.style = select.values[0]
        for option in select.options:
            option.default = option.value == self.style
        await interaction.response.edit_message(content=self.summary(), view=self)

    @discord.ui.select(placeholder="Expiry", row=1, options=[
        discord.SelectOption(label="7 days", value="7d"),
        discord.SelectOption(label="30 days", value="30d", default=True),
        discord.SelectOption(label="1 month", value="1m"),
        discord.SelectOption(label="3 months", value="3m"),
        discord.SelectOption(label="6 months", value="6m"),
        discord.SelectOption(label="1 year", value="1y"),
        discord.SelectOption(label="Lifetime*", value="lifetime"),
        discord.SelectOption(label="Custom • ตั้งเอง", value="custom", description="กรอกจำนวนวันเอง เช่น 45 / 2w / 3m"),
    ])
    async def choose_expiry(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        self.expiry = select.values[0]
        for option in select.options:
            option.default = option.value == self.expiry
        await interaction.response.edit_message(content=self.summary(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=2)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        modal = GenerateAmountModal(self.owner_id, self.style, self.expiry)
        await interaction.response.send_modal(modal)


class GenerateAmountModal(discord.ui.Modal, title="Generate Key"):
    amount = discord.ui.TextInput(label="จำนวนคีย์ (1-100)", placeholder="เช่น 50", min_length=1, max_length=3)
    program_name = discord.ui.TextInput(label="ชื่อโปรแกรม (เฉพาะแบบที่ 3)", placeholder="เช่น FIVEM", required=False, max_length=32)

    def __init__(self, owner_id: int, style: str, expiry: str):
        super().__init__()
        self.owner_id = owner_id
        self.style = style
        self.expiry = expiry
        self.program_name.default = setting_value("DEFAULT_PROGRAM_NAME")[:32] or None
        self.custom_duration: discord.ui.TextInput | None = None
        if expiry == "custom":
            self.custom_duration = discord.ui.TextInput(
                label="ระยะเวลา (เช่น 45, 3h, 90min, 2w, 3m, 1y)", placeholder="45 = 45 วัน • 3h = 3 ชั่วโมง • 90min = 90 นาที • 2w = 2 สัปดาห์",
                min_length=1, max_length=20,
            )
            self.add_item(self.custom_duration)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id or not await require_operator(interaction):
            return
        max_keys = setting_int("MAX_KEYS_PER_REQUEST", 100, minimum=1, maximum=100)
        try:
            amount = int(self.amount.value.strip())
            if not 1 <= amount <= max_keys:
                raise ValueError(f"จำนวนคีย์ต้องอยู่ระหว่าง 1-{max_keys}")
            duration_text = self.custom_duration.value if self.custom_duration else self.expiry
            expiry_days, expiry_label, exact_seconds = parse_duration(duration_text)
            mask, style_label, program = build_key_mask(self.style, self.program_name.value, expiry_days)
        except ValueError as exc:
            await reply_private(interaction, str(exc))
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if keygen_lock().locked():
            await interaction.followup.send("มีรายการสร้างคีย์กำลังทำงานอยู่ ลองอีกครั้งภายหลัง", ephemeral=True)
            return
        try:
            async with keygen_lock():
                keys = await asyncio.to_thread(generate_keys, amount, expiry_days, mask, exact_seconds)
        except Exception as exc:
            await send_failure(interaction, "Generate Key", exc)
            return
        detail = f"{len(keys)} คีย์ • {expiry_label} • {style_label}" + (f" • {program}" if program else "")
        await audit(interaction, "Generate Key", "success", detail)
        export_data = build_exports(keys)
        view = KeyResultView(interaction.user.id, keys, export_data)
        try:
            await interaction.followup.send(f"สร้างแล้ว {len(keys)} คีย์ • {expiry_label}\n{view.summary()}", view=view, ephemeral=True)
        except discord.HTTPException:
            fallback = DATA_DIR / "delivery_fallback"
            fallback.mkdir(parents=True, exist_ok=True)
            for filename, data in export_data.items():
                path = fallback / filename
                path.write_bytes(data)
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
            await audit(interaction, "Deliver Keys", "failure", "KeyAuth created the keys but Discord delivery failed; private server files were saved")
            try:
                await interaction.followup.send("สร้างคีย์แล้ว แต่ส่งกลับ Discord ไม่ได้ • ไฟล์อยู่ใน data/delivery_fallback อย่ากดสร้างซ้ำ", ephemeral=True)
            except discord.HTTPException:
                logger.exception("Could not notify about private key export fallback")


class KeyResultView(discord.ui.View):
    def __init__(self, owner_id: int, keys: list[str], files: dict[str, bytes]):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.keys = keys
        self.files = files
        self.page = 0
        self.page_size = 10
        self.sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await reply_private(interaction, "ไฟล์นี้เป็นของผู้สร้างคีย์เท่านั้น")
            return False
        return True

    def summary(self) -> str:
        pages = max(1, (len(self.keys) + self.page_size - 1) // self.page_size)
        return f"{len(self.keys)} keys • หน้า {self.page + 1}/{pages}"

    def sync_buttons(self) -> None:
        pages = max(1, (len(self.keys) + self.page_size - 1) // self.page_size)
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= pages - 1

    @discord.ui.button(label="Copy page", style=discord.ButtonStyle.primary, emoji="📋", row=0)
    async def copy_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        start = self.page * self.page_size
        subset = self.keys[start:start + self.page_size]
        await interaction.response.send_message("```text\n" + "\n".join(subset)[:1900] + "\n```", ephemeral=True)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self.sync_buttons()
        await interaction.response.edit_message(content=self.summary(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pages = max(1, (len(self.keys) + self.page_size - 1) // self.page_size)
        self.page = min(pages - 1, self.page + 1)
        self.sync_buttons()
        await interaction.response.edit_message(content=self.summary(), view=self)

    async def send_export(self, interaction: discord.Interaction, ext: str) -> None:
        pair = next(((n, b) for n, b in self.files.items() if n.endswith("." + ext)), None)
        if not pair:
            await reply_private(interaction, "ไม่พบไฟล์")
            return
        name, data = pair
        await interaction.response.send_message(f"{ext.upper()} • {len(self.keys)} keys", file=discord.File(io.BytesIO(data), filename=name), ephemeral=True)

    @discord.ui.button(label="TXT", style=discord.ButtonStyle.secondary, row=1)
    async def download_txt(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.send_export(interaction, "txt")

    @discord.ui.button(label="CSV", style=discord.ButtonStyle.secondary, row=1)
    async def download_csv(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.send_export(interaction, "csv")

    @discord.ui.button(label="PDF A4", style=discord.ButtonStyle.secondary, row=1)
    async def download_pdf(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.send_export(interaction, "pdf")


class KeyListView(discord.ui.View):
    def __init__(self, owner_id: int, rows: list[str], file_bytes: bytes):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.rows = rows
        self.file_bytes = file_bytes
        self.page = 0
        self.page_size = 10
        self.sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await reply_private(interaction, "รายการนี้เป็นของผู้เปิดเท่านั้น")
            return False
        return True

    def body(self) -> str:
        pages = max(1, (len(self.rows) + self.page_size - 1) // self.page_size)
        values = self.rows[self.page * self.page_size:(self.page + 1) * self.page_size]
        return f"**Licenses** • {len(self.rows)} รายการ • หน้า {self.page + 1}/{pages}\n" + "\n".join(values)

    def sync_buttons(self) -> None:
        pages = max(1, (len(self.rows) + self.page_size - 1) // self.page_size)
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= pages - 1

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=0)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self.sync_buttons()
        await interaction.response.edit_message(content=self.body(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pages = max(1, (len(self.rows) + self.page_size - 1) // self.page_size)
        self.page = min(pages - 1, self.page + 1)
        self.sync_buttons()
        await interaction.response.edit_message(content=self.body(), view=self)

    @discord.ui.button(label="CSV", style=discord.ButtonStyle.primary, row=0)
    async def export_csv(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("License list • CSV", file=discord.File(io.BytesIO(self.file_bytes), filename="lucaxshop-licenses.csv"), ephemeral=True)


class MultiKeyActionModal(discord.ui.Modal):
    def __init__(self, action: str):
        self.action = action
        titles = {"del": "Delete Key", "ban": "Ban Key", "unban": "Unban Key"}
        super().__init__(title=titles[action])
        self.keys_input = discord.ui.TextInput(label="คีย์ (1 บรรทัดต่อ 1 คีย์ / สูงสุด 25)", style=discord.TextStyle.paragraph, placeholder="วางคีย์ที่นี่", min_length=3, max_length=3500)
        self.add_item(self.keys_input)
        if action == "ban":
            self.reason = discord.ui.TextInput(label="เหตุผล", placeholder="เช่น Refund / Abuse", min_length=2, max_length=150)
            self.add_item(self.reason)
        if action == "del":
            self.confirm = discord.ui.TextInput(label="พิมพ์ DELETE เพื่อยืนยัน", placeholder="DELETE", min_length=6, max_length=6)
            self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_operator(interaction):
            return
        try:
            keys = split_keys(self.keys_input.value, limit=25)
        except ValueError as exc:
            await reply_private(interaction, str(exc))
            return
        if self.action == "del" and self.confirm.value.strip().upper() != "DELETE":
            await reply_private(interaction, "ยกเลิกแล้ว")
            return
        reason = self.reason.value.strip() if self.action == "ban" else ""
        await interaction.response.defer(ephemeral=True, thinking=True)
        semaphore = asyncio.Semaphore(setting_int("KEYAUTH_CONCURRENCY", 3, minimum=1, maximum=5))

        async def do_one(key: str) -> tuple[str, bool, str]:
            async with semaphore:
                try:
                    if self.action == "del":
                        result = await asyncio.to_thread(seller_action, "del", key=key, userToo="0")
                    elif self.action == "ban":
                        result = await asyncio.to_thread(seller_action, "ban", key=key, reason=reason, userToo="0")
                    else:
                        result = await asyncio.to_thread(seller_action, "unban", key=key)
                    return key, True, str(result.get("message", "OK"))[:100]
                except Exception as exc:
                    return key, False, safe_error_text(exc, 120)

        results = await asyncio.gather(*(do_one(key) for key in keys))
        ok = sum(1 for _, success, _ in results if success)
        bad = len(results) - ok
        hints = ", ".join(key_hint(k) for k, success, _ in results if not success)[:500]
        detail = f"สำเร็จ {ok}/{len(keys)}"
        if bad:
            detail += f" • ไม่สำเร็จ/ผลไม่ชัดเจน: {hints}"
        names = {"del": "Delete Key", "ban": "Ban Key", "unban": "Unban Key"}
        await audit(interaction, names[self.action], "success" if not bad else "failure", detail + (f" • เหตุผล: {reason}" if reason else ""))
        lines = [f"สำเร็จ {ok}/{len(keys)}"]
        failed = [(k, message) for k, success, message in results if not success]
        if failed:
            lines.append("**ตรวจสอบคีย์เหล่านี้:**")
            lines.extend(f"`{key_hint(k)}` — {message[:90]}" for k, message in failed[:8])
            if len(failed) > 8:
                lines.append(f"และอีก {len(failed) - 8} รายการ")
        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)


class ResetHwidModal(discord.ui.Modal, title="Reset HWID"):
    key = discord.ui.TextInput(label="License Key", placeholder="วางคีย์ลูกค้า", min_length=3, max_length=200)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_operator(interaction):
            return
        key = self.key.value.strip()
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            info = await asyncio.to_thread(seller_action, "info", key=key)
            username = str(info.get("usedby") or "").strip()
            if not username or username.lower() in {"n/a", "na", "none", "null", "not used", "unused"}:
                raise RuntimeError("คีย์นี้ยังไม่ผูกกับ Username หรือ API ไม่ส่ง Username • ไม่ได้รีเซ็ต")
            result = await asyncio.to_thread(seller_action, "resetuser", user=username)
            msg = str(result.get("message", "Reset OK"))[:200]
            await audit(interaction, "Reset HWID", "success", f"คีย์ {key_hint(key)} • User {username[:80]} • {msg}")
            await interaction.followup.send(f"Reset HWID สำเร็จ • `{username}`", ephemeral=True)
        except Exception as exc:
            await send_failure(interaction, "Reset HWID", exc)


class KeyInfoModal(discord.ui.Modal, title="Key Info"):
    key = discord.ui.TextInput(label="License Key", placeholder="วางคีย์ที่ต้องการค้นหา", min_length=3, max_length=200)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_operator(interaction):
            return
        key = self.key.value.strip()
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await asyncio.to_thread(seller_action, "info", key=key)
            await audit(interaction, "Key Info", "success", f"คีย์ {key_hint(key)} • สถานะ {data.get('status', '-')} • Level {data.get('level', '-')}")
            await interaction.followup.send(key_info_text(key, data), ephemeral=True)
        except Exception as exc:
            await send_failure(interaction, "Key Info", exc)


class OperationsPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Generate Key", style=discord.ButtonStyle.primary, emoji="🔑", custom_id="lucax:generate", row=0)
    async def generate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_operator(interaction):
            return
        view = GenerateOptionsView(interaction.user.id)
        await interaction.response.send_message(view.summary(), view=view, ephemeral=True)

    @discord.ui.button(label="Reset HWID", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="lucax:reset_hwid", row=0)
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_operator(interaction):
            await interaction.response.send_modal(ResetHwidModal())

    @discord.ui.button(label="Key Info", style=discord.ButtonStyle.secondary, emoji="🔎", custom_id="lucax:key_info", row=0)
    async def key_info(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_operator(interaction):
            await interaction.response.send_modal(KeyInfoModal())

    @discord.ui.button(label="Key List", style=discord.ButtonStyle.secondary, emoji="📚", custom_id="lucax:key_list", row=0)
    async def key_list(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_operator(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await asyncio.to_thread(seller_action, "fetchallkeys")
            raw = data.get("keys", [])
            if isinstance(raw, str):
                try:
                    decoded = json.loads(raw)
                    raw = decoded if isinstance(decoded, list) else [raw]
                except json.JSONDecodeError:
                    raw = [s for s in re.split(r"[\r\n,;]+", raw) if s.strip()]
            if not isinstance(raw, list) or not raw:
                await interaction.followup.send("ไม่พบคีย์ในผลลัพธ์จาก KeyAuth", ephemeral=True)
                await audit(interaction, "Key List", "success", "KeyAuth returned an empty key list")
                return
            rows: list[str] = []
            csv_buffer = io.StringIO(newline="")
            writer = csv.writer(csv_buffer)
            writer.writerow(["Key", "Status", "Level", "Duration", "Creation Date"])
            for item in raw:
                if isinstance(item, dict):
                    key = str(item.get("key", item.get("license", "")))
                    status = str(item.get("status", "-"))
                    level = str(item.get("level", "-"))
                    duration = str(item.get("duration", "-"))
                    created = str(item.get("creationdate", "-"))
                else:
                    key, status, level, duration, created = str(item).strip(), "-", "-", "-", "-"
                if not key:
                    continue
                rows.append(f"`{key_hint(key)}` • {status} • L{level} • {duration}")
                writer.writerow([key, status, level, duration, created])
            if not rows:
                raise RuntimeError("ไม่พบรายการ License ในคำตอบ")
            view = KeyListView(interaction.user.id, rows[:500], csv_buffer.getvalue().encode("utf-8-sig"))
            await interaction.followup.send(view.body(), view=view, ephemeral=True)
            await audit(interaction, "Key List", "success", f"แสดง {len(rows)} คีย์ (ซ่อนคีย์เต็มในหน้าแสดงผล)")
        except Exception as exc:
            await send_failure(interaction, "Key List", exc)

    @discord.ui.button(label="Delete Key", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="lucax:delete_key", row=1)
    async def delete_key(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_operator(interaction):
            await interaction.response.send_modal(MultiKeyActionModal("del"))

    @discord.ui.button(label="Ban Key", style=discord.ButtonStyle.danger, emoji="⛔", custom_id="lucax:ban_key", row=1)
    async def ban_key(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_operator(interaction):
            await interaction.response.send_modal(MultiKeyActionModal("ban"))

    @discord.ui.button(label="Unban Key", style=discord.ButtonStyle.secondary, emoji="♻️", custom_id="lucax:unban_key", row=1)
    async def unban_key(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_operator(interaction):
            await interaction.response.send_modal(MultiKeyActionModal("unban"))


class RedeemPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Redeem Key", style=discord.ButtonStyle.success, emoji="🎟️", custom_id="lucax:redeem_key")
    async def redeem(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        redeem_channel = setting_int("REDEEM_CHANNEL_ID", 0)
        if redeem_channel and interaction.channel_id != redeem_channel:
            await reply_private(interaction, "ใช้ในห้อง Redeem เท่านั้น")
            return
        await interaction.response.send_modal(RedeemKeyModal())


class RedeemKeyModal(discord.ui.Modal, title="Redeem Key"):
    key = discord.ui.TextInput(label="License Key", placeholder="วางคีย์ของคุณ", min_length=3, max_length=200)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await reply_private(interaction, "ใช้ในเซิร์ฟเวอร์เท่านั้น")
            return
        target_channel = setting_int("REDEEM_CHANNEL_ID", 0)
        if target_channel and interaction.channel_id != target_channel:
            await reply_private(interaction, "ใช้ในห้อง Redeem เท่านั้น")
            return
        key = self.key.value.strip()
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            # KeyAuth verifies the key; info returns its status and level for role mapping.
            await asyncio.to_thread(seller_action, "verify", key=key)
            info = await asyncio.to_thread(seller_action, "info", key=key)
            status = str(info.get("status", "")).strip().lower()
            if status.startswith(("banned", "blocked", "expired", "invalid", "deleted", "blacklisted")) or status in {"ban", "block", "expire", "invalid", "deleted"}:
                raise RuntimeError("คีย์ถูกแบน หมดอายุ หรือใช้งานไม่ได้")
            expires_at = key_expiry_datetime(info)
            if expires_at and expires_at < utc_now():
                raise RuntimeError("คีย์หมดอายุแล้ว")
            level = str(info.get("level", "")).strip()
            role_id = level_role_map().get(level)
            if not level or not role_id:
                raise RuntimeError(f"คีย์ใช้ได้ แต่ยังไม่ได้ตั้งยศสำหรับ Level {level or '?'}")
            role = interaction.guild.get_role(role_id)
            if role is None:
                try:
                    roles = await interaction.guild.fetch_roles()
                    role = next((item for item in roles if item.id == role_id), None)
                except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                    role = None
            if role is None:
                raise RuntimeError(f"ไม่พบยศที่ตั้งไว้สำหรับ Level {level}")
            fingerprint = key_fingerprint(key)
            existing = None
            with db_connect() as conn:
                existing = conn.execute("SELECT guild_id,user_id,role_id,status FROM redeemed_keys WHERE key_hash=?", (fingerprint,)).fetchone()
                if existing and (str(existing[0]) != str(interaction.guild.id) or str(existing[1]) != str(interaction.user.id)):
                    raise RuntimeError("คีย์นี้ถูก Redeem ไปแล้ว")
                if not existing:
                    try:
                        conn.execute(
                            "INSERT INTO redeemed_keys(key_hash,guild_id,user_id,role_id,level,key_hint,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (fingerprint, str(interaction.guild.id), str(interaction.user.id), str(role_id), level, key_hint(key), "pending", utc_now().isoformat()),
                        )
                        conn.commit()
                    except sqlite3.IntegrityError:
                        raise RuntimeError("คีย์นี้ถูก Redeem ไปแล้ว") from None
            try:
                await interaction.user.add_roles(role, reason=f"Luca X Shop redeem {key_hint(key)} / level {level}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                if not existing:
                    with db_connect() as conn:
                        conn.execute("DELETE FROM redeemed_keys WHERE key_hash=? AND user_id=?", (fingerprint, str(interaction.user.id)))
                        conn.commit()
                raise RuntimeError("บอทให้ยศไม่ได้ • ตรวจสิทธิ์ Manage Roles และลำดับยศของบอท") from exc
            with db_connect() as conn:
                conn.execute("UPDATE redeemed_keys SET status='active', role_id=?, level=? WHERE key_hash=?", (str(role_id), level, fingerprint))
                conn.commit()
            await audit(interaction, "Redeem Key", "success", f"คีย์ {key_hint(key)} • Level {level} • Role ID {role_id}")
            await interaction.followup.send(f"รับยศ **{role.name}** เรียบร้อย", ephemeral=True)
        except Exception as exc:
            await send_failure(interaction, "Redeem Key", exc)


class AdminConfigView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not has_superadmin_access(interaction):
            await reply_private(interaction, "ไม่มีสิทธิ์")
            return False
        return True

    @discord.ui.button(label="Channels", style=discord.ButtonStyle.secondary, row=0)
    async def channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ChannelsConfigModal())

    @discord.ui.button(label="Images", style=discord.ButtonStyle.secondary, row=0)
    async def images(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ImagesConfigModal())

    @discord.ui.button(label="Permissions", style=discord.ButtonStyle.secondary, row=0)
    async def permissions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PermissionsConfigModal(interaction.user.id))

    @discord.ui.button(label="Key Settings", style=discord.ButtonStyle.secondary, row=1)
    async def key_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(KeySettingsModal())

    @discord.ui.button(label="Redeem Roles", style=discord.ButtonStyle.secondary, row=1)
    async def redeem_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(RedeemRolesModal())

    @discord.ui.button(label="Refresh Panels", style=discord.ButtonStyle.primary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        reports = await refresh_panels()
        await audit(interaction, "Refresh Panels", "success", " | ".join(reports))
        await interaction.followup.send("\n".join(reports), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class ChannelsConfigModal(discord.ui.Modal, title="Channels"):
    key_channel = discord.ui.TextInput(label="ห้องจัดการคีย์ ID", required=False, max_length=22)
    redeem_channel = discord.ui.TextInput(label="ห้อง Redeem ID", required=False, max_length=22)
    log_channel = discord.ui.TextInput(label="ห้อง Log ID", required=False, max_length=22)

    def __init__(self):
        super().__init__()
        self.key_channel.default = setting_value("KEY_CHANNEL_ID") or None
        self.redeem_channel.default = setting_value("REDEEM_CHANNEL_ID") or None
        self.log_channel.default = setting_value("LOG_CHANNEL_ID") or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_superadmin(interaction):
            return
        values = {"KEY_CHANNEL_ID": self.key_channel.value.strip(), "REDEEM_CHANNEL_ID": self.redeem_channel.value.strip(), "LOG_CHANNEL_ID": self.log_channel.value.strip()}
        if any(value and not value.isdigit() for value in values.values()):
            await reply_private(interaction, "ใส่ Channel ID เป็นตัวเลข")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        for name, value in values.items():
            if value:
                channel = await fetch_channel(int(value))
                if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                    await interaction.followup.send(f"ไม่พบห้องสำหรับ {name}", ephemeral=True)
                    return
                if interaction.guild and channel.guild.id != interaction.guild.id:
                    await interaction.followup.send("ใช้ห้องในเซิร์ฟเวอร์เดียวกัน", ephemeral=True)
                    return
        for name, value in values.items():
            persist_setting(name, value)
        reports = await refresh_panels()
        await audit(interaction, "Config Channels", "success", "Updated key/redeem/log channels | " + " | ".join(reports))
        await interaction.followup.send("บันทึกแล้ว\n" + "\n".join(reports), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class ImagesConfigModal(discord.ui.Modal, title="Images"):
    logo_url = discord.ui.TextInput(label="Logo URL", required=False, max_length=400, style=discord.TextStyle.paragraph)
    banner_url = discord.ui.TextInput(label="Banner / GIF URL", required=False, max_length=400, style=discord.TextStyle.paragraph)
    extra_url = discord.ui.TextInput(label="รูปใหญ่เพิ่มเติม URL", required=False, max_length=400, style=discord.TextStyle.paragraph)

    def __init__(self):
        super().__init__()
        self.logo_url.default = setting_value("PANEL_LOGO_URL") or None
        self.banner_url.default = setting_value("PANEL_BANNER_URL") or None
        self.extra_url.default = setting_value("PANEL_EXTRA_IMAGE_URL") or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_superadmin(interaction):
            return
        values = {"PANEL_LOGO_URL": self.logo_url.value.strip(), "PANEL_BANNER_URL": self.banner_url.value.strip(), "PANEL_EXTRA_IMAGE_URL": self.extra_url.value.strip()}
        if any(value and not value.startswith(("https://", "http://")) for value in values.values()):
            await reply_private(interaction, "ลิงก์รูปต้องขึ้นต้นด้วย https://")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        for name, value in values.items():
            persist_setting(name, value)
        reports = await refresh_panels()
        await audit(interaction, "Config Images", "success", "Updated logo/banner/extra image")
        await interaction.followup.send("บันทึกรูปแล้ว\n" + "\n".join(reports), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class PermissionsConfigModal(discord.ui.Modal, title="Permissions"):
    keygen_roles = discord.ui.TextInput(label="ยศ GenKey IDs (คั่นด้วย ,)", required=False, max_length=850, style=discord.TextStyle.paragraph)
    keygen_users = discord.ui.TextInput(label="User IDs ที่ใช้ปุ่มได้", required=False, max_length=850, style=discord.TextStyle.paragraph)
    super_roles = discord.ui.TextInput(label="ยศ Superadmin IDs", required=False, max_length=850, style=discord.TextStyle.paragraph)
    super_users = discord.ui.TextInput(label="User IDs Superadmin", required=False, max_length=850, style=discord.TextStyle.paragraph)
    level_map = discord.ui.TextInput(label="Level:RoleID เช่น 1:123,2:456", required=False, max_length=900, style=discord.TextStyle.paragraph)

    def __init__(self, actor_id: int):
        super().__init__()
        self.actor_id = actor_id
        self.keygen_roles.default = setting_value("KEYGEN_ROLE_IDS") or None
        self.keygen_users.default = setting_value("ADMIN_USER_IDS") or None
        self.super_roles.default = setting_value("SUPERADMIN_ROLE_IDS") or None
        self.super_users.default = setting_value("SUPERADMIN_USER_IDS") or None
        self.level_map.default = setting_value("LEVEL_ROLE_MAP") or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_superadmin(interaction):
            return
        values = {
            "KEYGEN_ROLE_IDS": self.keygen_roles.value,
            "ADMIN_USER_IDS": self.keygen_users.value,
            "SUPERADMIN_ROLE_IDS": self.super_roles.value,
            "SUPERADMIN_USER_IDS": self.super_users.value,
            "LEVEL_ROLE_MAP": self.level_map.value,
        }
        for name, value in values.items():
            if name == "LEVEL_ROLE_MAP":
                for pair in value.split(","):
                    pair = pair.strip()
                    if pair and (":" not in pair or not pair.split(":", 1)[1].strip().isdigit() or not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", pair.split(":", 1)[0].strip())):
                        await reply_private(interaction, "Level map ใช้รูปแบบ 1:ROLE_ID,2:ROLE_ID")
                        return
            else:
                for item in value.split(","):
                    item = item.strip()
                    if item and not item.isdigit():
                        await reply_private(interaction, f"{name}: ใส่ ID ตัวเลขคั่นด้วย ,")
                        return
        # Keep the current superadmin authorized to undo a typo.
        super_users = {part.strip() for part in values["SUPERADMIN_USER_IDS"].split(",") if part.strip().isdigit()}
        super_users.add(str(interaction.user.id))
        values["SUPERADMIN_USER_IDS"] = ",".join(sorted(super_users, key=int))
        await interaction.response.defer(ephemeral=True, thinking=True)
        for name, value in values.items():
            persist_setting(name, value)
        await audit(interaction, "Config Permissions", "success", "Updated GenKey/Superadmin IDs and level-role map")
        await interaction.followup.send("บันทึกสิทธิ์แล้ว", ephemeral=True)


class KeySettingsModal(discord.ui.Modal, title="Key Settings"):
    level = discord.ui.TextInput(label="KeyAuth Level", required=True, max_length=12)
    random_mask = discord.ui.TextInput(label="Random format (* = random, {days} = อายุ)", required=True, max_length=64)
    max_keys = discord.ui.TextInput(label="สูงสุดต่อครั้ง (1-100)", required=True, max_length=3)
    timeout = discord.ui.TextInput(label="API timeout (8-90 วินาที)", required=True, max_length=2)
    default_program = discord.ui.TextInput(label="ชื่อโปรแกรมเริ่มต้น", required=False, max_length=32)

    def __init__(self):
        super().__init__()
        self.level.default = setting_value("KEYAUTH_LEVEL", "1")
        self.random_mask.default = setting_value("KEYAUTH_MASK", "******-******-******-{days}D")
        self.max_keys.default = str(setting_int("MAX_KEYS_PER_REQUEST", 100, minimum=1, maximum=100))
        self.timeout.default = str(setting_int("KEYAUTH_TIMEOUT_SECONDS", 35, minimum=8, maximum=90))
        self.default_program.default = setting_value("DEFAULT_PROGRAM_NAME") or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_superadmin(interaction):
            return
        level = self.level.value.strip()
        mask = self.random_mask.value.strip()
        try:
            maximum, timeout = int(self.max_keys.value.strip()), int(self.timeout.value.strip())
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,12}", level):
                raise ValueError("Level ไม่ถูกต้อง")
            test_mask = mask.replace("{days}", "30")
            if len(test_mask) > 64 or not re.fullmatch(r"[A-Za-z0-9*-]+", test_mask) or "*" not in test_mask:
                raise ValueError("Mask ใช้ A-Z, 0-9, -, *, {days} เท่านั้น")
            if not 1 <= maximum <= 100:
                raise ValueError("จำนวนสูงสุดต้องเป็น 1-100")
            if not 8 <= timeout <= 90:
                raise ValueError("Timeout ต้องเป็น 8-90")
        except ValueError as exc:
            await reply_private(interaction, str(exc))
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        for name, value in {
            "KEYAUTH_LEVEL": level,
            "KEYAUTH_MASK": mask,
            "MAX_KEYS_PER_REQUEST": str(maximum),
            "KEYAUTH_TIMEOUT_SECONDS": str(timeout),
            "DEFAULT_PROGRAM_NAME": self.default_program.value.strip(),
        }.items():
            persist_setting(name, value)
        await audit(interaction, "Config Key Settings", "success", f"Level {level}; max {maximum}; timeout {timeout}s; random mask updated")
        await interaction.followup.send("บันทึกแล้ว", ephemeral=True)


class RedeemRolesModal(discord.ui.Modal, title="Redeem Roles"):
    role_map = discord.ui.TextInput(label="Level:RoleID เช่น 1:123,2:456", required=True, max_length=900, style=discord.TextStyle.paragraph)

    def __init__(self):
        super().__init__()
        self.role_map.default = setting_value("LEVEL_ROLE_MAP") or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_superadmin(interaction):
            return
        value = self.role_map.value.strip()
        if not value:
            await reply_private(interaction, "ใส่ Level:RoleID ก่อน")
            return
        for pair in value.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if ":" not in pair:
                await reply_private(interaction, "ใช้รูปแบบ 1:ROLE_ID,2:ROLE_ID")
                return
            level, rid = pair.split(":", 1)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", level.strip()) or not rid.strip().isdigit():
                await reply_private(interaction, "ใช้รูปแบบ 1:ROLE_ID,2:ROLE_ID")
                return
        await interaction.response.defer(ephemeral=True, thinking=True)
        persist_setting("LEVEL_ROLE_MAP", value)
        await audit(interaction, "Config Redeem Roles", "success", f"Role mapping updated for levels {', '.join(level_role_map().keys())}")
        await interaction.followup.send("บันทึกยศแล้ว", ephemeral=True)


async def send_logs(interaction: discord.Interaction, limit: int = 10) -> None:
    def load_rows():
        with db_connect() as conn:
            return conn.execute("SELECT timestamp,actor_name,action,outcome,details FROM audit_logs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 15)),)).fetchall()
    try:
        rows = await asyncio.to_thread(load_rows)
    except Exception as exc:
        await reply_private(interaction, f"อ่าน Log ไม่ได้: {str(exc)[:150]}")
        return
    if not rows:
        await reply_private(interaction, "ยังไม่มี Log")
        return
    lines = []
    for stamp, actor, action, outcome, detail in rows:
        icon = "✓" if outcome == "success" else "!"
        line = f"{icon} `{str(stamp).replace('T', ' ')[:19]} UTC` **{action}** • {actor}"
        if detail:
            line += f" — {detail}"
        lines.append(line[:350])
    await reply_private(interaction, "\n".join(lines)[:1950])


@bot.tree.command(name="setup", description="ตั้งค่าห้องและส่งแผงบอท")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def setup_command(interaction: discord.Interaction) -> None:
    if not await require_superadmin(interaction):
        return
    view = AdminConfigView(interaction.user.id)
    await interaction.response.send_message("**Luca X Shop • ตั้งค่า**", view=view, ephemeral=True)
    reports = await refresh_panels()
    env_status = (
        f".env: {'พบไฟล์' if ENV_PATH.is_file() else 'ไม่พบไฟล์'}"
        f" • Seller Key: {'ตั้งแล้ว' if get_seller_key() else 'ยังไม่พบ'}"
        f"\nไฟล์ตั้งค่า: `{ENV_PATH}`"
    )
    await interaction.followup.send(env_status + "\n" + "\n".join(reports), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="adminconfig", description="ตั้งค่าบอท Luca X Shop")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def adminconfig(interaction: discord.Interaction) -> None:
    if not await require_superadmin(interaction):
        return
    view = AdminConfigView(interaction.user.id)
    await interaction.response.send_message("**Luca X Shop • Config**", view=view, ephemeral=True)


@bot.tree.command(name="botstatus", description="ดูสถานะบอท")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def botstatus(interaction: discord.Interaction) -> None:
    if not await require_superadmin(interaction):
        return
    try:
        with db_connect() as conn:
            audit_count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
            redeem_count = conn.execute("SELECT COUNT(*) FROM redeemed_keys WHERE status='active'").fetchone()[0]
        writable = os.access(DATA_DIR, os.W_OK)
    except Exception:
        audit_count, redeem_count, writable = "?", "?", False
    lines = [
        f"Bot: {'Online' if bot.is_ready() else 'Starting'}",
        f".env: {'Found' if ENV_PATH.is_file() else 'Missing'}",
        f".env path: {ENV_PATH}",
        f"Seller Key: {'Set' if get_seller_key() else 'Missing'}",
        f"Key channel: {setting_int('KEY_CHANNEL_ID', 0) or '-'}",
        f"Redeem channel: {setting_int('REDEEM_CHANNEL_ID', 0) or '-'}",
        f"Log channel: {setting_int('LOG_CHANNEL_ID', 0) or '-'}",
        f"Level roles: {len(level_role_map())}",
        f"Redeems: {redeem_count} • Logs: {audit_count}",
        f"Storage: {'OK' if writable else 'Error'} • Ping: {round(bot.latency * 1000)} ms",
    ]
    await reply_private(interaction, "\n".join(lines))


@bot.tree.command(name="auditlogs", description="ดูกิจกรรมล่าสุด")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(limit="จำนวนรายการ 1-15")
async def auditlogs(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 15] = 10) -> None:
    if not await require_superadmin(interaction):
        return
    await send_logs(interaction, int(limit))


@bot.event
async def on_ready() -> None:
    global COMMANDS_SYNCED
    logger.info("Online as %s (%s)", bot.user, getattr(bot.user, "id", "-"))
    logger.info("Environment file: %s | Seller Key configured: %s", ENV_PATH, bool(get_seller_key()))
    if not COMMANDS_SYNCED:
        try:
            available_guilds = list(bot.guilds)
            if GUILD_ID:
                selected = [guild for guild in available_guilds if guild.id == GUILD_ID]
                if selected:
                    available_guilds = selected
                else:
                    logger.warning("DISCORD_GUILD_ID is not a guild this bot can see; syncing to connected guilds instead")
            if available_guilds:
                total = 0
                for guild_obj in available_guilds:
                    guild_ref = discord.Object(id=guild_obj.id)
                    bot.tree.copy_global_to(guild=guild_ref)
                    synced = await bot.tree.sync(guild=guild_ref)
                    total += len(synced)
                    logger.info("Synced %s slash commands to guild %s", len(synced), guild_obj.id)
                logger.info("Slash command sync done (%s registrations)", total)
                COMMANDS_SYNCED = True
            else:
                logger.warning("Bot is not in any guild yet; slash commands were not synced")
        except Exception:
            logger.exception("Slash command sync failed")
    global PANELS_READY
    if PANELS_READY:
        return
    try:
        reports = await refresh_panels()
        for report in reports:
            logger.info("Panel setup: %s", report)
        PANELS_READY = True
    except Exception:
        logger.exception("Could not auto-post/update configured panels")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    original = getattr(error, "original", error)
    traceback = getattr(original, "__traceback__", None)
    logger.error(
        "Slash command failed: %s",
        type(original).__name__,
        exc_info=(type(original), original, traceback) if traceback else None,
    )
    short = safe_error_text(original, 260)
    await reply_private(interaction, f"คำสั่งมีปัญหา: {short} • ดู Log เพิ่มเติมได้")


async def main() -> None:
    init_db()
    bot.add_view(OperationsPanelView())
    bot.add_view(RedeemPanelView())
    if not BOT_TOKEN:
        raise SystemExit("ใส่ DISCORD_BOT_TOKEN ใน .env ก่อน")
    try:
        await bot.start(BOT_TOKEN, reconnect=True)
    finally:
        await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped")
