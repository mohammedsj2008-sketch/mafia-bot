"""
Mafia 42 Discord Bot - Full rewrite aligned with Mafia42 game system.
32 official roles from mafia42.fandom.com, with proper Cult team, Lover, and
the official role distribution table (4-12 players).

Author: Antigravity (rewrite)
Version: 4.0.0
"""
from __future__ import annotations

import asyncio
import io
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
BOT_VERSION = "4.0.0"
INITIAL_POINTS = 1000
MIN_PLAYERS = 4
MAX_PLAYERS = 12
DAY_DURATION_DEFAULT = 90
NIGHT_DURATION_DEFAULT = 75
VOTE_DURATION = 45

DATA_DIR = Path(os.environ.get("MAFIA_DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
RANKS_FILE = DATA_DIR / "mafia_ranks.json"
ALLOWED_CHANNELS_FILE = DATA_DIR / "mafia_allowed_channels.json"
STATS_FILE = DATA_DIR / "mafia_stats.json"
ACHIEVEMENTS_FILE = DATA_DIR / "mafia_achievements.json"
HISTORY_FILE = DATA_DIR / "mafia_history.json"

# Logger
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


# ============================================================================
# جدول توزيع الأدوار الرسمي من Mafia42 (Classic Mode)
# كل صف: (عدد_اللاعبين, mafia, helper, cult_leader, fanatic, special)
# Special = Cop/Vigilante + Doctor + Citizens الأخرى
# ============================================================================
# نولّد خريطة التوزيع برمجياً بناءً على العدد:
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

# خزّانات الأدوار لكل فئة - تُستخدم لتوليد التوزيع
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

# أدوار Cop/Vigilante/Doctor مضمونة دائماً
ALWAYS_PRESENT = {
    4: ["الشرطي", "الطبيب"],
    5: ["الشرطي", "الطبيب"],
    6: ["الشرطي", "الطبيب"],
    7: ["الشرطي", "الطبيب"],
    8: ["الشرطي", "الطبيب"],
    9: ["الشرطي", "الطبيب"],
    10: ["الشرطي", "الطبيب"],
    11: ["الشرطي", "الطبيب"],
    12: ["الشرطي", "الطبيب"],
}


# ============================================================================
# نموذج الدور
# ============================================================================
@dataclass(frozen=True)
class Role:
    name: str
    description: str
    team: str  # mafia | citizens | cult | neutral | helper
    emoji: str
    rarity: str = "common"  # common | rare | legendary
    night_action: bool = False
    cult_team_eligible: bool = False  # هل يمكنه الانضمام للطائفة

    def display(self) -> str:
        star = "⭐" if self.rarity == "legendary" else "🔵" if self.rarity == "rare" else ""
        return f"{self.emoji} **{self.name}** {star}".strip()


# ============================================================================
# 32 دور رسمي من Mafia42
# ============================================================================
ROLES: dict[str, Role] = {
    # -------- المافيا (4 أدوار) --------
    "رئيس_المافيا": Role(
        name="رئيس_المافيا",
        description=(
            "🎭 **زعيم المافيا.** يجتمع مع المافيا كل ليلة.\n"
            "إذا مات، يُمرّر دوره لأحد المافيا الأحياء عشوائياً.\n"
            "✅ له صوت مضاعف عند اختيار هدف المافيا."
        ),
        team="mafia", emoji="👑", rarity="legendary", night_action=True,
    ),
    "العميل": Role(
        name="العميل",
        description=(
            "🕵️ **عميل سري.** يجتمع مع المافيا.\n"
            "يظهر للشرطي كمحقق (متمرد) ليُضلّله."
        ),
        team="mafia", emoji="🕵️", rarity="rare", night_action=True,
    ),
    "المُنفّذ": Role(
        name="المُنفّذ",
        description=(
            "🔪 **القاتل المأجور.** يجتمع مع المافيا.\n"
            "🎯 **نادراً:** يمكنه تنفيذ ضربة قاتلة مستقلة (مرة واحدة في اللعبة)."
        ),
        team="mafia", emoji="🔪", rarity="rare", night_action=True,
    ),
    "المحتال": Role(
        name="المحتال",
        description=(
            "🦹 **محتال ذكي.** يجتمع مع المافيا.\n"
            "🛡️ **درع:** إذا حاول القنّاص قتله، يبقى حياً ويستلم دور القنّاص."
        ),
        team="mafia", emoji="🦹", rarity="legendary", night_action=True,
    ),
    # -------- المافيا المساعدة (Helpers - تظهر كمافيا عند التحقيق) (7 أدوار) --------
    "الجاسوسة": Role(
        name="الجاسوسة",
        description=(
            "🔮 **جاسوسة ذكية.** تجتمع مع المافيا.\n"
            "✅ تحقق من لاعب كل ليلة → تظهر نتيجته للزعيم فقط."
        ),
        team="helper", emoji="🔮", rarity="rare", night_action=True,
    ),
    "المضيفة": Role(
        name="المضيفة",
        description=(
            "💋 **مضيفة فاتنة.** تجتمع مع المافيا.\n"
            "👄 **الإغواء:** في النهار، تصوّت على لاعب بدل اللاعب المُختار ليُنفّذ."
        ),
        team="helper", emoji="💋", rarity="rare", night_action=False,
    ),
    "اللص": Role(
        name="اللص",
        description=(
            "🦝 **لص ماهر.** يجتمع مع المافيا.\n"
            "💰 يسرق ممتلكات لاعب عند موته (سرقة موته الأولى)."
        ),
        team="helper", emoji="🦝", rarity="common", night_action=False,
    ),
    "الرجل_الوحش": Role(
        name="الرجل_الوحش",
        description=(
            "🐺 **مخلوق وحشي.** يجتمع مع المافيا.\n"
            "🦴 إذا هاجم لاعباً ومات معه، يُفترس ويبقى المافيا."
        ),
        team="helper", emoji="🐺", rarity="legendary", night_action=False,
    ),
    "العالم_المجنون": Role(
        name="العالم_المجنون",
        description=(
            "🧪 **عالم مجنون.** يجتمع مع المافيا.\n"
            "🧪 يحقن لاعباً كل ليلتين: يقتله ببطء في الليلة الموالية."
        ),
        team="helper", emoji="🧪", rarity="legendary", night_action=True,
    ),
    "الساحرة": Role(
        name="الساحرة",
        description=(
            "🧙‍♀️ **ساحرة غامضة.** تجتمع مع المافيا.\n"
            "🧪 تخلط جرعة تعيد لاعباً من الموت (مرة واحدة)."
        ),
        team="helper", emoji="🧙‍♀️", rarity="legendary", night_action=True,
    ),
    "المحتال_الانتهازي": Role(
        name="المحتال_انتهازي",
        description=(
            "🎭 **محتال ينتهز الفرص.** يجتمع مع المافيا.\n"
            "✅ **التقمّص:** يعرف مواطناً عشوائياً من البداية. عند مهاجمته ليلاً، يلتقي بالمافيا."
        ),
        team="helper", emoji="🎭", rarity="legendary", night_action=False,
    ),
    # -------- المواطنون - ضمان (Cop/Vigilante + Doctor) --------
    "الشرطي": Role(
        name="الشرطي",
        description=(
            "👮 **محقق.** يحقق من لاعب كل ليلة.\n"
            "✅ المافيا تظهر كمتمردين (🔴). المواطنون يظهرون كأبرياء (🟢)."
        ),
        team="citizens", emoji="👮", rarity="rare", night_action=True,
    ),
    "القنّاص": Role(
        name="القنّاص",
        description=(
            "🔫 **قنّاص ثائر.** يقتل لاعباً مرة واحدة في اللعبة.\n"
            "✅ إذا أطلق النار على محتال، يبقى القنّاص حيّاً."
        ),
        team="citizens", emoji="🔫", rarity="legendary", night_action=True,
    ),
    "الطبيب": Role(
        name="الطبيب",
        description=(
            "💉 **طبيب.** يحمي لاعباً كل ليلة.\n"
            "🛡️ يحمي الهدف من القتل (لا يستطيع حماية نفسه ليلتين متتاليتين)."
        ),
        team="citizens", emoji="💉", rarity="rare", night_action=True,
    ),
    "الممرضة": Role(
        name="الممرضة",
        description=(
            "👩‍⚕️ **ممرضة شابة.** تحمي لاعباً كل ليلة.\n"
            "🛡️ مثل الطبيب. يمكنها أن تحلّ محله في حال موته (تختاره المافيا)."
        ),
        team="citizens", emoji="👩‍⚕️", rarity="rare", night_action=True,
    ),
    # -------- المواطنون الآخرون (16 دور) --------
    "الجندي": Role(
        name="الجندي",
        description=(
            "🛡️ **جندي شجاع.** يجتاز هجوماً واحداً على حياته (مرة واحدة)."
        ),
        team="citizens", emoji="🛡️", rarity="common", night_action=False,
    ),
    "السياسي": Role(
        name="السياسي",
        description=(
            "🎩 **سياسي.** لا يمكن أن يُحقق من قبل الشرطي (يظهر كمشبوه فقط)."
        ),
        team="citizens", emoji="🎩", rarity="common", night_action=False,
    ),
    "الروحي": Role(
        name="الروحي",
        description=(
            "🔮 **روحي.** يرى لاعباً واحداً عشوائياً من المافيا في الليلة الأولى."
        ),
        team="citizens", emoji="🔮", rarity="common", night_action=False,
    ),
    "المراسل": Role(
        name="المراسل",
        description=(
            "📰 **مراسل.** يحقق من لاعب في ليلتين عشوائيتين → نتيجته: مافيا أو غير مافيا."
        ),
        team="citizens", emoji="📰", rarity="common", night_action=False,
    ),
    "المحقق": Role(
        name="المحقق",
        description=(
            "🕵️ **محقق خاص.** يحقق من لاعب واحد في اللعبة → نتيجته دقيقة 100%."
        ),
        team="citizens", emoji="🕵️", rarity="legendary", night_action=True,
    ),
    "الغول": Role(
        name="الغول",
        description=(
            "👹 **غول خطير.** إذا مات، يختار لاعباً ليموت معه (بعد معرفة دوره)."
        ),
        team="citizens", emoji="👹", rarity="rare", night_action=False,
    ),
    "الشهد": Role(
        name="الشهد",
        description=(
            "💀 **شهد/ناسك.** إذا مات، يتكلم ويكشف دوره قبل موته."
        ),
        team="citizens", emoji="💀", rarity="rare", night_action=False,
    ),
    "الكاهن": Role(
        name="الكاهن",
        description=(
            "⛪ **كاهن.** إذا هاجم المافيا هدفه، يكتشفهم ويظهرون لكل المواطنين."
        ),
        team="citizens", emoji="⛪", rarity="legendary", night_action=False,
    ),
    "الزعيم": Role(
        name="الزعيم",
        description=(
            "👔 **زعيم عصابة.** يجتمع مع المافيا.\n"
            "✅ إذا مات، يخسر المافيا 50% من قوتهم في التصويت."
        ),
        team="citizens", emoji="👔", rarity="legendary", night_action=False,
    ),
    "الساحر": Role(
        name="الساحر",
        description=(
            "🎩 **ساحر.** يبدّل أدوار لاعبين ليلتين متتاليتين (مرة واحدة)."
        ),
        team="citizens", emoji="🎩", rarity="legendary", night_action=True,
    ),
    "الهاكر": Role(
        name="الهاكر",
        description=(
            "💻 **هاكر.** يحقق من لاعب في الليلتين 2 و 4 → نتيجته 100% دقيقة."
        ),
        team="citizens", emoji="💻", rarity="legendary", night_action=True,
    ),
    "القاضي": Role(
        name="القاضي",
        description=(
            "⚖️ **قاضي.** في النهار، إذا حقّق لاعب في لاعب آخر، القاضي يعرف النتيجة."
        ),
        team="citizens", emoji="⚖️", rarity="legendary", night_action=False,
    ),
    "النبي": Role(
        name="النبي",
        description=(
            "🌟 **نبي.** إذا كان حياً، المافيا لا تستطيع الفوز في النهار."
        ),
        team="citizens", emoji="🌟", rarity="legendary", night_action=False,
    ),
    "المعالج_النفسي": Role(
        name="المعالج_النفسي",
        description=(
            "🧠 **معالج نفسي.** يحقق من لاعب → يعرف حالته (سليم/مخدّر/مهاجم)."
        ),
        team="citizens", emoji="🧠", rarity="legendary", night_action=True,
    ),
    "المرتزق": Role(
        name="المرتزق",
        description=(
            "💰 **مرتزق.** يقبل رشوة من المافيا: يصبح مساعداً لهم (سراً)."
        ),
        team="citizens", emoji="💰", rarity="rare", night_action=False,
    ),
    "المسؤول": Role(
        name="المسؤول",
        description=(
            "👔 **مسؤول.** إذا مات، تُكشف جميع أدوار المافيا (مرة واحدة)."
        ),
        team="citizens", emoji="👔", rarity="legendary", night_action=False,
    ),
    # -------- فريق Cult (2 أدوار) --------
    "زعيم_الطائفة": Role(
        name="زعيم_الطائفة",
        description=(
            "⛧ **زعيم طائفة مظلمة.** يجند لاعباً واحداً كل ليلتين فرديتين (1، 3، 5...).\n"
            "🛐 يجتمع مع أعضاء الطائفة. إذا مات، يخسر أعضاء الطائفة قوتهم."
        ),
        team="cult", emoji="⛧", rarity="legendary", night_action=True, cult_team_eligible=True,
    ),
    "المتعصب": Role(
        name="المتعصب",
        description=(
            "🛐 **متعصب.** يبحث عن زعيم الطائفة ليلاً.\n"
            "✅ إذا وجده، يصبح عضواً في الطائفة ويتحول لعب دور cult_member."
        ),
        team="cult", emoji="🛐", rarity="legendary", night_action=True, cult_team_eligible=True,
    ),
    # -------- محايد خاص --------
    "العاشق": Role(
        name="العاشق",
        description=(
            "💕 **عاشقان.** لاعبان عشوائيان يقترنان.\n"
            "✅ إذا مات أحدهما، يموت الآخر.\n"
            "✅ إذا صوّت كلاهما على نفس اللاعب، يُضاعف الصوت.\n"
            "🏆 **الفوز:** يربحان فقط إذا كانا آخر اثنين على قيد الحياة."
        ),
        team="neutral", emoji="💕", rarity="legendary", night_action=False,
    ),
}


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
    "first_blood": Achievement("first_blood", "الدم الأول", "كن أول من يموت في اللعبة", "🩸"),
    "survivor": Achievement("survivor", "الناجي", "ابقَ حتى آخر اللعبة", "🛡️"),
    "mafia_lord": Achievement("mafia_lord", "سيد المافيا", "فز بـ 10 لعب كمافيا", "👑"),
    "citizen_hero": Achievement("citizen_hero", "بطل المواطن", "فز بـ 10 لعب كمواطن", "🦸"),
    "lucky_sheriff": Achievement("lucky_sheriff", "الشرطي المحظوظ", "اكشف 5 مافيا بنجاح", "👮"),
    "medic_ace": Achievement("medic_ace", "الطبيب الماهر", "احمِ 5 أهداف بنجاح", "💉"),
    "lone_wolf": Achievement("lone_wolf", "الذئب الوحيد", "فز كمافيا ضد 5+ مواطنين", "🐺"),
    "perfect_game": Achievement("perfect_game", "لعبة مثالية", "فز دون أن يموت أحد من فريقك", "⭐"),
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
    protected_today: bool = False  # محمي من الطبيب
    investigated: dict[str, str] = field(default_factory=dict)  # نتائج التحقيقات
    actions_this_game: int = 0
    actions_used: int = 0
    # حقول Mafia42
    cult_team: bool = False  # عضو في الطائفة
    cult_known_cult: set[int] = field(default_factory=set)  # يعرف أعضاء الطائفة
    cult_target: Optional[int] = None  # هدف التجنيد لزعيم الطائفة
    cult_recruited: bool = False  # تم تجنيده
    lover_with: Optional[int] = None  # العاشق المقترن
    joined_mafia: bool = False  # متى يتكلم مع المافيا (للـ helpers)
    met_mafia: bool = False  # هل التقى بالمافيا
    swindled_role: Optional[str] = None  # دور المواطن الذي يعرفه المحتال
    detective_used: bool = False  # المحقق: مرة واحدة
    reporter_used: bool = False  # المراسل: مرتان
    hacker_used: bool = False  # الهاكر: مرتان
    soldier_used: bool = False  # الجندي: مرة واحدة
    sniper_used: bool = False  # القنّاص: مرة واحدة
    chemist_brew_used: bool = False  # الساحرة: مرة واحدة
    magician_swap_used: bool = False  # الساحر: مرة واحدة
    beast_visited: bool = False  # الرجل الوحش
    mad_scientist_target: Optional[int] = None  # العالم المجنون
    mad_scientist_pending: bool = False  # سيتم القتل في الليلة الموالية
    night_action_target: Optional[int] = None  # هدف الليلة
    day_vote_target: Optional[int] = None  # هدف النهار
    night_dead: bool = False
    inquirer_target: Optional[int] = None  # هدف القاضي
    loyal_visit_target: Optional[int] = None  # هدف العاشق
    pol_skill_used: bool = False  # السياسي: مرة واحدة
    dying_action_used: bool = False  # الشهد: مرة واحدة
    dying_reveal: Optional[str] = None  # دور الشهد الذي كشفه
    sd_target: Optional[int] = None  # المحقق
    police_target: Optional[int] = None  # الشرطي


@dataclass
class GameState:
    guild_id: int
    channel_id: int
    host_id: int
    players: list[PlayerState] = field(default_factory=list)
    phase: str = "lobby"  # lobby | day | vote | night | ended
    day: int = 0
    day_start_time: Optional[datetime] = None
    night_messages: dict[int, str] = field(default_factory=dict)
    dead_players: list[PlayerState] = field(default_factory=list)
    day_votes: dict[int, int] = field(default_factory=dict)  # user_id -> target_id
    day_voters: list[int] = field(default_factory=list)
    night_actions: dict[int, dict] = field(default_factory=dict)  # role_name -> {actor_id: target_id}
    night_kill_votes: dict[int, int] = field(default_factory=dict)  # mafia -> target
    used_lovers: bool = False
    used_cult: bool = False
    winner: Optional[str] = None
    inciter_choice: Optional[dict] = None  # {inciter_id, victim_id}
    doctor_protect: Optional[int] = None
    night_event_messages: list[str] = field(default_factory=list)
    morning_messages: list[str] = field(default_factory=list)
    day_action_messages: list[str] = field(default_factory=list)
    is_fast: bool = False
    started: bool = False
    is_lovers: bool = False
    lovers_ids: tuple[int, int] = (0, 0)
    setup_lock: bool = False
    day_continue_event: Optional[asyncio.Event] = None
    night_continue_event: Optional[asyncio.Event] = None
    auto_vote_task: Optional[asyncio.Task] = None
    day_task: Optional[asyncio.Task] = None
    night_task: Optional[asyncio.Task] = None
    next_phase_event: Optional[asyncio.Event] = None
    last_murder_target: Optional[int] = None
    pre_game_cult: list[int] = field(default_factory=list)
    pre_game_lover: list[int] = field(default_factory=list)
    last_protected_id: Optional[int] = None  # الطبيب
    had_mafia: bool = False  # هل كانت اللعبة تحتوي على مافيا في البداية


# ============================================================================
# دوال مساعدة للملفات
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
    if points >= 5000:
        return "👑 أسطورة"
    if points >= 4000:
        return "💎 ماسي"
    if points >= 3000:
        return "🥇 ذهبي"
    if points >= 2000:
        return "🥈 فضي"
    if points >= 1500:
        return "🥉 برونزي"
    if points >= 1000:
        return "⚪ مبتدئ"
    return "🐣 جديد"


def get_stats(user_id: int) -> dict:
    stats = _load_stats()
    return stats.get(str(user_id), {
        "games_played": 0,
        "wins_as_mafia": 0,
        "games_as_mafia": 0,
        "wins_as_citizen": 0,
        "games_as_citizen": 0,
        "times_survived": 0,
        "max_win_streak": 0,
        "roles_played": {},
    })


def get_player_achievements(user_id: int) -> list[str]:
    achs = _load_achievements()
    return achs.get(str(user_id), [])


def unlock_achievement(user_id: int, ach_id: str) -> bool:
    achs = _load_achievements()
    user_key = str(user_id)
    if ach_id in achs.get(user_key, []):
        return False
    achs.setdefault(user_key, []).append(ach_id)
    _save_json(ACHIEVEMENTS_FILE, achs)
    return True


# ============================================================================
# مساعدات التقديم
# ============================================================================
def get_role_image_path(role_name: str) -> Optional[Path]:
    return None  # لا توجد صور افتراضية


def build_role_embed(role: Role) -> discord.Embed:
    team_color = {
        "mafia": discord.Color.dark_red(),
        "citizens": discord.Color.green(),
        "cult": discord.Color.purple(),
        "neutral": discord.Color.gold(),
        "helper": discord.Color.orange(),
    }.get(role.team, discord.Color.greyple())
    embed = discord.Embed(title=f"{role.emoji} {role.name}", description=role.description, color=team_color)
    embed.add_field(name="الفريق", value=role.team, inline=True)
    embed.add_field(name="الندرة", value=role.rarity, inline=True)
    if role.night_action:
        embed.add_field(name="🌙 فعل ليلي", value="نعم", inline=True)
    embed.set_footer(text=f"مافيا 42 v{BOT_VERSION}")
    return embed



# ============================================================================
# منطق توزيع الأدوار (المطابق لجدول Mafia42 الرسمي)
# ============================================================================
def distribute_roles(player_count: int) -> list[str]:
    """
    يولّد قائمة أسماء أدوار لـ N لاعب حسب الجدول الرسمي.
    النتيجة: قائمة بأسماء الأدوار بالضبط بطول N.
    """
    if player_count not in DISTRIBUTION_TABLE:
        raise ValueError(f"عدد اللاعبين يجب أن يكون بين {MIN_PLAYERS} و {MAX_PLAYERS}")
    table = DISTRIBUTION_TABLE[player_count]
    roles: list[str] = []
    # 1. Mafia دائماً
    mafia_count = table["mafia"]
    if mafia_count == 1:
        roles.append("رئيس_المافيا")
    else:
        roles.extend(["رئيس_المافيا", "المُنفّذ"])
        # إضافة محتالين حتى نصل للعدد
        remaining = mafia_count - len(roles)
        if remaining > 0:
            roles.extend(["المحتال"] * remaining)
    # 2. Cult Leader + Fanatic
    if table["cult_leader"] > 0:
        roles.append(CULT_LEADER_ROLE)
    if table["fanatic"] > 0:
        roles.append(FANATIC_ROLE)
    # 3. Helper
    helper_count = table["helper"]
    if helper_count > 0:
        # في 8 لاعب: الجاسوسة، في 9-12: الجاسوسة
        roles.append("الجاسوسة")
        helper_count -= 1
        if helper_count > 0:
            # أضف أدوار مساعدة أخرى
            extra_helpers = ["المضيفة", "اللص", "المحتال_الانتهازي", "الرجل_الوحش"]
            roles.extend(extra_helpers[:helper_count])
    # 4. ضمان الشرطي والطبيب
    roles.append("الشرطي")
    roles.append("الطبيب")
    # 5. Special = الباقي حتى نصل لـ N
    while len(roles) < player_count:
        # نولّد من Citizen pool
        candidate = random.choice(CITIZEN_POOL)
        if candidate not in roles:
            roles.append(candidate)
        else:
            for c in CITIZEN_POOL:
                if c not in roles:
                    roles.append(c)
                    break
    # 6. Lover: نُضيف فقط في 10+ لاعبين (لعبة نادرة)
    # (تُدار منفصلة - أضف عاشقين بدورَين عاديين)
    # ملاحظة: العاشق يحلّ محل دورَين عاديين، لذا نُضيف لاعبَين
    if player_count >= 10 and not _is_lover_in_roles(roles):
        # ابحث عن دورَين عاديين لاستبدالهما
        citizen_indices = [i for i, r in enumerate(roles) if ROLES[r].team == "citizens" and r not in ("الشرطي", "الطبيب")]
        if len(citizen_indices) >= 2:
            for idx in citizen_indices[-2:]:
                roles[idx] = LOVER_ROLE  # كلا اللاعبين عاشقين (مقترنان)
    # 7. قصّ لـ player_count
    roles = roles[:player_count]
    # 8. تأكد من العدد
    while len(roles) < player_count:
        roles.append(random.choice(CITIZEN_POOL))
    random.shuffle(roles)
    return roles


def _is_lover_in_roles(roles: list[str]) -> bool:
    return LOVER_ROLE in roles


# ============================================================================
# بناء الحالة حسب الأدوار الموزعة
# ============================================================================
def assign_roles_to_players(game: GameState, role_names: list[str]) -> None:
    """تخصيص الأدوار للاعبين + تجنيد العاشقين + تجنيد الطائفة إن لزم."""
    for i, player in enumerate(game.players):
        player.role_name = role_names[i]
    # هل اللعبة بدأت بمافيا؟
    game.had_mafia = any(ROLES[n].team == "mafia" for n in role_names)
    # العاشق: لاعبان عشوائيان
    lover_indices = [i for i, n in enumerate(role_names) if n == LOVER_ROLE]
    if len(lover_indices) == 2:
        i, j = lover_indices
        game.players[i].lover_with = game.players[j].user_id
        game.players[j].lover_with = game.players[i].user_id
        game.lovers_ids = (game.players[i].user_id, game.players[j].user_id)
        game.is_lovers = True
    # المافيا يجتمعون تلقائياً
    for p in game.players:
        if ROLES[p.role_name].team in ("mafia", "helper"):
            p.met_mafia = True
            p.joined_mafia = True
    # العاشق: مرئي للنبي - لا، العاشق يبقى عشوائياً
    # المحتال_الانتهازي: يختار مواطناً عشوائياً
    citizen_players = [p for p in game.players if ROLES[p.role_name].team == "citizens"]
    for p in game.players:
        if p.role_name == "المحتال_الانتهازي" and citizen_players:
            target = random.choice(citizen_players)
            p.swindled_role = target.role_name


# ============================================================================
# التحقق من شروط الفوز (مطابق لـ Mafia42)
# ============================================================================
def check_winner(game: GameState) -> Optional[str]:
    """
    يُعيد اسم الفريق الفائز أو None.
    القواعد (Mafia42):
    - المافيا تفوز إذا:
        * في 4-8 لاعبين: أصوات المافيا >= أصوات المواطنين (والمافيا > 0).
        * في 9-12 لاعب: أصوات المافيا >= أصوات المواطنين + الطائفة.
        * أصوات Helpers لا تُحسب قبل أن يتكلموا مع المافيا (met_mafia=True).
        * القاتل (المُنفّذ) يُحسب فقط بعد joined_mafia=True.
        * لا تُحسب أصوات المافيا قبل أول ليلة (تحتاج على الأقل ليلة واحدة).
    - المواطنون يفوزون إذا:
        * كل المافيا ماتوا (mafia_count=0) ولا يوجد فائز آخر.
    - الطائفة تفوز إذا:
        * عدد الطائفة >= عدد المواطنين (Cult + Cult_Leader > Citizens).
    - العاشقون يفوزون إذا:
        * بقيا آخر اثنين على قيد الحياة.
    - إذا النبي حي، المافيا لا تفوز إلا بموت النبي.
    """
    alive = [p for p in game.players if p.alive]
    if not alive:
        return None
    # 1. العاشق: إذا بقيا آخر اثنين
    lovers = [p for p in alive if p.role_name == LOVER_ROLE]
    if game.is_lovers and len(alive) == 2 and len(lovers) == 2:
        return "lovers"
    # 2. المافيا: لا تُحسب قبل أول ليلة
    if game.day == 0:
        return None
    # 3. حساب الأصوات
    mafia_votes = 0
    for p in alive:
        team = ROLES[p.role_name].team
        if team == "mafia":
            if not p.joined_mafia:
                continue
            if p.role_name == "رئيس_المافيا":
                mafia_votes += 2
            else:
                mafia_votes += 1
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
    # 4. شرط النبي: إذا النبي حي، المافيا لا تفوز
    prophet_alive = any(p.alive and p.role_name == "النبي" for p in game.players)
    # 5. ترتيب الأولويات: cult > mafia > citizens
    # 5a. الطائفة تفوز إذا cult >= citizens و cult > 0
    if cult_votes > 0 and cult_votes >= citizen_votes:
        return "cult"
    # 5b. المافيا تفوز إذا mafia >= citizens + cult و النبي ميت
    if mafia_votes > 0 and mafia_votes >= citizen_votes + cult_votes and not prophet_alive:
        return "mafia"
    # 5c. المواطنون يفوزون إذا المافيا ماتت كلها (وكانت اللعبة بدأت بمافيا)
    if mafia_votes == 0 and game.had_mafia:
        return "citizens"
    return None


# ============================================================================
# أوامر البوت الأساسية
# ============================================================================
@bot.event
async def on_ready():
    log.info("✅ بوت مافيا 42 جاهز (متصل كـ %s)", bot.user)
    log.info("📊 %d دور، %d إنجاز", len(ROLES), len(ACHIEVEMENTS))
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name="مافيا 42 | &مساعدة"))


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send("❌ هذا الأمر للمشرفين فقط.")
    if isinstance(error, commands.CheckFailure):
        return await ctx.send("❌ لا تملك الإذن لتنفيذ هذا الأمر.")
    log.error("خطأ في الأمر: %s", error)
    await ctx.send(f"❌ حدث خطأ: {error}")


