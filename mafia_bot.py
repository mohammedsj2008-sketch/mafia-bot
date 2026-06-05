"""
Mafia 42 Discord Bot - Full rewrite aligned with Mafia42 game system.
32 official roles from mafia42.fandom.com, with proper Cult team, Lover, and
the official role distribution table (4-12 players).

Interactive UI: lobby/day/night all use Discord buttons + ephemeral feedback.

Author: Antigravity (rewrite)
Version: 4.1.0
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import discord
from discord.ext import commands

# ============================================================================
# الإعدادات العامة
# ============================================================================
BOT_VERSION = "4.1.0"
INITIAL_POINTS = 1000
MIN_PLAYERS = 4
MAX_PLAYERS = 12
DAY_DURATION_DEFAULT = 90
NIGHT_DURATION_DEFAULT = 60
VOTE_DURATION = 45

DATA_DIR = Path(os.environ.get("MAFIA_DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
RANKS_FILE = DATA_DIR / "mafia_ranks.json"
ALLOWED_CHANNELS_FILE = DATA_DIR / "mafia_allowed_channels.json"
STATS_FILE = DATA_DIR / "mafia_stats.json"
ACHIEVEMENTS_FILE = DATA_DIR / "mafia_achievements.json"
HISTORY_FILE = DATA_DIR / "mafia_history.json"
CHALLENGES_FILE = DATA_DIR / "mafia_challenges.json"
ELO_FILE = DATA_DIR / "mafia_elo.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("mafia42")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="&", intents=intents, help_command=None)
games: dict[int, "GameState"] = {}
persistent_views_registered = False
lobby_refresh_tasks: dict[int, asyncio.Task] = {}


# ============================================================================
# جدول توزيع الأدوار الرسمي من Mafia42 (Classic Mode)
# ============================================================================
DISTRIBUTION_TABLE: dict[int, dict[str, int]] = {
    4:  {"mafia": 1, "helper": 0, "cult_leader": 0, "fanatic": 0, "special": 3},
    5:  {"mafia": 1, "helper": 0, "cult_leader": 0, "fanatic": 0, "special": 4},
    6:  {"mafia": 1, "helper": 1, "cult_leader": 0, "fanatic": 0, "special": 4},
    7:  {"mafia": 2, "helper": 0, "cult_leader": 0, "fanatic": 0, "special": 5},
    8:  {"mafia": 2, "helper": 1, "cult_leader": 0, "fanatic": 0, "special": 5},
    9:  {"mafia": 2, "helper": 1, "cult_leader": 1, "fanatic": 0, "special": 5},
    10: {"mafia": 2, "helper": 1, "cult_leader": 1, "fanatic": 0, "special": 6},
    11: {"mafia": 2, "helper": 1, "cult_leader": 1, "fanatic": 1, "special": 6},
    12: {"mafia": 2, "helper": 1, "cult_leader": 1, "fanatic": 1, "special": 7},
}

MAFIA_POOL = ["رئيس_المافيا", "العميل", "المُنفّذ", "المحتال"]
HELPER_POOL = ["الجاسوسة", "المضيفة", "اللص", "الرجل_الوحش", "العالم_المجنون", "الساحرة", "المحتال_الانتهازي"]
COP_VIG_POOL = ["الشرطي", "القنّاص"]
DOCTOR_POOL = ["الطبيب", "الممرضة"]
CITIZEN_POOL = [
    "الجندي", "السياسي", "الروحي", "المراسل", "المحقق", "الغول",
    "الشهد", "الكاهن", "الزعيم", "الساحر", "الهاكر", "القاضي",
    "النبي", "المعالج_النفسي", "المرتزق", "المسؤول",
]
CULT_LEADER_ROLE = "زعيم_الطائفة"
FANATIC_ROLE = "المتعصب"
LOVER_ROLE = "العاشق"


# ============================================================================
# نموذج الدور
# ============================================================================
@dataclass(frozen=True)
class Role:
    name: str
    description: str
    team: str
    emoji: str
    rarity: str = "common"
    night_action: bool = False
    cult_team_eligible: bool = False

    def display(self) -> str:
        star = "⭐" if self.rarity == "legendary" else "🔵" if self.rarity == "rare" else ""
        return f"{self.emoji} **{self.name}** {star}".strip()


# ============================================================================
# 32 دور رسمي من Mafia42
# ============================================================================
ROLES: dict[str, Role] = {
    "رئيس_المافيا": Role("رئيس_المافيا", "🎭 **زعيم المافيا.** يجتمع مع المافيا كل ليلة.\nإذا مات، يُمرّر دوره لمافيا آخر عشوائياً.\n✅ له صوت مضاعف.", "mafia", "👑", "legendary", True),
    "العميل": Role("العميل", "🕵️ **عميل سري.** يجتمع مع المافيا.\nيظهر للشرطي كمحقق ليُضلّله.", "mafia", "🕵️", "rare", True),
    "المُنفّذ": Role("المُنفّذ", "🔪 **القاتل المأجور.** يجتمع مع المافيا.\n🎯 يمكنه ضربة قاتلة مستقلة (مرة واحدة).", "mafia", "🔪", "rare", True),
    "المحتال": Role("المحتال", "🦹 **محتال ذكي.** يجتمع مع المافيا.\n🛡️ إذا حاول القنّاص قتله، يبقى حياً.", "mafia", "🦹", "legendary", True),
    "الجاسوسة": Role("الجاسوسة", "🔮 **جاسوسة ذكية.** تجتمع مع المافيا.\n✅ تحقق من لاعب كل ليلة.", "helper", "🔮", "rare", True),
    "المضيفة": Role("المضيفة", "💋 **مضيفة فاتنة.** تجتمع مع المافيا.\n👄 الإغواء: تصوّت في النهار على لاعب بدل المُختار.", "helper", "💋", "rare", False),
    "اللص": Role("اللص", "🦝 **لص ماهر.** يجتمع مع المافيا.\n💰 يسرق ممتلكات لاعب عند موته.", "helper", "🦝", "common", False),
    "الرجل_الوحش": Role("الرجل_الوحش", "🐺 **مخلوق وحشي.** يجتمع مع المافيا.\n🦴 إذا هاجم ومات، يُفترس.", "helper", "🐺", "legendary", False),
    "العالم_المجنون": Role("العالم_المجنون", "🧪 **عالم مجنون.** يجتمع مع المافيا.\n🧪 يحقن لاعباً كل ليلتين.", "helper", "🧪", "legendary", True),
    "الساحرة": Role("الساحرة", "🧙‍♀️ **ساحرة غامضة.** تجتمع مع المافيا.\n🧪 تخلط جرعة إعادة (مرة واحدة).", "helper", "🧙‍♀️", "legendary", True),
    "المحتال_الانتهازي": Role("المحتال_الانتهازي", "🎭 **محتال ينتهز الفرص.** يجتمع مع المافيا.\n✅ يعرف مواطناً عشوائياً.", "helper", "🎭", "legendary", False),
    "الشرطي": Role("الشرطي", "👮 **محقق.** يحقق من لاعب كل ليلة.\n✅ المافيا = متمردين، المواطنون = أبرياء.", "citizens", "👮", "rare", True),
    "القنّاص": Role("القنّاص", "🔫 **قنّاص ثائر.** يقتل لاعباً مرة واحدة.\n✅ المحتال يبقى حياً منه.", "citizens", "🔫", "legendary", True),
    "الطبيب": Role("الطبيب", "💉 **طبيب.** يحمي لاعباً كل ليلة.\n🛡️ لا يحمي نفسه ليلتين متتاليتين.", "citizens", "💉", "rare", True),
    "الممرضة": Role("الممرضة", "👩‍⚕️ **ممرضة شابة.** تحمي لاعباً كل ليلة.", "citizens", "👩‍⚕️", "rare", True),
    "الجندي": Role("الجندي", "🛡️ **جندي شجاع.** يجتاز هجوماً واحداً (مرة واحدة).", "citizens", "🛡️", "common", False),
    "السياسي": Role("السياسي", "🎩 **سياسي.** لا يمكن أن يُحقق من قبل الشرطي.", "citizens", "🎩", "common", False),
    "الروحي": Role("الروحي", "🔮 **روحي.** يرى لاعباً عشوائياً من المافيا في الليلة الأولى.", "citizens", "🔮", "common", False),
    "المراسل": Role("المراسل", "📰 **مراسل.** يحقق من لاعب في ليلتين عشوائيتين.", "citizens", "📰", "common", False),
    "المحقق": Role("المحقق", "🕵️ **محقق خاص.** تحقيق واحد 100% دقيق.", "citizens", "🕵️", "legendary", True),
    "الغول": Role("الغول", "👹 **غول خطير.** عند موته، يختار لاعباً ليموت معه.", "citizens", "👹", "rare", False),
    "الشهد": Role("الشهد", "💀 **شهد/ناسك.** عند موته، يكشف دوره.", "citizens", "💀", "rare", False),
    "الكاهن": Role("الكاهن", "⛪ **كاهن.** إذا هاجم المافيا هدفه، يكتشفهم.", "citizens", "⛪", "legendary", False),
    "الزعيم": Role("الزعيم", "👔 **زعيم عصابة.** يجتمع مع المافيا.\n✅ موته يخسرهم 50% من قوتهم.", "citizens", "👔", "legendary", False),
    "الساحر": Role("الساحر", "🎩 **ساحر.** يبدّل أدوار لاعبين (مرة واحدة).", "citizens", "🎩", "legendary", True),
    "الهاكر": Role("الهاكر", "💻 **هاكر.** يحقق بدقة 100% في الليلتين 2 و 4.", "citizens", "💻", "legendary", True),
    "القاضي": Role("القاضي", "⚖️ **قاضي.** يعرف نتائج تحقيقات النهار.", "citizens", "⚖️", "legendary", False),
    "النبي": Role("النبي", "🌟 **نبي.** إذا حيّاً، المافيا لا تفوز في النهار.", "citizens", "🌟", "legendary", False),
    "المعالج_النفسي": Role("المعالج_النفسي", "🧠 **معالج نفسي.** يعرف حالة اللاعب (سليم/مخدّر/مهاجم).", "citizens", "🧠", "legendary", True),
    "المرتزق": Role("المرتزق", "💰 **مرتزق.** يقبل رشوة من المافيا سراً.", "citizens", "💰", "rare", False),
    "المسؤول": Role("المسؤول", "👔 **مسؤول.** موته يُكشف كل أدوار المافيا.", "citizens", "👔", "legendary", False),
    "زعيم_الطائفة": Role("زعيم_الطائفة", "⛧ **زعيم طائفة.** يجند لاعباً في كل ليل فردية (1،3،5).\n🛐 إذا مات، الطائفة تخسر قوتها.", "cult", "⛧", "legendary", True, True),
    "المتعصب": Role("المتعصب", "🛐 **متعصب.** يبحث عن زعيم الطائفة.\n✅ إذا وجده، يصبح عضواً في الطائفة.", "cult", "🛐", "legendary", True, True),
    "العاشق": Role("العاشق", "💕 **عاشقان.** يقترنان.\n✅ موت أحدهما = موت الآخر. تصويت مضاعف.\n🏆 يفوزان إذا بقيا آخر اثنين.", "neutral", "💕", "legendary", False),
}


# ============================================================================
# رسائل موت متنوعة حسب طريقة القتل
# ============================================================================
DEATH_MESSAGES: dict[str, list[str]] = {
    "vote": [
        "🗳️ صوّت الأغلبية على {target}. رحل عن عالمنا بسلام.",
        "⚖️ العدالة انتصرت. {target} أُعدم بالتصويت.",
        "📜 الحكم صدر. {target} ودع العالم بكرامة.",
        "🔥 الغضب الشعبي ابتلع {target}.",
    ],
    "mafia": [
        "🔪 سحبت المافيا خيوطها في الظلام. {target} وجد حتفه.",
        "🌑 في جوف الليل، تسلل القاتل. {target} لم يسمع خطواته.",
        "🗡️ سكين حادة وصمت مطبق. {target} رحل.",
        "🩸 دماء {target} تلطخت بالظلام.",
    ],
    "sniper": [
        "🔫 طلقة واحدة دوّت في الظلام. {target} سقط.",
        "🎯 مقتل {target} بطلقة محققة.",
        "💥 صوت فتح النار يهز الحي. {target} سقط.",
    ],
    "lover_death": [
        "💔 انكسر قلب العاشق. {target} مات مع حبيبه.",
        "💕 لا حياة بدون حبيب. {target} رحل مع عشقه.",
        "🥀 الذبول أصاب {target} مع فراق عشقه.",
    ],
    "cult": [
        "⛧ الطائفة ابتعت {target}. صار واحداً منهم.",
        "🕯️ شموع الطائفة أضاءت لـ {target}.",
    ],
}


# ============================================================================
# نصائح استراتيجية لكل دور
# ============================================================================
ROLE_TIPS: dict[str, list[str]] = {
    "رئيس_المافيا": ["لا تُعلن عن نفسك مبكراً.", "اختر أهدافك بحكمة — قتك تُخسر المافيا.", "استخدم صوتك المضاعف في التصويت."],
    "العميل": ["أظهر للشرطي كمحقق — سيخدعه.", "ادعَ أنك تحقق في المافيا."],
    "المُنفّذ": ["استخدم ضربتك القاتلة في الوقت المناسب.", "لا تستعجل — اختر هدفاً يقلب اللعبة."],
    "المحتال": ["ادعَ أنك أي دور آخر.", "القنّاص لا يقتلك — استخدم ذلك."],
    "الجاسوسة": ["تحققي في اللاعبين المشبوهين.", "لا تُعلني نتائجك في الشات العام."],
    "المضيفة": ["الإغواء في التصويت يقلب نتائج اليوم.", "اختاري الهدف بحكمة."],
    "اللص": ["اسرق دوراً قوياً عند موته.", "السرقة تعطيك معلومات قيّمة."],
    "الرجل_الوحش": ["إذا هجمت وماتت، ستُفترس.", "التعرف على المافيا يعطيك ميزة."],
    "الشرطي": ["لا تُعلن عن نفسك إلا إذا كنت واثقاً.", "تحقّق في اللاعبين الصامتين أولاً."],
    "القنّاص": ["رصاصة واحدة — اختر الوقت بحكمة.", "هدفك يجب أن يكون المشبوه الأكثر."],
    "الطبيب": ["غيّر أهدافك كل ليلة.", "لا تحمي نفسك ليلتين متتاليتين."],
    "الممرضة": ["احمِ اللاعبين المهمين.", "لا تُعلني عن نفسك."],
    "الجندي": ["استخدم درعك في اللحظة المناسبة.", "ادعَ أنك أي دور آخر."],
    "الروحي": ["سترى مافيا في الليلة الأولى — تتبعه.", "المعلومات قيّمة، لا تُضيعها."],
    "الغول": ["عند موتك، اختر من يموت معك.", "لا تكشف دورك مبكراً."],
    "الشهد": ["عند موتك ستُكشف — استخدمه لصالحك.", "ادعَ دوراً لجذب الانتباه."],
    "الكاهن": ["إذا هاجم المافيا هدفك، ستكشفهم.", "استخدم المعلومات في التصويت."],
    "الزعيم": ["موتك يخسر المافيا — احذر.", "ادعَ دوراً ضعيفاً."],
    "الساحر": ["بدّل أدوار اللاعبين في الوقت المناسب.", "التبديل يقلب اللعبة."],
    "الهاكر": ["الليلتان 2 و 4 تعطيك نتائج دقيقة.", "استخدمها لصالحك."],
    "القاضي": ["اعرف نتائج تحقيقات النهار.", "شارك المعلومات مع الفريق."],
    "النبي": ["إذا بقيت حياً، المافيا لا تفوز نهاراً.", "بقياتك تحمي الفريق."],
    "المعالج_النفسي": ["اعرف حالة اللاعبين — مهاجم أو سليم.", "شارك المعلومات."],
    "المرتزق": ["اقبل رشوة المافيا بحكمة.", "الجانبان يفيدانك."],
    "المسؤول": ["موتك يكشف أدوار المافيا كلها.", "ادعَ دوراً آمناً."],
    "زعيم_الطائفة": ["تجنيدك في الليالي الفردية.", "اختر أعضاء بحكمة."],
    "المتعصب": ["ابحث عن زعيم الطائفة.", "إذا وجدته، ستصبح واحداً منهم."],
    "العاشق": ["حافظ على حبيبك — موتك يقتله.", "إذا بقيتما آخر اثنين، تفوزان."],
}


# ============================================================================
# تحديات يومية
# ============================================================================
DAILY_CHALLENGES: list[dict[str, Any]] = [
    {"id": "win_mafia", "name": "العودة الظلامة", "description": "افوز كمافيا", "team": "mafia", "reward": 50},
    {"id": "win_citizen", "name": "بطل الشعب", "description": "افوز كمواطن", "team": "citizens", "reward": 40},
    {"id": "survive", "name": "الناجي", "description": "ابقَ حياً حتى نهاية اللعبة", "team": "any", "reward": 20},
    {"id": "kill_mafia", "name": "صياد المافيا", "description": "اقتل لاعباً من المافيا (قنّاص)", "role": "القنّاص", "reward": 60},
    {"id": "heal_target", "name": "المخلص", "description": "احمِ لاعباً كان سيُقتل (طبيب)", "role": "الطبيب", "reward": 30},
    {"id": "vote_eliminate", "name": "الحكم", "description": "ساهم في إعدام لاعب بالتصويت", "team": "any", "reward": 25},
    {"id": "cult_leader_win", "name": "زعيم الطائفة", "description": "افوز كزعيم طائفة", "role": "زعيم_الطائفة", "reward": 70},
    {"id": "lovers_win", "name": "قصة حب", "description": "افوز كلعاشقين", "role": "العاشق", "reward": 80},
    {"id": "sheriff_catch", "name": "المحقق", "description": "اكشف عضو مافيا بالتحقيق", "role": "الشرطي", "reward": 50},
    {"id": "three_games", "name": "المنتظم", "description": "العب 3 ألعاب اليوم", "team": "any", "reward": 30},
]


# ============================================================================
# الإنجازات
# ============================================================================
@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    description: str
    emoji: str


ACHIEVEMENTS: dict[str, Achievement] = {
    "first_blood": Achievement("first_blood", "الدم الأول", "كن أول من يموت", "🩸"),
    "survivor": Achievement("survivor", "الناجي", "ابقَ حتى آخر اللعبة", "🛡️"),
    "mafia_lord": Achievement("mafia_lord", "سيد المافيا", "فز بـ 10 لعب كمافيا", "👑"),
    "citizen_hero": Achievement("citizen_hero", "بطل المواطن", "فز بـ 10 لعب كمواطن", "🦸"),
    "lucky_sheriff": Achievement("lucky_sheriff", "الشرطي المحظوظ", "اكشف 5 مافيا بنجاح", "👮"),
    "medic_ace": Achievement("medic_ace", "الطبيب الماهر", "احمِ 5 أهداف بنجاح", "💉"),
    "lone_wolf": Achievement("lone_wolf", "الذئب الوحيد", "فز كمافيا ضد 5+ مواطنين", "🐺"),
    "perfect_game": Achievement("perfect_game", "لعبة مثالية", "فز دون موت أحد من فريقك", "⭐"),
    "veteran": Achievement("veteran", "مخضرم", "العب 50 لعبة", "🎖️"),
    "lovers_fate": Achievement("lovers_fate", "مصير العاشقين", "فز كلعاشقين", "💕"),
    "cult_master": Achievement("cult_master", "سيد الطائفة", "فز كزعيم طائفة", "⛧"),
}


# ============================================================================
# حالة اللعبة
# ============================================================================
@dataclass
class PlayerState:
    user_id: int
    display_name: str
    role_name: str = ""
    alive: bool = True
    protected_today: bool = False
    cult_team: bool = False
    cult_known_cult: set[int] = field(default_factory=set)
    cult_target: Optional[int] = None
    lover_with: Optional[int] = None
    joined_mafia: bool = False
    met_mafia: bool = False
    swindled_role: Optional[str] = None
    sniper_used: bool = False
    night_action_target: Optional[int] = None
    day_vote_target: Optional[int] = None
    night_action_taken: bool = False
    afk_warns: int = 0


@dataclass
class GameState:
    guild_id: int
    channel_id: int
    host_id: int
    players: list[PlayerState] = field(default_factory=list)
    phase: str = "lobby"
    day: int = 0
    dead_players: list[PlayerState] = field(default_factory=list)
    day_votes: dict[int, int] = field(default_factory=dict)
    night_kill_votes: dict[int, int] = field(default_factory=dict)
    night_actions: dict[str, dict[int, Optional[int]]] = field(default_factory=dict)
    doctor_protect: Optional[int] = None
    is_fast: bool = False
    started: bool = False
    is_lovers: bool = False
    lovers_ids: tuple[int, int] = (0, 0)
    had_mafia: bool = False
    lobby_message_id: Optional[int] = None
    day_message_id: Optional[int] = None
    night_message_id: Optional[int] = None
    is_running: bool = False
    next_phase_event: Optional[asyncio.Event] = None
    last_murder_target: Optional[int] = None


# ============================================================================
# ملفات JSON
# ============================================================================
def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("فشل تحميل %s: %s", path, e)
        return default


def _save_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("فشل حفظ %s: %s", path, e)


def _load_ranks() -> dict[str, int]:
    return _load_json(RANKS_FILE, {})


def _save_ranks(ranks: dict[str, int]) -> None:
    _save_json(RANKS_FILE, ranks)


def _load_allowed() -> dict[str, list[int]]:
    return _load_json(ALLOWED_CHANNELS_FILE, {})


def _load_stats() -> dict[str, dict]:
    return _load_json(STATS_FILE, {})


def _load_achievements() -> dict[str, list[str]]:
    return _load_json(ACHIEVEMENTS_FILE, {})


def get_rank_title(points: int) -> str:
    if points >= 5000: return "👑 أسطورة"
    if points >= 4000: return "💎 ماسي"
    if points >= 3000: return "🥇 ذهبي"
    if points >= 2000: return "🥈 فضي"
    if points >= 1500: return "🥉 برونزي"
    if points >= 1000: return "⚪ مبتدئ"
    return "🐣 جديد"


def get_stats(user_id: int) -> dict:
    stats = _load_stats()
    return stats.get(str(user_id), {
        "games_played": 0, "wins_as_mafia": 0, "games_as_mafia": 0,
        "wins_as_citizen": 0, "games_as_citizen": 0, "times_survived": 0,
        "max_win_streak": 0, "roles_played": {},
    })


def get_player_achievements(user_id: int) -> list[str]:
    return _load_achievements().get(str(user_id), [])


def unlock_achievement(user_id: int, ach_id: str) -> bool:
    achs = _load_achievements()
    user_key = str(user_id)
    if ach_id in achs.get(user_key, []):
        return False
    achs.setdefault(user_key, []).append(ach_id)
    _save_json(ACHIEVEMENTS_FILE, achs)
    return True


# ============================================================================
# نظام ELO
# ============================================================================
def _load_elo() -> dict[str, dict]:
    return _load_json(ELO_FILE, {})


def _save_elo(data: dict[str, dict]) -> None:
    _save_json(ELO_FILE, data)


def get_elo(user_id: int) -> int:
    elo_data = _load_elo()
    return elo_data.get(str(user_id), {}).get("elo", 1000)


def get_elo_title(elo: int) -> str:
    if elo >= 2000: return "🏆 أسطوري"
    if elo >= 1800: return "💎 ماسي"
    if elo >= 1600: return "🥇 ذهبي"
    if elo >= 1400: return "🥈 فضي"
    if elo >= 1200: return "🥉 برونزي"
    if elo >= 1000: return "⚪ مبتدئ"
    return "🐣 جديد"


def calculate_elo(winner_elo: int, loser_elo: int, is_draw: bool = False) -> tuple[int, int]:
    K = 32
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_loser = 1 / (1 + 10 ** ((winner_elo - loser_elo) / 400))
    if is_draw:
        new_winner = int(winner_elo + K * (0.5 - expected_winner))
        new_loser = int(loser_elo + K * (0.5 - expected_loser))
    else:
        new_winner = int(winner_elo + K * (1 - expected_winner))
        new_loser = int(loser_elo + K * (0 - expected_loser))
    return new_winner, new_loser


def update_elo_for_game(game: GameState, winner: str) -> None:
    elo_data = _load_elo()
    alive = [p for p in game.players if p.alive or True]
    winning_team = winner
    for p in game.players:
        key = str(p.user_id)
        if key not in elo_data:
            elo_data[key] = {"elo": 1000, "games": 0, "wins": 0, "streak": 0, "peak": 1000}
        elo_data[key]["games"] = elo_data[key].get("games", 0) + 1
        team = ROLES[p.role_name].team
        is_winner = False
        if winning_team == "mafia" and team in ("mafia", "helper"):
            is_winner = True
        elif winning_team == "citizens" and team == "citizens":
            is_winner = True
        elif winning_team == "cult" and team == "cult":
            is_winner = True
        elif winning_team == "lovers" and p.role_name == LOVER_ROLE:
            is_winner = True
        if is_winner:
            elo_data[key]["wins"] = elo_data[key].get("wins", 0) + 1
            elo_data[key]["streak"] = elo_data[key].get("streak", 0) + 1
        else:
            elo_data[key]["streak"] = 0
    team_elos: dict[str, list[int]] = {}
    team_winners: dict[str, list[int]] = {}
    for p in game.players:
        key = str(p.user_id)
        team = ROLES[p.role_name].team
        is_w = False
        if winning_team == "mafia" and team in ("mafia", "helper"):
            is_w = True
        elif winning_team == "citizens" and team == "citizens":
            is_w = True
        elif winning_team == "cult" and team == "cult":
            is_w = True
        elif winning_team == "lovers" and p.role_name == LOVER_ROLE:
            is_w = True
        team_elos.setdefault(team, []).append(elo_data[key]["elo"])
        if is_w:
            team_winners.setdefault(team, []).append(elo_data[key]["elo"])
    for p in game.players:
        key = str(p.user_id)
        old_elo = elo_data[key]["elo"]
        team = ROLES[p.role_name].team
        is_w = False
        if winning_team == "mafia" and team in ("mafia", "helper"):
            is_w = True
        elif winning_team == "citizens" and team == "citizens":
            is_w = True
        elif winning_team == "cult" and team == "cult":
            is_w = True
        elif winning_team == "lovers" and p.role_name == LOVER_ROLE:
            is_w = True
        avg_loser = 0
        opponent_team = "citizens" if team in ("mafia", "helper") else "mafia"
        if opponent_team in team_elos and team_elos[opponent_team]:
            avg_loser = sum(team_elos[opponent_team]) // len(team_elos[opponent_team])
        if avg_loser == 0:
            avg_loser = 1000
        if is_w:
            new_elo, _ = calculate_elo(old_elo, avg_loser)
        else:
            _, new_elo = calculate_elo(avg_loser, old_elo)
        elo_data[key]["elo"] = max(0, new_elo)
        elo_data[key]["peak"] = max(elo_data[key].get("peak", 0), elo_data[key]["elo"])
    _save_elo(elo_data)


# ============================================================================
# نظام التحديات اليومية
# ============================================================================
def _load_daily_challenges() -> dict[str, Any]:
    return _load_json(CHALLENGES_FILE, {})


def _save_daily_challenges(data: dict[str, Any]) -> None:
    _save_json(CHALLENGES_FILE, data)


def get_today_challenges() -> list[dict[str, Any]]:
    from datetime import date
    today = date.today().isoformat()
    data = _load_daily_challenges()
    if data.get("date") == today:
        return data.get("challenges", [])
    selected = random.sample(DAILY_CHALLENGES, min(3, len(DAILY_CHALLENGES)))
    _save_daily_challenges({"date": today, "challenges": selected})
    return selected


def check_challenge_completion(user_id: int, game: GameState, winner: str) -> list[str]:
    completed = []
    challenges = get_today_challenges()
    stats = get_stats(user_id)
    player = next((p for p in game.players if p.user_id == user_id), None)
    if not player:
        return completed
    for ch in challenges:
        cid = ch["id"]
        team = ch.get("team", "any")
        role = ch.get("role")
        if cid == "win_mafia":
            pteam = ROLES[player.role_name].team
            if winner == "mafia" and pteam in ("mafia", "helper"):
                completed.append(cid)
        elif cid == "win_citizen":
            if winner == "citizens" and ROLES[player.role_name].team == "citizens":
                completed.append(cid)
        elif cid == "survive":
            if player.alive:
                completed.append(cid)
        elif cid == "kill_mafia":
            if role and player.role_name == role and winner in ("citizens",):
                completed.append(cid)
        elif cid == "heal_target":
            if role and player.role_name == role and winner in ("citizens",):
                completed.append(cid)
        elif cid == "vote_eliminate":
            if player.user_id in game.day_votes:
                completed.append(cid)
        elif cid == "cult_leader_win":
            if role and player.role_name == role and winner == "cult":
                completed.append(cid)
        elif cid == "lovers_win":
            if role and player.role_name == role and winner == "lovers":
                completed.append(cid)
        elif cid == "sheriff_catch":
            if role and player.role_name == role and winner in ("citizens",):
                completed.append(cid)
        elif cid == "three_games":
            if stats.get("games_played", 0) >= 3:
                completed.append(cid)
    return completed


# ============================================================================
# توزيع الأدوار
# ============================================================================
def distribute_roles(player_count: int) -> list[str]:
    if player_count not in DISTRIBUTION_TABLE:
        raise ValueError(f"عدد اللاعبين يجب أن يكون بين {MIN_PLAYERS} و {MAX_PLAYERS}")
    table = DISTRIBUTION_TABLE[player_count]
    roles: list[str] = []
    mafia_count = table["mafia"]
    if mafia_count == 1:
        roles.append("رئيس_المافيا")
    else:
        roles.extend(["رئيس_المافيا", "المُنفّذ"])
        if mafia_count - len(roles) > 0:
            roles.extend(["المحتال"] * (mafia_count - len(roles)))
    if table["cult_leader"] > 0:
        roles.append(CULT_LEADER_ROLE)
    if table["fanatic"] > 0:
        roles.append(FANATIC_ROLE)
    helper_count = table["helper"]
    if helper_count > 0:
        roles.append("الجاسوسة")
        helper_count -= 1
        if helper_count > 0:
            extra = ["المضيفة", "اللص", "المحتال_الانتهازي", "الرجل_الوحش"]
            roles.extend(extra[:helper_count])
    roles.append("الشرطي")
    roles.append("الطبيب")
    while len(roles) < player_count:
        for c in CITIZEN_POOL:
            if c not in roles:
                roles.append(c)
                break
        else:
            roles.append(random.choice(CITIZEN_POOL))
    if player_count >= 10 and LOVER_ROLE not in roles:
        citizen_indices = [i for i, r in enumerate(roles) if ROLES[r].team == "citizens" and r not in ("الشرطي", "الطبيب")]
        if len(citizen_indices) >= 2:
            for idx in citizen_indices[-2:]:
                roles[idx] = LOVER_ROLE
    roles = roles[:player_count]
    while len(roles) < player_count:
        roles.append(random.choice(CITIZEN_POOL))
    random.shuffle(roles)
    return roles


def assign_roles_to_players(game: GameState, role_names: list[str]) -> None:
    for i, player in enumerate(game.players):
        player.role_name = role_names[i]
    game.had_mafia = any(ROLES[n].team == "mafia" for n in role_names)
    lover_indices = [i for i, n in enumerate(role_names) if n == LOVER_ROLE]
    if len(lover_indices) == 2:
        i, j = lover_indices
        game.players[i].lover_with = game.players[j].user_id
        game.players[j].lover_with = game.players[i].user_id
        game.lovers_ids = (game.players[i].user_id, game.players[j].user_id)
        game.is_lovers = True
    for p in game.players:
        if ROLES[p.role_name].team in ("mafia", "helper"):
            p.met_mafia = True
            p.joined_mafia = True
    citizen_players = [p for p in game.players if ROLES[p.role_name].team == "citizens"]
    for p in game.players:
        if p.role_name == "المحتال_الانتهازي" and citizen_players:
            target = random.choice(citizen_players)
            p.swindled_role = target.role_name


def check_winner(game: GameState) -> Optional[str]:
    alive = [p for p in game.players if p.alive]
    if not alive:
        return None
    lovers = [p for p in alive if p.role_name == LOVER_ROLE]
    if game.is_lovers and len(alive) == 2 and len(lovers) == 2:
        return "lovers"
    if game.day == 0:
        return None
    mafia_votes = 0
    for p in alive:
        team = ROLES[p.role_name].team
        if team == "mafia":
            if not p.joined_mafia:
                continue
            mafia_votes += 2 if p.role_name == "رئيس_المافيا" else 1
        elif team == "helper":
            if p.met_mafia:
                mafia_votes += 1
    citizen_votes = 0
    cult_votes = 0
    for p in alive:
        team = ROLES[p.role_name].team
        if team in ("mafia", "helper"):
            continue
        if team == "cult":
            cult_votes += 1
        else:
            citizen_votes += 1
    prophet_alive = any(p.alive and p.role_name == "النبي" for p in game.players)
    if cult_votes > 0 and cult_votes >= citizen_votes:
        return "cult"
    if mafia_votes > 0 and mafia_votes >= citizen_votes + cult_votes and not prophet_alive:
        return "mafia"
    if mafia_votes == 0 and game.had_mafia:
        return "citizens"
    return None


# ============================================================================
# بناء الـ Embeds
# ============================================================================
def build_lobby_embed(game: GameState) -> discord.Embed:
    player_list = "\n".join(f"• <@{p.user_id}>" for p in game.players) or "— لا أحد بعد —"
    color = discord.Color.dark_red() if game.started else discord.Color.blurple()
    desc = (
        f"**المضيف:** <@{game.host_id}>\n"
        f"**الوضع:** {'⚡ سريع' if game.is_fast else '🐢 عادي'}\n"
        f"**اللاعبون:** {len(game.players)}/{MAX_PLAYERS} (الحد الأدنى {MIN_PLAYERS})\n\n"
        f"👥 **اللاعبون المنضمون:**\n{player_list}\n\n"
        f"📥 اضغط **انضم** للدخول\n"
        f"📤 اضغط **خروج** للخروج\n"
        f"▶️ **ابدأ** متاح للمضيف فقط"
    )
    embed = discord.Embed(title="🕵️ لعبة مافيا جديدة", description=desc, color=color)
    if game.started:
        embed.add_field(name="المرحلة", value="بدأت", inline=True)
    embed.set_footer(text=f"مافيا 42 v{BOT_VERSION}")
    return embed


def build_day_embed(game: GameState, alive: list[PlayerState], remaining: int = 0) -> discord.Embed:
    dead_text = ""
    if game.day > 0 and game.dead_players:
        recent = game.dead_players[-5:]
        dead_text = "\n".join(f"💀 <@{p.user_id}> — {ROLES[p.role_name].name}" for p in recent)
    timer_text = ""
    if remaining > 0:
        progress = int((remaining / DAY_DURATION_DEFAULT) * 10)
        bar = "█" * progress + "░" * (10 - progress)
        if remaining <= 10:
            timer_text = f"🔴 **الوقت المتبقي:** {remaining} ثانية ⚠️\n`{bar}`"
        else:
            timer_text = f"⏰ **الوقت المتبقي:** {remaining} ثانية\n`{bar}`"
    else:
        timer_text = "⏰ **الوقت:** انتهى!"
    alive_voters = [p for p in alive]
    voted = len(game.day_votes)
    total_voters = len(alive_voters)
    vote_status = f"🗳️ **الأصوات:** {voted}/{total_voters}"
    if voted > 0:
        vote_status += " ✅"
    else:
        vote_status += " ⏳"
    desc = (
        f"🧑 **الأحياء:** {len(alive)}/{len(game.players)}\n"
        f"{timer_text}\n"
        f"{vote_status}\n\n"
        f"💬 ناقشوا الأدوار بحرية، ثم صوّتوا على المشبوه.\n"
        f"🗳️ اضغط على اسم اللاعب للتصويت عليه.\n"
        f"🔄 يمكنك تغيير تصويتك قبل انتهاء الوقت."
    )
    if dead_text:
        desc += f"\n\n**ماتوا الليلة:**\n{dead_text}"
    embed = discord.Embed(title=f"☀️ النهار {game.day}", description=desc, color=discord.Color.gold())
    embed.set_footer(text=f"مافيا 42 v{BOT_VERSION}")
    return embed


def build_night_embed_for_player(game: GameState, player: PlayerState, alive: list[PlayerState]) -> discord.Embed:
    role = ROLES[player.role_name]
    desc = role.description + "\n\n**اختر هدفك من الأسفل:**"
    if player.night_action_target:
        target = next((p for p in game.players if p.user_id == player.night_action_target), None)
        if target:
            desc += f"\n\n✅ **اخترت:** <@{target.user_id}>"
    embed = discord.Embed(title=f"🌙 الليل {game.day} — {role.emoji} {role.name}", description=desc, color=discord.Color.dark_purple())
    return embed


def build_mafia_night_embed(game: GameState, mafia_players: list[PlayerState], alive: list[PlayerState]) -> discord.Embed:
    mafia_list = "\n".join(f"{ROLES[p.role_name].emoji} <@{p.user_id}> ({p.role_name})" for p in mafia_players)
    desc = f"🔪 **أعضاء المافيا في هذه اللعبة:**\n{mafia_list}\n\n"
    desc += "🩸 **اختارتوا الضحية:**\n"
    votes = game.night_kill_votes
    for mp in mafia_players:
        target = votes.get(mp.user_id)
        if target:
            desc += f"• <@{mp.user_id}> → <@{target}>\n"
        else:
            desc += f"• <@{mp.user_id}> → لم يصوّت\n"
    desc += "\n**اختر الضحية من الأزرار أدناه:**"
    embed = discord.Embed(title=f"🌙 الليل {game.day} — 🔪 غرفة المافيا", description=desc, color=discord.Color.dark_red())
    return embed


def build_role_dm_embed(role: Role) -> discord.Embed:
    team_color = {
        "mafia": discord.Color.dark_red(),
        "citizens": discord.Color.green(),
        "cult": discord.Color.purple(),
        "neutral": discord.Color.gold(),
        "helper": discord.Color.orange(),
    }.get(role.team, discord.Color.greyple())
    embed = discord.Embed(title=f"🎭 دورك: {role.emoji} {role.name}", description=role.description, color=team_color)
    team_ar = {"mafia": "🔴 المافيا", "citizens": "🟢 المواطنون", "cult": "🟣 الطائفة", "neutral": "🟡 محايد", "helper": "🟠 مساعدو المافيا"}
    embed.add_field(name="الفريق", value=team_ar.get(role.team, role.team), inline=True)
    if role.night_action:
        embed.add_field(name="🌙 فعل ليلي", value="✅ نعم", inline=True)
    tips = ROLE_TIPS.get(role.name, [])
    if tips:
        tips_text = "\n".join(f"• {t}" for t in tips)
        embed.add_field(name="💡 نصائح استراتيجية", value=tips_text, inline=False)
    embed.set_footer(text="هذه الرسالة مخفية — لا أحد يراها غيرك")
    return embed


def build_winner_embed(game: GameState, winner: str) -> discord.Embed:
    name_ar = {"mafia": "🔪 المافيا", "citizens": "🟢 المواطنون", "cult": "⛧ الطائفة", "lovers": "💕 العاشقون"}.get(winner, winner)
    color = {"mafia": discord.Color.dark_red(), "citizens": discord.Color.green(), "cult": discord.Color.purple(), "lovers": discord.Color.magenta()}.get(winner, discord.Color.gold())
    desc = f"# 🏆 الفائز: {name_ar}\n\n"
    desc += "**الأدوار:**\n"
    for p in game.players:
        role = ROLES[p.role_name]
        status = "💀" if not p.alive else "🟢"
        desc += f"{status} <@{p.user_id}> — {role.emoji} {role.name}\n"
    embed = discord.Embed(title="🎉 انتهت اللعبة", description=desc, color=color)
    embed.set_footer(text=f"مافيا 42 v{BOT_VERSION}")
    return embed


# ============================================================================
# الواجهات التفاعلية (Views)
# ============================================================================
class MafiaLobbyView(discord.ui.View):
    """لوبي اللعبة - أزرار انضمام/خروج/بدء/إنهاء."""

    def __init__(self):
        super().__init__(timeout=None)

    def _get_game(self, interaction: discord.Interaction) -> Optional[GameState]:
        gid = interaction.guild.id if interaction.guild else interaction.user.id
        return games.get(gid)

    async def _refresh(self, interaction: discord.Interaction, game: GameState) -> None:
        try:
            await interaction.message.edit(embed=build_lobby_embed(game), view=self)
        except Exception:
            pass

    @discord.ui.button(label="📥 انضم", style=discord.ButtonStyle.green, custom_id="ml_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self._get_game(interaction)
        if not game:
            return await interaction.response.send_message("❌ لا توجد لعبة. أنشئ لعبة جديدة بـ `&مافيا`", ephemeral=True)
        if game.started:
            return await interaction.response.send_message("⚠️ اللعبة بدأت بالفعل.", ephemeral=True)
        if len(game.players) >= MAX_PLAYERS:
            return await interaction.response.send_message(f"❌ وصلنا الحد ({MAX_PLAYERS}).", ephemeral=True)
        if any(p.user_id == interaction.user.id for p in game.players):
            return await interaction.response.send_message("⚠️ أنت منضم.", ephemeral=True)
        game.players.append(PlayerState(user_id=interaction.user.id, display_name=interaction.user.display_name))
        await self._refresh(interaction, game)
        await interaction.response.send_message(f"✅ انضممت! ({len(game.players)}/{MAX_PLAYERS})", ephemeral=True)

    @discord.ui.button(label="📤 خروج", style=discord.ButtonStyle.red, custom_id="ml_leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self._get_game(interaction)
        if not game or game.started:
            return await interaction.response.send_message("❌ لا يمكن الخروج الآن.", ephemeral=True)
        for i, p in enumerate(game.players):
            if p.user_id == interaction.user.id:
                game.players.pop(i)
                await self._refresh(interaction, game)
                return await interaction.response.send_message("✅ غادرت.", ephemeral=True)
        await interaction.response.send_message("⚠️ لست منضماً.", ephemeral=True)

    @discord.ui.button(label="▶️ ابدأ اللعبة", style=discord.ButtonStyle.blurple, custom_id="ml_start")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self._get_game(interaction)
        if not game:
            return await interaction.response.send_message("❌ لا توجد لعبة.", ephemeral=True)
        if interaction.user.id != game.host_id:
            return await interaction.response.send_message("❌ فقط المضيف.", ephemeral=True)
        if game.started:
            return await interaction.response.send_message("⚠️ بدأت بالفعل.", ephemeral=True)
        if len(game.players) < MIN_PLAYERS:
            return await interaction.response.send_message(f"❌ تحتاج {MIN_PLAYERS} لاعبين على الأقل.目前: {len(game.players)}", ephemeral=True)
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(embed=build_lobby_embed(game), view=self)
        except Exception:
            pass
        await interaction.response.send_message("🎮 جارٍ بدء اللعبة...", ephemeral=True)
        bot.loop.create_task(start_game_flow(game, interaction.channel))
        # إلغاء مهمة تحديث اللوبي
        if game.guild_id in lobby_refresh_tasks:
            lobby_refresh_tasks.pop(game.guild_id).cancel()

    @discord.ui.button(label="⛔ إنهاء", style=discord.ButtonStyle.grey, custom_id="ml_end")
    async def end(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self._get_game(interaction)
        if not game:
            return await interaction.response.send_message("❌ لا توجد لعبة.", ephemeral=True)
        if not is_game_admin(interaction, game):
            return await interaction.response.send_message("❌ فقط المضيف أو المشرف يمكنهم الإنهاء.", ephemeral=True)
        confirm = ConfirmView(timeout=15)
        await interaction.response.send_message("⚠️ هل أنت متأكد من إنهاء اللعبة؟", view=confirm, ephemeral=True)
        await confirm.wait()
        if confirm.result is not True:
            return await interaction.followup.send("❌ تم الإلغاء.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(embed=build_lobby_embed(game), view=self)
        except Exception:
            pass
        if game.next_phase_event:
            game.next_phase_event.set()
        games.pop(game.guild_id, None)
        # إلغاء مهمة تحديث اللوبي
        if game.guild_id in lobby_refresh_tasks:
            lobby_refresh_tasks.pop(game.guild_id).cancel()
        await interaction.followup.send("⛔ تم إنهاء اللعبة.", ephemeral=False)


class DayVoteView(discord.ui.View):
    """تصويت النهار - زر لكل لاعب حي."""

    def __init__(self, game: GameState, guild_id: int, channel_id: int):
        super().__init__(timeout=DAY_DURATION_DEFAULT if not game.is_fast else 45)
        self.game = game
        self.guild_id = guild_id
        self.channel_id = channel_id
        self._build_buttons()

    def _build_buttons(self):
        alive = [p for p in self.game.players if p.alive]
        for p in alive:
            btn = discord.ui.Button(
                label=p.display_name[:32],
                style=discord.ButtonStyle.primary,
                custom_id=f"dv_{p.user_id}",
            )
            btn.callback = self._make_vote_cb(p.user_id)
            self.add_item(btn)
        skip = discord.ui.Button(label="⏭ تخطي", style=discord.ButtonStyle.secondary, custom_id="dv_skip")
        skip.callback = self._make_vote_cb(None)
        self.add_item(skip)
        end = discord.ui.Button(label="⏩ إنهاء النهار (للمضيف)", style=discord.ButtonStyle.danger, custom_id="dv_end")
        end.callback = self.end_day
        self.add_item(end)

    def _make_vote_cb(self, target_id: Optional[int]):
        async def callback(interaction: discord.Interaction):
            await self._handle_vote(interaction, target_id)
        return callback

    async def _handle_vote(self, interaction: discord.Interaction, target_id: Optional[int]):
        if not self.game or self.game.phase != "day":
            return await interaction.response.send_message("❌ ليس في النهار.", ephemeral=True)
        player = next((p for p in self.game.players if p.user_id == interaction.user.id), None)
        if not player or not player.alive:
            return await interaction.response.send_message("❌ أنت ميت.", ephemeral=True)
        if target_id and not any(p.user_id == target_id and p.alive for p in self.game.players):
            return await interaction.response.send_message("❌ اللاعب غير متاح.", ephemeral=True)
        old_vote = self.game.day_votes.get(interaction.user.id)
        self.game.day_votes[interaction.user.id] = target_id or 0
        # عرض عدد الأصوات المتبقية
        alive_voters = [p for p in self.game.players if p.alive]
        voted = len(self.game.day_votes)
        remaining = len(alive_voters) - voted
        if target_id:
            msg = f"🗳️ صوّت على <@{target_id}>"
            if old_vote and old_vote != 0 and old_vote != target_id:
                msg += " (تم تغيير التصويت)"
            msg += f"\n📊 الأصوات المتبقية: {remaining}"
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.response.send_message(f"⏭ تخطيت التصويت\n📊 الأصوات المتبقية: {remaining}", ephemeral=True)

    async def end_day(self, interaction: discord.Interaction):
        game = self.game
        if interaction.user.id != game.host_id:
            return await interaction.response.send_message("❌ فقط المضيف يمكنه إنهاء النهار.", ephemeral=True)
        if game and game.next_phase_event:
            game.next_phase_event.set()
        await interaction.response.send_message("⏩ تم إنهاء النهار بواسطة المضيف.", ephemeral=False)

    async def on_timeout(self):
        if self.game and self.game.next_phase_event:
            self.game.next_phase_event.set()


class NightActionView(discord.ui.View):
    """اختيار هدف الفعل الليلي مع تأكيد — DM لكل لاعب."""

    def __init__(self, game: GameState, actor_id: int, alive_targets: list[PlayerState]):
        timeout = 30 if game.is_fast else 45
        super().__init__(timeout=timeout)
        self.game = game
        self.actor_id = actor_id
        self.alive_targets = alive_targets
        self.selected_target: Optional[int] = None
        self.confirmed = False
        self._build_target_buttons()

    def _build_target_buttons(self):
        self.clear_items()
        for p in self.alive_targets:
            if p.user_id == self.actor_id:
                continue
            btn = discord.ui.Button(label=p.display_name[:32], style=discord.ButtonStyle.danger, custom_id=f"na_{p.user_id}")
            btn.callback = self._make_select_cb(p.user_id)
            self.add_item(btn)
        skip = discord.ui.Button(label="⏭ تخطي", style=discord.ButtonStyle.secondary, custom_id="na_skip")
        skip.callback = self._make_select_cb(None)
        self.add_item(skip)

    def _build_confirm_buttons(self):
        self.clear_items()
        if self.selected_target:
            btn = discord.ui.Button(label=f"✅ تأكيد: {self._get_target_name()}", style=discord.ButtonStyle.success, custom_id="na_confirm")
            btn.callback = self._confirm_cb
            self.add_item(btn)
        else:
            btn = discord.ui.Button(label="✅ تخطي (لا فعل)", style=discord.ButtonStyle.secondary, custom_id="na_confirm_skip")
            btn.callback = self._confirm_cb
            self.add_item(btn)
        back = discord.ui.Button(label="🔄 تغيير الاختيار", style=discord.ButtonStyle.grey, custom_id="na_back")
        back.callback = self._back_cb
        self.add_item(back)

    def _get_target_name(self) -> str:
        if not self.selected_target:
            return ""
        p = next((p for p in self.alive_targets if p.user_id == self.selected_target), None)
        return p.display_name if p else "?"
    
    def _make_select_cb(self, target_id: Optional[int]):
        async def callback(interaction: discord.Interaction):
            await self._handle_select(interaction, target_id)
        return callback

    async def _handle_select(self, interaction: discord.Interaction, target_id: Optional[int]):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ ليس دورك.", ephemeral=True)
        self.selected_target = target_id
        actor = next((p for p in self.game.players if p.user_id == self.actor_id), None)
        if not actor:
            return
        role = ROLES[actor.role_name]
        if target_id:
            t = next((p for p in self.game.players if p.user_id == target_id), None)
            tname = t.display_name if t else "?"
            desc = f"**اخترت:** {role.emoji} <@{target_id}> ({tname})\n\n⚠️ **اضغط تأكيد لتنفيذ الفعل**"
        else:
            desc = "اخترت **تخطي** الفعل الليلي.\n\n⚠️ **اضغط تأكيد للتأكيد**"
        embed = discord.Embed(title=f"🌙 الليل {self.game.day} — {role.emoji} {role.name}", description=desc, color=discord.Color.dark_purple())
        self._build_confirm_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _confirm_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ ليس دورك.", ephemeral=True)
        if self.confirmed:
            return await interaction.response.send_message("❌ تم التأكيد بالفعل.", ephemeral=True)
        self.confirmed = True
        actor = next((p for p in self.game.players if p.user_id == self.actor_id), None)
        if not actor:
            return
        role = ROLES[actor.role_name]
        target_id = self.selected_target
        self.game.night_actions.setdefault(role.name, {})[self.actor_id] = target_id
        actor.night_action_target = target_id
        actor.night_action_taken = True
        if role.name in ("الطبيب", "الممرضة"):
            self.game.doctor_protect = target_id
            if target_id:
                t = next((p for p in self.game.players if p.user_id == target_id), None)
                if t:
                    t.protected_today = True
        if role.name == "زعيم_الطائفة" and target_id:
            actor.cult_target = target_id
        if role.name == "المتعصب" and target_id:
            cl = next((p for p in self.game.players if p.alive and p.role_name == "زعيم_الطائفة"), None)
            if cl and target_id == cl.user_id:
                actor.cult_team = True
                actor.cult_known_cult.add(cl.user_id)
                cl.cult_known_cult.add(actor.user_id)
        if role.name == "القنّاص" and target_id and not actor.sniper_used:
            actor.sniper_used = True
        if role.name == "الشرطي" and target_id:
            t = next((p for p in self.game.players if p.user_id == target_id), None)
            if t:
                team = ROLES[t.role_name].team
                result = "🔴 مافيا" if team in ("mafia", "helper") else ("🟣 طائفة" if team == "cult" else "🟢 مواطن")
                embed = discord.Embed(title="🔍 نتيجة التحقيق", description=f"**{t.display_name}**: {result}", color=discord.Color.blue())
                embed.set_footer(text="مخفي — لا أحد غيرك يراه")
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(embed=embed, view=self)
                return
        if target_id:
            desc = f"✅ **تم التأكيد:** {role.emoji} <@{target_id}>\n\n🔒 لا يمكنك تغيير اختيارك."
        else:
            desc = "✅ **تم التأكيد:** تخطي الفعل.\n\n🔒 لا يمكنك تغيير اختيارك."
        embed = discord.Embed(title=f"🌙 الليل {self.game.day} — {role.emoji} {role.name}", description=desc, color=discord.Color.green())
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    async def _back_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message("❌ ليس دورك.", ephemeral=True)
        actor = next((p for p in self.game.players if p.user_id == self.actor_id), None)
        if not actor:
            return
        role = ROLES[actor.role_name]
        self.selected_target = None
        desc = role.description + "\n\n**اختر هدفك من الأسفل:**"
        embed = discord.Embed(title=f"🌙 الليل {self.game.day} — {role.emoji} {role.name}", description=desc, color=discord.Color.dark_purple())
        self._build_target_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        pass


class MafiaNightView(discord.ui.View):
    """تصويت القتل - DM جماعي للمافيا."""

    def __init__(self, game: GameState, mafia_players: list[PlayerState], alive_targets: list[PlayerState]):
        timeout = 30 if game.is_fast else 45
        super().__init__(timeout=timeout)
        self.game = game
        self.mafia_players = mafia_players
        self.alive_targets = alive_targets
        for p in alive_targets:
            if ROLES[p.role_name].team in ("mafia", "helper"):
                continue
            btn = discord.ui.Button(label=p.display_name[:32], style=discord.ButtonStyle.danger, custom_id=f"mn_{p.user_id}")
            btn.callback = self._make_cb(p.user_id)
            self.add_item(btn)
        skip = discord.ui.Button(label="⏭ تخطي", style=discord.ButtonStyle.secondary, custom_id="mn_skip")
        skip.callback = self._make_cb(None)
        self.add_item(skip)

    def _make_cb(self, target_id: Optional[int]):
        async def callback(interaction: discord.Interaction):
            await self._handle(interaction, target_id)
        return callback

    async def _handle(self, interaction: discord.Interaction, target_id: Optional[int]):
        if not any(p.user_id == interaction.user.id for p in self.mafia_players):
            return await interaction.response.send_message("❌ لست من المافيا.", ephemeral=True)
        actor = next((p for p in self.mafia_players if p.user_id == interaction.user.id), None)
        if actor:
            actor.night_action_taken = True
        if target_id:
            self.game.night_kill_votes[interaction.user.id] = target_id
            # تحديث رسالة المافيا
            try:
                embed = build_mafia_night_embed(self.game, self.mafia_players, self.alive_targets)
                await interaction.message.edit(embed=embed, view=self)
            except Exception:
                pass
            await interaction.response.send_message(f"✅ اخترت الضحية: <@{target_id}>", ephemeral=True)
        else:
            self.game.night_kill_votes.pop(interaction.user.id, None)
            try:
                embed = build_mafia_night_embed(self.game, self.mafia_players, self.alive_targets)
                await interaction.message.edit(embed=embed, view=self)
            except Exception:
                pass
            await interaction.response.send_message("⏭ تخطيت.", ephemeral=True)

    async def on_timeout(self):
        pass


# ============================================================================
# متصفح الأدوار التفاعلي
# ============================================================================
class RoleBrowserView(discord.ui.View):
    """متصفح تفاعلي للأدوار - فئات ← أدوار ← تفاصيل."""

    CATEGORIES = {
        "mafia": ("🔴", "المافيا", [n for n, r in ROLES.items() if r.team == "mafia"]),
        "helper": ("🟠", "مساعدو المافيا", [n for n, r in ROLES.items() if r.team == "helper"]),
        "citizens": ("🟢", "المواطنون", [n for n, r in ROLES.items() if r.team == "citizens"]),
        "cult": ("🟣", "الطائفة", [n for n, r in ROLES.items() if r.team == "cult"]),
        "neutral": ("🟡", "محايد", [n for n, r in ROLES.items() if r.team == "neutral"]),
    }

    def __init__(self):
        super().__init__(timeout=120)
        self.build_categories()

    async def on_timeout(self):
        for c in self.children:
            c.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass

    def clear_and_add(self, *items):
        self.clear_items()
        for item in items:
            self.add_item(item)

    def build_categories(self):
        """الصفحة الرئيسية: أزرار الفئات."""
        self.clear_items()
        for key, (emoji, name, _) in self.CATEGORIES.items():
            cnt = len(self.CATEGORIES[key][2])
            btn = discord.ui.Button(label=f"{emoji} {name} ({cnt})", style=discord.ButtonStyle.secondary, custom_id=f"rb_cat_{key}")
            btn.callback = self.make_cat_cb(key)
            self.add_item(btn)

    def make_cat_cb(self, cat_key: str):
        async def cb(interaction: discord.Interaction):
            await self.show_category(interaction, cat_key)
        return cb

    async def show_category(self, interaction: discord.Interaction, cat_key: str):
        emoji, cat_name, roles = self.CATEGORIES[cat_key]
        self.clear_items()
        for rname in roles:
            role = ROLES[rname]
            btn = discord.ui.Button(label=f"{role.emoji} {role.name}", style=discord.ButtonStyle.primary, custom_id=f"rb_role_{rname}")
            btn.callback = self.make_role_cb(rname)
            self.add_item(btn)
        back = discord.ui.Button(label="🔙 رجوع", style=discord.ButtonStyle.grey, custom_id="rb_back")
        back.callback = self.go_back
        self.add_item(back)
        embed = discord.Embed(title=f"{emoji} {cat_name} ({len(roles)})", color=discord.Color.dark_blue())
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    def make_role_cb(self, rname: str):
        async def cb(interaction: discord.Interaction):
            await self.show_role(interaction, rname)
        return cb

    async def show_role(self, interaction: discord.Interaction, rname: str):
        role = ROLES[rname]
        team_ar = {"mafia": "🔴 المافيا", "citizens": "🟢 المواطنون", "cult": "🟣 الطائفة", "neutral": "🟡 محايد", "helper": "🟠 مساعدو المافيا"}
        color = {"mafia": discord.Color.dark_red(), "citizens": discord.Color.green(), "cult": discord.Color.purple(), "neutral": discord.Color.gold(), "helper": discord.Color.orange()}.get(role.team, discord.Color.greyple())
        embed = discord.Embed(title=f"{role.emoji} {role.name}", description=role.description, color=color)
        embed.add_field(name="👥 الفريق", value=team_ar.get(role.team, role.team), inline=True)
        embed.add_field(name="💎 الندرة", value=role.rarity, inline=True)
        if role.night_action:
            embed.add_field(name="🌙 فعل ليلي", value="✅", inline=True)
        tips = ROLE_TIPS.get(role.name, [])
        if tips:
            tips_text = "\n".join(f"• {t}" for t in tips)
            embed.add_field(name="💡 نصائح استراتيجية", value=tips_text, inline=False)
        self.clear_items()
        back_cat = discord.ui.Button(label="🔙 رجوع للفئة", style=discord.ButtonStyle.grey, custom_id="rb_back_cat")
        back_cat.callback = self.go_back
        home = discord.ui.Button(label="🏠 الفئات", style=discord.ButtonStyle.secondary, custom_id="rb_home")
        home.callback = self.go_home
        self.add_item(back_cat)
        self.add_item(home)
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    async def go_back(self, interaction: discord.Interaction):
        # العودة للفئة الأخيرة (من custom_id)
        msg = interaction.message
        embed = msg.embeds[0] if msg.embeds else None
        title = embed.title if embed else ""
        for key, (emoji, name, _) in self.CATEGORIES.items():
            if f"{emoji} {name}" in title:
                return await self.show_category(interaction, key)
        # If not found, go home
        await self.go_home(interaction)

    async def go_home(self, interaction: discord.Interaction):
        self.build_categories()
        embed = discord.Embed(title="📖 أدوار مافيا 42", description=f"**{len(ROLES)} دور رسمي**\nاختر فئة لتصفّح الأدوار.", color=discord.Color.dark_blue())
        await interaction.response.edit_message(content=None, embed=embed, view=self)


# ============================================================================
# واجهة الحساب التفاعلية (نقاط + إحصائيات + إنجازات)
# ============================================================================
class AccountView(discord.ui.View):
    """واجهة حساب اللاعب التفاعلية."""

    def __init__(self, user_id: int, display_name: str, avatar_url: str):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.display_name = display_name
        self.avatar_url = avatar_url
        self.page = "points"
        self.achievement_ids = get_player_achievements(user_id)
        self.build_buttons()

    def build_buttons(self):
        self.clear_items()
        pts = discord.ui.Button(label="💎 نقاط", style=discord.ButtonStyle.green if self.page == "points" else discord.ButtonStyle.grey, custom_id="ac_points")
        pts.callback = self.go_points
        self.add_item(pts)
        stats = discord.ui.Button(label="📊 إحصائيات", style=discord.ButtonStyle.blurple if self.page == "stats" else discord.ButtonStyle.grey, custom_id="ac_stats")
        stats.callback = self.go_stats
        self.add_item(stats)
        ach = discord.ui.Button(label="🏆 إنجازات", style=discord.ButtonStyle.gold if self.page == "achievements" else discord.ButtonStyle.grey, custom_id="ac_ach")
        ach.callback = self.go_achievements
        self.add_item(ach)

    async def _update(self, interaction: discord.Interaction):
        self.build_buttons()
        embed = await self._build_embed()
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    async def _build_embed(self) -> discord.Embed:
        if self.page == "points":
            ranks = _load_ranks()
            pts = ranks.get(str(self.user_id), INITIAL_POINTS)
            title = get_rank_title(pts)
            embed = discord.Embed(title=f"💎 نقاط {self.display_name}", description=f"**النقاط:** {pts:,}\n**الرتبة:** {title}", color=discord.Color.gold())
            embed.set_thumbnail(url=self.avatar_url)
            return embed
        if self.page == "stats":
            s = get_stats(self.user_id)
            embed = discord.Embed(title=f"📊 إحصائيات {self.display_name}", color=discord.Color.blue())
            embed.set_thumbnail(url=self.avatar_url)
            embed.add_field(name="🎮 مجموع الألعاب", value=str(s.get("games_played", 0)), inline=True)
            embed.add_field(name="✅ مافيا انتصارات", value=f"{s.get('wins_as_mafia', 0)}/{s.get('games_as_mafia', 0)}", inline=True)
            embed.add_field(name="✅ مواطن انتصارات", value=f"{s.get('wins_as_citizen', 0)}/{s.get('games_as_citizen', 0)}", inline=True)
            embed.add_field(name="🛡️ مرات البقاء", value=str(s.get("times_survived", 0)), inline=True)
            top = ", ".join(f"{r} ({n})" for r, n in sorted(s.get("roles_played", {}).items(), key=lambda x: -x[1])[:3]) or "—"
            embed.add_field(name="⭐ أكثر الأدوار", value=top, inline=False)
            return embed
        achs = [ACHIEVEMENTS[aid] for aid in self.achievement_ids if aid in ACHIEVEMENTS]
        lines = [f"{a.emoji} **{a.name}** — {a.description}" for a in achs]
        embed = discord.Embed(title=f"🏆 إنجازات {self.display_name} ({len(achs)}/{len(ACHIEVEMENTS)})", description="\n".join(lines[:20]) or "لا يوجد", color=discord.Color.gold())
        embed.set_thumbnail(url=self.avatar_url)
        return embed

    async def go_points(self, interaction: discord.Interaction):
        self.page = "points"
        await self._update(interaction)

    async def go_stats(self, interaction: discord.Interaction):
        self.page = "stats"
        await self._update(interaction)

    async def go_achievements(self, interaction: discord.Interaction):
        self.page = "achievements"
        await self._update(interaction)


# ============================================================================
# واجهة المساعدة التفاعلية
# ============================================================================
class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.build_main()

    def build_main(self):
        self.clear_items()
        game = discord.ui.Button(label="🎮 اللعبة", style=discord.ButtonStyle.primary, custom_id="hp_game")
        game.callback = self.show_game
        self.add_item(game)
        player = discord.ui.Button(label="👤 اللاعب", style=discord.ButtonStyle.success, custom_id="hp_player")
        player.callback = self.show_player
        self.add_item(player)
        info = discord.ui.Button(label="📖 معلومات", style=discord.ButtonStyle.blurple, custom_id="hp_info")
        info.callback = self.show_info
        self.add_item(info)
        admin = discord.ui.Button(label="🔧 مشرف", style=discord.ButtonStyle.danger, custom_id="hp_admin")
        admin.callback = self.show_admin
        self.add_item(admin)

    async def show_main(self, interaction: discord.Interaction):
        self.build_main()
        embed = discord.Embed(title=f"📚 مساعدة مافيا 42 v{BOT_VERSION}", description="اختر فئة لرؤية الأوامر.", color=discord.Color.blurple())
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    async def _show_cat(self, interaction: discord.Interaction, title: str, text: str, color: discord.Color):
        self.clear_items()
        back = discord.ui.Button(label="🔙 رجوع", style=discord.ButtonStyle.grey, custom_id="hp_back")
        back.callback = self.show_main
        self.add_item(back)
        embed = discord.Embed(title=title, description=text, color=color)
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    async def show_game(self, interaction: discord.Interaction):
        await self._show_cat(interaction, "🎮 أوامر اللعبة",
            "`&مافيا` — افتح لوبي تفاعلي\n"
            "**بعد بدء اللعبة:**\n"
            "• أزرار انضمام/خروج في اللوبي\n"
            "• أزرار تصويت في النهار\n"
            "• أزرار أفعال ليلية في الخاص\n"
            "• `&إنهاء` — إنهاء اللعبة\n"
            "• `&حالة` — لوحة الحالة التفاعلية\n"
            "• `&دوري` — دورك في الخاص",
            discord.Color.green())

    async def show_player(self, interaction: discord.Interaction):
        await self._show_cat(interaction, "👤 أوامر اللاعب",
            "`&نقاط [@لاعب]` — نقاطك ورتبتك\n"
            "`&إحصائيات [@لاعب]` — إحصائيات\n"
            "`&إنجازاتي [@لاعب]` — الإنجازات\n"
            "`&تصنيف` — أفضل 10 لاعبين\n\n"
            "كل هذه الأوامر تفاعلية بأزرار.",
            discord.Color.blue())

    async def show_info(self, interaction: discord.Interaction):
        await self._show_cat(interaction, "📖 معلومات",
            "`&أدوار` — متصفح أدوار تفاعلي\n"
            "`&دور <اسم>` — تفاصيل دور\n"
            "`&نصيحة` — نصيحة عشوائية\n"
            "`&بوت` — معلومات البوت",
            discord.Color.gold())

    async def show_admin(self, interaction: discord.Interaction):
        await self._show_cat(interaction, "🔧 أوامر المشرف",
            "`&اضافه_قناة <ID>` — إضافة قناة\n"
            "`&حذف_قناة <ID>` — حذف قناة\n"
            "`&قنوات` — عرض القنوات\n"
            "`&ريست_نقاط` — إعادة ضبط\n"
            "`&اعطاء_نقاط @لاعب <كم>`\n"
            "`&حذف_نقاط @لاعب <كم>`\n"
            "`&اعلان <رسالة>`\n"
            "`&باكب` — نسخة احتياطية",
            discord.Color.red())


# ============================================================================
# لوحة حالة اللعبة التفاعلية
# ============================================================================
class GameStatusView(discord.ui.View):
    def __init__(self, game: GameState):
        super().__init__(timeout=60)
        self.game = game
        myrole = discord.ui.Button(label="🎭 دوري", style=discord.ButtonStyle.primary, custom_id="gs_myrole")
        myrole.callback = self.send_my_role
        self.add_item(myrole)
        whisper = discord.ui.Button(label="💬 همس للمافيا", style=discord.ButtonStyle.danger, custom_id="gs_whisper")
        whisper.callback = self.open_whisper
        self.add_item(whisper)

    async def send_my_role(self, interaction: discord.Interaction):
        player = next((p for p in self.game.players if p.user_id == interaction.user.id), None)
        if not player:
            return await interaction.response.send_message("❌ لست في اللعبة.", ephemeral=True)
        try:
            embed = build_role_dm_embed(ROLES[player.role_name])
            await interaction.user.send(embed=embed)
            await interaction.response.send_message("✅ أرسلت دورك في الخاص!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ افتح الخاص.", ephemeral=True)

    async def open_whisper(self, interaction: discord.Interaction):
        player = next((p for p in self.game.players if p.user_id == interaction.user.id), None)
        if not player:
            return await interaction.response.send_message("❌ لست في اللعبة.", ephemeral=True)
        role = ROLES[player.role_name]
        if role.team not in ("mafia", "helper") and not player.met_mafia:
            return await interaction.response.send_message("❌ لست من المافيا.", ephemeral=True)
        await interaction.response.send_modal(WhisperModal(self.game))


# صندوق الهمس للمافيا (Modal)
class WhisperModal(discord.ui.Modal, title="💬 همسة للمافيا"):
    message = discord.ui.TextInput(label="رسالتك", style=discord.TextStyle.paragraph, max_length=500, placeholder="اكتب رسالتك السرية هنا...")

    def __init__(self, game: GameState):
        super().__init__()
        self.game = game

    async def on_submit(self, interaction: discord.Interaction):
        player = next((p for p in self.game.players if p.user_id == interaction.user.id), None)
        if not player or not player.alive:
            return await interaction.response.send_message("❌ أنت ميت.", ephemeral=True)
        role = ROLES[player.role_name]
        if role.team not in ("mafia", "helper") and not player.met_mafia:
            return await interaction.response.send_message("❌ لست من المافيا.", ephemeral=True)
        sent = 0
        for p in self.game.players:
            if ROLES[p.role_name].team in ("mafia", "helper") and p.met_mafia:
                try:
                    user = bot.get_user(p.user_id)
                    if user:
                        embed = discord.Embed(title="💬 همسة 🔪", description=f"من {interaction.user.display_name}:\n{self.message.value}", color=discord.Color.dark_red())
                        await user.send(embed=embed)
                        sent += 1
                except Exception:
                    pass
        await interaction.response.send_message(f"✅ أُرسلت لـ {sent} عضو في المافيا.", ephemeral=True)


# ============================================================================
# أحداث البوت
# ============================================================================
@bot.event
async def on_ready():
    log.info("✅ بوت مافيا 42 v%s جاهز (متصل كـ %s)", BOT_VERSION, bot.user)
    log.info("📊 %d دور، %d إنجاز", len(ROLES), len(ACHIEVEMENTS))
    bot.add_view(MafiaLobbyView())
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name="مافيا 42 | &مساعدة"))


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send("❌ للمشرفين فقط.", delete_after=5)
    log.error("خطأ: %s", error)
    await ctx.send(f"❌ {error}", delete_after=10)


def is_allowed_channel(ctx: commands.Context) -> bool:
    if ctx.guild is None:
        return True
    allowed = _load_allowed()
    channels = allowed.get(str(ctx.guild.id), [])
    if not channels:
        return True
    return ctx.channel.id in channels


def is_game_admin(interaction: discord.Interaction, game: GameState) -> bool:
    """التحقق من أن المستخدم هو المضيف أو مشرف السيرفر."""
    if interaction.user.id == game.host_id:
        return True
    member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
    return bool(member and member.permissions.administrator)


# ============================================================================
# نافذة التأكيد
# ============================================================================
class ConfirmView(discord.ui.View):
    """نافذة تأكيد بزرين: نعم / لا."""

    def __init__(self, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.result: Optional[bool] = None

    @discord.ui.button(label="✅ نعم", style=discord.ButtonStyle.green, custom_id="confirm_yes")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.result = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="❌ لا", style=discord.ButtonStyle.red, custom_id="confirm_no")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.result = False
        self.stop()
        await interaction.response.defer()

    async def on_timeout(self):
        self.result = False
        self.stop()


# ============================================================================
# منطق اللعبة
# ============================================================================
async def start_game_flow(game: GameState, channel: discord.TextChannel) -> None:
    """يبدأ اللعبة بعد ضغط 'ابدأ' في اللوبي."""
    if game.is_running:
        return
    game.is_running = True
    game.started = True
    try:
        # 1. توزيع الأدوار
        role_names = distribute_roles(len(game.players))
        assign_roles_to_players(game, role_names)
        # 2. رسائل الأدوار الفردية (DM)
        await send_role_dms(game)
        # 3. رسالة المافيا الجماعية
        await send_mafia_dm(game)
        # 4. رسائل الطائفة
        await send_cult_dm(game)
        # 5. رسائل العاشقين
        await send_lovers_dm(game)
        # 6. حلقة النهار/الليل
        await game_loop(game, channel)
    except Exception as e:
        log.error("خطأ في اللعبة: %s", e)
        traceback.print_exc()
    finally:
        game.is_running = False
        games.pop(game.guild_id, None)


async def send_role_dms(game: GameState) -> None:
    for p in game.players:
        try:
            user = bot.get_user(p.user_id) or await bot.fetch_user(p.user_id)
            if user:
                role = ROLES[p.role_name]
                embed = build_role_dm_embed(role)
                await user.send(embed=embed)
        except (discord.Forbidden, discord.NotFound):
            pass


async def send_mafia_dm(game: GameState) -> None:
    mafia_players = [p for p in game.players if ROLES[p.role_name].team in ("mafia", "helper") and p.met_mafia]
    if len(mafia_players) < 2:
        return
    alive = [p for p in game.players if p.alive]
    for mp in mafia_players:
        try:
            user = bot.get_user(mp.user_id) or await bot.fetch_user(mp.user_id)
            if not user:
                continue
            members_text = "\n".join(f"{ROLES[m.role_name].emoji} {m.display_name} ({m.role_name})" for m in mafia_players if m.user_id != mp.user_id)
            embed = discord.Embed(
                title="🔪 فريق المافيا",
                description=f"**زملاؤك في المافيا:**\n{members_text or '—'}\n\nسيتم إرسال غرفة القتل كل ليلة.",
                color=discord.Color.dark_red(),
            )
            embed.set_footer(text="مخفي — لا أحد غير المافيا يراه")
            await user.send(embed=embed)
        except (discord.Forbidden, discord.NotFound):
            pass


async def send_cult_dm(game: GameState) -> None:
    cult_players = [p for p in game.players if ROLES[p.role_name].team == "cult" or p.cult_team]
    if len(cult_players) < 1:
        return
    for cp in cult_players:
        try:
            user = bot.get_user(cp.user_id) or await bot.fetch_user(cp.user_id)
            if not user:
                continue
            members = "\n".join(f"{ROLES[m.role_name].emoji} {m.display_name}" for m in cult_players if m.user_id != cp.user_id)
            embed = discord.Embed(
                title="⛧ الطائفة",
                description=f"أنت عضو في الطائفة.\n**زملاؤك:**\n{members or 'أنت الزعيم.'}",
                color=discord.Color.purple(),
            )
            embed.set_footer(text="مخفي — لا أحد غير الطائفة يراه")
            await user.send(embed=embed)
        except (discord.Forbidden, discord.NotFound):
            pass


async def send_lovers_dm(game: GameState) -> None:
    if not game.is_lovers or game.lovers_ids == (0, 0):
        return
    i1, i2 = game.lovers_ids
    for uid in (i1, i2):
        other = i1 if uid == i2 else i2
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            if user:
                embed = discord.Embed(
                    title="💕 عاشقان",
                    description=f"عاشقك هو <@{other}>.\n✅ إذا متّ، يموت معك.\n🏆 تفوزان إذا بقيتما آخر اثنين.",
                    color=discord.Color.magenta(),
                )
                embed.set_footer(text="مخفي — لا أحد غيركما يعرف")
                await user.send(embed=embed)
        except (discord.Forbidden, discord.NotFound):
            pass


async def game_loop(game: GameState, channel: discord.TextChannel) -> None:
    """حلقة النهار/الليل الرئيسية."""
    while True:
        # ===== النهار =====
        game.phase = "day"
        game.day += 1
        game.next_phase_event = asyncio.Event()
        await run_day_phase(game, channel)
        winner = check_winner(game)
        if winner:
            await end_game_show_winner(game, channel, winner)
            return
        # ===== الليل =====
        game.phase = "night"
        await run_night_phase(game, channel)
        winner = check_winner(game)
        if winner:
            await end_game_show_winner(game, channel, winner)
            return


async def run_day_phase(game: GameState, channel: discord.TextChannel) -> None:
    alive = [p for p in game.players if p.alive]
    duration = DAY_DURATION_DEFAULT if not game.is_fast else 45
    embed = build_day_embed(game, alive, duration)
    view = DayVoteView(game, game.guild_id, channel.id)
    msg = await channel.send(embed=embed, view=view)
    game.day_message_id = msg.id
    game.day_votes.clear()
    warned_10 = False
    warned_30 = False
    for remaining in range(duration, 0, -5):
        if game.next_phase_event and game.next_phase_event.is_set():
            break
        try:
            embed = build_day_embed(game, alive, remaining)
            await msg.edit(embed=embed)
        except Exception:
            pass
        if remaining == 30 and not warned_30:
            warned_30 = True
            try:
                await channel.send("⏰ **30 ثانية متبقية!** اسرعوا بالتصويت.", delete_after=8)
            except Exception:
                pass
        if remaining == 10 and not warned_10:
            warned_10 = True
            try:
                await channel.send("⚠️ **10 ثوانٍ متبقية!** انتهت اللعبة قريباً!", delete_after=8)
            except Exception:
                pass
        await asyncio.sleep(5)
    try:
        await asyncio.wait_for(game.next_phase_event.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass
    view.stop()
    try:
        for child in view.children:
            child.disabled = True
        await msg.edit(view=view)
    except Exception:
        pass
    await process_day_votes(game, channel)


async def process_day_votes(game: GameState, channel: discord.TextChannel) -> None:
    if not game.day_votes:
        embed = discord.Embed(title="⚖️ نتيجة التصويت", description="لم يصوّت أحد. لم يمت أحد.", color=discord.Color.greyple())
        await channel.send(embed=embed)
        return
    counts: dict[int, int] = {}
    for voter_id, target_id in game.day_votes.items():
        if target_id == 0:
            continue
        weight = 1
        if game.is_lovers and game.lovers_ids != (0, 0):
            i1, i2 = game.lovers_ids
            if (voter_id == i1 and game.day_votes.get(i2) == target_id) or (voter_id == i2 and game.day_votes.get(i1) == target_id):
                weight = 2
        counts[target_id] = counts.get(target_id, 0) + weight
    # عرض من صوّت على من
    vote_lines = []
    for voter_id, target_id in game.day_votes.items():
        voter = next((p for p in game.players if p.user_id == voter_id), None)
        if target_id and target_id != 0:
            target = next((p for p in game.players if p.user_id == target_id), None)
            vname = voter.display_name if voter else f"#{voter_id}"
            tname = target.display_name if target else f"#{target_id}"
            vote_lines.append(f"• {vname} → {tname}")
        else:
            vname = voter.display_name if voter else f"#{voter_id}"
            vote_lines.append(f"• {vname} → ⏭ تخطي")
    vote_display = "\n".join(vote_lines)
    embed_votes = discord.Embed(title="📊 نتائج الأصوات", description=vote_display, color=discord.Color.light_grey())
    await channel.send(embed=embed_votes)
    if not counts:
        embed = discord.Embed(title="⚖️ نتيجة التصويت", description="كل الأصوات تخطي. لم يمت أحد.", color=discord.Color.greyple())
        await channel.send(embed=embed)
        return
    max_votes = max(counts.values())
    targets = [tid for tid, c in counts.items() if c == max_votes]
    if len(targets) > 1:
        lines = "\n".join(f"• <@{tid}>: {counts[tid]} صوت" for tid in targets)
        embed = discord.Embed(title="⚖️ تعادل", description=f"تعادل بين:\n{lines}\nلم يمت أحد.", color=discord.Color.greyple())
        await channel.send(embed=embed)
        return
    target_id = targets[0]
    target = next((p for p in game.players if p.user_id == target_id), None)
    if not target or not target.alive:
        return
    target.alive = False
    game.dead_players.append(target)
    role = ROLES[target.role_name]
    death_msg = random.choice(DEATH_MESSAGES["vote"]).format(target=f"<@{target_id}>")
    embed = discord.Embed(
        title="⚖️ نتيجة التصويت",
        description=f"{death_msg}\n🎭 كان: {role.emoji} {role.name} ({target.display_name})\n📊 الأصوات: {counts[target_id]}",
        color=discord.Color.dark_grey(),
    )
    await channel.send(embed=embed)
    if target.lover_with:
        lover = next((p for p in game.players if p.user_id == target.lover_with), None)
        if lover and lover.alive:
            lover.alive = False
            game.dead_players.append(lover)
            lover_death = random.choice(DEATH_MESSAGES["lover_death"]).format(target=f"<@{lover.user_id}>")
            embed2 = discord.Embed(title="💔 موت العاشق", description=lover_death, color=discord.Color.magenta())
            await channel.send(embed=embed2)


async def run_night_phase(game: GameState, channel: discord.TextChannel) -> None:
    embed = discord.Embed(title=f"🌙 الليل {game.day}", description="🛌 الجميع نائمون...\nسترسل رسائل خاصة لمن لديه فعل ليلي.", color=discord.Color.dark_purple())
    await channel.send(embed=embed)
    for p in game.players:
        p.protected_today = False
        p.night_action_target = None
        p.night_action_taken = False
    game.night_kill_votes.clear()
    game.night_actions.clear()
    game.doctor_protect = None
    alive = [p for p in game.players if p.alive]
    # 1. إرسال غرفة المافيا
    mafia_players = [p for p in alive if ROLES[p.role_name].team in ("mafia", "helper") and p.met_mafia]
    if len(mafia_players) >= 1:
        mafia_targets = [p for p in alive if ROLES[p.role_name].team not in ("mafia", "helper")]
        if mafia_targets:
            view = MafiaNightView(game, mafia_players, mafia_targets)
            for mp in mafia_players:
                try:
                    user = bot.get_user(mp.user_id) or await bot.fetch_user(mp.user_id)
                    if user:
                        embed_m = build_mafia_night_embed(game, mafia_players, mafia_targets)
                        await user.send(embed=embed_m, view=view)
                except (discord.Forbidden, discord.NotFound):
                    pass
    # 2. إرسال أزرار الفعل الليلي للآخرين
    for p in alive:
        role = ROLES[p.role_name]
        if not role.night_action:
            continue
        if role.team in ("mafia", "helper"):
            continue  # المافيا يتعاملون معاً في الأعلى
        # العاشق لا يفعل ليلاً
        if role.name == LOVER_ROLE:
            continue
        # بناء القائمة المتاحة
        targets = [t for t in alive if t.user_id != p.user_id]
        if not targets:
            continue
        try:
            user = bot.get_user(p.user_id) or await bot.fetch_user(p.user_id)
            if not user:
                continue
            embed_n = build_night_embed_for_player(game, p, alive)
            view = NightActionView(game, p.user_id, targets)
            await user.send(embed=embed_n, view=view)
        except (discord.Forbidden, discord.NotFound):
            pass
    # 3. انتظار
    wait_time = 30 if game.is_fast else 45
    await asyncio.sleep(wait_time)
    # 4. حلّ النتائج
    await resolve_night(game, channel)
    # 5. كشف AFK — تنبيه من لم يتصرف ليلاً
    afk_players = []
    for p in alive:
        role = ROLES[p.role_name]
        if role.night_action and role.team not in ("mafia", "helper") and role.name != LOVER_ROLE:
            if not p.night_action_taken:
                p.afk_warns += 1
                afk_players.append(p)
    if afk_players:
        afk_text = "\n".join(f"• <@{p.user_id}> ({p.role_name}) - تحذير {p.afk_warns}/3" for p in afk_players)
        embed = discord.Embed(
            title="💤 كشف AFK",
            description=f"اللاعبون التاليون لم يتصرفوا ليلاً:\n{afk_text}\n\n⚠️ **3 تحذيرات = طرد تلقائي**",
            color=discord.Color.orange(),
        )
        await channel.send(embed=embed)
        for p in afk_players:
            if p.afk_warns >= 3:
                p.alive = False
                game.dead_players.append(p)
                embed_afk = discord.Embed(
                    title="💤 طرد AFK",
                    description=f"**{p.display_name}** طُرد لأنه لم يتصرف 3 ليالٍ متتالية.\n🎭 كان: {ROLES[p.role_name].emoji} {ROLES[p.role_name].name}",
                    color=discord.Color.red(),
                )
                await channel.send(embed=embed_afk)


async def resolve_night(game: GameState, channel: discord.TextChannel) -> None:
    kill_target_id = None
    if game.night_kill_votes:
        counts: dict[int, int] = {}
        for voter, target in game.night_kill_votes.items():
            vp = next((p for p in game.players if p.user_id == voter), None)
            w = 2 if vp and vp.role_name == "رئيس_المافيا" else 1
            counts[target] = counts.get(target, 0) + w
        if counts:
            kill_target_id = max(counts, key=counts.get)
    sniper_actions = game.night_actions.get("القنّاص", {})
    dead_ids: set[int] = set()
    events: list[str] = []
    if kill_target_id:
        target = next((p for p in game.players if p.user_id == kill_target_id), None)
        if target and target.alive:
            if target.protected_today or target.user_id == game.doctor_protect:
                events.append(f"🛡️ <@{target.user_id}> كان محمياً الليلة!")
            else:
                target.alive = False
                game.dead_players.append(target)
                dead_ids.add(target.user_id)
                events.append(random.choice(DEATH_MESSAGES["mafia"]).format(target=f"<@{target.user_id}>"))
                game.last_murder_target = target.user_id
    for actor_id, target_id in sniper_actions.items():
        if not target_id:
            continue
        target = next((p for p in game.players if p.user_id == target_id), None)
        if target and target.alive and target.user_id not in dead_ids:
            if target.role_name == "المحتال":
                events.append(f"🛡️ <@{target.user_id}> المحتال دُرع! القنّاص فشل.")
            else:
                target.alive = False
                game.dead_players.append(target)
                dead_ids.add(target.user_id)
                events.append(random.choice(DEATH_MESSAGES["sniper"]).format(target=f"<@{target.user_id}>"))
    cl = next((p for p in game.players if p.alive and p.role_name == "زعيم_الطائفة"), None)
    if cl and cl.cult_target and game.day % 2 == 1:
        target = next((p for p in game.players if p.user_id == cl.cult_target), None)
        if target and target.alive and not target.cult_team:
            target.cult_team = True
            target.cult_known_cult.add(cl.user_id)
            cl.cult_known_cult.add(target.user_id)
            events.append(random.choice(DEATH_MESSAGES["cult"]).format(target=f"<@{target.user_id}>"))
    for p in game.players:
        if p.alive and p.role_name in ("الرجل_الوحش", "المحتال_الانتهازي") and p.user_id == kill_target_id:
            p.met_mafia = True
    for did in list(dead_ids):
        d = next((p for p in game.players if p.user_id == did), None)
        if d and d.lover_with:
            lover = next((p for p in game.players if p.user_id == d.lover_with), None)
            if lover and lover.alive:
                lover.alive = False
                game.dead_players.append(lover)
                events.append(random.choice(DEATH_MESSAGES["lover_death"]).format(target=f"<@{lover.user_id}>"))
    if events:
        embed = discord.Embed(title=f"🌅 الصباح — أحداث الليل {game.day}", description="\n".join(events), color=discord.Color.dark_orange())
        await channel.send(embed=embed)


async def end_game_show_winner(game: GameState, channel: discord.TextChannel, winner: str) -> None:
    game.phase = "ended"
    for p in game.players:
        stats = _load_stats()
        key = str(p.user_id)
        s = stats.get(key, {})
        s["games_played"] = s.get("games_played", 0) + 1
        rp = s.setdefault("roles_played", {})
        rp[p.role_name] = rp.get(p.role_name, 0) + 1
        if p.alive:
            s["times_survived"] = s.get("times_survived", 0) + 1
        team = ROLES[p.role_name].team
        if team in ("mafia", "helper"):
            s["games_as_mafia"] = s.get("games_as_mafia", 0) + 1
            if winner == "mafia":
                s["wins_as_mafia"] = s.get("wins_as_mafia", 0) + 1
        else:
            s["games_as_citizen"] = s.get("games_as_citizen", 0) + 1
            if winner == "citizens":
                s["wins_as_citizen"] = s.get("wins_as_citizen", 0) + 1
        stats[key] = s
        _save_json(STATS_FILE, stats)
        ranks = _load_ranks()
        if winner == "mafia" and team in ("mafia", "helper"):
            ranks[key] = ranks.get(key, INITIAL_POINTS) + 30
        elif winner == "citizens" and team == "citizens":
            ranks[key] = ranks.get(key, INITIAL_POINTS) + 30
        elif winner == "lovers" and p.role_name == LOVER_ROLE:
            ranks[key] = ranks.get(key, INITIAL_POINTS) + 50
        elif winner == "cult" and team == "cult":
            ranks[key] = ranks.get(key, INITIAL_POINTS) + 50
        else:
            ranks[key] = ranks.get(key, INITIAL_POINTS) + 5
        _save_ranks(ranks)
    update_elo_for_game(game, winner)
    # فحص التحديات اليومية
    challenge_rewards: dict[int, int] = {}
    for p in game.players:
        completed = check_challenge_completion(p.user_id, game, winner)
        if completed:
            total_reward = 0
            for cid in completed:
                ch = next((c for c in DAILY_CHALLENGES if c["id"] == cid), None)
                if ch:
                    total_reward += ch["reward"]
            if total_reward > 0:
                challenge_rewards[p.user_id] = total_reward
                ranks = _load_ranks()
                key = str(p.user_id)
                ranks[key] = ranks.get(key, INITIAL_POINTS) + total_reward
                _save_ranks(ranks)
    # فحص إنجازات جديدة
    new_achievements: dict[int, list[str]] = {}
    for p in game.players:
        newly_unlocked = []
        if winner == "mafia" and ROLES[p.role_name].team in ("mafia", "helper"):
            if unlock_achievement(p.user_id, "mafia_lord"):
                newly_unlocked.append("mafia_lord")
        if winner == "citizens" and ROLES[p.role_name].team == "citizens":
            if unlock_achievement(p.user_id, "citizen_hero"):
                newly_unlocked.append("citizen_hero")
        if winner == "lovers" and p.role_name == LOVER_ROLE:
            if unlock_achievement(p.user_id, "lovers_fate"):
                newly_unlocked.append("lovers_fate")
        if winner == "cult" and ROLES[p.role_name].team == "cult":
            if unlock_achievement(p.user_id, "cult_master"):
                newly_unlocked.append("cult_master")
        if p.alive and game.day > 3:
            if unlock_achievement(p.user_id, "survivor"):
                newly_unlocked.append("survivor")
        stats = get_stats(p.user_id)
        if stats.get("games_played", 0) >= 50:
            if unlock_achievement(p.user_id, "veteran"):
                newly_unlocked.append("veteran")
        if newly_unlocked:
            new_achievements[p.user_id] = newly_unlocked
    embed = build_winner_embed(game, winner)
    # إضافة معلومات التحديات
    if challenge_rewards:
        reward_text = "\n".join(f"• <@{uid}>: +{pts} نقطة" for uid, pts in challenge_rewards.items())
        embed.add_field(name="🎯 مكافآت التحديات", value=reward_text, inline=False)
    # إضافة معلومات الإنجازات
    if new_achievements:
        ach_text_lines = []
        for uid, achs in new_achievements.items():
            for ach_id in achs:
                ach = ACHIEVEMENTS.get(ach_id)
                if ach:
                    ach_text_lines.append(f"• <@{uid}>: {ach.emoji} {ach.name}")
        if ach_text_lines:
            embed.add_field(name="🏆 إنجازات جديدة", value="\n".join(ach_text_lines[:10]), inline=False)
    # إضافة معلومات ELO
    elo_text_lines = []
    for p in game.players:
        key = str(p.user_id)
        elo = get_elo(p.user_id)
        elo_title = get_elo_title(elo)
        elo_text_lines.append(f"• <@{p.user_id}>: **{elo}** {elo_title}")
    if elo_text_lines:
        embed.add_field(name="📊 ELO", value="\n".join(elo_text_lines[:12]), inline=False)
    await channel.send(embed=embed)


# ============================================================================
# الأوامر
# ============================================================================
@bot.command(name="مافيا", aliases=["mafiastart", "m", "ابدأ"])
async def cmd_start(ctx: commands.Context, mode: str = ""):
    """بدء لعبة جديدة - سيفتح لوبي تفاعلي."""
    if not is_allowed_channel(ctx):
        return await ctx.send("❌ هذه القناة غير مفعّلة.", delete_after=5)
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id in games and games[guild_id].phase != "ended":
        return await ctx.send("⚠️ توجد لعبة جارية. اضغط زر **إنهاء** في رسالة اللوبي، أو `&إنهاء`.", delete_after=8)
    game = GameState(guild_id=guild_id, channel_id=ctx.channel.id, host_id=ctx.author.id)
    game.is_fast = mode in ("سريع", "fast", "سريعة")
    games[guild_id] = game
    embed = build_lobby_embed(game)
    view = MafiaLobbyView()
    msg = await ctx.send(embed=embed, view=view)
    game.lobby_message_id = msg.id
    lobby_refresh_tasks[guild_id] = bot.loop.create_task(
        lobby_refresh_task(guild_id, ctx.channel.id, msg.id)
    )


@bot.command(name="إنهاء", aliases=["end", "انهاء"])
async def cmd_end(ctx: commands.Context):
    """إنهاء اللعبة (نص)."""
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return await ctx.send("❌ لا توجد لعبة.", delete_after=5)
    game = games[guild_id]
    if ctx.author.id != game.host_id and not (ctx.author.guild_permissions and ctx.author.guild_permissions.administrator):
        return await ctx.send("❌ للمضيف/المشرف فقط.", delete_after=5)
    if game.next_phase_event:
        game.next_phase_event.set()
    games.pop(guild_id, None)
    await ctx.send("⛔ تم إنهاء اللعبة.", delete_after=5)


@bot.command(name="حالة", aliases=["status"])
async def cmd_status(ctx: commands.Context):
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return await ctx.send("❌ لا توجد لعبة.", delete_after=5)
    game = games[guild_id]
    phase_ar = {"lobby": "اللوبي 🟣", "day": "النهار ☀️", "night": "الليل 🌙", "ended": "انتهت ⛔"}.get(game.phase, game.phase)
    embed = discord.Embed(title="📊 حالة اللعبة", color=discord.Color.blue())
    alive = [p for p in game.players if p.alive]
    dead = [p for p in game.players if not p.alive]
    embed.add_field(name="المرحلة", value=phase_ar, inline=True)
    embed.add_field(name="اليوم", value=str(game.day), inline=True)
    embed.add_field(name="اللاعبون", value=f"{len(game.players)}/{MAX_PLAYERS}", inline=True)
    embed.add_field(name="🟢 الأحياء", value="\n".join(f"• <@{p.user_id}>" for p in alive[:10]) or "—", inline=True)
    if dead:
        embed.add_field(name="💀 الأموات", value="\n".join(f"• <@{p.user_id}>" for p in dead[:10]) or "—", inline=True)
    embed.set_footer(text="اضغط 🎭 دوري لمعرفة دورك")
    await ctx.send(embed=embed, view=GameStatusView(game))


@bot.command(name="نقاط", aliases=["نقاطي", "points", "score"])
async def cmd_points(ctx: commands.Context, member: discord.Member = None):
    """نقاط اللاعب — تفاعلي مع أزرار للتبديل بين الإحصائيات والإنجازات."""
    target = member or ctx.author
    view = AccountView(target.id, target.display_name, str(target.display_avatar.url))
    view.page = "points"
    embed = await view._build_embed()
    view.build_buttons()
    await ctx.send(embed=embed, view=view)


@bot.command(name="إحصائيات", aliases=["احصائيات", "stats"])
async def cmd_stats_cmd(ctx: commands.Context, member: discord.Member = None):
    """إحصائيات اللاعب — تفاعلي مع أزرار للتبديل بين النقاط والإنجازات."""
    target = member or ctx.author
    view = AccountView(target.id, target.display_name, str(target.display_avatar.url))
    view.page = "stats"
    embed = await view._build_embed()
    view.build_buttons()
    await ctx.send(embed=embed, view=view)


@bot.command(name="إنجازاتي", aliases=["انجازاتي", "achievements"])
async def cmd_achievements(ctx: commands.Context, member: discord.Member = None):
    """إنجازات اللاعب — تفاعلي مع أزرار للتبديل بين النقاط والإحصائيات."""
    target = member or ctx.author
    view = AccountView(target.id, target.display_name, str(target.display_avatar.url))
    view.page = "achievements"
    embed = await view._build_embed()
    view.build_buttons()
    if not view.achievement_ids and view.page == "achievements":
        return await ctx.send(f"**{target.display_name}** لا يملك إنجازات بعد.", delete_after=10)
    await ctx.send(embed=embed, view=view)


@bot.command(name="تصنيف", aliases=["ترتيب", "leaderboard", "top"])
async def cmd_leaderboard(ctx: commands.Context):
    elo_data = _load_elo()
    if not elo_data:
        return await ctx.send("لا يوجد تصنيف بعد.")
    sorted_elo = sorted(elo_data.items(), key=lambda x: -x[1].get("elo", 1000))[:10]
    medals = ["🥇", "🥈", "🥉"] + [f"**{i}.**" for i in range(4, 11)]
    lines = []
    for i, (uid, data) in enumerate(sorted_elo):
        elo = data.get("elo", 1000)
        title = get_elo_title(elo)
        games = data.get("games", 0)
        wins = data.get("wins", 0)
        try:
            user = await bot.fetch_user(int(uid))
            name = user.display_name
        except Exception:
            name = f"User#{uid}"
        winrate = f"{wins}/{games}" if games > 0 else "0/0"
        lines.append(f"{medals[i]} {name}: **{elo}** | {title} | {winrate}")
    embed = discord.Embed(title="🏆 أفضل 10 لاعبين (ELO)", description="\n".join(lines), color=discord.Color.gold())
    embed.set_footer(text="التصنيف مبني على نظام ELO الحقيقي")
    await ctx.send(embed=embed)


@bot.command(name="أدوار", aliases=["ادوار", "roles"])
async def cmd_roles(ctx: commands.Context):
    embed = discord.Embed(title="📖 أدوار مافيا 42", description=f"**{len(ROLES)} دور رسمي**\nاختر فئة لتصفّح الأدوار.", color=discord.Color.dark_blue())
    await ctx.send(embed=embed, view=RoleBrowserView())


@bot.command(name="دور", aliases=["role"])
async def cmd_role_info(ctx: commands.Context, *, role_name: str = ""):
    if not role_name:
        embed = discord.Embed(title="📖 أدوار مافيا 42", description=f"**{len(ROLES)} دور رسمي**\nاختر فئة لتصفّح الأدوار.", color=discord.Color.dark_blue())
        return await ctx.send(embed=embed, view=RoleBrowserView())
    found = None
    for name, role in ROLES.items():
        if role_name.strip() in name or name in role_name.strip():
            found = role
            break
    if not found:
        return await ctx.send("❌ لم أجد دوراً بهذا الاسم. افتح متصفح الأدوار بـ `&أدوار`.", delete_after=5)
    team_ar = {"mafia": "🔴 المافيا", "citizens": "🟢 المواطنون", "cult": "🟣 الطائفة", "neutral": "🟡 محايد", "helper": "🟠 مساعدو المافيا"}
    color = {"mafia": discord.Color.dark_red(), "citizens": discord.Color.green(), "cult": discord.Color.purple(), "neutral": discord.Color.gold(), "helper": discord.Color.orange()}.get(found.team, discord.Color.greyple())
    embed = discord.Embed(title=f"{found.emoji} {found.name}", description=found.description, color=color)
    embed.add_field(name="👥 الفريق", value=team_ar.get(found.team, found.team), inline=True)
    embed.add_field(name="💎 الندرة", value=found.rarity, inline=True)
    if found.night_action:
        embed.add_field(name="🌙 فعل ليلي", value="✅", inline=True)
    tips = ROLE_TIPS.get(found.name, [])
    if tips:
        tips_text = "\n".join(f"• {t}" for t in tips)
        embed.add_field(name="💡 نصائح استراتيجية", value=tips_text, inline=False)
    await ctx.send(embed=embed, view=RoleBrowserView())


@bot.command(name="دوري", aliases=["myrole"])
async def cmd_my_role(ctx: commands.Context):
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return await ctx.send("❌ لا توجد لعبة.", delete_after=5)
    game = games[guild_id]
    player = next((p for p in game.players if p.user_id == ctx.author.id), None)
    if not player:
        return await ctx.send("❌ لست في اللعبة.", delete_after=5)
    try:
        embed = build_role_dm_embed(ROLES[player.role_name])
        await ctx.author.send(embed=embed)
        await ctx.message.add_reaction("✅")
    except discord.Forbidden:
        await ctx.send("❌ افتح الخاص لأرسل لك دورك.", delete_after=5)


@bot.command(name="همس", aliases=["whisper"])
async def cmd_whisper(ctx: commands.Context, *, message: str = ""):
    """إرسال همسة سرية للمافيا."""
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return await ctx.send("❌ لا توجد لعبة.", delete_after=5)
    game = games[guild_id]
    if not message:
        return await ctx.send("استخدم: `&همس <رسالة>` أو افتح لوحة الحالة `&حالة` واضغط همس.", delete_after=5)
    player = next((p for p in game.players if p.user_id == ctx.author.id), None)
    if not player or not player.alive:
        return await ctx.send("❌ أنت ميت.", delete_after=5)
    role = ROLES[player.role_name]
    if role.team not in ("mafia", "helper") and not player.met_mafia:
        return await ctx.send("❌ لست من المافيا.", delete_after=5)
    sent = 0
    for p in game.players:
        if ROLES[p.role_name].team in ("mafia", "helper") and p.met_mafia:
            try:
                user = bot.get_user(p.user_id)
                if user:
                    embed = discord.Embed(title="💬 همسة 🔪", description=f"من {ctx.author.display_name}:\n{message}", color=discord.Color.dark_red())
                    await user.send(embed=embed)
                    sent += 1
            except Exception:
                pass
    await ctx.send(f"✅ أُرسلت لـ {sent} عضو.", delete_after=5)


@bot.command(name="وريث", aliases=["successor"])
async def cmd_successor(ctx: commands.Context, member: discord.Member = None):
    """تعيين وريث (لرئيس المافيا فقط)."""
    if not member:
        return await ctx.send("استخدم: `&وريث @لاعب`", delete_after=5)
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return await ctx.send("❌ لا توجد لعبة.", delete_after=5)
    game = games[guild_id]
    player = next((p for p in game.players if p.user_id == ctx.author.id), None)
    if not player or player.role_name != "رئيس_المافيا":
        return await ctx.send("❌ فقط رئيس المافيا.", delete_after=5)
    target = next((p for p in game.players if p.user_id == member.id), None)
    if not target or not target.alive:
        return await ctx.send("❌ اللاعب غير متاح.", delete_after=5)
    if ROLES[target.role_name].team not in ("mafia", "helper"):
        return await ctx.send("❌ يجب أن يكون من المافيا.", delete_after=5)
    player.role_name, target.role_name = target.role_name, player.role_name
    await ctx.send(f"✅ تم نقل القيادة إلى {member.mention}", delete_after=10)


@bot.command(name="مساعدة", aliases=["help", "h", "مساعده"])
async def cmd_help(ctx: commands.Context, section: str = ""):
    if section in ("أدوار", "ادوار", "roles"):
        embed = discord.Embed(title="📖 أدوار مافيا 42", description=f"**{len(ROLES)} دور رسمي**\nاختر فئة لتصفّح الأدوار.", color=discord.Color.dark_blue())
        return await ctx.send(embed=embed, view=RoleBrowserView())
    embed = discord.Embed(
        title=f"📚 مساعدة مافيا 42 v{BOT_VERSION}",
        description=f"بوت مافيا 42 التفاعلي مع {len(ROLES)} دور و {len(ACHIEVEMENTS)} إنجاز.\nاختر فئة من الأسفل.",
        color=discord.Color.blurple(),
    )
    await ctx.send(embed=embed, view=HelpView())


@bot.command(name="نصيحة", aliases=["tip"])
async def cmd_tip(ctx: commands.Context):
    tips = [
        "💡 الشرطي: لا تُعلن قبل أن تتأكد.",
        "💡 المافيا: تكلم كمواطن عادي.",
        "💡 الطبيب: غيّر أهدافك كل ليلة.",
        "💡 القنّاص: رصاصة واحدة، استخدمها بحكمة.",
        "💡 العاشق: إذا متّ، يموت حبيبك.",
        "💡 زعيم الطائفة: تجنيدك يقلب الموازين.",
    ]
    await ctx.send(random.choice(tips))


@bot.command(name="تحديات", aliases=["challenges", "تحدي"])
async def cmd_challenges(ctx: commands.Context):
    """عرض التحديات اليومية المتاحة."""
    challenges = get_today_challenges()
    if not challenges:
        return await ctx.send("❌ لا توجد تحديات اليوم.")
    desc = "🎯 **تحديات اليوم**\n\n"
    for i, ch in enumerate(challenges, 1):
        desc += f"**{i}.** {ch['name']}\n"
        desc += f"   📋 {ch['description']}\n"
        desc += f"   💰 مكافأة: **{ch['reward']}** نقطة\n\n"
    embed = discord.Embed(title="🎯 تحديات اليوم", description=desc, color=discord.Color.orange())
    embed.set_footer(text="أكمل التحديات للكسب! التحديات تتغير يومياً.")
    await ctx.send(embed=embed)


@bot.command(name="بوت", aliases=["botinfo", "about"])
async def cmd_bot_info(ctx: commands.Context):
    embed = discord.Embed(title=f"🤖 مافيا 42 v{BOT_VERSION}", description="بوت لعبة مافيا 42 التفاعلي بأزرار ورسائل مخفية.", color=discord.Color.blurple())
    embed.add_field(name="🎮 الأدوار", value=f"{len(ROLES)} دور", inline=True)
    embed.add_field(name="🏆 الإنجازات", value=f"{len(ACHIEVEMENTS)} إنجاز", inline=True)
    embed.add_field(name="🎯 التحديات", value=f"{len(DAILY_CHALLENGES)} تحدي يومي", inline=True)
    embed.add_field(name="🏠 السيرفرات", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="🎯 الألعاب النشطة", value=str(len([g for g in games.values() if g.started])), inline=True)
    embed.add_field(name="📊 نظام ELO", value="تصنيف تنافسي", inline=True)
    embed.add_field(name="💤 كشف AFK", value="تنبيه تلقائي", inline=True)
    embed.add_field(name="🔔 تأكيد ليلي", value="اختيار + تأكيد", inline=True)
    embed.add_field(name="البادئة", value="`&`", inline=True)
    embed.add_field(name="اللاعبون", value=f"{MIN_PLAYERS}-{MAX_PLAYERS}", inline=True)
    await ctx.send(embed=embed)


# ============================================================================
# أوامر المشرف
# ============================================================================
@bot.command(name="اضافه_قناة", aliases=["addchannel"])
@commands.has_permissions(administrator=True)
async def cmd_add_channel(ctx: commands.Context, channel_id: int = 0):
    if not channel_id:
        return await ctx.send("استخدم: `&اضافه_قناة <ID>`", delete_after=5)
    allowed = _load_allowed()
    guild_key = str(ctx.guild.id)
    allowed.setdefault(guild_key, [])
    if channel_id in allowed[guild_key]:
        return await ctx.send("⚠️ مضافة بالفعل.", delete_after=5)
    allowed[guild_key].append(channel_id)
    _save_json(ALLOWED_CHANNELS_FILE, allowed)
    await ctx.send(f"✅ تم إضافة <#{channel_id}>.")


@bot.command(name="حذف_قناة", aliases=["removechannel"])
@commands.has_permissions(administrator=True)
async def cmd_remove_channel(ctx: commands.Context, channel_id: int = 0):
    if not channel_id:
        return await ctx.send("استخدم: `&حذف_قناة <ID>`", delete_after=5)
    allowed = _load_allowed()
    guild_key = str(ctx.guild.id)
    if channel_id in allowed.get(guild_key, []):
        allowed[guild_key].remove(channel_id)
        _save_json(ALLOWED_CHANNELS_FILE, allowed)
        await ctx.send(f"✅ تم حذف <#{channel_id}>.")
    else:
        await ctx.send("⚠️ غير مضافة.", delete_after=5)


@bot.command(name="قنوات", aliases=["channels"])
@commands.has_permissions(administrator=True)
async def cmd_channels(ctx: commands.Context):
    allowed = _load_allowed()
    channels = allowed.get(str(ctx.guild.id), [])
    if not channels:
        return await ctx.send("كل القنوات مسموح بها.")
    await ctx.send("📋 القنوات المسموح بها:\n" + "\n".join(f"• <#{c}>" for c in channels))


@bot.command(name="ريست_نقاط", aliases=["reset_ranks"])
@commands.has_permissions(administrator=True)
async def cmd_reset_ranks(ctx: commands.Context):
    _save_ranks({})
    _save_json(STATS_FILE, {})
    await ctx.send("✅ تم إعادة الضبط.")


@bot.command(name="اعطاء_نقاط", aliases=["give_points"])
@commands.has_permissions(administrator=True)
async def cmd_give_points(ctx: commands.Context, member: discord.Member = None, amount: int = 0):
    if not member or amount == 0:
        return await ctx.send("استخدم: `&اعطاء_نقاط @لاعب <كم>`", delete_after=5)
    ranks = _load_ranks()
    key = str(member.id)
    ranks[key] = max(0, ranks.get(key, INITIAL_POINTS) + amount)
    _save_ranks(ranks)
    sign = "+" if amount >= 0 else ""
    await ctx.send(f"✅ {member.mention}: {sign}{amount} → **{ranks[key]:,}**")


@bot.command(name="حذف_نقاط", aliases=["remove_points"])
@commands.has_permissions(administrator=True)
async def cmd_remove_points(ctx: commands.Context, member: discord.Member = None, amount: int = 0):
    if not member or amount == 0:
        return await ctx.send("استخدم: `&حذف_نقاط @لاعب <كم>`", delete_after=5)
    ranks = _load_ranks()
    key = str(member.id)
    ranks[key] = max(0, ranks.get(key, INITIAL_POINTS) - amount)
    _save_ranks(ranks)
    await ctx.send(f"✅ {member.mention}: -{amount} → **{ranks[key]:,}**")


@bot.command(name="اعلان", aliases=["announce"])
@commands.has_permissions(administrator=True)
async def cmd_announce(ctx: commands.Context, *, message: str = ""):
    if not message:
        return await ctx.send("استخدم: `&اعلان <رسالة>`", delete_after=5)
    embed = discord.Embed(title="📢 إعلان مافيا 42", description=message, color=discord.Color.gold())
    embed.set_footer(text=f"من: {ctx.author.display_name}")
    await ctx.send(embed=embed)


@bot.command(name="باكب", aliases=["backup"])
@commands.has_permissions(administrator=True)
async def cmd_backup(ctx: commands.Context):
    files = []
    for path in (RANKS_FILE, ALLOWED_CHANNELS_FILE, STATS_FILE, ACHIEVEMENTS_FILE):
        if path.exists():
            files.append(discord.File(str(path)))
    if not files:
        return await ctx.send("لا توجد ملفات.", delete_after=5)
    await ctx.send("✅ نسخة احتياطية:", files=files)


# ============================================================================
# نقطة الدخول
# ============================================================================
@bot.event
async def on_ready():
    global persistent_views_registered
    if not persistent_views_registered:
        bot.add_view(MafiaLobbyView())
        persistent_views_registered = True
        log.info("✅ تم تسجيل الـ persistent views")
    log.info("✅ البوت جاهز: %s (ID: %s)", bot.user, bot.user.id)
    log.info("   السيرفرات: %d", len(bot.guilds))
    log.info("   الألعاب النشطة: %d", len([g for g in games.values() if g.started]))


async def lobby_refresh_task(guild_id: int, channel_id: int, message_id: int):
    """يحدّث رسالة اللوبي كل 5 دقائق للحفاظ على نشاط الأزرار."""
    while guild_id in games and not games[guild_id].started:
        await asyncio.sleep(300)
        if guild_id not in games or games[guild_id].started:
            break
        game = games[guild_id]
        try:
            channel = bot.get_channel(channel_id)
            if channel:
                msg = await channel.fetch_message(message_id)
                if msg:
                    await msg.edit(embed=build_lobby_embed(game), view=MafiaLobbyView())
        except Exception:
            pass


def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        log.error("❌ متغير DISCORD_TOKEN غير موجود.")
        sys.exit(1)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