def is_allowed_channel(ctx: commands.Context) -> bool:
    if ctx.guild is None:
        return True
    allowed = _load_allowed()
    guild_id = str(ctx.guild.id)
    channels = allowed.get(guild_id, [])
    if not channels:
        return True  # مسموح في كل القنوات افتراضياً
    return ctx.channel.id in channels


@bot.command(name="مافيا", aliases=["mafiastart", "m", "ابدأ"])
async def cmd_start(ctx: commands.Context, mode: str = ""):
    """بدء لعبة مافيا جديدة."""
    if not is_allowed_channel(ctx):
        return await ctx.send("❌ هذه القناة غير مفعّلة للعب.")
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id in games and games[guild_id].started and games[guild_id].phase != "ended":
        return await ctx.send("⚠️ توجد لعبة جارية بالفعل. أنهِها أولاً بـ `&إنهاء`.")
    game = GameState(guild_id=guild_id, channel_id=ctx.channel.id, host_id=ctx.author.id)
    game.is_fast = mode in ("سريع", "fast", "سريعة")
    games[guild_id] = game
    embed = discord.Embed(
        title="🕵️ لعبة مافيا جديدة",
        description=(
            f"**المضيف:** {ctx.author.mention}\n"
            f"**الوضع:** {'⚡ سريع' if game.is_fast else '🐢 عادي'}\n"
            f"**الحد الأدنى:** {MIN_PLAYERS} لاعب\n"
            f"**الحد الأقصى:** {MAX_PLAYERS} لاعب\n\n"
            f"📥 انضم بـ: `&انضم` أو `&دخول`\n"
            f"▶️ ابدأ بـ: `&ابدأ_اللعبة`\n"
            f"❌ اخرج بـ: `&خروج`\n"
            f"⛔ أنهِ بـ: `&إنهاء`"
        ),
        color=discord.Color.dark_red(),
    )
    embed.set_footer(text=f"مافيا 42 v{BOT_VERSION}")
    await ctx.send(embed=embed)


@bot.command(name="انضم", aliases=["دخول", "join"])
async def cmd_join(ctx: commands.Context):
    """الانضمام للعبة الجارية."""
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return await ctx.send("❌ لا توجد لعبة حالياً. ابدأ واحدة بـ `&مافيا`.")
    game = games[guild_id]
    if game.started:
        return await ctx.send("⚠️ اللعبة بدأت بالفعل.")
    if len(game.players) >= MAX_PLAYERS:
        return await ctx.send(f"❌ وصلت للحد الأقصى ({MAX_PLAYERS}).")
    if any(p.user_id == ctx.author.id for p in game.players):
        return await ctx.send("⚠️ أنت منضم بالفعل.")
    p = PlayerState(user_id=ctx.author.id, display_name=ctx.author.display_name)
    game.players.append(p)
    await ctx.send(f"✅ انضم {ctx.author.mention}. المجموع: **{len(game.players)}/{MAX_PLAYERS}**")


@bot.command(name="خروج", aliases=["leave"])
async def cmd_leave(ctx: commands.Context):
    """الخروج من اللعبة قبل بدئها."""
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return
    game = games[guild_id]
    if game.started:
        return await ctx.send("❌ لا يمكن الخروج بعد بدء اللعبة.")
    for i, p in enumerate(game.players):
        if p.user_id == ctx.author.id:
            game.players.pop(i)
            await ctx.send(f"✅ غادر {ctx.author.mention}. المجموع: **{len(game.players)}**")
            return
    await ctx.send("⚠️ أنت غير منضم.")


@bot.command(name="إنهاء", aliases=["end", "انهاء"])
async def cmd_end(ctx: commands.Context):
    """إنهاء اللعبة الحالية."""
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return await ctx.send("❌ لا توجد لعبة.")
    game = games[guild_id]
    if ctx.author.id != game.host_id and not ctx.author.guild_permissions.administrator:
        return await ctx.send("❌ فقط المضيف أو مشرف يمكنه الإنهاء.")
    # تنظيف المهام
    if game.day_task and not game.day_task.done():
        game.day_task.cancel()
    if game.night_task and not game.night_task.done():
        game.night_task.cancel()
    if game.day_continue_event:
        game.day_continue_event.set()
    if game.night_continue_event:
        game.night_continue_event.set()
    if game.next_phase_event:
        game.next_phase_event.set()
    games.pop(guild_id, None)
    await ctx.send("⛔ تم إنهاء اللعبة.")


@bot.command(name="حالة", aliases=["status"])
async def cmd_status(ctx: commands.Context):
    """عرض حالة اللعبة."""
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return await ctx.send("❌ لا توجد لعبة.")
    game = games[guild_id]
    phase_ar = {"lobby": "الانضمام", "day": "النهار", "vote": "التصويت", "night": "الليل", "ended": "انتهت"}[game.phase]
    embed = discord.Embed(title="📊 حالة اللعبة", color=discord.Color.blue())
    embed.add_field(name="المرحلة", value=phase_ar, inline=True)
    embed.add_field(name="اليوم", value=str(game.day), inline=True)
    embed.add_field(name="اللاعبون", value=f"{len(game.players)}/{MAX_PLAYERS}", inline=True)
    if game.started:
        alive = [p for p in game.players if p.alive]
        embed.add_field(name="الأحياء", value=f"{len(alive)}/{len(game.players)}", inline=True)
    await ctx.send(embed=embed)



# ============================================================================
# بداية اللعبة
# ============================================================================
@bot.command(name="ابدأ_اللعبة", aliases=["ابدأ_لعبة", "start", "go"])
async def cmd_start_game(ctx: commands.Context):
    """بدء اللعبة فعلياً بعد التوزيع."""
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return await ctx.send("❌ لا توجد لعبة.")
    game = games[guild_id]
    if game.started:
        return await ctx.send("❌ اللعبة بدأت بالفعل.")
    if len(game.players) < MIN_PLAYERS:
        return await ctx.send(f"❌ تحتاج {MIN_PLAYERS} لاعبين على الأقل.")
    if ctx.author.id != game.host_id and not ctx.author.guild_permissions.administrator:
        return await ctx.send("❌ فقط المضيف يمكنه البدء.")
    game.started = True
    # 1. توزيع الأدوار
    role_names = distribute_roles(len(game.players))
    assign_roles_to_players(game, role_names)
    # 2. رسائل خاصة لكل لاعب
    for player in game.players:
        try:
            role = ROLES[player.role_name]
            user = bot.get_user(player.user_id)
            if user:
                embed = discord.Embed(title="🎭 دورك", color=discord.Color.gold())
                embed.add_field(name=f"{role.emoji} {role.name}", value=role.description, inline=False)
                embed.add_field(name="الفريق", value=role.team, inline=True)
                embed.set_footer(text=f"مافيا 42 v{BOT_VERSION}")
                await user.send(embed=embed)
        except discord.Forbidden:
            pass
    # 3. رسالة المافيا الجماعية
    mafia_players = [p for p in game.players if ROLES[p.role_name].team in ("mafia", "helper") and p.met_mafia]
    if mafia_players:
        mafia_text = "🔪 **أعضاء المافيا:**\n"
        for p in mafia_players:
            role = ROLES[p.role_name]
            mafia_text += f"{role.emoji} {p.display_name} - {p.role_name}\n"
        for p in mafia_players:
            try:
                user = bot.get_user(p.user_id)
                if user:
                    members = "\n".join(f"{ROLES[mp.role_name].emoji} {mp.display_name} ({mp.role_name})" for mp in mafia_players if mp.user_id != p.user_id)
                    embed = discord.Embed(title="🔪 فريق المافيا", description=mafia_text + (f"\n**زملاؤك:**\n{members}" if members else ""), color=discord.Color.dark_red())
                    await user.send(embed=embed)
            except discord.Forbidden:
                pass
    # 4. رسالة الطائفة
    cult_players = [p for p in game.players if ROLES[p.role_name].team == "cult" or p.cult_team]
    if len(cult_players) > 1:
        for p in cult_players:
            try:
                user = bot.get_user(p.user_id)
                if user:
                    members = "\n".join(f"{ROLES[mp.role_name].emoji} {mp.display_name}" for mp in cult_players if mp.user_id != p.user_id)
                    embed = discord.Embed(title="⛧ الطائفة", description=f"أنت عضو في الطائفة.\n**زملاؤك:**\n{members}" if members else "أنت الزعيم.", color=discord.Color.purple())
                    await user.send(embed=embed)
            except discord.Forbidden:
                pass
    # 5. رسالة العاشقين
    if game.is_lovers and game.lovers_ids != (0, 0):
        i1, i2 = game.lovers_ids
        for uid in (i1, i2):
            other = i1 if uid == i2 else i2
            try:
                user = bot.get_user(uid)
                if user:
                    embed = discord.Embed(title="💕 عاشقان", description=f"عاشقك هو <@{other}>. إذا متّ، يموت معك. إذا بقيتما آخر اثنين، تفوزان.", color=discord.Color.magenta())
                    await user.send(embed=embed)
            except discord.Forbidden:
                pass
    # 6. بدء النهار الأول
    game.phase = "day"
    game.day = 0
    embed = discord.Embed(title="🌅 بدأ النهار 0", description=f"اللاعبون: {len(game.players)}\nسترسل لكم أدواركم في الخاص.", color=discord.Color.gold())
    await ctx.send(embed=embed)
    # 7. تشغيل حلقة اللعبة
    bot.loop.create_task(run_game_loop(game, ctx))


# ============================================================================
# حلقة اللعبة الرئيسية
# ============================================================================
async def run_game_loop(game: GameState, ctx: commands.Context) -> None:
    try:
        while True:
            # نهار
            game.phase = "day"
            game.day += 1
            game.day_continue_event = asyncio.Event()
            game.next_phase_event = asyncio.Event()
            await run_day(game, ctx)
            # تحقق من فوز
            winner = check_winner(game)
            if winner:
                await end_game(game, ctx, winner)
                return
            # ليل
            game.phase = "night"
            await run_night(game, ctx)
            # تحقق من فوز
            winner = check_winner(game)
            if winner:
                await end_game(game, ctx, winner)
                return
    except asyncio.CancelledError:
        log.info("تم إلغاء اللعبة")
    except Exception as e:
        log.error("خطأ في حلقة اللعبة: %s", e)
        traceback.print_exc()


async def run_day(game: GameState, ctx: commands.Context) -> None:
    """تشغيل مرحلة النهار."""
    channel = bot.get_channel(game.channel_id)
    if not channel:
        return
    alive = [p for p in game.players if p.alive]
    embed = discord.Embed(title=f"☀️ النهار {game.day}", description=f"🧑 الأحياء: {len(alive)}/{len(game.players)}\n\nناقشوا الأدوار ثم صوّتوا على المشبوه.\nاستخدموا: `&تصويت @لاعب`", color=discord.Color.gold())
    if game.day > 0 and game.dead_players:
        dead_text = "\n".join(f"💀 {p.display_name} - {ROLES[p.role_name].name}" for p in game.dead_players[-5:])
        embed.add_field(name="ماتوا الليلة", value=dead_text, inline=False)
    await channel.send(embed=embed)
    game.day_votes.clear()
    game.day_voters.clear()
    duration = 30 if game.is_fast else DAY_DURATION_DEFAULT
    try:
        await asyncio.wait_for(game.next_phase_event.wait(), timeout=duration)
    except asyncio.TimeoutError:
        pass
    # معالجة التصويت
    await process_day_votes(game, ctx)


async def process_day_votes(game: GameState, ctx: commands.Context) -> None:
    channel = bot.get_channel(game.channel_id)
    if not game.day_votes:
        if channel:
            await channel.send("⚖️ لم يصوّت أحد. لم يمت أحد.")
        return
    # عدّ الأصوات (مع العاشق المضاعف)
    counts: dict[int, int] = {}
    for voter_id, target_id in game.day_votes.items():
        # العاشق المضاعف: إذا صوّت كلا العاشقين على نفس الهدف
        weight = 1
        if game.is_lovers and game.lovers_ids != (0, 0):
            i1, i2 = game.lovers_ids
            if (voter_id == i1 and game.day_votes.get(i2) == target_id) or (voter_id == i2 and game.day_votes.get(i1) == target_id):
                weight = 2
        counts[target_id] = counts.get(target_id, 0) + weight
    # الأكثر أصوات
    max_votes = max(counts.values())
    targets = [tid for tid, c in counts.items() if c == max_votes]
    if len(targets) > 1:
        if channel:
            await channel.send(f"⚖️ تعادل بين {len(targets)} لاعبين. لم يمت أحد.")
        return
    target_id = targets[0]
    target = next((p for p in game.players if p.user_id == target_id), None)
    if not target or not target.alive:
        return
    # القتل
    target.alive = False
    game.dead_players.append(target)
    if channel:
        role = ROLES[target.role_name]
        embed = discord.Embed(title="⚰️ نتيجة التصويت", description=f"صوّت الأغلبية على <@{target_id}>\n💀 مات: **{target.display_name}**\n🎭 كان: {role.emoji} {role.name}", color=discord.Color.dark_grey())
        await channel.send(embed=embed)
    # العاشق يموت مع شريكه
    if target.lover_with:
        lover = next((p for p in game.players if p.user_id == target.lover_with), None)
        if lover and lover.alive:
            lover.alive = False
            game.dead_players.append(lover)
            if channel:
                await channel.send(f"💔 <@{lover.user_id}> مات حزناً على حبيبه.")


@bot.command(name="تصويت", aliases=["vote", "صوّت"])
async def cmd_vote(ctx: commands.Context, target: discord.Member = None):
    """التصويت على لاعب في النهار."""
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return
    game = games[guild_id]
    if game.phase != "day":
        return await ctx.send("❌ ليس في مرحلة النهار.")
    player = next((p for p in game.players if p.user_id == ctx.author.id), None)
    if not player or not player.alive:
        return await ctx.send("❌ أنت ميت.")
    if not target:
        return await ctx.send("استخدم: `&تصويت @لاعب`")
    target_player = next((p for p in game.players if p.user_id == target.id), None)
    if not target_player or not target_player.alive:
        return await ctx.send("❌ اللاعب غير موجود أو ميت.")
    game.day_votes[ctx.author.id] = target.id
    game.day_voters.append(ctx.author.id)
    # محرّض: المصوّت يتبع اختيار المحرّض
    if game.inciter_choice and game.inciter_choice.get("voter_id") == ctx.author.id:
        forced = game.inciter_choice.get("target_id")
        if forced and forced != target.id:
            await ctx.send(f"⚠️ تم تغيير تصويتك إلى <@{forced}> بسبب المحرّض.")
            game.day_votes[ctx.author.id] = forced
    await ctx.send(f"🗳️ صوّت {ctx.author.mention} على {target.mention}")


@bot.command(name="انتهاء_النهار", aliases=["endday", "تصويت_نهائي"])
async def cmd_end_day(ctx: commands.Context):
    """إنهاء مرحلة النهار قبل انتهاء الوقت."""
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return
    game = games[guild_id]
    if game.phase != "day":
        return
    if game.next_phase_event:
        game.next_phase_event.set()
    await ctx.send("⏩ تم إنهاء النهار.")


# ============================================================================
# مرحلة الليل
# ============================================================================
async def run_night(game: GameState, ctx: commands.Context) -> None:
    channel = bot.get_channel(game.channel_id)
    if not channel:
        return
    embed = discord.Embed(title=f"🌙 الليل {game.day}", description="🛌 الجميع نائمون. ستُرسل رسائل خاصة لمن لديه فعل ليلي.", color=discord.Color.dark_purple())
    await channel.send(embed=embed)
    # إعادة ضبط الحالات
    for p in game.players:
        p.protected_today = False
        p.night_action_target = None
    game.night_kill_votes.clear()
    game.night_actions.clear()
    game.doctor_protect = None
    # دعوة كل لاعب لديه فعل ليلي
    alive = [p for p in game.players if p.alive]
    for player in alive:
        role = ROLES[player.role_name]
        if not role.night_action:
            continue
        try:
            user = bot.get_user(player.user_id)
            if not user:
                continue
            embed = await build_night_embed(game, player, alive)
            view = build_night_view(game, player, alive)
            if view:
                await user.send(embed=embed, view=view)
            else:
                await user.send(embed=embed)
        except discord.Forbidden:
            pass
    # انتظار 45 ثانية للقرارات
    await asyncio.sleep(45 if not game.is_fast else 25)
    # حلّ النتائج
    await resolve_night(game, ctx)


async def build_night_embed(game: GameState, player: PlayerState, alive: list[PlayerState]) -> discord.Embed:
    role = ROLES[player.role_name]
    embed = discord.Embed(title=f"🌙 {role.emoji} {role.name} - الليل {game.day}", description="اختر هدفك أدناه:", color=discord.Color.dark_purple())
    targets_text = "\n".join(f"`{i+1}`. {p.display_name}" for i, p in enumerate(alive) if p.user_id != player.user_id)
    embed.add_field(name="الأهداف المتاحة", value=targets_text or "لا أحد", inline=False)
    return embed


def build_night_view(game: GameState, player: PlayerState, alive: list[PlayerState]) -> Optional[discord.ui.View]:
    """بناء واجهة الأزرار للاختيار الليلي."""
    role = ROLES[player.role_name]
    # بعض الأدوار لا تحتاج اختياراً (مثل الرجل الوحش)
    if role.name in ("الرجل_الوحش", "الجندي", "السياسي", "الزعيم", "الكاهن", "القاضي", "النبي", "الروحي", "المراسل", "المسؤول", "الغول", "الشهد", "المرتزق", "المضيفة", "اللص", "المحتال_الانتهازي"):
        return None
    view = discord.ui.View(timeout=60)
    for p in alive:
        if p.user_id == player.user_id:
            continue
        # تخطي المحمي
        if p.protected_today and role.name in ("المُنفّذ",):
            continue
        btn = discord.ui.Button(label=p.display_name[:20], style=discord.ButtonStyle.danger, custom_id=f"night_{player.user_id}_{p.user_id}")
        btn.callback = lambda i, pid=p.user_id: night_action_callback(i, game, player, pid)
        view.add_item(btn)
    # زر تخطي
    skip = discord.ui.Button(label="⏭ تخطي", style=discord.ButtonStyle.secondary, custom_id=f"skip_{player.user_id}")
    skip.callback = lambda i: night_action_callback(i, game, player, None)
    view.add_item(skip)
    return view


async def night_action_callback(interaction: discord.Interaction, game: GameState, player: PlayerState, target_id: Optional[int]) -> None:
    if interaction.user.id != player.user_id:
        return await interaction.response.send_message("❌ ليس دورك.", ephemeral=True)
    role = ROLES[player.role_name]
    # تسجيل الفعل
    game.night_actions.setdefault(role.name, {})[player.user_id] = target_id
    player.night_action_target = target_id
    # الطبيب: حماية
    if role.name == "الطبيب":
        game.doctor_protect = target_id
        if target_id:
            t = next((p for p in game.players if p.user_id == target_id), None)
            if t:
                t.protected_today = True
        await interaction.response.send_message(f"✅ تم اختيار الهدف: {'<@' + str(target_id) + '>' if target_id else 'تخطي'}", ephemeral=True)
        return
    # المافيا: تصويت القتل
    if role.team in ("mafia", "helper"):
        if target_id:
            game.night_kill_votes[player.user_id] = target_id
        await interaction.response.send_message(f"✅ تم اختيار الضحية: {'<@' + str(target_id) + '>' if target_id else 'تخطي'}", ephemeral=True)
        return
    # الممرضة: مثل الطبيب
    if role.name == "الممرضة":
        game.doctor_protect = target_id
        if target_id:
            t = next((p for p in game.players if p.user_id == target_id), None)
            if t:
                t.protected_today = True
        await interaction.response.send_message(f"✅ تم اختيار الهدف: {'<@' + str(target_id) + '>' if target_id else 'تخطي'}", ephemeral=True)
        return
    # زعيم الطائفة: تجنيد
    if role.name == CULT_LEADER_ROLE:
        if target_id:
            player.cult_target = target_id
        await interaction.response.send_message(f"✅ تم اختيار الهدف للتجنيد: {'<@' + str(target_id) + '>' if target_id else 'تخطي'}", ephemeral=True)
        return
    # المتعصب: بحث عن زعيم الطائفة
    if role.name == FANATIC_ROLE:
        cult_leader = next((p for p in game.players if p.alive and p.role_name == CULT_LEADER_ROLE), None)
        if cult_leader and target_id == cult_leader.user_id:
            player.cult_team = True
            player.cult_known_cult.add(cult_leader.user_id)
            cult_leader.cult_known_cult.add(player.user_id)
        await interaction.response.send_message(f"✅ بحثك اكتمل: {'<@' + str(target_id) + '>' if target_id else 'لم تجد'}", ephemeral=True)
        return
    # رجل الشرطة
    if role.name == "الشرطي":
        if target_id:
            target = next((p for p in game.players if p.user_id == target_id), None)
            if target:
                team = ROLES[target.role_name].team
                result = "🔴 مافيا" if team in ("mafia", "helper") else "🟢 مواطن"
                if team == "cult":
                    result = "🟣 طائفة"
                player.investigated[f"night_{game.day}"] = f"{target.display_name}: {result}"
                try:
                    user = bot.get_user(player.user_id)
                    if user:
                        embed = discord.Embed(title="🔍 نتيجة التحقيق", description=f"{target.display_name}: {result}", color=discord.Color.blue())
                        await user.send(embed=embed)
                except discord.Forbidden:
                    pass
        await interaction.response.send_message(f"✅ تم التحقيق", ephemeral=True)
        return
    # القنّاص: مرة واحدة
    if role.name == "القنّاص":
        if target_id and not player.sniper_used:
            player.sniper_used = True
            target = next((p for p in game.players if p.user_id == target_id), None)
            if target:
                # القنّاص لا يقتل المحتال
                if target.role_name == "المحتال":
                    await interaction.response.send_message(f"🛡️ المحتال دُرع! قناصك فشل.", ephemeral=True)
                    return
                target.night_dead = True
        await interaction.response.send_message(f"✅ تم الإطلاق", ephemeral=True)
        return
    await interaction.response.send_message("✅ تم تسجيل فعلك.", ephemeral=True)


async def resolve_night(game: GameState, ctx: commands.Context) -> None:
    """حلّ نتائج الليل."""
    channel = bot.get_channel(game.channel_id)
    if not channel:
        return
    # 1. تصويت المافيا
    kill_target_id = None
    if game.night_kill_votes:
        counts: dict[int, int] = {}
        for voter, target in game.night_kill_votes.items():
            # رئيس المافيا صوته مضاعف
            voter_p = next((p for p in game.players if p.user_id == voter), None)
            w = 2 if voter_p and voter_p.role_name == "رئيس_المافيا" else 1
            counts[target] = counts.get(target, 0) + w
        if counts:
            kill_target_id = max(counts, key=counts.get)
    # 2. القنّاص
    sniper_targets = [p.user_id for p in game.players if p.night_dead and p.alive]
    # 3. القتل
    dead_ids: set[int] = set()
    # قتل المافيا
    if kill_target_id:
        target = next((p for p in game.players if p.user_id == kill_target_id), None)
        if target and target.alive:
            # محمي؟
            if target.protected_today or target.user_id == game.doctor_protect:
                if channel:
                    await channel.send(f"🛡️ <@{target.user_id}> كان محمياً الليلة!")
            else:
                target.alive = False
                game.dead_players.append(target)
                dead_ids.add(target.user_id)
                game.last_murder_target = target.user_id
    # القنّاص
    for tid in sniper_targets:
        target = next((p for p in game.players if p.user_id == tid), None)
        if target and target.alive:
            # المحتال يمتصّ
            if target.role_name == "المحتال":
                if channel:
                    await channel.send(f"🛡️ <@{target.user_id}> المحتال دُرع! القنّاص فشل.")
                continue
            target.alive = False
            game.dead_players.append(target)
            dead_ids.add(target.user_id)
    # 4. تجنيد الطائفة
    cult_leader = next((p for p in game.players if p.alive and p.role_name == CULT_LEADER_ROLE), None)
    if cult_leader and cult_leader.cult_target and game.day % 2 == 1:
        target = next((p for p in game.players if p.user_id == cult_leader.cult_target), None)
        if target and target.alive and not target.cult_team:
            target.cult_team = True
            target.cult_known_cult.add(cult_leader.user_id)
            cult_leader.cult_known_cult.add(target.user_id)
            try:
                user = bot.get_user(target.user_id)
                if user:
                    embed = discord.Embed(title="⛧ تم تجنيدك", description="انضممت للطائفة. تعرف أعضاءها الآن.", color=discord.Color.purple())
                    await user.send(embed=embed)
            except discord.Forbidden:
                pass
    # 5. الرجل الوحش: إذا قتل مع المافيا
    # 6. المافيا تلتقي بمهاجميها
    for p in game.players:
        if p.alive and p.role_name in ("الرجل_الوحش", "المحتال_الانتهازي") and p.user_id == kill_target_id:
            p.met_mafia = True
    # 7. العاشق يموت
    for did in list(dead_ids):
        d = next((p for p in game.players if p.user_id == did), None)
        if d and d.lover_with:
            lover = next((p for p in game.players if p.user_id == d.lover_with), None)
            if lover and lover.alive:
                lover.alive = False
                game.dead_players.append(lover)
                if channel:
                    await channel.send(f"💔 <@{lover.user_id}> مات مع حبيبه.")


async def end_game(game: GameState, ctx: commands.Context, winner: str) -> None:
    """إنهاء اللعبة وإعلان الفائز."""
    channel = bot.get_channel(game.channel_id)
    game.phase = "ended"
    game.winner = winner
    name_ar = {"mafia": "🔪 المافيا", "citizens": "🟢 المواطنون", "cult": "⛧ الطائفة", "lovers": "💕 العاشقون"}.get(winner, winner)
    # تحديث الإحصائيات
    for p in game.players:
        stats = _load_stats()
        key = str(p.user_id)
        s = stats.get(key, {})
        s.setdefault("games_played", 0)
        s["games_played"] += 1
        s.setdefault("roles_played", {})
        s["roles_played"][p.role_name] = s["roles_played"].get(p.role_name, 0) + 1
        if p.alive:
            s.setdefault("times_survived", 0)
            s["times_survived"] += 1
        role_team = ROLES[p.role_name].team
        if role_team in ("mafia", "helper"):
            s.setdefault("games_as_mafia", 0)
            s.setdefault("wins_as_mafia", 0)
            s["games_as_mafia"] += 1
            if winner == "mafia":
                s["wins_as_mafia"] += 1
        else:
            s.setdefault("games_as_citizen", 0)
            s.setdefault("wins_as_citizen", 0)
            s["games_as_citizen"] += 1
            if winner == "citizens":
                s["wins_as_citizen"] += 1
        stats[key] = s
        _save_json(STATS_FILE, stats)
        # تحديث النقاط
        ranks = _load_ranks()
        if winner == "mafia" and role_team in ("mafia", "helper"):
            ranks[key] = ranks.get(key, INITIAL_POINTS) + 30
        elif winner == "citizens" and role_team == "citizens":
            ranks[key] = ranks.get(key, INITIAL_POINTS) + 30
        elif winner == "lovers" and p.role_name == LOVER_ROLE:
            ranks[key] = ranks.get(key, INITIAL_POINTS) + 50
        else:
            ranks[key] = ranks.get(key, INITIAL_POINTS) + 5
        _save_ranks(ranks)
    if channel:
        embed = discord.Embed(title="🏆 انتهت اللعبة", description=f"**الفائز: {name_ar}**", color=discord.Color.gold())
        for p in game.players:
            role = ROLES[p.role_name]
            embed.add_field(name=f"{role.emoji} {p.display_name}", value=f"{role.name} {'💀' if not p.alive else '🟢'}", inline=False)
        await channel.send(embed=embed)
    guild_id = game.guild_id
    games.pop(guild_id, None)



# ============================================================================
# أوامر معلوماتية
# ============================================================================
@bot.command(name="نقاط", aliases=["نقاطي", "points", "score"])
async def cmd_points(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    ranks = _load_ranks()
    pts = ranks.get(str(target.id), INITIAL_POINTS)
    title = get_rank_title(pts)
    embed = discord.Embed(title=f"💎 نقاط {target.display_name}", description=f"**النقاط:** {pts:,}\n**الرتبة:** {title}", color=discord.Color.gold())
    embed.set_thumbnail(url=target.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="إحصائيات", aliases=["احصائيات", "stats"])
async def cmd_stats_cmd(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    s = get_stats(target.id)
    embed = discord.Embed(title=f"📊 إحصائيات {target.display_name}", color=discord.Color.blue())
    embed.set_thumbnail(url=target.display_avatar.url)
    roles_played = s.get("roles_played", {})
    roles_text = "\n".join(f"• {ROLES.get(r, Role(r,'','','⬜',False)).emoji} {r}: {n}" for r, n in sorted(roles_played.items(), key=lambda x: -x[1])[:5]) or "لا يوجد"
    embed.add_field(name="🎮 مجموع الألعاب", value=str(s.get("games_played", 0)), inline=True)
    embed.add_field(name="✅ مافيا انتصارات", value=f"{s.get('wins_as_mafia', 0)}/{s.get('games_as_mafia', 0)}", inline=True)
    embed.add_field(name="✅ مواطن انتصارات", value=f"{s.get('wins_as_citizen', 0)}/{s.get('games_as_citizen', 0)}", inline=True)
    embed.add_field(name="🛡️ مرات البقاء", value=str(s.get("times_survived", 0)), inline=True)
    roles_top = ", ".join(f"{r} ({n})" for r, n in sorted(roles_played.items(), key=lambda x: -x[1])[:3]) or "—"
    embed.add_field(name="⭐ أكثر الأدوار", value=roles_top, inline=False)
    await ctx.send(embed=embed)


@bot.command(name="إنجازاتي", aliases=["انجازاتي", "achievements"])
async def cmd_achievements(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    ach_ids = get_player_achievements(target.id)
    if not ach_ids:
        return await ctx.send(f"**{target.display_name}** لا يملك إنجازات بعد.")
    achs = [ACHIEVEMENTS[aid] for aid in ach_ids if aid in ACHIEVEMENTS]
    lines = [f"{a.emoji} **{a.name}** — {a.description}" for a in achs]
    embed = discord.Embed(title=f"🏆 إنجازات {target.display_name} ({len(achs)}/{len(ACHIEVEMENTS)})", description="\n".join(lines[:20]), color=discord.Color.gold())
    await ctx.send(embed=embed)


@bot.command(name="تصنيف", aliases=["ترتيب", "leaderboard", "top"])
async def cmd_leaderboard(ctx: commands.Context):
    ranks = _load_ranks()
    if not ranks:
        return await ctx.send("لا يوجد ترتيب بعد.")
    sorted_ranks = sorted(ranks.items(), key=lambda x: -x[1])[:10]
    medals = ["🥇", "🥈", "🥉"] + [f"**{i}.**" for i in range(4, 11)]
    lines = []
    for i, (uid, pts) in enumerate(sorted_ranks):
        try:
            user = await bot.fetch_user(int(uid))
            name = user.display_name
        except Exception:
            name = f"User#{uid}"
        rank_title = get_rank_title(pts)
        lines.append(f"{medals[i]} {name}: **{pts:,}** | {rank_title}")
    embed = discord.Embed(title="🏆 أفضل 10 لاعبين", description="\n".join(lines), color=discord.Color.gold())
    embed.set_footer(text=f"مافيا 42 v{BOT_VERSION}")
    await ctx.send(embed=embed)


@bot.command(name="أدوار", aliases=["ادوار", "roles"])
async def cmd_roles(ctx: commands.Context):
    mafia_roles = [(n, r) for n, r in ROLES.items() if r.team == "mafia"]
    citizen_roles = [(n, r) for n, r in ROLES.items() if r.team == "citizens"]
    cult_roles = [(n, r) for n, r in ROLES.items() if r.team == "cult"]
    neutral_roles = [(n, r) for n, r in ROLES.items() if r.team in ("neutral", "helper")]
    embed = discord.Embed(title="📖 أدوار مافيا 42", description=f"**{len(ROLES)} دور رسمي**", color=discord.Color.dark_red())
    embed.add_field(name="🔴 المافيا", value="\n".join(f"{r.emoji} **{n}** {'⭐' if r.rarity=='legendary' else '🔵' if r.rarity=='rare' else ''}" for n, r in mafia_roles), inline=False)
    embed.add_field(name="🟠 مساعدو المافيا", value="\n".join(f"{r.emoji} **{n}** {'⭐' if r.rarity=='legendary' else '🔵' if r.rarity=='rare' else ''}" for n, r in neutral_roles if r.team == "helper") or "—", inline=False)
    embed.add_field(name="🟢 المواطنون", value="\n".join(f"{r.emoji} **{n}** {'⭐' if r.rarity=='legendary' else '🔵' if r.rarity=='rare' else ''}" for n, r in citizen_roles), inline=False)
    if cult_roles:
        embed.add_field(name="🟣 الطائفة", value="\n".join(f"{r.emoji} **{n}** ⭐" for n, r in cult_roles), inline=False)
    if [n for n, r in neutral_roles if r.team == "neutral"]:
        embed.add_field(name="🟡 محايد", value="\n".join(f"{r.emoji} **{n}** ⭐" for n, r in neutral_roles if r.team == "neutral"), inline=False)
    embed.set_footer(text="⚪ شائع  🔵 نادر  ⭐ أسطوري | استخدم &دور <اسم> للتفاصيل")
    await ctx.send(embed=embed)


@bot.command(name="دور", aliases=["role"])
async def cmd_role_info(ctx: commands.Context, *, role_name: str = ""):
    if not role_name:
        return await ctx.send("استخدم: `&دور <اسم الدور>`")
    found = None
    for name, role in ROLES.items():
        if role_name.strip() in name or name in role_name.strip():
            found = role
            break
    if not found:
        return await ctx.send(f"لم أجد دوراً بهذا الاسم. استخدم `&أدوار` لعرض الكل.")
    await ctx.send(embed=build_role_embed(found))


@bot.command(name="مساعدة", aliases=["help", "h", "مساعده"])
async def cmd_help(ctx: commands.Context, section: str = ""):
    if section in ("أدوار", "ادوار", "roles"):
        return await cmd_roles(ctx)
    embed = discord.Embed(
        title=f"📚 مساعدة مافيا 42 — v{BOT_VERSION}",
        description=f"بوت مافيا 42 الرسمي مع {len(ROLES)} دور و {len(ACHIEVEMENTS)} إنجاز.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="🎮 أوامر اللعبة", value=(
        "`&مافيا` — بدء لعبة\n"
        "`&مافيا سريع` — وضع سريع\n"
        "`&انضم` — الانضمام\n"
        "`&خروج` — الخروج (قبل البدء)\n"
        "`&ابدأ_اللعبة` — بدء اللعبة\n"
        "`&إنهاء` — إنهاء اللعبة\n"
        "`&حالة` — حالة اللعبة\n"
        "`&تصويت @لاعب` — التصويت\n"
        "`&انتهاء_النهار` — إنهاء النهار\n"
        "`&همس <رسالة>` — همسة للمافيا\n"
        "`&وريث @لاعب` — تعيين وريث (رئيس المافيا)"
    ), inline=False)
    embed.add_field(name="👤 أوامر اللاعب", value=(
        "`&نقاط [@لاعب]` — النقاط والرتبة\n"
        "`&إحصائيات [@لاعب]` — إحصائيات مفصلة\n"
        "`&إنجازاتي [@لاعب]` — الإنجازات\n"
        "`&تصنيف` — أفضل 10 لاعبين\n"
        "`&دوري` — دورك الحالي (في اللعبة)"
    ), inline=False)
    embed.add_field(name="📖 معلومات", value=(
        "`&أدوار` — كل الأدوار\n"
        "`&دور <اسم>` — تفاصيل دور\n"
        "`&نصيحة` — نصيحة عشوائية\n"
        "`&بوت` — معلومات البوت"
    ), inline=False)
    embed.add_field(name="🔧 أوامر المشرف", value=(
        "`&اضافه_قناة <ID>` — إضافة قناة\n"
        "`&حذف_قناة <ID>` — حذف قناة\n"
        "`&قنوات` — عرض القنوات\n"
        "`&ريست_نقاط` — إعادة ضبط\n"
        "`&اعطاء_نقاط @لاعب <كم>` — إعطاء نقاط\n"
        "`&حذف_نقاط @لاعب <كم>` — حذف نقاط\n"
        "`&اعلان <رسالة>` — إعلان\n"
        "`&باكب` — نسخة احتياطية"
    ), inline=False)
    embed.set_footer(text=f"مافيا 42 v{BOT_VERSION} | البادئة & | {MIN_PLAYERS}-{MAX_PLAYERS} لاعبين")
    await ctx.send(embed=embed)


@bot.command(name="نصيحة", aliases=["tip"])
async def cmd_tip(ctx: commands.Context):
    tips = [
        "💡 كشرطي، لا تُعلن عن دورك قبل أن تتأكد.",
        "💡 المافيا: حاول الكلام كمواطن عادي، لا تتعاطف مع المشبوهين.",
        "💡 الطبيب: غيّر أهدافك، لا تحمِ نفسك كل ليلة.",
        "💡 القنّاص: رصاصة واحدة فقط، استخدمها بحكمة.",
        "💡 المحقق: تحقيق واحد دقيق قد يكشف اللعبة.",
        "💡 العاشق: إذا متّ، يموت حبيبك.",
        "💡 زعيم الطائفة: تجنيدك يقلب الموازين.",
    ]
    await ctx.send(random.choice(tips))


@bot.command(name="بوت", aliases=["botinfo", "about"])
async def cmd_bot_info(ctx: commands.Context):
    embed = discord.Embed(title=f"🤖 مافيا 42 — v{BOT_VERSION}", description="بوت لعبة المافيا الاحترافي بأدوار Mafia42 الرسمية.", color=discord.Color.blurple())
    embed.add_field(name="🎮 الأدوار", value=f"{len(ROLES)} دور", inline=True)
    embed.add_field(name="🏆 الإنجازات", value=f"{len(ACHIEVEMENTS)} إنجاز", inline=True)
    embed.add_field(name="🏠 السيرفرات", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="🎯 الألعاب النشطة", value=str(len(games)), inline=True)
    embed.add_field(name="البادئة", value="`&`", inline=True)
    embed.add_field(name="اللاعبون", value=f"{MIN_PLAYERS}-{MAX_PLAYERS}", inline=True)
    embed.set_footer(text="مافيا 42 — النسخة الرسمية")
    await ctx.send(embed=embed)


@bot.command(name="دوري", aliases=["myrole"])
async def cmd_my_role(ctx: commands.Context):
    """كشف دور اللاعب في اللعبة الحالية (في الخاص)."""
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return await ctx.send("❌ لا توجد لعبة.")
    game = games[guild_id]
    player = next((p for p in game.players if p.user_id == ctx.author.id), None)
    if not player:
        return await ctx.send("❌ أنت لست في اللعبة.")
    role = ROLES[player.role_name]
    try:
        embed = build_role_embed(role)
        await ctx.author.send(embed=embed)
        await ctx.message.add_reaction("✅")
    except discord.Forbidden:
        await ctx.send("❌ لم أستطع مراسلتك. افتح الخاص.")


# ============================================================================
# أوامر الأدوار داخل اللعبة
# ============================================================================
@bot.command(name="همس", aliases=["whisper"])
async def cmd_whisper(ctx: commands.Context, *, message: str = ""):
    """إرسال رسالة خاصة للمافيا."""
    if not message:
        return await ctx.send("استخدم: `&همس <رسالة>`")
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return
    game = games[guild_id]
    player = next((p for p in game.players if p.user_id == ctx.author.id), None)
    if not player or not player.alive:
        return
    role = ROLES[player.role_name]
    if role.team not in ("mafia", "helper") and not player.met_mafia:
        return await ctx.send("❌ أنت لست من المافيا.")
    # إرسال لجميع المافيا
    for p in game.players:
        if ROLES[p.role_name].team in ("mafia", "helper") and p.met_mafia:
            try:
                user = bot.get_user(p.user_id)
                if user:
                    embed = discord.Embed(title="💬 همسة", description=f"من {ctx.author.mention}:\n{message}", color=discord.Color.dark_red())
                    await user.send(embed=embed)
            except discord.Forbidden:
                pass
    await ctx.message.add_reaction("✅")


@bot.command(name="وريث", aliases=["successor"])
async def cmd_successor(ctx: commands.Context, member: discord.Member = None):
    """تعيين وريث لرئيس المافيا."""
    if not member:
        return await ctx.send("استخدم: `&وريث @لاعب`")
    guild_id = ctx.guild.id if ctx.guild else ctx.author.id
    if guild_id not in games:
        return
    game = games[guild_id]
    player = next((p for p in game.players if p.user_id == ctx.author.id), None)
    if not player or player.role_name != "رئيس_المافيا":
        return await ctx.send("❌ فقط رئيس المافيا يمكنه.")
    target = next((p for p in game.players if p.user_id == member.id), None)
    if not target or not target.alive:
        return await ctx.send("❌ اللاعب غير متاح.")
    if ROLES[target.role_name].team not in ("mafia", "helper"):
        return await ctx.send("❌ يجب أن يكون من المافيا.")
    # تبديل الأدوار
    player.role_name, target.role_name = target.role_name, player.role_name
    await ctx.send(f"✅ تم نقل قيادة المافيا إلى {member.mention}")


# ============================================================================
# أوامر المشرف
# ============================================================================
@bot.command(name="اضافه_قناة", aliases=["addchannel"])
@commands.has_permissions(administrator=True)
async def cmd_add_channel(ctx: commands.Context, channel_id: int = 0):
    if not channel_id:
        return await ctx.send("استخدم: `&اضافه_قناة <ID>`")
    allowed = _load_allowed()
    guild_key = str(ctx.guild.id)
    allowed.setdefault(guild_key, [])
    if channel_id in allowed[guild_key]:
        return await ctx.send("⚠️ القناة مضافة بالفعل.")
    allowed[guild_key].append(channel_id)
    _save_json(ALLOWED_CHANNELS_FILE, allowed)
    await ctx.send(f"✅ تم إضافة القناة <#{channel_id}>.")


@bot.command(name="حذف_قناة", aliases=["removechannel"])
@commands.has_permissions(administrator=True)
async def cmd_remove_channel(ctx: commands.Context, channel_id: int = 0):
    if not channel_id:
        return await ctx.send("استخدم: `&حذف_قناة <ID>`")
    allowed = _load_allowed()
    guild_key = str(ctx.guild.id)
    if channel_id in allowed.get(guild_key, []):
        allowed[guild_key].remove(channel_id)
        _save_json(ALLOWED_CHANNELS_FILE, allowed)
        await ctx.send(f"✅ تم حذف <#{channel_id}>.")
    else:
        await ctx.send("⚠️ القناة غير مضافة.")


@bot.command(name="قنوات", aliases=["channels"])
@commands.has_permissions(administrator=True)
async def cmd_channels(ctx: commands.Context):
    allowed = _load_allowed()
    guild_key = str(ctx.guild.id)
    channels = allowed.get(guild_key, [])
    if not channels:
        return await ctx.send("كل القنوات مسموح بها (لا توجد قيود).")
    text = "\n".join(f"• <#{c}>" for c in channels)
    await ctx.send(f"📋 القنوات المسموح بها:\n{text}")


@bot.command(name="ريست_نقاط", aliases=["reset_ranks"])
@commands.has_permissions(administrator=True)
async def cmd_reset_ranks(ctx: commands.Context):
    _save_ranks({})
    _save_json(STATS_FILE, {})
    await ctx.send("✅ تم إعادة ضبط النقاط والإحصائيات.")


@bot.command(name="اعطاء_نقاط", aliases=["give_points"])
@commands.has_permissions(administrator=True)
async def cmd_give_points(ctx: commands.Context, member: discord.Member = None, amount: int = 0):
    if not member or amount == 0:
        return await ctx.send("استخدم: `&اعطاء_نقاط @لاعب <كمية>`")
    ranks = _load_ranks()
    key = str(member.id)
    ranks[key] = max(0, ranks.get(key, INITIAL_POINTS) + amount)
    _save_ranks(ranks)
    sign = "+" if amount >= 0 else ""
    await ctx.send(f"✅ {member.mention}: {sign}{amount} نقطة → **{ranks[key]:,}**")


@bot.command(name="حذف_نقاط", aliases=["remove_points"])
@commands.has_permissions(administrator=True)
async def cmd_remove_points(ctx: commands.Context, member: discord.Member = None, amount: int = 0):
    if not member or amount == 0:
        return await ctx.send("استخدم: `&حذف_نقاط @لاعب <كمية>`")
    ranks = _load_ranks()
    key = str(member.id)
    ranks[key] = max(0, ranks.get(key, INITIAL_POINTS) - amount)
    _save_ranks(ranks)
    await ctx.send(f"✅ {member.mention}: -{amount} نقطة → **{ranks[key]:,}**")


@bot.command(name="اعلان", aliases=["announce"])
@commands.has_permissions(administrator=True)
async def cmd_announce(ctx: commands.Context, *, message: str = ""):
    if not message:
        return await ctx.send("استخدم: `&اعلان <رسالة>`")
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
        return await ctx.send("لا توجد ملفات بعد.")
    await ctx.send("✅ نسخة احتياطية:", files=files)


# معالجة أخطاء الصلاحيات
for cmd in (cmd_reset_ranks, cmd_give_points, cmd_remove_points, cmd_announce, cmd_backup, cmd_add_channel, cmd_remove_channel, cmd_channels):
    @cmd.error
    async def perm_error(ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ للمشرفين فقط.")


# ============================================================================
# نقطة الدخول
# ============================================================================
def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        log.error("❌ متغير البيئة DISCORD_TOKEN غير موجود.")
        sys.exit(1)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()



