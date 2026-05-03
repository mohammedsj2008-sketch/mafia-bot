"""Mafia 42 — بوت ديسكورد متكامل النسخة 3.0

نظام مستوحى من لعبة Mafia42 الأصلية مع تطوير شامل.
يشمل: 30+ دور، نظام إنجازات، إحصائيات، مراقبون، وضع سريع، بطولات، ومزيداً.
كل تفاعلات الأدوار تتم عبر رسائل مخفية (ephemeral) داخل قناة اللعبة.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time
import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Set, Tuple, Any

import discord
from discord.ext import commands


# ============================================================================
# الإعدادات الأساسية
# ============================================================================

BOT_VERSION = "3.0.0"

NIGHT_SECONDS = 35
DISCUSSION_SECONDS = 90
VOTE_SECONDS = 25
CONFIRM_SECONDS = 25
MIN_PLAYERS = 4
MAX_PLAYERS = 20

# الأوضاع المتاحة
MODE_NORMAL = "normal"
MODE_FAST = "fast"
MODE_RANKED = "ranked"
MODE_CUSTOM = "custom"

# إعدادات الوضع السريع
FAST_NIGHT_SECONDS = 20
FAST_DISCUSSION_SECONDS = 45
FAST_VOTE_SECONDS = 15
FAST_CONFIRM_SECONDS = 15

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mafia-bot")


# ============================================================================
# تعريف الأدوار
# ============================================================================

@dataclass(frozen=True)
class Role:
    name: str
    team: str          # "mafia" | "citizens" | "neutral" | "killer"
    description: str
    emoji: str
    has_night_action: bool
    rarity: str = "common"     # common | rare | legendary
    tips: str = ""             # نصيحة للاعب
    win_condition: str = ""    # شرط الفوز المخصص


ROLES: dict[str, Role] = {

    # =========================================================
    # فريق المافيا الأساسي
    # =========================================================
    "مافيا": Role(
        "مافيا", "mafia",
        "يقتل لاعباً كل ليلة بالتنسيق مع باقي المافيا.",
        "🔪", True, "common",
        "نسّق مع زملائك في المافيا وتجنّب اختيار هدف محروس.",
        "فوز المافيا عند تساويهم مع المواطنين.",
    ),
    "رئيس المافيا": Role(
        "رئيس المافيا", "mafia",
        "يقتل كل ليلة. إذا مات، يختار وريثاً سرياً من بقية المافيا يأخذ دور 'مافيا' جديد.",
        "👑", True, "legendary",
        "اختر الوريث بحكمة — الأقدر على الاختباء.",
        "فوز المافيا عند تساويهم مع المواطنين.",
    ),
    "وحش": Role(
        "وحش", "mafia",
        "يبحث عن المافيا كل ليلة. إذا وقع اختياره على مافيا، تخترق ضربتهم حماية الطبيب والجندي. وإذا لم يبقَ مافيا أحياء، يقتل هدفه بنفسه.",
        "👹", True, "rare",
        "ابحث عن لاعب تعتقد أنه مواطن لكشف القوة.",
        "فوز المافيا.",
    ),
    "مضيفة": Role(
        "مضيفة", "mafia",
        "تختار لاعباً وتمنعه من تنفيذ دوره الليلة.",
        "💋", True, "common",
        "امنعي الشرطي أو الطبيب في الليالي الحساسة.",
        "فوز المافيا.",
    ),
    "ساحرة": Role(
        "ساحرة", "mafia",
        "تسحر لاعباً كل ليلة فتمنع قدرته (لا يعمل سحرها على المافيا).",
        "🧙‍♀️", True, "rare",
        "الفرق بينها وبين المضيفة: سحرها لا يؤثر على المافيا.",
        "فوز المافيا.",
    ),
    "محتال": Role(
        "محتال", "mafia",
        "يظهر بريئاً عند تحقيق الشرطي معه.",
        "🎭", False, "rare",
        "كن أكثر هجومية في النقاش — أنت بريء رسمياً.",
        "فوز المافيا.",
    ),
    "جاسوسة": Role(
        "جاسوسة", "mafia",
        "تكشف هوية لاعب كل ليلة، وتشاركها مع باقي المافيا.",
        "🕵️‍♀️", True, "common",
        "ابدئي بالأدوار الخطرة كالشرطي والحارسة.",
        "فوز المافيا.",
    ),
    "مزوّر": Role(
        "مزوّر", "mafia",
        "كل ليلة يختار لاعباً ويغيّر الدور الذي سيظهر للعرافة أو المراسلة عنه.",
        "🖊️", True, "legendary",
        "استخدمه ضد العرافة والشرطي لبث الفوضى.",
        "فوز المافيا.",
    ),
    "مخبر": Role(
        "مخبر", "mafia",
        "يصطنع أدلة كاذبة — يجعل نتيجة تحقيق الشرطي عكس الحقيقة لليلة واحدة.",
        "📋", True, "legendary",
        "استخدمه ضد الشرطي لإضلاله عن أحد زملائك.",
        "فوز المافيا.",
    ),
    "محرّض": Role(
        "محرّض", "mafia",
        "يختار لاعباً في النهار (سراً) فيُجبَر على التصويت ضد هدف المحرّض.",
        "📢", True, "legendary",
        "استخدمه لتوجيه الإعدام نحو لاعب بريء قوي.",
        "فوز المافيا.",
    ),
    "عنكبوت": Role(
        "عنكبوت", "mafia",
        "ينصب فخاً على لاعب: إذا استهدفه أي دور ليلاً (شرطي/طبيب...)، يكشف العنكبوت هويته.",
        "🕷️", True, "legendary",
        "ضع الفخ على نفسك أو على لاعب تعتقد أن الشرطي سيحقق معه.",
        "فوز المافيا.",
    ),

    # =========================================================
    # فريق المواطنين
    # =========================================================
    "شرطي": Role(
        "شرطي", "citizens",
        "يحقق مع لاعب ليكشف هل هو مافيا أم لا. المحتال يبدو بريئاً.",
        "🚓", True, "common",
        "لا تكشف نتائجك مبكراً — قد تُستهدف في الليل.",
        "فوز المواطنين.",
    ),
    "طبيب": Role(
        "طبيب", "citizens",
        "يحمي لاعباً من القتل (يستطيع حماية نفسه).",
        "💉", True, "common",
        "حاول عدم حماية نفسك كل ليلة — وزّع الحماية.",
        "فوز المواطنين.",
    ),
    "حارسة": Role(
        "حارسة", "citizens",
        "تحرس لاعباً — إذا هاجمته المافيا، تقتل المهاجم.",
        "🛡️", True, "rare",
        "لا تحرسي الشرطي في كل ليلة — المافيا ستتجنبه.",
        "فوز المواطنين.",
    ),
    "جندي": Role(
        "جندي", "citizens",
        "ينجو من أول هجوم عليه (مرة واحدة فقط).",
        "💂", False, "common",
        "استخدم بقاءك لتصويت قوي في النهار.",
        "فوز المواطنين.",
    ),
    "ممرضة": Role(
        "ممرضة", "citizens",
        "ترث دور الطبيب تلقائياً إذا مات.",
        "🏥", False, "common",
        "ابقَ هادئاً — ستكشف قيمتك عند موت الطبيب.",
        "فوز المواطنين.",
    ),
    "عميل سري": Role(
        "عميل سري", "citizens",
        "ابتداءً من الليلة الثانية، يكشف دور مواطن عشوائي كل ليلة.",
        "🕴️", True, "rare",
        "استخدم معلوماتك بذكاء — لا تكشفها بسرعة.",
        "فوز المواطنين.",
    ),
    "كاهن": Role(
        "كاهن", "citizens",
        "يعيد لاعباً ميتاً للحياة — مرة واحدة فقط.",
        "⛪", True, "rare",
        "أعِد لاعباً ذا دور قوي كالشرطي أو الحارسة.",
        "فوز المواطنين.",
    ),
    "عرافة": Role(
        "عرافة", "citizens",
        "تكشف دور لاعب ميت كل ليلة.",
        "🔮", True, "rare",
        "ابدئي بأكثر المشبوهين وفاةً.",
        "فوز المواطنين.",
    ),
    "مراسلة": Role(
        "مراسلة", "citizens",
        "تنشر دور لاعب علناً في الصباح — مرة واحدة فقط.",
        "📰", True, "common",
        "استخدمي القدرة في اللحظة المناسبة — ليس مبكراً جداً.",
        "فوز المواطنين.",
    ),
    "شهيد": Role(
        "شهيد", "citizens",
        "إذا أُعدم أو قُتل ليلاً، يأخذ معه أحداً (أول من صوّت ضده، أو قاتله الليلي).",
        "💀", False, "rare",
        "دعهم يصوّتون ضدك — ثم تفاجأ بالانتقام.",
        "فوز المواطنين.",
    ),
    "رجل عصابة": Role(
        "رجل عصابة", "citizens",
        "يمنع لاعباً من التصويت في النهار التالي.",
        "🚫", True, "common",
        "امنع أكثر المصوّتين ضد المواطنين.",
        "فوز المواطنين.",
    ),
    "سياسي": Role(
        "سياسي", "citizens",
        "صوته يُحسب مرتين، ولا يمكن إعدامه بالتصويت — يكشف البوت أنه شخص عادي.",
        "🎩", False, "rare",
        "استغل تأثيرك في المواقف الحاسمة.",
        "فوز المواطنين.",
    ),
    "مواطن": Role(
        "مواطن", "citizens",
        "لا قدرات خاصة. يصوت فقط في النهار.",
        "👤", False, "common",
        "الملاحظة الجيدة والتصويت الصحيح سلاحك الوحيد.",
        "فوز المواطنين.",
    ),
    "نائب الشرطي": Role(
        "نائب الشرطي", "citizens",
        "يرث دور الشرطي تلقائياً عند موته.",
        "🚔", False, "common",
        "ابقَ صامتاً حتى يموت الشرطي.",
        "فوز المواطنين.",
    ),
    "قنّاص": Role(
        "قنّاص", "citizens",
        "يمتلك رصاصة واحدة يستطيع إطلاقها ليلاً لقتل أي لاعب مباشرة (بدون حماية).",
        "🎯", True, "legendary",
        "استخدم رصاصتك على اليقين — لا تضيّعها.",
        "فوز المواطنين.",
    ),
    "مراقب": Role(
        "مراقب", "citizens",
        "يراقب لاعباً ليلاً: يكشف كم شخص استهدفه في تلك الليلة.",
        "👁️", True, "rare",
        "راقب اللاعبين المشبوهين لمعرفة إذا كانوا مستهدَفين.",
        "فوز المواطنين.",
    ),
    "محقق": Role(
        "محقق", "citizens",
        "يتحقق من لاعبَين في ليلة واحدة: يعرف إذا تفاعلا مع بعضهما (أحدهما استهدف الآخر).",
        "🔍", True, "rare",
        "ابحث عن روابط بين المشتبه بهم.",
        "فوز المواطنين.",
    ),
    "فارس": Role(
        "فارس", "citizens",
        "إذا استُهدف ليلاً، يقتل مهاجمه ويموت بدوره.",
        "⚔️", False, "legendary",
        "أنت شهيد يُقاتل — خاطر بنفسك ليحمي الآخرين.",
        "فوز المواطنين.",
    ),
    "سفير": Role(
        "سفير", "citizens",
        "يختار لاعباً يحميه من التصويت مرة واحدة طوال اللعبة.",
        "🤝", True, "legendary",
        "احمِ الأدوار القوية كالشرطي والطبيب.",
        "فوز المواطنين.",
    ),

    # =========================================================
    # محايدون
    # =========================================================
    "مجنون": Role(
        "مجنون", "neutral",
        "هدفه أن يُعدَم بالتصويت. يفوز وحده إذا نُفّذ إعدامه. يخسر إذا قُتل ليلاً.",
        "🤡", False, "legendary",
        "استفزّ اللاعبين وتصرّف بشكل مشبوه ليصوّتوا ضدك.",
        "يفوز إذا نُفّذ إعدامه بالتصويت.",
    ),
    "قاتل": Role(
        "قاتل", "killer",
        "ينتمي لفريق المافيا لكن لا يعرفهم. كل ليلة يختار شخصين (الطبيب مستثنى) ويخمّن دور كلٍ منهما: إن كان فيهم مافيا انضمّ لهم وعرَفهم، وإن كانا مواطنَين وخمّن دوريهما بدقة قتَلهما معاً.",
        "🗡️", True, "legendary",
        "لا تتسرّع في الانضمام — اجمع المعلومات أولاً.",
        "فوز المافيا.",
    ),
}

# خريطة ألوان الأدوار للـ embed
ROLE_COLORS: dict[str, discord.Color] = {
    "mafia":    discord.Color.dark_red(),
    "citizens": discord.Color.green(),
    "neutral":  discord.Color.purple(),
    "killer":   discord.Color.dark_gray(),
}


# ============================================================================
# منطق توزيع الأدوار
# ============================================================================

def distribute_roles(player_ids: list[int], custom_settings: "CustomSettings | None" = None) -> dict[int, Role]:
    """يوزّع الأدوار على اللاعبين حسب عددهم أو الإعدادات المخصصة."""
    n = len(player_ids)

    if custom_settings and custom_settings.fixed_roles:
        # وضع مخصص بأدوار محددة
        pool = [ROLES[r] for r in custom_settings.fixed_roles if r in ROLES]
        while len(pool) < n:
            pool.append(ROLES["مواطن"])
        pool = pool[:n]
        random.shuffle(pool)
        shuffled = player_ids[:]
        random.shuffle(shuffled)
        return dict(zip(shuffled, pool))

    # جدول توزيع ديناميكي
    if n <= 5:
        mafia_count, helper_count = 1, 0
    elif n <= 7:
        mafia_count, helper_count = 1, 1
    elif n <= 9:
        mafia_count, helper_count = 2, 1
    elif n == 10:
        mafia_count, helper_count = 2, 2
    elif n <= 12:
        mafia_count, helper_count = 3, 2
    elif n <= 15:
        mafia_count, helper_count = 3, 3
    else:
        mafia_count, helper_count = 4, 3

    # المافيا الأساسية
    pool: list[Role] = [ROLES["مافيا"]] * max(1, mafia_count - 1)

    # هل يُضاف رئيس المافيا؟ (في اللعبات الكبيرة)
    if n >= 10 and random.random() < 0.4:
        pool.append(ROLES["رئيس المافيا"])
    else:
        pool.append(ROLES["مافيا"])

    # مساعدو المافيا
    mafia_helpers = ["وحش", "مضيفة", "ساحرة", "محتال", "جاسوسة", "مزوّر", "مخبر", "محرّض", "عنكبوت"]
    random.shuffle(mafia_helpers)
    pool += [ROLES[name] for name in mafia_helpers[:helper_count]]

    # المواطنون الأساسيون
    pool += [ROLES["شرطي"], ROLES["طبيب"]]

    # الأدوار الخاصة للمواطنين
    citizen_specials = [
        "حارسة", "جندي", "ممرضة", "عميل سري", "كاهن",
        "عرافة", "مراسلة", "شهيد", "رجل عصابة", "سياسي",
        "نائب الشرطي", "قنّاص", "مراقب", "محقق", "فارس", "سفير",
    ]
    random.shuffle(citizen_specials)
    special_count = max(0, n - len(pool))

    # في اللعبات الكبيرة، أضف قاتلاً أو مجنوناً
    if n >= 8 and special_count > 0:
        neutrals = []
        if random.random() < 0.5:
            neutrals.append("قاتل")
        if n >= 10 and random.random() < 0.3:
            neutrals.append("مجنون")
        pool += [ROLES[r] for r in neutrals]
        special_count = max(0, n - len(pool))

    pool += [ROLES[name] for name in citizen_specials[:special_count]]

    while len(pool) < n:
        pool.append(ROLES["مواطن"])

    pool = pool[:n]
    random.shuffle(pool)

    shuffled = player_ids[:]
    random.shuffle(shuffled)
    return dict(zip(shuffled, pool))


# ============================================================================
# إعدادات اللعبة المخصصة
# ============================================================================

@dataclass
class CustomSettings:
    """إعدادات مخصصة يضبطها منشئ اللعبة."""
    night_seconds: int = NIGHT_SECONDS
    discussion_seconds: int = DISCUSSION_SECONDS
    vote_seconds: int = VOTE_SECONDS
    confirm_seconds: int = CONFIRM_SECONDS
    fixed_roles: list[str] = field(default_factory=list)  # إذا فارغة = توزيع ديناميكي
    reveal_role_on_death: bool = False   # هل يُكشف دور اللاعب عند موته؟
    mafia_knows_each_other: bool = True  # هل المافيا تعرف بعضها من البداية؟
    allow_skip: bool = True              # هل يُسمح بتخطّي الدور الليلي؟
    allow_spectators: bool = True        # هل يُسمح للمراقبين؟

    def apply_fast_mode(self):
        self.night_seconds = FAST_NIGHT_SECONDS
        self.discussion_seconds = FAST_DISCUSSION_SECONDS
        self.vote_seconds = FAST_VOTE_SECONDS
        self.confirm_seconds = FAST_CONFIRM_SECONDS


# ============================================================================
# نظام الإنجازات
# ============================================================================

@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    description: str
    emoji: str
    points: int  # نقاط إضافية عند الفوز بالإنجاز


ACHIEVEMENTS: dict[str, Achievement] = {
    # إنجازات الفوز
    "first_win": Achievement("first_win", "انتصاري الأول", "افز بلعبتك الأولى.", "🏆", 100),
    "win_streak_3": Achievement("win_streak_3", "ثلاثية مجيدة", "افز 3 مرات متتالية.", "🔥", 200),
    "win_streak_5": Achievement("win_streak_5", "خمسية أسطورية", "افز 5 مرات متتالية.", "⚡", 400),
    "win_10": Achievement("win_10", "عشرة انتصارات", "افز 10 مرات إجمالاً.", "💎", 150),
    "win_50": Achievement("win_50", "خمسون انتصاراً", "افز 50 مرة إجمالاً.", "👑", 500),

    # إنجازات الأدوار
    "mafia_winner": Achievement("mafia_winner", "رئيس العصابة", "افز مرة كدور مافيا.", "🔪", 75),
    "citizen_winner": Achievement("citizen_winner", "بطل الشعب", "افز مرة كمواطن.", "🏅", 75),
    "detective_ace": Achievement("detective_ace", "محقق بارع", "كشف الشرطي 3 مافيا في لعبة واحدة.", "🚓", 300),
    "saved_3": Achievement("saved_3", "منقذ الليل", "أنقذ الطبيب 3 لاعبين في لعبة واحدة.", "💉", 250),
    "jester_win": Achievement("jester_win", "فوز المجنون", "افز كمجنون عبر الإعدام.", "🤡", 500),
    "killer_joined": Achievement("killer_joined", "عميل مزدوج", "انضم القاتل للمافيا بنجاح.", "🗡️", 200),
    "guardian_kill": Achievement("guardian_kill", "الحارسة الانتقامية", "قتلت الحارسة مهاجماً.", "🛡️", 200),
    "martyr_revenge": Achievement("martyr_revenge", "انتقام الشهيد", "أخذ الشهيد معه شخصاً.", "💀", 200),
    "sniper_kill": Achievement("sniper_kill", "رصاصة الحسم", "أصاب القنّاص هدفه من المافيا.", "🎯", 300),
    "priest_revived": Achievement("priest_revived", "المسيح الثاني", "أعاد الكاهن لاعباً للحياة.", "⛪", 250),
    "soldier_survived": Achievement("soldier_survived", "الدرع الصامد", "صدّ الجندي هجوماً بنجاح.", "💂", 150),
    "forger_fooled": Achievement("forger_fooled", "فنان التزوير", "خدع المزوّر العرافة.", "🖊️", 200),
    "spy_master": Achievement("spy_master", "أسيادة التجسس", "كشفت الجاسوسة 3 أدوار في لعبة.", "🕵️‍♀️", 250),

    # إنجازات الألعاب
    "10_games": Achievement("10_games", "لاعب متمرّس", "العب 10 ألعاب.", "🎮", 100),
    "50_games": Achievement("50_games", "محترف اللعبة", "العب 50 لعبة.", "🎯", 300),
    "100_games": Achievement("100_games", "أسطورة المافيا", "العب 100 لعبة.", "🌟", 1000),
    "survivor": Achievement("survivor", "الناجي الأخير", "ابقَ حياً حتى نهاية اللعبة.", "🦺", 150),
    "speed_demon": Achievement("speed_demon", "الغول السريع", "افز بلعبة سريعة.", "⚡", 125),
    "night_hunter": Achievement("night_hunter", "صيّاد الليل", "مات لاعب كل ليلة في 5 ليالٍ متتالية.", "🌙", 200),
    "perfect_vote": Achievement("perfect_vote", "تصويت مثالي", "أُعدم كل لاعب صوّت ضد الكتلة الرابحة.", "🗳️", 250),
    "comeback": Achievement("comeback", "العودة البطولية", "افز وأنت الأقلية.", "💪", 350),
    "first_blood": Achievement("first_blood", "الدم الأول", "كن أول من يُقتل في لعبة.", "🩸", 50),
    "clean_sweep": Achievement("clean_sweep", "اكتساح نظيف", "افز المواطنون دون أي خسارة في الليل.", "✨", 400),
    "long_game": Achievement("long_game", "الحرب الطويلة", "العب لعبة تمتد لأكثر من 6 ليالٍ.", "🕰️", 200),
}


# ============================================================================
# نظام الإحصائيات
# ============================================================================

STATS_FILE = Path("mafia_stats.json")
RANKS_FILE = Path("mafia_ranks.json")
ACHIEVEMENTS_FILE = Path("mafia_achievements.json")
ALLOWED_CHANNELS_FILE = Path("mafia_allowed_channels.json")
HISTORY_FILE = Path("mafia_history.json")

INITIAL_POINTS = 4000


def _load_json(path: Path, default=None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path: Path, data: Any) -> None:
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("فشل حفظ %s", path)


def get_stats(user_id: int) -> dict:
    stats = _load_json(STATS_FILE)
    key = str(user_id)
    if key not in stats:
        stats[key] = {
            "games_played": 0,
            "wins": 0,
            "losses": 0,
            "kills_mafia": 0,        # عدد المرات التي قتلت فيها المافيا هدفاً باختياره
            "saves": 0,              # عدد مرات إنقاذ الطبيب
            "correct_investigations": 0,  # تحقيقات الشرطي الصحيحة
            "times_survived": 0,
            "roles_played": {},      # دور -> عدد المرات
            "win_streak": 0,
            "max_win_streak": 0,
            "games_as_mafia": 0,
            "wins_as_mafia": 0,
            "games_as_citizen": 0,
            "wins_as_citizen": 0,
            "games_as_neutral": 0,
            "wins_as_neutral": 0,
            "first_blood_count": 0,
            "night_kills": 0,
        }
        _save_json(STATS_FILE, stats)
    return stats[key]


def save_stats(user_id: int, data: dict) -> None:
    stats = _load_json(STATS_FILE)
    stats[str(user_id)] = data
    _save_json(STATS_FILE, stats)


def get_all_stats() -> dict:
    return _load_json(STATS_FILE)


# ============================================================================
# نظام النقاط والرتب
# ============================================================================

RANKS = [
    (0,     "🟤 مبتدئ",        "Beginner"),
    (1000,  "⚫ مجند",          "Recruit"),
    (2000,  "🔵 مدرّب",         "Trained"),
    (3000,  "🟢 محترف",         "Pro"),
    (4000,  "🟡 خبير",          "Expert"),
    (5500,  "🟠 نخبة",          "Elite"),
    (7000,  "🔴 ماهر",          "Master"),
    (9000,  "🟣 عميد",          "Grandmaster"),
    (12000, "⭐ أسطورة",         "Legend"),
    (16000, "🌟 أسطورة ذهبية", "Golden Legend"),
    (21000, "💎 ألماسي",        "Diamond"),
]


def get_rank_title(points: int) -> str:
    title = RANKS[0][1]
    for threshold, name, _ in RANKS:
        if points >= threshold:
            title = name
    return title


def _load_ranks() -> dict:
    return _load_json(RANKS_FILE)


def _save_ranks(ranks: dict) -> None:
    _save_json(RANKS_FILE, ranks)


def ensure_rank(user_id: int) -> int:
    ranks = _load_ranks()
    key = str(user_id)
    if key not in ranks:
        ranks[key] = INITIAL_POINTS
        _save_ranks(ranks)
    return ranks[key]


def get_rank_points(user_id: int) -> int:
    return _load_ranks().get(str(user_id), INITIAL_POINTS)


def _delta_for(player: "PlayerState", winner: str, game: "MafiaGame") -> int:
    role = player.role
    base = 0

    # مجنون — يفوز فقط إذا أُعدم
    if role.name == "مجنون":
        if player.killed_by == "vote":
            return 120
        return -20

    # القاتل ينتمي لفريق المافيا
    if role.name == "قاتل":
        return 60 if winner == "mafia" else -45

    # رئيس المافيا
    if role.name == "رئيس المافيا":
        base = 80 if winner == "mafia" else -65

    # مافيا أساسية
    elif role.name == "مافيا":
        base = 70 if winner == "mafia" else -60

    # مساعدو المافيا
    elif role.team == "mafia":
        base = 55 if winner == "mafia" else -45

    # المواطنون
    else:
        base = 45 if winner == "citizens" else -35

    # مكافأة البقاء على قيد الحياة
    if player.alive:
        base += 10

    return base


def update_ranks_after_game(game: "MafiaGame", winner: str) -> tuple[dict[int, int], dict[str, int]]:
    ranks = _load_ranks()
    deltas: dict[int, int] = {}
    for p in game.players.values():
        key = str(p.user.id)
        if key not in ranks:
            ranks[key] = INITIAL_POINTS
        delta = _delta_for(p, winner, game)
        ranks[key] = max(0, ranks[key] + delta)
        deltas[p.user.id] = delta
    _save_ranks(ranks)
    return deltas, ranks


# ============================================================================
# نظام الإنجازات
# ============================================================================

def _load_achievements() -> dict:
    return _load_json(ACHIEVEMENTS_FILE)


def _save_achievements(data: dict) -> None:
    _save_json(ACHIEVEMENTS_FILE, data)


def get_player_achievements(user_id: int) -> list[str]:
    data = _load_achievements()
    return data.get(str(user_id), [])


def grant_achievement(user_id: int, ach_id: str) -> bool:
    """يمنح إنجازاً للاعب. يعيد True إذا كان جديداً."""
    data = _load_achievements()
    key = str(user_id)
    if key not in data:
        data[key] = []
    if ach_id in data[key]:
        return False
    data[key].append(ach_id)
    _save_achievements(data)
    # أضف نقاط الإنجاز
    if ach_id in ACHIEVEMENTS:
        ach = ACHIEVEMENTS[ach_id]
        ranks = _load_ranks()
        ranks[key] = ranks.get(key, INITIAL_POINTS) + ach.points
        _save_ranks(ranks)
    return True


def check_and_grant_achievements(
    player: "PlayerState",
    game: "MafiaGame",
    winner: str,
    game_stats: dict,
) -> list[Achievement]:
    """يفحص ويمنح الإنجازات المستحقة بعد انتهاء اللعبة."""
    uid = player.user.id
    granted: list[Achievement] = []

    def try_grant(ach_id: str):
        if ach_id in ACHIEVEMENTS and grant_achievement(uid, ach_id):
            granted.append(ACHIEVEMENTS[ach_id])

    stats = get_stats(uid)

    # إنجازات الفوز
    won = (
        (player.role.team == "citizens" and winner == "citizens") or
        (player.role.team == "mafia" and winner == "mafia") or
        (player.role.name == "قاتل" and winner == "mafia") or
        (player.role.name == "مجنون" and player.killed_by == "vote")
    )

    if won:
        if stats["wins"] == 1:
            try_grant("first_win")
        if stats["wins"] >= 10:
            try_grant("win_10")
        if stats["wins"] >= 50:
            try_grant("win_50")
        if stats["win_streak"] >= 3:
            try_grant("win_streak_3")
        if stats["win_streak"] >= 5:
            try_grant("win_streak_5")

    if player.role.name == "مجنون" and player.killed_by == "vote":
        try_grant("jester_win")

    if player.role.team == "mafia" and won:
        try_grant("mafia_winner")

    if player.role.team == "citizens" and won:
        try_grant("citizen_winner")

    # إنجازات عدد الألعاب
    if stats["games_played"] >= 10:
        try_grant("10_games")
    if stats["games_played"] >= 50:
        try_grant("50_games")
    if stats["games_played"] >= 100:
        try_grant("100_games")

    # الناجي
    if player.alive:
        try_grant("survivor")

    # الكاهن
    if player.role.name == "كاهن" and game_stats.get("priest_revived", False):
        try_grant("priest_revived")

    # الجندي
    if player.role.name == "جندي" and player.soldier_shield_used:
        try_grant("soldier_survived")

    # الشهيد
    if player.role.name == "شهيد" and game_stats.get("martyr_triggered", False):
        try_grant("martyr_revenge")

    # الحارسة
    if player.role.name == "حارسة" and game_stats.get("guardian_kill", False):
        try_grant("guardian_kill")

    # القنّاص
    if player.role.name == "قنّاص" and game_stats.get("sniper_mafia_kill", False):
        try_grant("sniper_kill")

    # طول اللعبة
    if game.day_count >= 6:
        try_grant("long_game")

    # أول دم
    if game_stats.get(f"first_blood_{uid}", False):
        try_grant("first_blood")

    return granted


# ============================================================================
# قنوات اللعبة
# ============================================================================

def _load_allowed_channels() -> dict:
    return _load_json(ALLOWED_CHANNELS_FILE)


def _save_allowed_channels(data: dict) -> None:
    _save_json(ALLOWED_CHANNELS_FILE, data)


def get_allowed_channels(guild_id: int) -> list[int]:
    return _load_allowed_channels().get(str(guild_id), [])


def is_channel_allowed(guild_id: int, channel_id: int) -> bool:
    allowed = get_allowed_channels(guild_id)
    return channel_id in allowed if allowed else False


def add_allowed_channel(guild_id: int, channel_id: int) -> bool:
    data = _load_allowed_channels()
    key = str(guild_id)
    chans = data.get(key, [])
    if channel_id in chans:
        return False
    chans.append(channel_id)
    data[key] = chans
    _save_allowed_channels(data)
    return True


def remove_allowed_channel(guild_id: int, channel_id: int) -> bool:
    data = _load_allowed_channels()
    key = str(guild_id)
    chans = data.get(key, [])
    if channel_id not in chans:
        return False
    chans.remove(channel_id)
    data[key] = chans
    _save_allowed_channels(data)
    return True


# ============================================================================
# حالة اللعبة
# ============================================================================

@dataclass
class PlayerState:
    user: discord.User | discord.Member
    role: Role
    alive: bool = True
    journalist_used: bool = False
    priest_used: bool = False
    soldier_shield_used: bool = False
    knight_shield_used: bool = False
    sniper_used: bool = False
    ambassador_shield_id: int | None = None   # معرّف اللاعب المحمي بواسطة السفير
    blocked_from_voting_today: bool = False
    killed_by: str | None = None
    killed_by_player: int | None = None
    first_vote_against: int | None = None
    pending_notices: list[str] = field(default_factory=list)
    joined_mafia: bool = False
    forger_fake_role: str | None = None        # الدور المزيف الذي يظهر للعرافة
    heir_id: int | None = None                 # وريث رئيس المافيا
    is_protected_by_ambassador: bool = False   # محمي من الإعدام مرة
    spy_reveal_count: int = 0                  # عدد مرات كشف الجاسوسة
    cop_mafia_found: int = 0                   # عدد مافيا كشفها الشرطي


@dataclass
class NightActions:
    mafia_votes: dict[int, int] = field(default_factory=dict)
    beast_target: int | None = None
    hostess_block: int | None = None
    witch_block: int | None = None
    doctor_save: int | None = None
    guardian_target: int | None = None
    cop_target: tuple[int, int] | None = None
    spy_target: tuple[int, int] | None = None
    secret_agent: int | None = None
    oracle_target: tuple[int, int] | None = None
    priest_revive: int | None = None
    journalist_reveal: int | None = None
    gangster_block: int | None = None
    killer_guesses: list[int] = field(default_factory=list)
    killer_role_guesses: dict[int, str] = field(default_factory=dict)
    sniper_target: int | None = None           # هدف القنّاص
    watcher_target: int | None = None          # هدف المراقب
    detective_pair: tuple[int, int] | None = None   # زوج المحقق
    forger_target: int | None = None           # هدف المزوّر
    forger_fake_role: str | None = None        # الدور المزيف
    informant_target: int | None = None        # هدف المخبر (يعكس نتيجة شرطي)
    spider_trap: int | None = None             # فخ العنكبوت
    inciter_target: int | None = None          # هدف المحرّض (يُجبر على التصويت)
    inciter_voter: int | None = None           # من سيُجبر على التصويت
    heir_activated: bool = False               # هل فعّل رئيس المافيا الوريث
    mafia_boss_heir: int | None = None         # الوريث المختار


class MafiaGame:
    def __init__(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        mode: str = MODE_NORMAL,
        settings: CustomSettings | None = None,
    ):
        self.guild = guild
        self.channel = channel
        self.mode = mode
        self.settings: CustomSettings = settings or CustomSettings()
        if mode == MODE_FAST:
            self.settings.apply_fast_mode()

        self.players: dict[int, PlayerState] = {}
        self.lobby_user_ids: list[int] = []
        self.spectators: set[int] = set()          # مراقبون
        self.phase: str = "waiting"
        self.day_count: int = 0
        self.night_actions: NightActions = NightActions()
        self.day_votes: dict[int, int] = {}
        self.lobby_message: discord.Message | None = None
        self.phase_task: asyncio.Task | None = None
        self.original_overwrites: dict[int, discord.PermissionOverwrite | None] = {}
        self.game_stats: dict = {}   # إحصائيات الجلسة
        self.start_time: float = time.time()
        self.host_id: int = 0

    # --- إدارة الردهة ---
    def add_lobby_player(self, user_id: int) -> bool:
        if user_id in self.lobby_user_ids:
            return False
        self.lobby_user_ids.append(user_id)
        return True

    def remove_lobby_player(self, user_id: int) -> bool:
        if user_id not in self.lobby_user_ids:
            return False
        self.lobby_user_ids.remove(user_id)
        return True

    def add_spectator(self, user_id: int) -> bool:
        if user_id in self.lobby_user_ids or user_id in self.spectators:
            return False
        self.spectators.add(user_id)
        return True

    # --- استعلامات ---
    def alive_players(self) -> list[PlayerState]:
        return [p for p in self.players.values() if p.alive]

    def dead_players(self) -> list[PlayerState]:
        return [p for p in self.players.values() if not p.alive]

    def alive_mafia(self) -> list[PlayerState]:
        return [p for p in self.alive_players()
                if p.role.team == "mafia" or p.role.name == "قاتل"]

    def alive_citizens(self) -> list[PlayerState]:
        return [p for p in self.alive_players() if p.role.team == "citizens"]

    def mafia_team_members(self, *, alive_only: bool = True) -> list[PlayerState]:
        pool = self.alive_players() if alive_only else list(self.players.values())
        return [p for p in pool
                if p.role.team == "mafia" or (p.role.name == "قاتل" and p.joined_mafia)]

    def get(self, user_id: int) -> PlayerState | None:
        return self.players.get(user_id)

    def get_by_role(self, role_name: str) -> PlayerState | None:
        for p in self.players.values():
            if p.role.name == role_name:
                return p
        return None

    def alive_by_role(self, role_name: str) -> PlayerState | None:
        for p in self.alive_players():
            if p.role.name == role_name:
                return p
        return None

    def check_winner(self) -> str | None:
        mafia = len(self.alive_mafia())
        alive_c = self.alive_citizens()
        citizens = len(alive_c)

        # السياسي صوته مزدوج
        has_politician = any(p.role.name == "سياسي" for p in alive_c)
        effective_citizens = citizens + (1 if has_politician else 0)

        # إذا لا يوجد مافيا
        if mafia == 0:
            # فحص المجنون الحي (لم يُعدَم بعد فهو لم يفز)
            return "citizens"

        # إذا المافيا تساوي أو تفوق المواطنين
        if mafia >= effective_citizens and citizens > 0:
            return "mafia"

        if mafia == 0 and citizens == 0:
            return "citizens"

        return None

    def elapsed_time(self) -> str:
        elapsed = int(time.time() - self.start_time)
        m, s = divmod(elapsed, 60)
        return f"{m}د {s}ث" if m else f"{s}ث"


# ============================================================================
# إدارة الصلاحيات
# ============================================================================

async def _get_member(game: MafiaGame, uid: int) -> discord.Member | None:
    m = game.guild.get_member(uid)
    if m is None:
        try:
            m = await game.guild.fetch_member(uid)
        except discord.HTTPException:
            return None
    return m


async def snapshot_player_perms(game: MafiaGame) -> None:
    for uid in game.players:
        member = await _get_member(game, uid)
        if member is None:
            game.original_overwrites[uid] = None
            continue
        existing = game.channel.overwrites_for(member)
        game.original_overwrites[uid] = None if existing.is_empty() else existing


async def restore_player_perms(game: MafiaGame, uid: int) -> None:
    member = await _get_member(game, uid)
    if member is None:
        return
    original = game.original_overwrites.get(uid)
    try:
        await game.channel.set_permissions(member, overwrite=original, reason="انتهت اللعبة")
    except discord.HTTPException:
        pass


async def mute_player(game: MafiaGame, uid: int, reason: str = "") -> None:
    member = await _get_member(game, uid)
    if member is None:
        return
    original = game.original_overwrites.get(uid)
    new_ow = discord.PermissionOverwrite()
    if original and not original.is_empty():
        for k, v in original:
            setattr(new_ow, k, v)
    new_ow.send_messages = False
    new_ow.add_reactions = False
    new_ow.send_messages_in_threads = False
    new_ow.create_public_threads = False
    new_ow.create_private_threads = False
    try:
        await game.channel.set_permissions(member, overwrite=new_ow, reason=reason or "كتم مافيا")
    except discord.HTTPException:
        pass


async def unmute_player(game: MafiaGame, uid: int) -> None:
    await restore_player_perms(game, uid)


async def mute_all_alive(game: MafiaGame) -> None:
    for p in game.alive_players():
        await mute_player(game, p.user.id, reason="الليل — صمت")


async def unmute_all_alive(game: MafiaGame) -> None:
    for p in game.alive_players():
        await unmute_player(game, p.user.id)


async def mute_all_dead(game: MafiaGame) -> None:
    for p in game.dead_players():
        await mute_player(game, p.user.id, reason="لاعب ميت")


async def restore_all_perms(game: MafiaGame) -> None:
    for uid in list(game.original_overwrites.keys()):
        await restore_player_perms(game, uid)


# ============================================================================
# نظام الصور والأحداث
# ============================================================================

EVENT_IMAGES = {
    "mafia_kill":       "event_mafia_kill.png",
    "doctor_save":      "event_doctor_save.png",
    "execution":        "event_execution.png",
    "killer_success":   "event_killer_success.png",
    "journalist_reveal":"event_journalist_reveal.png",
    "quiet":            "event_quiet_night.png",
    "lobby":            "mafia_lobby.png",
    "win_citizens":     "event_citizens_win.png",
    "win_mafia":        "event_mafia_win.png",
    "jester_win":       "event_jester_win.png",
    "sniper_kill":      "event_sniper.png",
    "guardian_kill":    "event_guardian.png",
    "priest_revive":    "event_priest.png",
}


def _event_file(event_name: str) -> tuple[discord.File | None, str | None]:
    fname = EVENT_IMAGES.get(event_name)
    if not fname:
        return None, None
    path = Path(__file__).parent / "attached_assets" / fname
    if not path.exists():
        return None, None
    return discord.File(str(path), filename=fname), f"attachment://{fname}"


def _pick_morning_event(events: set[str]) -> str:
    priority = [
        "jester_win", "killer_success", "sniper_kill", "guardian_kill",
        "journalist_reveal", "priest_revive", "mafia_kill", "doctor_save",
    ]
    for ev in priority:
        if ev in events:
            return ev
    return "quiet"


def role_image_path(role_name: str) -> Path:
    """يعيد مسار صورة الدور إذا وُجدت."""
    safe = role_name.replace(" ", "_").replace("/", "_")
    path = Path(__file__).parent / "attached_assets" / f"role_{safe}.png"
    return path


async def send_with_optional_image(
    dest,
    embed: discord.Embed,
    event: str | None = None,
    **kwargs
) -> discord.Message:
    """يرسل embed مع صورة إذا وُجدت، أو بدونها."""
    if event:
        f, url = _event_file(event)
        if url:
            embed.set_image(url=url)
        if f:
            return await dest.send(embed=embed, file=f, **kwargs)
    return await dest.send(embed=embed, **kwargs)


# ============================================================================
# أدوات مساعدة للواجهة
# ============================================================================

def _player_options(candidates: list[PlayerState]) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=(p.user.display_name[:80] or str(p.user.id)),
            value=str(p.user.id),
            description=f"{'🟢 حي' if p.alive else '💀 ميت'}",
        )
        for p in candidates
    ]


def build_role_embed(role: Role) -> discord.Embed:
    """يبني embed احترافي لدور معين."""
    color = ROLE_COLORS.get(role.team, discord.Color.blurple())
    team_name = {
        "mafia": "🔴 فريق المافيا",
        "citizens": "🟢 فريق المواطنين",
        "neutral": "🟣 محايد",
        "killer": "⚫ قاتل سري",
    }.get(role.team, role.team)

    rarity_display = {
        "common": "⚪ شائع",
        "rare": "🔵 نادر",
        "legendary": "🟡 أسطوري",
    }.get(role.rarity, role.rarity)

    embed = discord.Embed(
        title=f"{role.emoji} {role.name}",
        description=role.description,
        color=color,
    )
    embed.add_field(name="👥 الفريق", value=team_name, inline=True)
    embed.add_field(name="⭐ الندرة", value=rarity_display, inline=True)
    if role.win_condition:
        embed.add_field(name="🏆 شرط الفوز", value=role.win_condition, inline=False)
    if role.tips:
        embed.add_field(name="💡 نصيحة", value=role.tips, inline=False)
    embed.set_footer(text="مافيا 42 — النسخة 3.0")

    # إضافة صورة الدور إذا وُجدت
    img_path = role_image_path(role.name)
    if img_path.exists():
        fname = img_path.name
        embed.set_thumbnail(url=f"attachment://{fname}")

    return embed


# ============================================================================
# واجهة الردهة المتطورة
# ============================================================================

class SettingsView(discord.ui.View):
    """واجهة تعديل إعدادات اللعبة قبل البدء."""

    def __init__(self, game: MafiaGame, host_id: int, lobby_view: "LobbyView"):
        super().__init__(timeout=300)
        self.game = game
        self.host_id = host_id
        self.lobby_view = lobby_view

    def _check_host(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.host_id

    @discord.ui.button(label="وضع عادي", style=discord.ButtonStyle.green, emoji="🎮", row=0)
    async def mode_normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_host(interaction):
            return await interaction.response.send_message("المنشئ فقط يمكنه تغيير الإعدادات.", ephemeral=True)
        self.game.mode = MODE_NORMAL
        self.game.settings = CustomSettings()
        await interaction.response.send_message("✅ تم ضبط الوضع العادي.", ephemeral=True)

    @discord.ui.button(label="وضع سريع", style=discord.ButtonStyle.blurple, emoji="⚡", row=0)
    async def mode_fast(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_host(interaction):
            return await interaction.response.send_message("المنشئ فقط يمكنه تغيير الإعدادات.", ephemeral=True)
        self.game.mode = MODE_FAST
        self.game.settings = CustomSettings()
        self.game.settings.apply_fast_mode()
        await interaction.response.send_message("✅ تم ضبط الوضع السريع (أوقات أقصر).", ephemeral=True)

    @discord.ui.button(label="كشف الأدوار عند الموت", style=discord.ButtonStyle.gray, emoji="👁️", row=1)
    async def toggle_reveal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_host(interaction):
            return await interaction.response.send_message("المنشئ فقط.", ephemeral=True)
        self.game.settings.reveal_role_on_death = not self.game.settings.reveal_role_on_death
        status = "مفعّل" if self.game.settings.reveal_role_on_death else "معطّل"
        await interaction.response.send_message(f"كشف الأدوار عند الموت: **{status}**", ephemeral=True)

    @discord.ui.button(label="إغلاق الإعدادات", style=discord.ButtonStyle.red, emoji="🔒", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_message("تم إغلاق الإعدادات.", ephemeral=True)


class LobbyView(discord.ui.View):
    def __init__(self, game: MafiaGame, host_id: int, on_start, on_cancel):
        super().__init__(timeout=900)
        self.game = game
        self.host_id = host_id
        self.on_start = on_start
        self.on_cancel = on_cancel

    def _build_embed(self) -> discord.Embed:
        ids = self.game.lobby_user_ids
        names = "\n".join(f"• <@{uid}>" for uid in ids) if ids else "_لا يوجد لاعبون بعد._"
        mode_name = {
            MODE_NORMAL: "🎮 عادي",
            MODE_FAST: "⚡ سريع",
            MODE_RANKED: "🏆 مصنّف",
            MODE_CUSTOM: "🔧 مخصص",
        }.get(self.game.mode, self.game.mode)

        embed = discord.Embed(
            title="🕵️‍♂️ غرفة انتظار مافيا 42",
            description=(
                f"👑 **المنشئ:** <@{self.host_id}>\n"
                f"🎮 **الوضع:** {mode_name}\n"
                f"👥 **الحد الأدنى:** {MIN_PLAYERS} لاعبين | الأقصى: {MAX_PLAYERS}\n\n"
                f"**اللاعبون ({len(ids)}/{MAX_PLAYERS}):**\n{names}"
            ),
            color=discord.Color.gold(),
        )
        if self.game.spectators:
            spec_names = " • ".join(f"<@{s}>" for s in self.game.spectators)
            embed.add_field(name="👁️ مراقبون", value=spec_names, inline=False)

        embed.set_image(url="attachment://mafia_lobby.png")
        embed.set_footer(text=f"مافيا 42 v{BOT_VERSION} • استخدم &مساعدة للأوامر")
        return embed

    async def _refresh(self, interaction: discord.Interaction):
        embed = self._build_embed()
        if interaction.message:
            try:
                await interaction.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="انضمام", style=discord.ButtonStyle.green, emoji="✅", row=0)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in self.game.spectators:
            self.game.spectators.discard(uid)
        if len(self.game.lobby_user_ids) >= MAX_PLAYERS:
            return await interaction.response.send_message("الردهة ممتلئة.", ephemeral=True)
        if self.game.add_lobby_player(uid):
            pts = ensure_rank(uid)
            rank = get_rank_title(pts)
            await interaction.response.send_message(
                f"انضممت! 🎉\nنقاطك: **{pts}** ({rank})", ephemeral=True
            )
            await self._refresh(interaction)
        else:
            await interaction.response.send_message("أنت موجود بالفعل.", ephemeral=True)

    @discord.ui.button(label="مراقب", style=discord.ButtonStyle.gray, emoji="👁️", row=0)
    async def spectate(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if not self.game.settings.allow_spectators:
            return await interaction.response.send_message("المراقبة غير مسموحة.", ephemeral=True)
        if uid in self.game.lobby_user_ids:
            return await interaction.response.send_message("أنت لاعب بالفعل.", ephemeral=True)
        if self.game.add_spectator(uid):
            await interaction.response.send_message("👁️ أصبحت مراقباً.", ephemeral=True)
            await self._refresh(interaction)
        else:
            await interaction.response.send_message("أنت مراقب بالفعل.", ephemeral=True)

    @discord.ui.button(label="خروج", style=discord.ButtonStyle.secondary, emoji="🚪", row=0)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        self.game.spectators.discard(uid)
        if self.game.remove_lobby_player(uid):
            await interaction.response.send_message("غادرت الردهة.", ephemeral=True)
            await self._refresh(interaction)
        else:
            await interaction.response.send_message("لست في الردهة.", ephemeral=True)

    @discord.ui.button(label="بدء اللعبة", style=discord.ButtonStyle.blurple, emoji="▶️", row=1)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message(
                f"فقط <@{self.host_id}> يستطيع البدء.", ephemeral=True
            )
        if len(self.game.lobby_user_ids) < MIN_PLAYERS:
            return await interaction.response.send_message(
                f"يجب {MIN_PLAYERS} لاعبين على الأقل (حالياً {len(self.game.lobby_user_ids)}).",
                ephemeral=True,
            )
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        if interaction.message:
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass
        await self.on_start(interaction)

    @discord.ui.button(label="إعدادات", style=discord.ButtonStyle.gray, emoji="⚙️", row=1)
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message("المنشئ فقط.", ephemeral=True)
        view = SettingsView(self.game, self.host_id, self)
        await interaction.response.send_message("⚙️ إعدادات اللعبة:", view=view, ephemeral=True)

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.red, emoji="🛑", row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message(
                f"فقط <@{self.host_id}> يستطيع الإلغاء.", ephemeral=True
            )
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        if interaction.message:
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass
        await self.on_cancel(interaction)


# ============================================================================
# أدوات الاختيار الليلي
# ============================================================================

class _RoleTargetSelect(discord.ui.Select):
    def __init__(self, candidates: list[PlayerState], placeholder: str, callback):
        opts = _player_options(candidates) or [
            discord.SelectOption(label="لا يوجد هدف متاح", value="none")
        ]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=opts[:25])
        self._callback = callback

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("لا يوجد هدف.", ephemeral=True)
        await self._callback(interaction, int(self.values[0]))


class _SkipButton(discord.ui.Button):
    def __init__(self, callback):
        super().__init__(label="تخطّي هذه الليلة", style=discord.ButtonStyle.gray, emoji="⏭️")
        self._callback = callback

    async def callback(self, interaction: discord.Interaction):
        await self._callback(interaction, None)


def _make_action_view(
    candidates: list[PlayerState],
    placeholder: str,
    callback,
    *,
    allow_skip: bool = True,
    timeout: float | None = None,
) -> discord.ui.View:
    view = discord.ui.View(timeout=timeout or NIGHT_SECONDS + 15)
    view.add_item(_RoleTargetSelect(candidates, placeholder, callback))
    if allow_skip:
        view.add_item(_SkipButton(callback))
    return view


class _MultiTargetSelect(discord.ui.Select):
    def __init__(self, candidates: list[PlayerState], placeholder: str, callback, count: int):
        opts = _player_options(candidates) or [
            discord.SelectOption(label="لا يوجد", value="none")
        ]
        super().__init__(
            placeholder=placeholder,
            min_values=count,
            max_values=count if len(opts) >= count else len(opts),
            options=opts[:25],
        )
        self._callback = callback

    async def callback(self, interaction: discord.Interaction):
        if any(v == "none" for v in self.values):
            return await interaction.response.send_message("لا يوجد هدف.", ephemeral=True)
        await self._callback(interaction, [int(v) for v in self.values])


def _make_multi_action_view(
    candidates: list[PlayerState],
    placeholder: str,
    callback,
    count: int,
    *,
    allow_skip: bool = True,
) -> discord.ui.View:
    view = discord.ui.View(timeout=NIGHT_SECONDS + 15)
    view.add_item(_MultiTargetSelect(candidates, placeholder, callback, count))
    if allow_skip:
        async def skip_cb(inter, _):
            await callback(inter, [])
        view.add_item(_SkipButton(skip_cb))
    return view


# ---- واجهة القاتل ----

class _KillerView(discord.ui.View):
    def __init__(self, candidates: list[PlayerState], role_names: list[str], on_submit):
        super().__init__(timeout=NIGHT_SECONDS + 15)
        self.target1: int | None = None
        self.target2: int | None = None
        self.role1: str | None = None
        self.role2: str | None = None
        self._on_submit = on_submit

        player_opts = _player_options(candidates)[:25] or [
            discord.SelectOption(label="لا يوجد", value="none")
        ]
        role_opts = [
            discord.SelectOption(label=f"{ROLES[r].emoji} {r}", value=r)
            for r in role_names if r in ROLES
        ][:25]

        sel_t1 = discord.ui.Select(placeholder="🎯 الهدف الأول", options=player_opts, row=0)
        sel_r1 = discord.ui.Select(placeholder="🎭 تخمين دور الهدف الأول", options=role_opts, row=1)
        sel_t2 = discord.ui.Select(placeholder="🎯 الهدف الثاني", options=player_opts, row=2)
        sel_r2 = discord.ui.Select(placeholder="🎭 تخمين دور الهدف الثاني", options=role_opts, row=3)

        async def cb_t1(i): self.target1 = None if sel_t1.values[0]=="none" else int(sel_t1.values[0]); await i.response.defer()
        async def cb_t2(i): self.target2 = None if sel_t2.values[0]=="none" else int(sel_t2.values[0]); await i.response.defer()
        async def cb_r1(i): self.role1 = sel_r1.values[0]; await i.response.defer()
        async def cb_r2(i): self.role2 = sel_r2.values[0]; await i.response.defer()

        sel_t1.callback = cb_t1; sel_t2.callback = cb_t2
        sel_r1.callback = cb_r1; sel_r2.callback = cb_r2
        self.add_item(sel_t1); self.add_item(sel_r1)
        self.add_item(sel_t2); self.add_item(sel_r2)

        confirm = discord.ui.Button(label="تأكيد", style=discord.ButtonStyle.danger, emoji="✅", row=4)
        async def cb_confirm(interaction):
            if not all([self.target1, self.target2, self.role1, self.role2]):
                return await interaction.response.send_message("⚠️ اختر الهدفين وخمّن دوريهما.", ephemeral=True)
            if self.target1 == self.target2:
                return await interaction.response.send_message("⚠️ شخصان مختلفان مطلوبان.", ephemeral=True)
            await self._on_submit(interaction, self.target1, self.role1, self.target2, self.role2)
        confirm.callback = cb_confirm
        self.add_item(confirm)

        skip = discord.ui.Button(label="تخطّي", style=discord.ButtonStyle.gray, emoji="⏭️", row=4)
        async def cb_skip(i): await self._on_submit(i, None, None, None, None)
        skip.callback = cb_skip
        self.add_item(skip)


# ---- واجهة المزوّر ----

class _ForgerView(discord.ui.View):
    def __init__(self, candidates: list[PlayerState], on_submit):
        super().__init__(timeout=NIGHT_SECONDS + 15)
        self.target_id: int | None = None
        self.fake_role: str | None = None
        self._on_submit = on_submit

        player_opts = _player_options(candidates)[:25]
        role_opts = [
            discord.SelectOption(label=f"{r.emoji} {r.name}", value=r.name)
            for r in ROLES.values()
            if r.team == "citizens"
        ][:25]

        sel_t = discord.ui.Select(placeholder="🎯 اختر الهدف", options=player_opts, row=0)
        sel_r = discord.ui.Select(placeholder="🎭 الدور المزيف", options=role_opts, row=1)

        async def cb_t(i): self.target_id = int(sel_t.values[0]); await i.response.defer()
        async def cb_r(i): self.fake_role = sel_r.values[0]; await i.response.defer()
        sel_t.callback = cb_t; sel_r.callback = cb_r
        self.add_item(sel_t); self.add_item(sel_r)

        confirm = discord.ui.Button(label="تأكيد التزوير", style=discord.ButtonStyle.danger, emoji="🖊️", row=2)
        async def cb_c(i):
            if not self.target_id or not self.fake_role:
                return await i.response.send_message("اختر الهدف والدور المزيف.", ephemeral=True)
            await self._on_submit(i, self.target_id, self.fake_role)
        confirm.callback = cb_c
        self.add_item(confirm)

        skip = discord.ui.Button(label="تخطّي", style=discord.ButtonStyle.gray, emoji="⏭️", row=2)
        async def cb_s(i): await self._on_submit(i, None, None)
        skip.callback = cb_s
        self.add_item(skip)


# ============================================================================
# بناء قوائم الأدوار الليلية
# ============================================================================

def _build_night_menu(
    game: MafiaGame,
    player: PlayerState,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """يبني embed وview مناسبَين لدور اللاعب في الليل."""
    role = player.role
    na = game.night_actions
    night_sec = game.settings.night_seconds
    candidates = [p for p in game.alive_players() if p.user.id != player.user.id]

    # أدوار بلا فعل ليلي
    if not role.has_night_action:
        embed = discord.Embed(
            title=f"{role.emoji} {role.name}",
            description="ليس لك فعل ليلي — نم بسلام.\n\n" + (role.tips or ""),
            color=ROLE_COLORS.get(role.team, discord.Color.dark_gray()),
        )
        return embed, None

    # كاهن استهلك قدرته
    if role.name == "كاهن" and player.priest_used:
        return discord.Embed(title="⛪ كاهن", description="استهلكت قدرتك من قبل.", color=discord.Color.gold()), None

    # مراسلة استهلكت
    if role.name == "مراسلة" and player.journalist_used:
        return discord.Embed(title="📰 مراسلة", description="استهلكت قدرتك من قبل.", color=discord.Color.gold()), None

    # قنّاص استهلك
    if role.name == "قنّاص" and player.sniper_used:
        return discord.Embed(title="🎯 قنّاص", description="أطلقت رصاصتك من قبل.", color=discord.Color.dark_green()), None

    # ===================== المافيا =====================

    if role.name in ("مافيا", "رئيس المافيا"):
        targets = [p for p in candidates if p.role.team != "mafia" and not (p.role.name == "قاتل" and p.joined_mafia)]

        async def cb_mafia(interaction, target_id):
            if target_id is None:
                na.mafia_votes.pop(player.user.id, None)
                msg = "تخطيت التصويت."
            else:
                na.mafia_votes[player.user.id] = target_id
                msg = f"🔪 صوّتت لقتل **{game.get(target_id).user.display_name}**."
            await interaction.response.send_message(msg, ephemeral=True)

        # رئيس المافيا: اختيار الوريث إذا لم يختر بعد
        extra_desc = ""
        if role.name == "رئيس المافيا" and not player.heir_id:
            extra_desc = "\n\n⚠️ **لم تختر وريثاً بعد!** استخدم &وريث @لاعب لاختيار وريثك."

        embed = discord.Embed(
            title="🔪 وقت المافيا",
            description=f"اختر الضحية. الأكثر تصويتاً يُقتل.{extra_desc}",
            color=discord.Color.dark_red(),
        )
        # أعضاء المافيا
        team = game.mafia_team_members(alive_only=True)
        if team:
            embed.add_field(
                name="👥 فريقك",
                value="\n".join(f"• {p.user.display_name} ({p.role.emoji} {p.role.name})" for p in team),
                inline=False,
            )
        return embed, _make_action_view(targets, "اختر ضحيتك", cb_mafia, timeout=night_sec + 15)

    if role.name == "وحش":
        async def cb_beast(interaction, target_id):
            na.beast_target = target_id
            msg = "لن تبحث الليلة." if target_id is None else (
                f"👹 ستبحث في **{game.get(target_id).user.display_name}**.\n"
                "إن كان مافيا → ضربتهم تخترق الحماية. إن لم يبقَ مافيا → تقتله."
            )
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="👹 وقت الوحش",
            description="اختر لاعباً للبحث عن المافيا فيه.\n"
                        "• وجد مافيا → ضربتهم تخترق طبيب+جندي\n"
                        "• لم يبقَ مافيا → يقتله مباشرة",
            color=discord.Color.dark_red(),
        ), _make_action_view(candidates, "اختر هدفاً", cb_beast, timeout=night_sec + 15)

    if role.name == "مضيفة":
        async def cb_hostess(interaction, target_id):
            na.hostess_block = target_id
            msg = "لن تمنعي أحداً." if not target_id else f"💋 منعتِ **{game.get(target_id).user.display_name}**."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="💋 وقت المضيفة",
            description="اختاري لاعباً لمنعه من تنفيذ دوره.",
            color=discord.Color.magenta(),
        ), _make_action_view(candidates, "اختاري هدفاً", cb_hostess, timeout=night_sec + 15)

    if role.name == "ساحرة":
        non_mafia = [p for p in candidates if p.role.team != "mafia"]
        async def cb_witch(interaction, target_id):
            na.witch_block = target_id
            msg = "لن تسحري أحداً." if not target_id else f"🧙‍♀️ سحرتِ **{game.get(target_id).user.display_name}**."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="🧙‍♀️ وقت الساحرة",
            description="اختاري مواطناً لتعطيل قدرته (السحر لا يؤثر على المافيا).",
            color=discord.Color.purple(),
        ), _make_action_view(non_mafia, "اختاري من تسحرين", cb_witch, timeout=night_sec + 15)

    if role.name == "جاسوسة":
        async def cb_spy(interaction, target_id):
            na.spy_target = (player.user.id, target_id) if target_id else None
            msg = "لن تتجسسي." if not target_id else "🕵️‍♀️ النتيجة في الصباح (تصل للمافيا أيضاً)."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="🕵️‍♀️ وقت الجاسوسة",
            description="اختاري لاعباً لكشف دوره — يُشارَك مع المافيا.",
            color=discord.Color.dark_red(),
        ), _make_action_view(candidates, "اختاري هدف التجسس", cb_spy, timeout=night_sec + 15)

    if role.name == "مزوّر":
        async def cb_forger(interaction, target_id, fake_role_name):
            na.forger_target = target_id
            na.forger_fake_role = fake_role_name
            if not target_id:
                msg = "لن تزوّر الليلة."
            else:
                msg = (f"🖊️ ستجعل **{game.get(target_id).user.display_name}** يبدو كـ"
                       f" {ROLES.get(fake_role_name, Role(fake_role_name,'','',' ',False)).emoji} **{fake_role_name}**.")
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="🖊️ وقت المزوّر",
            description="اختر لاعباً وزوّر دوره الذي يظهر للعرافة والمراسلة.",
            color=discord.Color.dark_orange(),
        ), _ForgerView(candidates, cb_forger)

    if role.name == "مخبر":
        async def cb_informant(interaction, target_id):
            na.informant_target = target_id
            msg = "لن تستخدم قدرتك." if not target_id else f"📋 ستعكس نتيجة الشرطي عن **{game.get(target_id).user.display_name}** الليلة."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="📋 وقت المخبر",
            description="اختر لاعباً: إذا حقّق الشرطي معه الليلة، تُعكس النتيجة.",
            color=discord.Color.dark_red(),
        ), _make_action_view(candidates, "اختر الهدف", cb_informant, timeout=night_sec + 15)

    if role.name == "عنكبوت":
        async def cb_spider(interaction, target_id):
            na.spider_trap = target_id
            msg = "لم تنصب فخاً." if not target_id else f"🕷️ نصبت فخاً على **{game.get(target_id).user.display_name}**."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="🕷️ وقت العنكبوت",
            description="انصب فخاً على لاعب: إذا استهدفه أي دور ليلاً، تعرف هوية المستهدِف.",
            color=discord.Color.dark_red(),
        ), _make_action_view(candidates, "اختر من تنصب عليه", cb_spider, timeout=night_sec + 15)

    if role.name == "محرّض":
        async def cb_inciter(interaction, target_id):
            na.inciter_voter = target_id
            msg = "لن تحرّض أحداً." if not target_id else f"📢 ستجبر **{game.get(target_id).user.display_name}** على التصويت ضد هدفك غداً."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="📢 وقت المحرّض",
            description="اختر لاعباً لإجباره على التصويت ضد الهدف الذي تختاره في النهار.",
            color=discord.Color.dark_red(),
        ), _make_action_view(candidates, "اختر من تحرّض", cb_inciter, timeout=night_sec + 15)

    # ===================== المواطنون =====================

    if role.name == "شرطي":
        async def cb_cop(interaction, target_id):
            na.cop_target = (player.user.id, target_id) if target_id else None
            msg = "لن تحقق." if not target_id else "🚓 تم التسجيل. النتيجة في الصباح."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="🚓 وقت الشرطي",
            description="اختر لاعباً للتحقيق معه.",
            color=discord.Color.blue(),
        ), _make_action_view(candidates, "اختر من تحقق معه", cb_cop, timeout=night_sec + 15)

    if role.name == "نائب الشرطي":
        return discord.Embed(
            title="🚔 نائب الشرطي",
            description="ستأخذ دور الشرطي تلقائياً عند وفاته. لا تنفيذ الآن.",
            color=discord.Color.blue(),
        ), None

    if role.name == "طبيب":
        all_alive = list(game.alive_players())
        async def cb_doctor(interaction, target_id):
            na.doctor_save = target_id
            if not target_id:
                msg = "لن تحمي أحداً."
            elif target_id == player.user.id:
                msg = "💉 ستحمي نفسك."
            else:
                msg = f"💉 ستحمي **{game.get(target_id).user.display_name}**."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="💉 وقت الطبيب",
            description="اختر لاعباً لحمايته (يشمل نفسك).",
            color=discord.Color.green(),
        ), _make_action_view(all_alive, "اختر من تحمي", cb_doctor, timeout=night_sec + 15)

    if role.name == "حارسة":
        async def cb_guardian(interaction, target_id):
            na.guardian_target = target_id
            msg = "لن تحرسي أحداً." if not target_id else f"🛡️ ستحرسين **{game.get(target_id).user.display_name}**."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="🛡️ وقت الحارسة",
            description="إذا هاجمت المافيا من تحرسينه، تقتلين المهاجم.",
            color=discord.Color.dark_teal(),
        ), _make_action_view(candidates, "اختاري من تحرسين", cb_guardian, timeout=night_sec + 15)

    if role.name == "عميل سري":
        na.secret_agent = player.user.id
        if game.day_count < 2:
            return discord.Embed(
                title="🕴️ عميل سري",
                description="تبدأ من الليلة الثانية. انتظر.",
                color=discord.Color.dark_blue(),
            ), None
        return discord.Embed(
            title="🕴️ عميل سري",
            description="ستكشف دور مواطن عشوائي تلقائياً في الصباح.",
            color=discord.Color.dark_blue(),
        ), None

    if role.name == "كاهن":
        dead = game.dead_players()
        if not dead:
            return discord.Embed(
                title="⛪ كاهن", description="لا يوجد موتى.", color=discord.Color.gold()
            ), None
        async def cb_priest(interaction, target_id):
            if not target_id:
                na.priest_revive = None
                msg = "احتفظت بقدرتك."
            else:
                na.priest_revive = target_id
                msg = f"⛪ ستعيد **{game.get(target_id).user.display_name}** (تستهلك القدرة)."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="⛪ وقت الكاهن",
            description="اختر ميتاً لإعادته — مرة واحدة.",
            color=discord.Color.gold(),
        ), _make_action_view(dead, "اختر من تعيد", cb_priest, timeout=night_sec + 15)

    if role.name == "عرافة":
        dead = game.dead_players()
        if not dead:
            return discord.Embed(
                title="🔮 عرافة", description="لا يوجد موتى.", color=discord.Color.dark_purple()
            ), None
        async def cb_oracle(interaction, target_id):
            na.oracle_target = (player.user.id, target_id) if target_id else None
            msg = "لن تستجوبي." if not target_id else "🔮 النتيجة في الصباح."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="🔮 وقت العرافة",
            description="اختاري ميتاً لمعرفة دوره.",
            color=discord.Color.dark_purple(),
        ), _make_action_view(dead, "اختاري ميتاً", cb_oracle, timeout=night_sec + 15)

    if role.name == "مراسلة":
        if game.day_count < 2:
            return discord.Embed(
                title="📰 مراسلة", description="لا يمكنك النشر في الليلة الأولى.",
                color=discord.Color.gold()
            ), None
        async def cb_journalist(interaction, target_id):
            if not target_id:
                na.journalist_reveal = None
                msg = "احتفظت بمنشورك."
            else:
                na.journalist_reveal = target_id
                msg = f"📰 ستنشرين دور **{game.get(target_id).user.display_name}** (تستهلك القدرة)."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="📰 وقت المراسلة",
            description="انشري دور لاعب علناً — مرة واحدة.",
            color=discord.Color.gold(),
        ), _make_action_view(candidates, "اختاري من تنشرين", cb_journalist, timeout=night_sec + 15)

    if role.name == "رجل عصابة":
        async def cb_gangster(interaction, target_id):
            na.gangster_block = target_id
            msg = "لن تمنع أحداً." if not target_id else f"🚫 ستمنع **{game.get(target_id).user.display_name}** من التصويت غداً."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="🚫 وقت رجل العصابة",
            description="اختر لاعباً لمنعه من التصويت في النهار التالي.",
            color=discord.Color.dark_gold(),
        ), _make_action_view(candidates, "اختر من تمنع", cb_gangster, timeout=night_sec + 15)

    if role.name == "قنّاص":
        async def cb_sniper(interaction, target_id):
            na.sniper_target = target_id
            msg = "احتفظت برصاصتك." if not target_id else f"🎯 ستطلق على **{game.get(target_id).user.display_name}** (رصاصة واحدة فقط)."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="🎯 وقت القنّاص",
            description="امتلك رصاصة واحدة تقتل أي لاعب مباشرة (لا يمكن الحماية منها).\nاستخدمها بحكمة!",
            color=discord.Color.dark_green(),
        ), _make_action_view(candidates, "اختر هدفك", cb_sniper, timeout=night_sec + 15)

    if role.name == "مراقب":
        async def cb_watcher(interaction, target_id):
            na.watcher_target = target_id
            msg = "لن تراقب." if not target_id else f"👁️ ستراقب **{game.get(target_id).user.display_name}** الليلة."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="👁️ وقت المراقب",
            description="اختر لاعباً لمراقبته: ستعرف كم شخص استهدفه هذه الليلة.",
            color=discord.Color.teal(),
        ), _make_action_view(candidates, "اختر من تراقب", cb_watcher, timeout=night_sec + 15)

    if role.name == "محقق":
        if len(candidates) < 2:
            return discord.Embed(
                title="🔍 محقق", description="لا يوجد لاعبان كافيان.", color=discord.Color.blue()
            ), None
        async def cb_detective(interaction, ids: list[int]):
            if len(ids) >= 2:
                na.detective_pair = (ids[0], ids[1])
                n1 = game.get(ids[0]).user.display_name
                n2 = game.get(ids[1]).user.display_name
                msg = f"🔍 ستفحص العلاقة بين **{n1}** و **{n2}**."
            else:
                msg = "تخطيت."
            await interaction.response.send_message(msg, ephemeral=True)
        return discord.Embed(
            title="🔍 وقت المحقق",
            description="اختر لاعبَين: ستعرف إذا كان أحدهما استهدف الآخر هذه الليلة.",
            color=discord.Color.blue(),
        ), _make_multi_action_view(candidates, "اختر لاعبَين", cb_detective, 2)

    if role.name == "سفير":
        # يحمي لاعباً من الإعدام بالتصويت مرة
        ambassadored = [p for p in candidates if not p.is_protected_by_ambassador]
        async def cb_ambassador(interaction, target_id):
            if not target_id:
                return await interaction.response.send_message("تخطيت.", ephemeral=True)
            tp = game.get(target_id)
            if tp:
                tp.is_protected_by_ambassador = True
                player.ambassador_shield_id = target_id
                await interaction.response.send_message(
                    f"🤝 منحت **{tp.user.display_name}** حصانة من الإعدام مرة واحدة.",
                    ephemeral=True,
                )
        return discord.Embed(
            title="🤝 وقت السفير",
            description="منح لاعب حصانة من الإعدام بالتصويت مرة واحدة (تستهلك القدرة).",
            color=discord.Color.teal(),
        ), _make_action_view(ambassadored or candidates, "اختر المحمي", cb_ambassador,
                             allow_skip=True, timeout=night_sec + 15)

    # القاتل
    if role.name == "قاتل":
        if player.joined_mafia:
            mafia_targets = [p for p in candidates if p.role.team != "mafia" and p.role.name != "قاتل"]
            async def cb_join(interaction, target_id):
                if not target_id:
                    na.mafia_votes.pop(player.user.id, None)
                    msg = "تخطيت."
                else:
                    na.mafia_votes[player.user.id] = target_id
                    msg = f"🔪 صوّتت لقتل **{game.get(target_id).user.display_name}**."
                await interaction.response.send_message(msg, ephemeral=True)
            return discord.Embed(
                title="🗡️🔪 قاتل (انضممت للمافيا)",
                description="صوّت مع المافيا.",
                color=discord.Color.dark_red(),
            ), _make_action_view(mafia_targets, "اختر ضحيتك", cb_join, timeout=night_sec + 15)

        killer_candidates = [
            p for p in candidates
            if p.role.name != "طبيب"
        ]
        guessable_roles = [
            r for r in ROLES
            if ROLES[r].team in ("citizens", "mafia") and r != "قاتل"
        ]

        async def on_killer_submit(interaction, t1_id, r1_name, t2_id, r2_name):
            if t1_id is None:
                na.killer_guesses = []
                na.killer_role_guesses = {}
                return await interaction.response.send_message("تخطيت.", ephemeral=True)
            na.killer_guesses = [t1_id, t2_id]
            na.killer_role_guesses = {t1_id: r1_name, t2_id: r2_name}
            n1 = game.get(t1_id).user.display_name if game.get(t1_id) else "؟"
            n2 = game.get(t2_id).user.display_name if game.get(t2_id) else "؟"
            await interaction.response.send_message(
                f"🗡️ اخترت:\n• **{n1}** → {r1_name}\n• **{n2}** → {r2_name}",
                ephemeral=True,
            )

        return discord.Embed(
            title="🗡️ وقت القاتل",
            description=(
                "أنت من **فريق المافيا** لكن لا تعرفهم.\n"
                "اختر **شخصَين** وخمّن دور كل واحد:\n"
                "• إن كان أحدهما مافيا → تنضم لهم\n"
                "• إن كانا مواطنَين **وخمّنت دوريهما بدقة** → يُقتلان"
            ),
            color=discord.Color.dark_gray(),
        ), _KillerView(killer_candidates, guessable_roles, on_killer_submit)

    return discord.Embed(title=role.name, description="لا تنفيذ متاح."), None


# ============================================================================
# زر افتح دورك الليلي
# ============================================================================

class _NightRoleView(discord.ui.View):
    def __init__(self, game: MafiaGame):
        super().__init__(timeout=game.settings.night_seconds + 15)
        self.game = game

    @discord.ui.button(label="🌙 افتح دورك", style=discord.ButtonStyle.blurple)
    async def open_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.game.get(interaction.user.id)
        if not player:
            return await interaction.response.send_message("لست في هذه اللعبة.", ephemeral=True)
        if not player.alive:
            return await interaction.response.send_message("💀 أنت ميت.", ephemeral=True)
        embed, view = _build_night_menu(self.game, player)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="👁️ أنا مراقب", style=discord.ButtonStyle.gray)
    async def spectator_view(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.game.spectators:
            return await interaction.response.send_message("لست مراقباً.", ephemeral=True)
        alive = self.game.alive_players()
        lines = [f"{'🟢' if p.alive else '💀'} {p.user.display_name}" for p in alive]
        await interaction.response.send_message(
            "👁️ **اللاعبون الأحياء:**\n" + "\n".join(lines),
            ephemeral=True,
        )


# ============================================================================
# واجهات النهار
# ============================================================================

class DiscussionTimer:
    def __init__(self, seconds: float):
        loop = asyncio.get_event_loop()
        self.deadline = loop.time() + seconds
        self._loop = loop

    def remaining(self) -> float:
        return max(0.0, self.deadline - self._loop.time())

    def extend(self, secs: float):
        self.deadline += secs

    def reduce(self, secs: float):
        self.deadline = max(self._loop.time() + 3, self.deadline - secs)

    def end_now(self):
        self.deadline = self._loop.time()


class DiscussionView(discord.ui.View):
    def __init__(self, game: MafiaGame, timer: DiscussionTimer, message_ref: dict):
        super().__init__(timeout=game.settings.discussion_seconds * 4)
        self.game = game
        self.timer = timer
        self.message_ref = message_ref

    def _can_use(self, interaction: discord.Interaction) -> bool:
        p = self.game.get(interaction.user.id)
        return p is not None and p.alive

    async def _refresh_msg(self):
        msg = self.message_ref.get("msg")
        if not msg:
            return
        embed = msg.embeds[0] if msg.embeds else discord.Embed()
        for i, f in enumerate(embed.fields):
            if "الوقت المتبقي" in f.name:
                embed.set_field_at(i, name="⏱️ الوقت المتبقي", value=f"{int(self.timer.remaining())} ثانية", inline=False)
                break
        try:
            await msg.edit(embed=embed)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="+١٥ ثانية", style=discord.ButtonStyle.green, emoji="➕", row=0)
    async def add_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_use(interaction):
            return await interaction.response.send_message("فقط اللاعبون الأحياء.", ephemeral=True)
        self.timer.extend(15)
        await interaction.response.send_message(f"⏱️ تمديد ١٥ث (متبقٍ: {int(self.timer.remaining())}ث)", ephemeral=True)
        await self._refresh_msg()

    @discord.ui.button(label="-١٥ ثانية", style=discord.ButtonStyle.red, emoji="➖", row=0)
    async def cut_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_use(interaction):
            return await interaction.response.send_message("فقط اللاعبون الأحياء.", ephemeral=True)
        self.timer.reduce(15)
        await interaction.response.send_message(f"⏱️ تقليل ١٥ث (متبقٍ: {int(self.timer.remaining())}ث)", ephemeral=True)
        await self._refresh_msg()

    @discord.ui.button(label="إنهاء النقاش", style=discord.ButtonStyle.blurple, emoji="🏁", row=0)
    async def end_discussion(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_use(interaction):
            return await interaction.response.send_message("فقط اللاعبون الأحياء.", ephemeral=True)
        self.timer.end_now()
        await interaction.response.send_message("⏩ طلبت إنهاء النقاش.", ephemeral=True)
        await self._refresh_msg()

    @discord.ui.button(label="الأدوار في اللعبة", style=discord.ButtonStyle.gray, emoji="📋", row=1)
    async def roles_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        alive = self.game.alive_players()
        dead = self.game.dead_players()
        alive_count = len(alive)
        dead_count = len(dead)

        desc = f"**أحياء ({alive_count}):**\n"
        desc += " • ".join(p.user.mention for p in alive)

        if dead:
            reveal = self.game.settings.reveal_role_on_death
            desc += f"\n\n**أموات ({dead_count}):**\n"
            desc += "\n".join(
                f"💀 {p.user.display_name}" + (f" — {p.role.emoji} {p.role.name}" if reveal else "")
                for p in dead
            )
        await interaction.response.send_message(
            embed=discord.Embed(
                title="📋 حالة اللعبة",
                description=desc,
                color=discord.Color.blurple(),
            ),
            ephemeral=True,
        )


class DayVoteSelect(discord.ui.Select):
    def __init__(self, game: MafiaGame):
        self.game = game
        options = _player_options(game.alive_players())[:24]
        options.append(discord.SelectOption(label="امتناع", value="abstain", emoji="🚫"))
        super().__init__(placeholder="صوّت لإعدام لاعب", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        voter = self.game.get(interaction.user.id)
        if not voter or not voter.alive:
            return await interaction.response.send_message("لا يمكنك التصويت.", ephemeral=True)
        if voter.blocked_from_voting_today:
            return await interaction.response.send_message("🚫 رجل العصابة منعك من التصويت.", ephemeral=True)
        choice = self.values[0]
        if choice == "abstain":
            self.game.day_votes.pop(interaction.user.id, None)
            return await interaction.response.send_message("امتنعت عن التصويت.", ephemeral=True)
        target_id = int(choice)
        self.game.day_votes[interaction.user.id] = target_id
        target = self.game.get(target_id)
        if target and target.first_vote_against is None:
            target.first_vote_against = interaction.user.id
        await interaction.response.send_message(f"🗳️ صوّتت ضد **{target.user.display_name}**.", ephemeral=True)


class DayVoteView(discord.ui.View):
    def __init__(self, game: MafiaGame):
        super().__init__(timeout=game.settings.vote_seconds + 15)
        self.add_item(DayVoteSelect(game))


class ConfirmExecutionView(discord.ui.View):
    def __init__(self, game: MafiaGame, target: PlayerState, state: dict):
        super().__init__(timeout=game.settings.confirm_seconds + 15)
        self.game = game
        self.target = target
        self.state = state

    def _validate(self, interaction: discord.Interaction) -> bool:
        p = self.game.get(interaction.user.id)
        return p is not None and p.alive

    @discord.ui.button(label="موافق على الإعدام", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._validate(interaction):
            return await interaction.response.send_message("لا يحق لك.", ephemeral=True)
        self.state["approve"].add(interaction.user.id)
        self.state["reject"].discard(interaction.user.id)
        await interaction.response.send_message("✅ سُجلت موافقتك.", ephemeral=True)

    @discord.ui.button(label="اعتراض", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._validate(interaction):
            return await interaction.response.send_message("لا يحق لك.", ephemeral=True)
        self.state["reject"].add(interaction.user.id)
        self.state["approve"].discard(interaction.user.id)
        await interaction.response.send_message("❌ سُجل اعتراضك.", ephemeral=True)


# ============================================================================
# إعداد البوت
# ============================================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="&", intents=intents, help_command=None)

games: dict[str, MafiaGame] = {}


def game_key(guild_id: int, channel_id: int) -> str:
    return f"{guild_id}_{channel_id}"


# ============================================================================
# دورة اللعبة الرئيسية
# ============================================================================

async def run_game(game: MafiaGame):
    first_blood_done = False
    try:
        async def member_lookup(uid: int):
            m = game.guild.get_member(uid)
            if m is None:
                try:
                    m = await game.guild.fetch_member(uid)
                except discord.HTTPException:
                    return None
            return m

        assignments = distribute_roles(game.lobby_user_ids, game.settings if game.settings.fixed_roles else None)
        for user_id, role in assignments.items():
            member = await member_lookup(user_id)
            if member is None:
                continue
            game.players[user_id] = PlayerState(user=member, role=role)
            ensure_rank(user_id)

        # إعلام المافيا ببعضهم
        if game.settings.mafia_knows_each_other:
            mafia_members = [p for p in game.players.values() if p.role.team == "mafia"]
            for mp in mafia_members:
                teammates = [p for p in mafia_members if p.user.id != mp.user.id]
                if teammates:
                    names = "\n".join(f"• {p.user.display_name} ({p.role.emoji} {p.role.name})" for p in teammates)
                    mp.pending_notices.append(
                        f"🔪 **مرحباً بك في فريق المافيا!**\n"
                        f"زملاؤك:\n{names}\n\n"
                        f"استخدم `&همس <رسالة>` للتواصل السري."
                    )

        roster = "\n".join(f"• {p.user.mention}" for p in game.players.values())
        mode_name = {MODE_FAST: "⚡ سريع", MODE_RANKED: "🏆 مصنّف"}.get(game.mode, "🎮 عادي")

        await game.channel.send(
            embed=discord.Embed(
                title="🎬 بدأت لعبة مافيا 42!",
                description=(
                    f"**الوضع:** {mode_name}\n"
                    f"تم توزيع **{len(game.players)} دور**.\n"
                    f"📩 اضغط زر 'افتح دورك' في كل ليلة للتصرف.\n\n"
                    f"**اللاعبون:**\n{roster}"
                ),
                color=discord.Color.gold(),
            ),
        )

        # إرسال إشعارات الفريق
        for p in game.players.values():
            if p.pending_notices:
                text = "\n\n".join(p.pending_notices)
                p.pending_notices.clear()
                try:
                    dm = await p.user.create_dm()
                    await dm.send(text)
                except discord.HTTPException:
                    pass

        await snapshot_player_perms(game)

        while True:
            winner = game.check_winner()
            if winner:
                await announce_winner(game, winner)
                return
            events = await run_night(game)
            if not first_blood_done:
                killed_this_night = [p for p in game.players.values() if not p.alive]
                for kp in killed_this_night:
                    game.game_stats[f"first_blood_{kp.user.id}"] = True
                first_blood_done = True
            winner = game.check_winner()
            if winner:
                await announce_winner(game, winner)
                return
            await run_day(game)

    except asyncio.CancelledError:
        log.info("Game cancelled in #%s", game.channel.name)
        raise
    except Exception:
        log.exception("Game crashed in #%s", game.channel.name)
        try:
            await game.channel.send("❌ حدث خطأ غير متوقع وانتهت اللعبة.")
        except discord.HTTPException:
            pass
    finally:
        try:
            await restore_all_perms(game)
        except Exception:
            pass
        games.pop(game_key(game.guild.id, game.channel.id), None)
        await _update_player_stats_after_game(game)


async def _update_player_stats_after_game(game: MafiaGame):
    """يحدّث إحصائيات اللاعبين في نهاية اللعبة."""
    winner = game.check_winner() or "unknown"
    for p in game.players.values():
        uid = p.user.id
        stats = get_stats(uid)
        stats["games_played"] += 1

        won = (
            (p.role.team == "citizens" and winner == "citizens") or
            (p.role.team == "mafia" and winner == "mafia") or
            (p.role.name == "قاتل" and winner == "mafia") or
            (p.role.name == "مجنون" and p.killed_by == "vote")
        )

        if won:
            stats["wins"] += 1
            stats["win_streak"] += 1
            stats["max_win_streak"] = max(stats["max_win_streak"], stats["win_streak"])
        else:
            stats["losses"] += 1
            stats["win_streak"] = 0

        if p.alive:
            stats["times_survived"] += 1

        role_name = p.role.name
        stats["roles_played"][role_name] = stats["roles_played"].get(role_name, 0) + 1

        if p.role.team == "mafia":
            stats["games_as_mafia"] += 1
            if won:
                stats["wins_as_mafia"] += 1
        elif p.role.team == "citizens":
            stats["games_as_citizen"] += 1
            if won:
                stats["wins_as_citizen"] += 1
        else:
            stats["games_as_neutral"] += 1
            if won:
                stats["wins_as_neutral"] += 1

        save_stats(uid, stats)


# ============================================================================
# الإعلان عن الفائز
# ============================================================================

async def announce_winner(game: MafiaGame, winner: str):
    game.phase = "ended"

    # الوضع الخاص بالمجنون
    jester = game.get_by_role("مجنون")
    jester_won = jester and jester.killed_by == "vote"
    if jester_won:
        winner = "jester"

    titles = {
        "citizens": "🏆 فوز فريق المواطنين!",
        "mafia":    "🔪 فوز فريق المافيا!",
        "jester":   "🤡 فوز المجنون!",
    }
    colors = {
        "citizens": discord.Color.green(),
        "mafia":    discord.Color.dark_red(),
        "jester":   discord.Color.purple(),
    }

    lines = []
    for p in game.players.values():
        status = "🟢" if p.alive else "💀"
        lines.append(f"{status} {p.user.mention} — {p.role.emoji} **{p.role.name}**")

    embed = discord.Embed(
        title=titles.get(winner, "🏁 انتهت اللعبة"),
        description="\n".join(lines),
        color=colors.get(winner, discord.Color.blurple()),
    )
    embed.add_field(
        name="⏱️ مدة اللعبة",
        value=f"الليالي: **{game.day_count}** | الوقت: **{game.elapsed_time()}**",
        inline=False,
    )

    # تحديث النقاط
    actual_winner = "citizens" if winner == "jester" else winner
    deltas, ranks = update_ranks_after_game(game, actual_winner)
    rank_lines = []
    for p in game.players.values():
        d = deltas.get(p.user.id, 0)
        new_pts = ranks.get(str(p.user.id), INITIAL_POINTS)
        sign = "+" if d >= 0 else ""
        rank = get_rank_title(new_pts)
        rank_lines.append(f"{p.user.mention}: **{sign}{d}** | {new_pts} ({rank})")
    if rank_lines:
        embed.add_field(name="🏅 تحديث النقاط", value="\n".join(rank_lines), inline=False)

    # إنجازات
    ach_lines = []
    for p in game.players.values():
        achs = check_and_grant_achievements(p, game, actual_winner, game.game_stats)
        for ach in achs:
            ach_lines.append(f"{p.user.mention} فاز بـ {ach.emoji} **{ach.name}** (+{ach.points} نقطة)")
    if ach_lines:
        embed.add_field(name="🏆 إنجازات جديدة", value="\n".join(ach_lines[:10]), inline=False)

    event = {
        "citizens": "win_citizens",
        "mafia": "win_mafia",
        "jester": "jester_win",
    }.get(winner, "quiet")

    await send_with_optional_image(game.channel, embed, event)
    await restore_all_perms(game)

    # حفظ اللعبة في السجل
    try:
        save_game_to_history(game, actual_winner)
    except Exception:
        log.exception("فشل حفظ سجل اللعبة")


# ============================================================================
# الليل
# ============================================================================

async def run_night(game: MafiaGame) -> set[str]:
    game.phase = "night"
    game.day_count += 1
    game.night_actions = NightActions()

    for p in game.players.values():
        p.blocked_from_voting_today = False
        p.first_vote_against = None

    await mute_all_alive(game)
    await mute_all_dead(game)

    night_view = _NightRoleView(game)
    night_embed = discord.Embed(
        title=f"🌙 الليلة {game.day_count}",
        description=(
            f"حلّ الظلام... لديكم **{game.settings.night_seconds} ثانية**.\n\n"
            "👇 اضغط **افتح دورك** لتنفيذ مهارتك (يظهر لك فقط).\n"
            "⚠️ المافيا: إذا لم تختر، لن يُقتل أحد."
        ),
        color=discord.Color.from_rgb(10, 10, 50),
    )
    night_embed.set_footer(text=f"اللاعبون الأحياء: {len(game.alive_players())}")

    # العميل السري
    for p in game.alive_players():
        if p.role.name == "عميل سري":
            game.night_actions.secret_agent = p.user.id

    night_msg = await game.channel.send(embed=night_embed, view=night_view)

    await asyncio.sleep(game.settings.night_seconds)

    for child in night_view.children:
        child.disabled = True
    try:
        await night_msg.edit(view=night_view)
    except discord.HTTPException:
        pass

    log_lines, events = await resolve_night(game)

    # إرسال الإشعارات الخاصة
    for p in game.players.values():
        if p.pending_notices:
            text = "\n\n".join(p.pending_notices)
            p.pending_notices.clear()
            try:
                dm = await p.user.create_dm()
                await dm.send(text)
            except discord.HTTPException:
                pass

    game.phase = "day"
    game.day_votes = {}
    await unmute_all_alive(game)
    await mute_all_dead(game)

    # بناء رسالة الصباح
    alive = game.alive_players()
    blocked_voters = [p for p in alive if p.blocked_from_voting_today]
    alive_mentions = " • ".join(p.user.mention for p in alive)

    desc_lines = list(log_lines) if log_lines else ["🌅 مرّت الليلة بسلام."]
    desc_lines.append(f"\n👥 **المتبقون ({len(alive)}):** {alive_mentions}")
    if blocked_voters:
        desc_lines.append("🚫 **ممنوعون من التصويت:** " + ", ".join(p.user.mention for p in blocked_voters))
    desc_lines.append("_استخدم الأزرار للتحكم بوقت النقاش._")

    timer = DiscussionTimer(game.settings.discussion_seconds)
    msg_ref: dict = {}
    discussion_view = DiscussionView(game, timer, msg_ref)

    morning = discord.Embed(
        title=f"🌅 صباح اليوم {game.day_count}",
        description="\n".join(desc_lines),
        color=discord.Color.orange(),
    )
    morning.add_field(name="⏱️ الوقت المتبقي", value=f"{int(timer.remaining())} ثانية", inline=False)
    morning.set_footer(text=f"الليلة {game.day_count} | مافيا 42 v{BOT_VERSION}")

    chosen_event = _pick_morning_event(events)
    img_file, img_url = _event_file(chosen_event)
    if img_url:
        morning.set_image(url=img_url)
    if img_file:
        morning_msg = await game.channel.send(embed=morning, file=img_file, view=discussion_view)
    else:
        morning_msg = await game.channel.send(embed=morning, view=discussion_view)
    msg_ref["msg"] = morning_msg

    while timer.remaining() > 0:
        await asyncio.sleep(min(timer.remaining(), 1))

    for c in discussion_view.children:
        c.disabled = True
    try:
        await morning_msg.edit(view=discussion_view)
    except discord.HTTPException:
        pass

    return events


# ============================================================================
# معالجة الليل — الحل الكامل
# ============================================================================

async def resolve_night(game: MafiaGame) -> tuple[list[str], set[str]]:
    log_lines: list[str] = []
    events: set[str] = set()
    na = game.night_actions

    # ---- 1. تحديد المحظورين ----
    blocked: set[int] = set()
    if na.hostess_block is not None:
        blocked.add(na.hostess_block)
    if na.witch_block is not None:
        witch = game.alive_by_role("ساحرة")
        if witch and witch.user.id not in blocked:
            target = game.get(na.witch_block)
            if target and target.role.team != "mafia":
                blocked.add(na.witch_block)

    def is_blocked(uid: int) -> bool:
        return uid in blocked

    # ---- 2. فخ العنكبوت ----
    if na.spider_trap is not None:
        spider = game.alive_by_role("عنكبوت")
        if spider and not is_blocked(spider.user.id):
            # من الذي استهدف الهدف الليلة؟
            trap_target = na.spider_trap
            attackers = []
            if na.mafia_votes and trap_target in na.mafia_votes.values():
                mafia_attackers = [mid for mid, tid in na.mafia_votes.items() if tid == trap_target]
                for mid in mafia_attackers:
                    mp = game.get(mid)
                    if mp:
                        attackers.append(mp.user.display_name)
            if na.cop_target and na.cop_target[1] == trap_target:
                cp = game.get(na.cop_target[0])
                if cp:
                    attackers.append(cp.user.display_name + " (شرطي)")
            if na.doctor_save == trap_target:
                doc = game.alive_by_role("طبيب")
                if doc:
                    attackers.append(doc.user.display_name + " (طبيب)")
            if attackers:
                spider.pending_notices.append(
                    f"🕷️ **فخّك أمسك!** من استهدف {game.get(trap_target).user.display_name if game.get(trap_target) else '؟'}:\n"
                    + "\n".join(f"• {a}" for a in attackers)
                )
            else:
                spider.pending_notices.append("🕷️ لم يستهدف أحد هدفك الليلة.")

    # ---- 3. المخبر (عكس نتيجة الشرطي) ----
    informant_reversed = False
    if na.informant_target is not None:
        informant = game.alive_by_role("مخبر")
        if informant and not is_blocked(informant.user.id):
            informant_reversed = True

    # ---- 4. أدوار المعلومات ----
    _process_info_roles(game, blocked, informant_reversed=informant_reversed and na.informant_target)

    # ---- 5. مراقب ----
    if na.watcher_target is not None:
        watcher = game.alive_by_role("مراقب")
        if watcher and not is_blocked(watcher.user.id):
            wt = na.watcher_target
            targeting_count = 0
            if na.mafia_votes.values() and wt in na.mafia_votes.values():
                targeting_count += sum(1 for v in na.mafia_votes.values() if v == wt)
            if na.cop_target and na.cop_target[1] == wt:
                targeting_count += 1
            if na.doctor_save == wt:
                targeting_count += 1
            if na.guardian_target == wt:
                targeting_count += 1
            if na.beast_target == wt:
                targeting_count += 1
            if na.spy_target and na.spy_target[1] == wt:
                targeting_count += 1
            watcher.pending_notices.append(
                f"👁️ **نتيجة المراقبة على {game.get(wt).user.display_name if game.get(wt) else '؟'}:**\n"
                f"استُهدف من قِبَل **{targeting_count}** شخص الليلة."
            )

    # ---- 6. محقق ----
    if na.detective_pair is not None:
        detective = game.alive_by_role("محقق")
        if detective and not is_blocked(detective.user.id):
            a_id, b_id = na.detective_pair
            a, b = game.get(a_id), game.get(b_id)
            if a and b:
                # هل استهدف أحدهما الآخر؟
                connected = False
                if na.mafia_votes.get(a_id) == b_id or na.mafia_votes.get(b_id) == a_id:
                    connected = True
                if (na.cop_target and {na.cop_target[0], na.cop_target[1]} == {a_id, b_id}):
                    connected = True
                if (na.spy_target and {na.spy_target[0], na.spy_target[1]} == {a_id, b_id}):
                    connected = True
                result = "✅ نعم، هناك تفاعل بينهما الليلة." if connected else "❌ لا، لا يوجد تفاعل."
                detective.pending_notices.append(
                    f"🔍 **نتيجة التحقيق** بين **{a.user.display_name}** و **{b.user.display_name}**:\n{result}"
                )

    # ---- 7. هدف المافيا ----
    valid_mafia_votes = {mid: tid for mid, tid in na.mafia_votes.items() if not is_blocked(mid)}
    mafia_target: int | None = None
    if valid_mafia_votes:
        tally = Counter(valid_mafia_votes.values())
        mafia_target = tally.most_common(1)[0][0]

    # ---- 8. الوحش ----
    beast = game.alive_by_role("وحش")
    beast_pierces = False
    beast_solo_target: int | None = None
    if beast and not is_blocked(beast.user.id) and na.beast_target:
        pick = game.get(na.beast_target)
        mafia_main_alive = any(p.role.name == "مافيا" for p in game.alive_players())
        if pick and pick.role.team == "mafia":
            beast_pierces = True
            beast.pending_notices.append(
                f"👹 وجدت مافيا في **{pick.user.display_name}** — ضربتهم الليلة تخترق الحماية!"
            )
        else:
            beast.pending_notices.append(
                f"👹 لم تجد مافيا في **{pick.user.display_name if pick else '؟'}**."
                + ("" if mafia_main_alive else " ستقتله بنفسك.")
            )
        if not mafia_main_alive and pick:
            beast_solo_target = na.beast_target

    # ---- 9. حماية الطبيب ----
    save_target: int | None = None
    doctor = game.alive_by_role("طبيب")
    if doctor and not is_blocked(doctor.user.id) and na.doctor_save is not None:
        save_target = na.doctor_save

    # ---- 10. الحارسة ----
    guardian_target: int | None = None
    guardian = game.alive_by_role("حارسة")
    if guardian and not is_blocked(guardian.user.id) and na.guardian_target is not None:
        guardian_target = na.guardian_target

    # ---- 11. القنّاص ----
    sniper_kill_target: int | None = None
    sniper = game.alive_by_role("قنّاص")
    if sniper and not is_blocked(sniper.user.id) and na.sniper_target and not sniper.sniper_used:
        sniper.sniper_used = True
        sniper_kill_target = na.sniper_target
        t = game.get(sniper_kill_target)
        if t:
            if t.role.team == "mafia":
                game.game_stats["sniper_mafia_kill"] = True

    # ---- حساب الضربات ----
    killed: dict[int, str] = {}
    killed_by_player: dict[int, int] = {}

    def attempt_kill(target_id: int, cause: str, attacker_id: int | None = None, *, pierce: bool = False):
        target = game.get(target_id)
        if not target or not target.alive:
            return
        if not pierce and target_id == save_target:
            log_lines.append(f"💉 الطبيب أنقذ {target.user.mention}!")
            events.add("doctor_save")
            return
        if not pierce and target.role.name == "جندي" and not target.soldier_shield_used:
            target.soldier_shield_used = True
            game.game_stats["soldier_survived"] = True
            log_lines.append(f"💂 صدّ الجندي الهجوم! ({target.user.mention})")
            return
        # الفارس: يموت لكن يقتل مهاجمه
        if target.role.name == "فارس" and attacker_id and not pierce:
            attacker = game.get(attacker_id)
            if attacker and attacker.alive:
                killed[attacker_id] = "knight_counter"
                killed_by_player[attacker_id] = target_id
                log_lines.append(f"⚔️ الفارس قاتل مهاجمه! ({target.user.mention} أخذ {attacker.user.mention})")
            killed[target_id] = cause
            if attacker_id:
                killed_by_player[target_id] = attacker_id
            return
        if target_id == guardian_target and attacker_id and cause in ("mafia", "beast"):
            attacker = game.get(attacker_id)
            if attacker and attacker.alive:
                killed[attacker_id] = "guardian"
                killed_by_player[attacker_id] = target_id
                game.game_stats["guardian_kill"] = True
                log_lines.append(f"🛡️ الحارسة قتلت المهاجم! ({attacker.user.mention})")
                events.add("guardian_kill")
                return
        killed[target_id] = cause
        if attacker_id:
            killed_by_player[target_id] = attacker_id

    # ضربة المافيا
    if mafia_target is not None:
        attacker = next((p for p in game.alive_mafia() if p.role.name == "مافيا"), None)
        attempt_kill(mafia_target, "mafia", attacker.user.id if attacker else None, pierce=beast_pierces)

    # ضربة رئيس المافيا (يصوّت مثل المافيا العادية)
    # (مدمج في na.mafia_votes بنفس الآلية)

    # ضربة الوحش المنفردة
    if beast_solo_target and beast:
        attempt_kill(beast_solo_target, "beast", beast.user.id)

    # ضربة القنّاص
    if sniper_kill_target:
        t = game.get(sniper_kill_target)
        if t and t.alive:
            t.alive = False
            t.killed_by = "sniper"
            t.killed_by_player = sniper.user.id if sniper else None
            log_lines.append(f"🎯 القنّاص أطلق رصاصته! {t.user.mention} قُتل.")
            events.add("sniper_kill")
            await mute_player(game, sniper_kill_target, reason="قتله القنّاص")

    # تطبيق الوفيات
    for uid, cause in killed.items():
        p = game.get(uid)
        if p and p.alive:
            p.alive = False
            p.killed_by = cause
            p.killed_by_player = killed_by_player.get(uid)
            role_text = f" ({p.role.emoji} {p.role.name})" if game.settings.reveal_role_on_death else ""
            log_lines.append(f"☠️ {p.user.mention}{role_text} قُتل في الليل.")
            await mute_player(game, uid, reason="مات في الليل")
            if cause in ("mafia", "beast"):
                events.add("mafia_kill")

    # انتقام الشهيد الليلي
    for uid in list(killed.keys()):
        victim = game.get(uid)
        if victim and victim.role.name == "شهيد" and victim.killed_by_player:
            killer_p = game.get(victim.killed_by_player)
            if killer_p and killer_p.alive:
                killer_p.alive = False
                killer_p.killed_by = "martyr"
                game.game_stats["martyr_triggered"] = True
                log_lines.append(f"💀 **انتقام الشهيد:** أخذ {victim.user.mention} معه {killer_p.user.mention}!")
                await mute_player(game, killer_p.user.id, reason="انتقام الشهيد")

    # ---- الكاهن ----
    if na.priest_revive is not None:
        priest = game.alive_by_role("كاهن")
        if priest and not is_blocked(priest.user.id) and not priest.priest_used:
            target = game.get(na.priest_revive)
            if target and not target.alive:
                target.alive = True
                target.killed_by = None
                priest.priest_used = True
                game.game_stats["priest_revived"] = True
                log_lines.append(f"⛪ الكاهن أعاد {target.user.mention} للحياة!")
                events.add("priest_revive")
                await unmute_player(game, target.user.id)

    # ---- ممرضة ترث الطبيب ----
    dead_doctor = next((p for p in game.players.values() if p.role.name == "طبيب" and not p.alive), None)
    if dead_doctor:
        nurse = game.alive_by_role("ممرضة")
        if nurse:
            game.players[nurse.user.id].role = ROLES["طبيب"]
            nurse.pending_notices.append("🏥 **ورثتِ دور الطبيب!** ابدئي بحماية اللاعبين.")
            log_lines.append("🏥 الممرضة ورثت دور الطبيب!")

    # ---- نائب الشرطي ----
    dead_cop = next((p for p in game.players.values() if p.role.name == "شرطي" and not p.alive), None)
    if dead_cop:
        deputy = game.alive_by_role("نائب الشرطي")
        if deputy:
            deputy.role = ROLES["شرطي"]
            deputy.pending_notices.append("🚔 **ورثتَ دور الشرطي!** ابدأ بالتحقيق.")
            log_lines.append("🚔 نائب الشرطي ورث دور الشرطي!")

    # ---- رجل العصابة ----
    if na.gangster_block is not None:
        gangster = game.alive_by_role("رجل عصابة")
        if gangster and not is_blocked(gangster.user.id):
            target = game.get(na.gangster_block)
            if target and target.alive:
                target.blocked_from_voting_today = True

    # ---- المراسلة ----
    if na.journalist_reveal is not None:
        journalist = next((p for p in game.players.values() if p.role.name == "مراسلة"), None)
        if journalist and not is_blocked(journalist.user.id) and not journalist.journalist_used:
            target = game.get(na.journalist_reveal)
            if target:
                journalist.journalist_used = True
                role_shown = target.forger_fake_role or target.role.name
                r_obj = ROLES.get(role_shown, target.role)
                log_lines.append(
                    f"📰 **خبر عاجل**: {target.user.mention} هو {r_obj.emoji} **{role_shown}**!"
                )
                events.add("journalist_reveal")

    # ---- المزوّر ----
    if na.forger_target is not None and na.forger_fake_role:
        forger = game.alive_by_role("مزوّر")
        if forger and not is_blocked(forger.user.id):
            target = game.get(na.forger_target)
            if target:
                target.forger_fake_role = na.forger_fake_role

    # ---- معالجة القاتل ----
    killer = next((p for p in game.alive_players() if p.role.name == "قاتل" and not p.joined_mafia), None)
    if killer and na.killer_guesses:
        if is_blocked(killer.user.id):
            killer.pending_notices.append("💋 منعتك المضيفة من التحرك.")
        else:
            picks = [game.get(tid) for tid in na.killer_guesses]
            picks = [p for p in picks if p and p.alive]
            mafia_picks = [p for p in picks if p.role.team == "mafia"]
            citizen_picks = [p for p in picks if p.role.team == "citizens"]

            if mafia_picks:
                killer.joined_mafia = True
                game.game_stats["killer_joined"] = True
                mate_names = ", ".join(
                    f"{m.user.display_name} ({m.role.emoji} {m.role.name})"
                    for m in game.mafia_team_members(alive_only=True)
                    if m.user.id != killer.user.id
                )
                killer.pending_notices.append(
                    f"🗡️ ✅ اكتشفت المافيا — انضممت لهم!\nزملاؤك: {mate_names or 'لا أحد'}"
                )
                for m in game.mafia_team_members(alive_only=True):
                    if m.user.id != killer.user.id:
                        m.pending_notices.append(f"🗡️ القاتل **{killer.user.display_name}** انضم للفريق.")
            elif len(citizen_picks) >= 2:
                all_correct = all(
                    na.killer_role_guesses.get(p.user.id) == p.role.name
                    for p in citizen_picks
                )
                if all_correct:
                    for victim in citizen_picks:
                        if victim.alive:
                            victim.alive = False
                            victim.killed_by = "killer"
                            victim.killed_by_player = killer.user.id
                            log_lines.append(f"🗡️ {victim.user.mention} قُتل في الليل.")
                            await mute_player(game, victim.user.id, reason="قتله القاتل")
                    killer.pending_notices.append("🗡️ ✅ خمّنت بدقة — قتلتهما!")
                    events.add("killer_success")
                else:
                    killer.pending_notices.append("🗡️ ❌ تخمين خاطئ — لم يحدث شيء.")
            else:
                killer.pending_notices.append("🗡️ لم يحدث شيء الليلة.")

    # ---- وريث رئيس المافيا ----
    boss = next((p for p in game.players.values() if p.role.name == "رئيس المافيا" and not p.alive), None)
    if boss and boss.heir_id and not na.heir_activated:
        heir = game.get(boss.heir_id)
        if heir and heir.role.name != "مافيا" and heir.alive:
            heir.role = ROLES["مافيا"]
            heir.pending_notices.append("👑 رئيس المافيا مات — أنت الآن **مافيا** وريثاً!")
            na.heir_activated = True
            log_lines.append("👑 وريث رئيس المافيا نشط!")

    if not killed and not any(["قُتل" in l or "أنقذ" in l for l in log_lines]):
        log_lines.insert(0, "🌅 مرّت الليلة بسلام، لم يُقتل أحد.")

    return log_lines, events


def _process_info_roles(game: MafiaGame, blocked: set[int], *, informant_reversed: int | None = None):
    na = game.night_actions

    # شرطي
    if na.cop_target:
        cop_id, target_id = na.cop_target
        cop = game.get(cop_id)
        target = game.get(target_id)
        if cop and target:
            if cop_id in blocked:
                cop.pending_notices.append("💋 منعتك المضيفة من التحقيق.")
            else:
                is_reversed = informant_reversed == target_id
                is_mafia = target.role.team == "mafia" and target.role.name != "محتال"
                if target.role.name == "محتال":
                    verdict = "👤 **بريء**"
                elif is_mafia:
                    verdict = "❌ **بريء**" if is_reversed else "🔪 **مافيا**"
                else:
                    verdict = "🔪 **مافيا**" if is_reversed else "👤 **بريء**"
                cop.pending_notices.append(f"🚓 نتيجة التحقيق مع **{target.user.display_name}**: {verdict}")
                cop.cop_mafia_found += (1 if "مافيا" in verdict and target.role.team == "mafia" else 0)

    # جاسوسة
    if na.spy_target:
        spy_id, target_id = na.spy_target
        spy = game.get(spy_id)
        target = game.get(target_id)
        if spy and target:
            if spy_id in blocked:
                spy.pending_notices.append("💋 منعتك المضيفة من التجسس.")
            else:
                role_shown = target.forger_fake_role or target.role.name
                r_obj = ROLES.get(role_shown, target.role)
                spy.pending_notices.append(
                    f"🕵️‍♀️ دور **{target.user.display_name}** هو: {r_obj.emoji} **{role_shown}**"
                )
                spy.spy_reveal_count += 1
                for mate in game.alive_mafia():
                    if mate.user.id == spy_id:
                        continue
                    mate.pending_notices.append(
                        f"🕵️‍♀️ كشفت الجاسوسة دور **{target.user.display_name}**: {r_obj.emoji} **{role_shown}**"
                    )

    # عميل سري
    if na.secret_agent and game.day_count >= 2:
        agent = game.get(na.secret_agent)
        if agent:
            if na.secret_agent in blocked:
                agent.pending_notices.append("💋 منعتك المضيفة من جمع المعلومات.")
            else:
                citizens = [
                    p for p in game.alive_citizens()
                    if p.user.id != agent.user.id
                ]
                if citizens:
                    pick = random.choice(citizens)
                    role_shown = pick.forger_fake_role or pick.role.name
                    r_obj = ROLES.get(role_shown, pick.role)
                    agent.pending_notices.append(
                        f"🕴️ كشفت أن **{pick.user.display_name}** هو {r_obj.emoji} **{role_shown}**"
                    )
                else:
                    agent.pending_notices.append("🕴️ لا يوجد مواطن للكشف.")

    # عرافة
    if na.oracle_target:
        oracle_id, dead_id = na.oracle_target
        oracle = game.get(oracle_id)
        dead = game.get(dead_id)
        if oracle and dead:
            if oracle_id in blocked:
                oracle.pending_notices.append("💋 منعتك المضيفة من استجواب الموتى.")
            else:
                role_shown = dead.forger_fake_role or dead.role.name
                r_obj = ROLES.get(role_shown, dead.role)
                oracle.pending_notices.append(
                    f"🔮 **{dead.user.display_name}** كان دوره: {r_obj.emoji} **{role_shown}**"
                )


# ============================================================================
# النهار
# ============================================================================

async def run_day(game: MafiaGame):
    if game.check_winner():
        return

    vote_view = DayVoteView(game)
    vote_msg = await game.channel.send(
        embed=discord.Embed(
            title="🗳️ وقت التصويت!",
            description=(
                f"لديكم **{game.settings.vote_seconds} ثانية** للتصويت.\n"
                "السياسي صوته يُحسب مرتين.\n"
                "السفير يمنح حصانة مرة واحدة."
            ),
            color=discord.Color.red(),
        ),
        view=vote_view,
    )
    await asyncio.sleep(game.settings.vote_seconds)
    for c in vote_view.children:
        c.disabled = True
    try:
        await vote_msg.edit(view=vote_view)
    except discord.HTTPException:
        pass

    # حساب الأصوات
    counts: Counter[int] = Counter()
    for voter_id, target_id in game.day_votes.items():
        voter = game.get(voter_id)
        target = game.get(target_id)
        if not voter or not voter.alive or voter.blocked_from_voting_today:
            continue
        if not target or not target.alive:
            continue
        # محرّض
        if game.night_actions.inciter_voter == voter_id and game.night_actions.inciter_target:
            counts[game.night_actions.inciter_target] += 2 if voter.role.name == "سياسي" else 1
            continue
        weight = 2 if voter.role.name == "سياسي" else 1
        counts[target_id] += weight

    if not counts:
        return await game.channel.send("🤐 لم يصوّت أحد — لا إعدام اليوم.")

    breakdown = "\n".join(
        f"• {game.get(uid).user.mention}: **{n}** صوت"
        for uid, n in sorted(counts.items(), key=lambda x: -x[1])
        if game.get(uid)
    )
    top_target, top_votes = counts.most_common(1)[0]
    ties = [uid for uid, v in counts.items() if v == top_votes]

    if len(ties) > 1:
        return await game.channel.send(
            embed=discord.Embed(
                title="⚖️ تعادل!",
                description=f"تعادل — لا إعدام اليوم.\n\n{breakdown}",
                color=discord.Color.light_gray(),
            )
        )

    candidate = game.get(top_target)
    if not candidate:
        return

    # السياسي محمي
    if candidate.role.name == "سياسي":
        return await game.channel.send(
            embed=discord.Embed(
                title="🎩 لا، هذا شخص عادي!",
                description=f"حصل {candidate.user.mention} على أعلى الأصوات، لكنه شخص عادي ولا يُعدَم.\n\n{breakdown}",
                color=discord.Color.gold(),
            )
        )

    # السفير يحمي من الإعدام مرة
    if candidate.is_protected_by_ambassador:
        candidate.is_protected_by_ambassador = False
        return await game.channel.send(
            embed=discord.Embed(
                title="🤝 حصانة السفير!",
                description=f"{candidate.user.mention} محمي بحصانة السفير — نجا من الإعدام اليوم!\n\n{breakdown}",
                color=discord.Color.teal(),
            )
        )

    # مرحلة التأكيد
    confirm_state = {"approve": set(), "reject": set()}
    confirm_view = ConfirmExecutionView(game, candidate, confirm_state)
    confirm_msg = await game.channel.send(
        embed=discord.Embed(
            title=f"⚖️ تأكيد إعدام {candidate.user.display_name}",
            description=(
                f"حصل {candidate.user.mention} على أعلى الأصوات ({top_votes}).\n"
                f"لديكم **{game.settings.confirm_seconds} ثانية** للموافقة أو الاعتراض.\n"
                f"**عند التعادل: يُنفَّذ الإعدام.**\n\n{breakdown}"
            ),
            color=discord.Color.orange(),
        ),
        view=confirm_view,
    )
    await asyncio.sleep(game.settings.confirm_seconds)
    for c in confirm_view.children:
        c.disabled = True
    try:
        await confirm_msg.edit(view=confirm_view)
    except discord.HTTPException:
        pass

    a = len(confirm_state["approve"])
    r = len(confirm_state["reject"])

    if a < r:
        return await game.channel.send(
            embed=discord.Embed(
                title="❌ رُفض الإعدام",
                description=f"الاعتراضات ({r}) أكثر من الموافقات ({a}). نجا {candidate.user.mention}.",
                color=discord.Color.green(),
            )
        )

    # تنفيذ الإعدام
    candidate.alive = False
    candidate.killed_by = "vote"
    await mute_player(game, candidate.user.id, reason="أُعدم بالتصويت")

    role_text = f"\nكان دوره: {candidate.role.emoji} **{candidate.role.name}**" if game.settings.reveal_role_on_death else ""
    msg = f"تم إعدام {candidate.user.mention}.{role_text}\nموافقون: **{a}** | معترضون: **{r}**"

    # المجنون يفوز بإعدامه
    if candidate.role.name == "مجنون":
        msg += "\n\n🤡 **المجنون يضحك أخيراً! فاز!**"

    # انتقام الشهيد
    if candidate.role.name == "شهيد" and candidate.first_vote_against:
        martyr_target = game.get(candidate.first_vote_against)
        if martyr_target and martyr_target.alive:
            martyr_target.alive = False
            martyr_target.killed_by = "martyr"
            game.game_stats["martyr_triggered"] = True
            await mute_player(game, martyr_target.user.id, reason="انتقام الشهيد")
            msg += f"\n\n💀 **انتقام الشهيد:** أخذ معه {martyr_target.user.mention}!"

    exec_embed = discord.Embed(title="⚖️ تم الإعدام", description=msg, color=discord.Color.dark_red())
    await send_with_optional_image(game.channel, exec_embed, "execution")


# ============================================================================
# الأوامر
# ============================================================================

@bot.event
async def on_ready():
    log.info("✅ %s متصل وجاهز! (id=%s) | v%s", bot.user.name, bot.user.id, BOT_VERSION)
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="مافيا 42 | &مساعدة"
        )
    )


# ---- بدء اللعبة ----

@bot.command(name="مافيا", aliases=["mafia", "العبة"])
async def cmd_start(ctx: commands.Context, mode: str = ""):
    if not ctx.guild:
        return await ctx.send("هذا الأمر يعمل في السيرفرات فقط.")

    if not is_channel_allowed(ctx.guild.id, ctx.channel.id):
        allowed = get_allowed_channels(ctx.guild.id)
        if not allowed:
            return await ctx.send(
                "⚠️ **لم تُضف قناة للعبة بعد.**\n"
                "المسؤول يضيف قناة عبر: `&اضافه_قناة <ID>`"
            )
        return await ctx.send(
            "❌ **هذه القناة غير مضافة للعبة.**\n"
            "اطلب من المسؤول إضافتها، أو انظر: `&قنوات`"
        )

    key = game_key(ctx.guild.id, ctx.channel.id)
    if key in games:
        return await ctx.send("توجد لعبة جارية بالفعل. استخدم `&إنهاء` لإيقافها.")

    # تحديد الوضع
    game_mode = MODE_NORMAL
    if mode in ("سريع", "fast", "f"):
        game_mode = MODE_FAST
    elif mode in ("مصنف", "ranked", "r"):
        game_mode = MODE_RANKED

    game = MafiaGame(ctx.guild, ctx.channel, mode=game_mode)
    game.host_id = ctx.author.id
    games[key] = game
    game.add_lobby_player(ctx.author.id)

    async def on_start(interaction: discord.Interaction):
        game.phase_task = asyncio.create_task(run_game(game))

    async def on_cancel(interaction: discord.Interaction):
        games.pop(key, None)
        await ctx.send("🛑 تم إلغاء غرفة الانتظار.")

    view = LobbyView(game, ctx.author.id, on_start, on_cancel)
    embed = view._build_embed()

    lobby_img = Path(__file__).parent / "attached_assets" / "mafia_lobby.png"
    if lobby_img.exists():
        f = discord.File(str(lobby_img), filename="mafia_lobby.png")
        game.lobby_message = await ctx.send(embed=embed, view=view, file=f)
    else:
        game.lobby_message = await ctx.send(embed=embed, view=view)


# ---- إنهاء اللعبة ----

@bot.command(name="إنهاء", aliases=["انهاء", "end", "stop"])
async def cmd_end(ctx: commands.Context):
    if not ctx.guild:
        return
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.pop(key, None)
    if not game:
        return await ctx.send("لا توجد لعبة جارية في هذه القناة.")

    # التحقق من الصلاحية (المنشئ أو المسؤول)
    is_host = ctx.author.id == game.host_id
    is_admin = ctx.author.guild_permissions.administrator
    if not is_host and not is_admin:
        return await ctx.send("❌ فقط المنشئ أو المسؤول يستطيع إنهاء اللعبة.")

    if game.phase_task and not game.phase_task.done():
        game.phase_task.cancel()
    try:
        await restore_all_perms(game)
    except Exception:
        pass
    await ctx.send("🛑 تم إنهاء اللعبة.")


# ---- همس المافيا ----

@bot.command(name="همس", aliases=["whisper", "w"])
async def cmd_whisper(ctx: commands.Context, *, message: str = ""):
    if not ctx.guild or not message:
        return
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة جارية.", delete_after=5)

    player = game.get(ctx.author.id)
    if not player:
        return await ctx.send("لست في اللعبة.", delete_after=5)
    if not player.alive:
        return await ctx.send("أنت ميت.", delete_after=5)

    team = game.mafia_team_members(alive_only=True)
    if player not in team:
        return await ctx.send("هذا الأمر للمافيا فقط.", delete_after=5)

    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass

    for mate in team:
        if mate.user.id == player.user.id:
            continue
        try:
            dm = await mate.user.create_dm()
            await dm.send(
                f"🔪 **رسالة سرية من {player.user.display_name}:**\n{message}"
            )
        except discord.HTTPException:
            pass

    await ctx.send(
        f"🔪 **رسالة سرية من {player.user.mention}:** (وصلت لزملائك في المافيا)",
        delete_after=10,
    )


# ---- وريث رئيس المافيا ----

@bot.command(name="وريث")
async def cmd_heir(ctx: commands.Context, member: discord.Member | None = None):
    if not ctx.guild:
        return
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة.", delete_after=5)

    player = game.get(ctx.author.id)
    if not player or player.role.name != "رئيس المافيا":
        return await ctx.send("هذا الأمر لرئيس المافيا فقط.", delete_after=5)

    if member is None:
        return await ctx.send("استخدم: `&وريث @لاعب`", delete_after=5)

    target = game.get(member.id)
    if not target or not target.alive:
        return await ctx.send("اللاعب غير موجود أو ميت.", delete_after=5)
    if target.role.team != "mafia":
        return await ctx.send("يجب أن يكون الوريث من فريق المافيا.", delete_after=5)

    player.heir_id = member.id
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass
    await ctx.send(f"👑 تم تعيين وريثك سراً.", ephemeral=True, delete_after=5)


# ---- الأوامر الإدارية ----

@bot.command(name="اضافه_قناة", aliases=["إضافه_قناة", "إضافة_قناة", "اضافة_قناة", "add_channel"])
@commands.has_permissions(administrator=True)
async def cmd_add_channel(ctx: commands.Context, channel_id: int | None = None):
    if not ctx.guild:
        return
    if channel_id is None:
        return await ctx.send("استخدم: `&اضافه_قناة <ID>`")
    channel = ctx.guild.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        return await ctx.send("❌ القناة غير موجودة أو ليست نصية.")
    if add_allowed_channel(ctx.guild.id, channel_id):
        await ctx.send(f"✅ تم إضافة {channel.mention} لقنوات المافيا.")
    else:
        await ctx.send(f"⚠️ {channel.mention} موجودة بالفعل.")


@cmd_add_channel.error
async def cmd_add_channel_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ للمسؤولين فقط.")


@bot.command(name="حذف_قناة", aliases=["remove_channel"])
@commands.has_permissions(administrator=True)
async def cmd_remove_channel(ctx: commands.Context, channel_id: int | None = None):
    if not ctx.guild:
        return
    if channel_id is None:
        return await ctx.send("استخدم: `&حذف_قناة <ID>`")
    if remove_allowed_channel(ctx.guild.id, channel_id):
        ch = ctx.guild.get_channel(channel_id)
        name = ch.mention if ch else f"`{channel_id}`"
        await ctx.send(f"✅ تم حذف {name} من قنوات المافيا.")
    else:
        await ctx.send("⚠️ هذه القناة ليست في القائمة.")


@cmd_remove_channel.error
async def cmd_remove_channel_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ للمسؤولين فقط.")


@bot.command(name="قنوات_اللعبة", aliases=["قنوات", "channels"])
async def cmd_list_channels(ctx: commands.Context):
    if not ctx.guild:
        return
    chans = get_allowed_channels(ctx.guild.id)
    if not chans:
        return await ctx.send("لا توجد قنوات مضافة.")
    lines = [f"• {ctx.guild.get_channel(cid).mention if ctx.guild.get_channel(cid) else f'`{cid}`'}" for cid in chans]
    await ctx.send(embed=discord.Embed(
        title="🎮 قنوات لعبة المافيا",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    ))


@bot.command(name="حالة", aliases=["status"])
async def cmd_status(ctx: commands.Context):
    if not ctx.guild:
        return
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة جارية.")

    alive = game.alive_players()
    dead = game.dead_players()
    phase_names = {
        "waiting": "⏳ انتظار",
        "night": "🌙 ليل",
        "day": "☀️ نهار",
        "ended": "🏁 انتهت",
    }

    embed = discord.Embed(
        title="📊 حالة اللعبة",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="المرحلة", value=phase_names.get(game.phase, game.phase), inline=True)
    embed.add_field(name="الليلة", value=str(game.day_count), inline=True)
    embed.add_field(name="الوقت المنقضي", value=game.elapsed_time(), inline=True)
    embed.add_field(name=f"أحياء ({len(alive)})", value=" • ".join(p.user.mention for p in alive) or "—", inline=False)
    if dead:
        reveal = game.settings.reveal_role_on_death
        dead_text = "\n".join(
            f"💀 {p.user.display_name}" + (f" ({p.role.emoji} {p.role.name})" if reveal else "")
            for p in dead
        )
        embed.add_field(name=f"أموات ({len(dead)})", value=dead_text, inline=False)
    await ctx.send(embed=embed)


# ---- نقاط وإحصائيات ----

@bot.command(name="نقاط", aliases=["points", "score"])
async def cmd_points(ctx: commands.Context, member: discord.Member | None = None):
    target = member or ctx.author
    pts = ensure_rank(target.id)
    rank = get_rank_title(pts)
    stats = get_stats(target.id)
    achs = get_player_achievements(target.id)

    embed = discord.Embed(
        title=f"🏅 ملف اللاعب: {target.display_name}",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="💎 النقاط", value=f"**{pts:,}**", inline=True)
    embed.add_field(name="🏆 الرتبة", value=rank, inline=True)
    embed.add_field(name="🎮 الألعاب", value=str(stats.get("games_played", 0)), inline=True)
    embed.add_field(name="✅ انتصارات", value=str(stats.get("wins", 0)), inline=True)
    embed.add_field(name="❌ هزائم", value=str(stats.get("losses", 0)), inline=True)
    win_rate = round(stats.get("wins", 0) / max(stats.get("games_played", 1), 1) * 100)
    embed.add_field(name="📊 نسبة الفوز", value=f"{win_rate}%", inline=True)
    embed.add_field(name="🔥 أطول سلسلة", value=str(stats.get("max_win_streak", 0)), inline=True)
    embed.add_field(name="🏅 الإنجازات", value=str(len(achs)), inline=True)

    # أكثر دور لعبه
    roles_played = stats.get("roles_played", {})
    if roles_played:
        top_role = max(roles_played, key=roles_played.get)
        r = ROLES.get(top_role)
        embed.add_field(
            name="⭐ أكثر دور لعبه",
            value=f"{r.emoji if r else ''} {top_role} ({roles_played[top_role]} مرة)",
            inline=False,
        )

    await ctx.send(embed=embed)


@bot.command(name="تصنيف", aliases=["leaderboard", "top"])
async def cmd_leaderboard(ctx: commands.Context):
    ranks = _load_ranks()
    if not ranks:
        return await ctx.send("لا يوجد لاعبون مسجلون.")
    sorted_ranks = sorted(ranks.items(), key=lambda x: -x[1])[:10]
    medals = ["🥇", "🥈", "🥉"] + [f"**{i}.**" for i in range(4, 11)]
    lines = []
    for i, (uid, pts) in enumerate(sorted_ranks):
        rank_title = get_rank_title(pts)
        lines.append(f"{medals[i]} <@{uid}>: **{pts:,}** | {rank_title}")

    embed = discord.Embed(
        title="🏆 أفضل اللاعبين",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"مافيا 42 v{BOT_VERSION}")
    await ctx.send(embed=embed)


@bot.command(name="إحصائيات", aliases=["احصائيات", "stats"])
async def cmd_stats(ctx: commands.Context, member: discord.Member | None = None):
    target = member or ctx.author
    stats = get_stats(target.id)

    embed = discord.Embed(
        title=f"📊 إحصائيات {target.display_name}",
        color=discord.Color.blue(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)

    # الأدوار الأكثر
    roles_played = stats.get("roles_played", {})
    roles_text = "\n".join(
        f"• {ROLES.get(r, Role(r,'','','⬜',False)).emoji} {r}: {n}"
        for r, n in sorted(roles_played.items(), key=lambda x: -x[1])[:5]
    ) or "لا يوجد"

    embed.add_field(name="🎮 مجموع الألعاب", value=str(stats.get("games_played", 0)), inline=True)
    embed.add_field(name="✅ مافيا انتصارات", value=f"{stats.get('wins_as_mafia', 0)}/{stats.get('games_as_mafia', 0)}", inline=True)
    embed.add_field(name="✅ مواطن انتصارات", value=f"{stats.get('wins_as_citizen', 0)}/{stats.get('games_as_citizen', 0)}", inline=True)
    embed.add_field(name="🦺 مرات البقاء", value=str(stats.get("times_survived", 0)), inline=True)
    embed.add_field(name="🔥 أطول سلسلة", value=str(stats.get("max_win_streak", 0)), inline=True)
    embed.add_field(name="⭐ أكثر الأدوار", value=roles_text, inline=False)
    await ctx.send(embed=embed)


@bot.command(name="إنجازاتي", aliases=["انجازاتي", "achievements"])
async def cmd_achievements(ctx: commands.Context, member: discord.Member | None = None):
    target = member or ctx.author
    ach_ids = get_player_achievements(target.id)

    if not ach_ids:
        return await ctx.send(f"**{target.display_name}** لا يملك إنجازات بعد.")

    achs = [ACHIEVEMENTS[aid] for aid in ach_ids if aid in ACHIEVEMENTS]
    lines = [f"{a.emoji} **{a.name}** — {a.description}" for a in achs]

    embed = discord.Embed(
        title=f"🏆 إنجازات {target.display_name} ({len(achs)}/{len(ACHIEVEMENTS)})",
        description="\n".join(lines[:20]),
        color=discord.Color.gold(),
    )
    await ctx.send(embed=embed)


@bot.command(name="كل_الإنجازات", aliases=["all_achievements"])
async def cmd_all_achievements(ctx: commands.Context):
    categories = {
        "فوز": [],
        "أدوار": [],
        "ألعاب": [],
    }
    for ach in ACHIEVEMENTS.values():
        if "win" in ach.id or "ناجي" in ach.name or "انتصار" in ach.name:
            categories["فوز"].append(ach)
        elif any(r in ach.id for r in ["mafia","citizen","detective","saved","jester","killer","guardian","martyr","sniper","priest","soldier","forger","spy"]):
            categories["أدوار"].append(ach)
        else:
            categories["ألعاب"].append(ach)

    embed = discord.Embed(
        title=f"📚 كل الإنجازات ({len(ACHIEVEMENTS)} إنجاز)",
        color=discord.Color.purple(),
    )
    for cat, items in categories.items():
        if items:
            embed.add_field(
                name=f"🔷 {cat}",
                value="\n".join(f"{a.emoji} {a.name} (+{a.points})" for a in items),
                inline=False,
            )
    await ctx.send(embed=embed)


# ---- الأدوار ----

@bot.command(name="ادوار", aliases=["أدوار", "roles"])
async def cmd_roles(ctx: commands.Context):
    mafia_roles = [(n, r) for n, r in ROLES.items() if r.team == "mafia"]
    citizen_roles = [(n, r) for n, r in ROLES.items() if r.team == "citizens"]
    neutral_roles = [(n, r) for n, r in ROLES.items() if r.team in ("neutral", "killer")]

    embed = discord.Embed(title="📖 أدوار مافيا 42", color=discord.Color.dark_red())

    mafia_text = "\n".join(f"{r.emoji} **{n}** {'⭐' if r.rarity=='legendary' else '🔵' if r.rarity=='rare' else ''}" for n, r in mafia_roles)
    citizen_text = "\n".join(f"{r.emoji} **{n}** {'⭐' if r.rarity=='legendary' else '🔵' if r.rarity=='rare' else ''}" for n, r in citizen_roles)
    neutral_text = "\n".join(f"{r.emoji} **{n}** ⭐" for n, r in neutral_roles)

    embed.add_field(name="🔴 المافيا", value=mafia_text, inline=True)
    embed.add_field(name="🟢 المواطنون", value=citizen_text, inline=True)
    embed.add_field(name="🟣 محايدون", value=neutral_text or "—", inline=True)
    embed.set_footer(text="⚪شائع 🔵نادر ⭐أسطوري | استخدم &دور <اسم> لمعرفة التفاصيل")
    await ctx.send(embed=embed)


@bot.command(name="دور", aliases=["role"])
async def cmd_role_info(ctx: commands.Context, *, role_name: str = ""):
    if not role_name:
        return await ctx.send("استخدم: `&دور <اسم الدور>`\nاستخدم `&أدوار` لرؤية كل الأدوار.")

    # بحث مرن
    found = None
    for name, role in ROLES.items():
        if role_name.strip() in name or name in role_name.strip():
            found = role
            break
    if not found:
        return await ctx.send(f"لم أجد دوراً باسم **{role_name}**. استخدم `&أدوار` لرؤية الأدوار المتاحة.")

    embed = build_role_embed(found)
    img_path = role_image_path(found.name)
    if img_path.exists():
        f = discord.File(str(img_path), filename=img_path.name)
        embed.set_image(url=f"attachment://{img_path.name}")
        await ctx.send(embed=embed, file=f)
    else:
        await ctx.send(embed=embed)


# ---- المساعدة ----

@bot.command(name="مساعدة", aliases=["help", "h", "مساعده"])
async def cmd_help(ctx: commands.Context, section: str = ""):
    if section in ("أدوار", "ادوار", "roles"):
        return await cmd_roles(ctx)

    embed = discord.Embed(
        title=f"📚 مساعدة مافيا 42 — النسخة {BOT_VERSION}",
        description="بوت لعبة المافيا الاحترافية.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🎮 أوامر اللعبة",
        value=(
            "`&مافيا` — بدء لعبة عادية\n"
            "`&مافيا سريع` — وضع سريع ⚡\n"
            "`&إنهاء` — إنهاء اللعبة الجارية\n"
            "`&همس <رسالة>` — رسالة سرية للمافيا 🔪\n"
            "`&وريث @لاعب` — تعيين وريث (رئيس المافيا)\n"
            "`&حالة` — حالة اللعبة الحالية\n"
            "`&وقت` — المرحلة الحالية للعبة"
        ),
        inline=False,
    )
    embed.add_field(
        name="👤 أوامر اللاعب",
        value=(
            "`&نقاط [@لاعب]` — عرض النقاط والرتبة\n"
            "`&رتبي` — رتبتك مع شريط التقدم\n"
            "`&إحصائيات [@لاعب]` — إحصائيات مفصلة\n"
            "`&إنجازاتي [@لاعب]` — الإنجازات المكتسبة\n"
            "`&تصنيف` — أفضل 10 لاعبين\n"
            "`&دوري` — تذكير بدورك سراً\n"
            "`&فريقي` — عرض فريق المافيا\n"
            "`&اعترف` — كشف دورك بعد الموت 👻\n"
            "`&مكافأة` — مكافأة يومية 🎁\n"
            "`&تبرع @لاعب <كمية>` — تحويل نقاط\n"
            "`&مقارنة @ل1 @ل2` — مقارنة لاعبَين"
        ),
        inline=False,
    )
    embed.add_field(
        name="📖 معلومات ومتعة",
        value=(
            "`&أدوار` — كل الأدوار المتاحة\n"
            "`&دور <اسم>` — تفاصيل دور معين\n"
            "`&أدوار_الفريق مافيا|مواطنون|محايد` — أدوار فريق\n"
            "`&كل_الإنجازات` — قائمة الإنجازات\n"
            "`&مسابقة` — مسابقة سريعة (+50 نقطة) 🎯\n"
            "`&نصيحة` — نصيحة عشوائية\n"
            "`&سجل_الألعاب` — سجل الألعاب الأخيرة\n"
            "`&إحصائيات_السيرفر` — إحصائيات السيرفر\n"
            "`&لوحة_الشرف` — أكثر اللاعبين فوزاً\n"
            "`&سرعة_اللعبة` — أوقات اللعبة الحالية"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏆 البطولات",
        value=(
            "`&بطولة [اسم]` — إنشاء بطولة (مسؤول)\n"
            "`&نتائج_البطولة` — عرض بطولة نشطة\n"
            "`&إنهاء_بطولة` — إنهاء البطولة (مسؤول)"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔧 أوامر المسؤول",
        value=(
            "`&اضافه_قناة <ID>` — إضافة قناة للعبة\n"
            "`&حذف_قناة <ID>` — حذف قناة\n"
            "`&قنوات` — عرض القنوات المضافة\n"
            "`&ريست_نقاط` — إعادة ضبط نقاط الكل\n"
            "`&اعطاء_نقاط @لاعب <كمية>` — إعطاء نقاط\n"
            "`&حذف_نقاط @لاعب <كمية>` — حذف نقاط\n"
            "`&طرد_من_اللعبة @لاعب` — طرد لاعب\n"
            "`&فرض_دور @لاعب <دور>` — تغيير دور\n"
            "`&كشف_أدوار` — كشف أدوار الجميع\n"
            "`&تذكير` — تذكير اللاعبين في الردهة\n"
            "`&إعادة_ضبط_يومي [@لاعب]` — إعادة مكافأة يومية\n"
            "`&اعلان <نص>` — إرسال إعلان\n"
            "`&بوت` — معلومات البوت"
        ),
        inline=False,
    )
    embed.set_footer(text=f"مافيا 42 v{BOT_VERSION} | البادئة: & | {len(ROLES)} دور | {len(ACHIEVEMENTS)} إنجاز")
    await ctx.send(embed=embed)


# ---- إعادة ضبط النقاط ----

@bot.command(name="ريست_نقاط", aliases=["reset_ranks"])
@commands.has_permissions(administrator=True)
async def cmd_reset_ranks(ctx: commands.Context):
    _save_ranks({})
    _save_json(STATS_FILE, {})
    await ctx.send("✅ تم إعادة ضبط جميع النقاط والإحصائيات.")


@cmd_reset_ranks.error
async def cmd_reset_ranks_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ للمسؤولين فقط.")


# ---- إعطاء نقاط يدوياً ----

@bot.command(name="اعطاء_نقاط", aliases=["give_points"])
@commands.has_permissions(administrator=True)
async def cmd_give_points(ctx: commands.Context, member: discord.Member | None = None, amount: int = 0):
    if not member or amount == 0:
        return await ctx.send("استخدم: `&اعطاء_نقاط @لاعب <كمية>`")
    ranks = _load_ranks()
    key = str(member.id)
    ranks[key] = max(0, ranks.get(key, INITIAL_POINTS) + amount)
    _save_ranks(ranks)
    sign = "+" if amount >= 0 else ""
    await ctx.send(f"✅ {member.mention}: {sign}{amount} نقطة → **{ranks[key]:,}**")


# ---- حذف نقاط ----

@bot.command(name="حذف_نقاط", aliases=["remove_points"])
@commands.has_permissions(administrator=True)
async def cmd_remove_points(ctx: commands.Context, member: discord.Member | None = None, amount: int = 0):
    if not member or amount == 0:
        return await ctx.send("استخدم: `&حذف_نقاط @لاعب <كمية>`")
    ranks = _load_ranks()
    key = str(member.id)
    ranks[key] = max(0, ranks.get(key, INITIAL_POINTS) - amount)
    _save_ranks(ranks)
    await ctx.send(f"✅ {member.mention}: -{amount} نقطة → **{ranks[key]:,}**")


# ---- نصائح اللعبة ----

GAME_TIPS = [
    "💡 كشرطي، لا تعلن عن دورك قبل أن يكون لديك معلومات كافية.",
    "💡 المافيا: حاول الكلام مثل مواطن عادي — لا تبدو متعاطفاً مع مشبوه.",
    "💡 الطبيب: لا تحمي نفسك كل ليلة — المافيا قد تتجنبك.",
    "💡 الحارسة: لا تحمي نفس الشخص كل ليلة — غيّر أهدافك.",
    "💡 الشهيد: لا تدافع عن نفسك كثيراً — اجعلهم يصوّتون ضدك.",
    "💡 كمواطن عادي: راقب من يدافع عن المشبوهين.",
    "💡 القنّاص: لا تطلق رصاصتك باكراً — انتظر حتى تتأكد.",
    "💡 المجنون: كن مثيراً للجدل لكن لا تكن واضحاً جداً.",
]


@bot.command(name="نصيحة", aliases=["tip"])
async def cmd_tip(ctx: commands.Context):
    await ctx.send(random.choice(GAME_TIPS))


# ---- بث إعلان ----

@bot.command(name="اعلان", aliases=["announce"])
@commands.has_permissions(administrator=True)
async def cmd_announce(ctx: commands.Context, *, message: str = ""):
    if not message:
        return await ctx.send("استخدم: `&اعلان <رسالة>`")
    embed = discord.Embed(
        title="📢 إعلان مافيا 42",
        description=message,
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"من: {ctx.author.display_name}")
    await ctx.send(embed=embed)


# ---- معلومات البوت ----

@bot.command(name="بوت", aliases=["botinfo", "about"])
async def cmd_bot_info(ctx: commands.Context):
    embed = discord.Embed(
        title=f"🤖 مافيا 42 — النسخة {BOT_VERSION}",
        description=(
            "بوت لعبة المافيا الاحترافي مع 30+ دور، نظام إنجازات، وإحصائيات مفصّلة."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="🎮 الأدوار", value=f"{len(ROLES)} دور", inline=True)
    embed.add_field(name="🏆 الإنجازات", value=f"{len(ACHIEVEMENTS)} إنجاز", inline=True)
    embed.add_field(name="🏠 السيرفرات", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="🎯 الألعاب النشطة", value=str(len(games)), inline=True)
    embed.add_field(name="البادئة", value="`&`", inline=True)
    embed.set_footer(text="مافيا 42 — ألعب وافز!")
    await ctx.send(embed=embed)


# ============================================================================
# نظام البطولة (Tournament System)
# ============================================================================

TOURNAMENTS_FILE = Path("mafia_tournaments.json")


def _load_tournaments() -> dict:
    return _load_json(TOURNAMENTS_FILE)


def _save_tournaments(data: dict) -> None:
    _save_json(TOURNAMENTS_FILE, data)


@dataclass
class Tournament:
    id: str
    name: str
    guild_id: int
    channel_id: int
    host_id: int
    participants: list[int] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)  # [{winner_id, loser_id, ...}]
    current_round: int = 0
    status: str = "registration"  # registration | active | ended
    winner_id: int | None = None
    created_at: float = field(default_factory=time.time)


# active tournaments: guild_id -> Tournament
_active_tournaments: dict[int, Tournament] = {}


class TournamentLobbyView(discord.ui.View):
    def __init__(self, tournament: Tournament):
        super().__init__(timeout=600)
        self.tournament = tournament

    @discord.ui.button(label="تسجيل في البطولة", style=discord.ButtonStyle.green, emoji="✅")
    async def register(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in self.tournament.participants:
            return await interaction.response.send_message("أنت مسجّل بالفعل.", ephemeral=True)
        self.tournament.participants.append(uid)
        await interaction.response.send_message(
            f"✅ تم تسجيلك في البطولة! المسجّلون: {len(self.tournament.participants)}",
            ephemeral=True,
        )

    @discord.ui.button(label="إلغاء التسجيل", style=discord.ButtonStyle.red, emoji="🚪")
    async def unregister(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid not in self.tournament.participants:
            return await interaction.response.send_message("لست مسجّلاً.", ephemeral=True)
        self.tournament.participants.remove(uid)
        await interaction.response.send_message("تم إلغاء تسجيلك.", ephemeral=True)

    @discord.ui.button(label="قائمة المسجّلين", style=discord.ButtonStyle.gray, emoji="📋")
    async def list_participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.tournament.participants:
            return await interaction.response.send_message("لا يوجد مسجّلون بعد.", ephemeral=True)
        names = "\n".join(f"• <@{uid}>" for uid in self.tournament.participants)
        await interaction.response.send_message(
            f"📋 **المسجّلون ({len(self.tournament.participants)}):**\n{names}",
            ephemeral=True,
        )


@bot.command(name="بطولة", aliases=["tournament"])
@commands.has_permissions(administrator=True)
async def cmd_tournament(ctx: commands.Context, *, name: str = "بطولة مافيا 42"):
    if not ctx.guild:
        return
    if ctx.guild.id in _active_tournaments:
        return await ctx.send("⚠️ توجد بطولة نشطة بالفعل. استخدم `&إنهاء_بطولة` لإنهائها.")

    t = Tournament(
        id=f"{ctx.guild.id}_{int(time.time())}",
        name=name,
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        host_id=ctx.author.id,
    )
    _active_tournaments[ctx.guild.id] = t

    view = TournamentLobbyView(t)
    embed = discord.Embed(
        title=f"🏆 بطولة مافيا 42: {name}",
        description=(
            f"👑 **المنظّم:** {ctx.author.mention}\n\n"
            "سجّل الآن للمشاركة في البطولة!\n"
            "يحتاج الفوز في البطولة لنقاط إضافية كبيرة.\n\n"
            "**الجوائز:**\n"
            "🥇 الأول: +2000 نقطة\n"
            "🥈 الثاني: +1000 نقطة\n"
            "🥉 الثالث: +500 نقطة"
        ),
        color=discord.Color.gold(),
    )
    await ctx.send(embed=embed, view=view)


@bot.command(name="إنهاء_بطولة", aliases=["end_tournament"])
@commands.has_permissions(administrator=True)
async def cmd_end_tournament(ctx: commands.Context):
    if not ctx.guild or ctx.guild.id not in _active_tournaments:
        return await ctx.send("لا توجد بطولة نشطة.")
    t = _active_tournaments.pop(ctx.guild.id)
    await ctx.send(f"🛑 تم إنهاء بطولة **{t.name}** مع {len(t.participants)} مشارك.")


@bot.command(name="نتائج_البطولة", aliases=["tournament_results"])
async def cmd_tournament_results(ctx: commands.Context):
    if not ctx.guild or ctx.guild.id not in _active_tournaments:
        return await ctx.send("لا توجد بطولة نشطة.")
    t = _active_tournaments[ctx.guild.id]
    embed = discord.Embed(
        title=f"🏆 بطولة: {t.name}",
        color=discord.Color.gold(),
    )
    embed.add_field(name="📊 الحالة", value=t.status, inline=True)
    embed.add_field(name="👥 المشاركون", value=str(len(t.participants)), inline=True)
    embed.add_field(name="🔄 الجولات", value=str(t.current_round), inline=True)

    if t.participants:
        embed.add_field(
            name="📋 المشاركون",
            value="\n".join(f"• <@{uid}>" for uid in t.participants[:20]),
            inline=False,
        )
    await ctx.send(embed=embed)


# ============================================================================
# نظام سجل الألعاب (Game History)
# ============================================================================

HISTORY_FILE = Path("mafia_history.json")


def save_game_to_history(game: MafiaGame, winner: str) -> None:
    history = _load_json(HISTORY_FILE) if HISTORY_FILE.exists() else []
    if not isinstance(history, list):
        history = []

    record = {
        "timestamp": time.time(),
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "guild_id": game.guild.id,
        "channel_id": game.channel.id,
        "mode": game.mode,
        "winner": winner,
        "nights": game.day_count,
        "duration_seconds": int(time.time() - game.start_time),
        "players": [
            {
                "id": p.user.id,
                "name": p.user.display_name,
                "role": p.role.name,
                "team": p.role.team,
                "survived": p.alive,
                "killed_by": p.killed_by,
            }
            for p in game.players.values()
        ],
    }
    history.append(record)
    # احتفظ بأحدث 500 لعبة فقط
    if len(history) > 500:
        history = history[-500:]
    _save_json(HISTORY_FILE, history)


@bot.command(name="سجل_الألعاب", aliases=["history", "سجل"])
async def cmd_history(ctx: commands.Context):
    history = _load_json(HISTORY_FILE) if HISTORY_FILE.exists() else []
    if not isinstance(history, list) or not history:
        return await ctx.send("لا يوجد سجل ألعاب بعد.")

    # الألعاب الأخيرة في هذا السيرفر
    guild_games = [g for g in history if g.get("guild_id") == ctx.guild.id][-10:]
    if not guild_games:
        return await ctx.send("لا يوجد سجل ألعاب في هذا السيرفر.")

    lines = []
    for g in reversed(guild_games):
        winner_text = {"citizens": "🟢 مواطنون", "mafia": "🔴 مافيا", "jester": "🤡 مجنون"}.get(g.get("winner", ""), "؟")
        duration = f"{g.get('duration_seconds', 0)//60}د"
        lines.append(
            f"**{g.get('date', '؟')}** — {winner_text} | {g.get('nights', 0)} ليالٍ | {len(g.get('players', []))} لاعب | {duration}"
        )

    embed = discord.Embed(
        title=f"📜 سجل آخر {len(guild_games)} ألعاب",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"مجموع الألعاب: {len(guild_games)}")
    await ctx.send(embed=embed)


@bot.command(name="إحصائيات_السيرفر", aliases=["server_stats"])
async def cmd_server_stats(ctx: commands.Context):
    history = _load_json(HISTORY_FILE) if HISTORY_FILE.exists() else []
    if not isinstance(history, list):
        history = []

    guild_games = [g for g in history if g.get("guild_id") == ctx.guild.id]
    if not guild_games:
        return await ctx.send("لا يوجد بيانات بعد.")

    total = len(guild_games)
    citizen_wins = sum(1 for g in guild_games if g.get("winner") == "citizens")
    mafia_wins = sum(1 for g in guild_games if g.get("winner") == "mafia")
    jester_wins = sum(1 for g in guild_games if g.get("winner") == "jester")
    avg_nights = sum(g.get("nights", 0) for g in guild_games) / max(total, 1)
    avg_duration = sum(g.get("duration_seconds", 0) for g in guild_games) / max(total, 1)

    embed = discord.Embed(
        title=f"📊 إحصائيات السيرفر: {ctx.guild.name}",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="🎮 مجموع الألعاب", value=str(total), inline=True)
    embed.add_field(name="🟢 انتصارات المواطنين", value=f"{citizen_wins} ({round(citizen_wins/max(total,1)*100)}%)", inline=True)
    embed.add_field(name="🔴 انتصارات المافيا", value=f"{mafia_wins} ({round(mafia_wins/max(total,1)*100)}%)", inline=True)
    embed.add_field(name="🤡 انتصارات المجنون", value=str(jester_wins), inline=True)
    embed.add_field(name="🌙 متوسط الليالي", value=f"{avg_nights:.1f}", inline=True)
    embed.add_field(name="⏱️ متوسط المدة", value=f"{int(avg_duration//60)}د {int(avg_duration%60)}ث", inline=True)
    await ctx.send(embed=embed)


# ============================================================================
# نظام الاعتراف (Confession)
# ============================================================================

@bot.command(name="اعترف", aliases=["confess"])
async def cmd_confess(ctx: commands.Context):
    """اللاعب الميت يكشف دوره طوعياً."""
    if not ctx.guild:
        return
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة جارية.", delete_after=5)

    player = game.get(ctx.author.id)
    if not player:
        return await ctx.send("لست في اللعبة.", delete_after=5)
    if player.alive:
        return await ctx.send("لا يمكنك الاعتراف وأنت حي! انتظر حتى تموت.", delete_after=5)

    embed = discord.Embed(
        title=f"👻 اعتراف {player.user.display_name}",
        description=(
            f"كنت دور: {player.role.emoji} **{player.role.name}**\n"
            f"الفريق: {'🔴 مافيا' if player.role.team == 'mafia' else '🟢 مواطنون' if player.role.team == 'citizens' else '🟣 محايد'}\n"
            f"قُتل بسبب: **{player.killed_by or 'غير معروف'}**"
        ),
        color=ROLE_COLORS.get(player.role.team, discord.Color.gray()),
    )
    await ctx.send(embed=embed)


# ============================================================================
# نظام المكافأة اليومية (Daily Bonus)
# ============================================================================

DAILY_FILE = Path("mafia_daily.json")
DAILY_BONUS_BASE = 200
DAILY_BONUS_STREAK_EXTRA = 50  # إضافي لكل يوم متواصل


def _load_daily() -> dict:
    return _load_json(DAILY_FILE)


def _save_daily(data: dict) -> None:
    _save_json(DAILY_FILE, data)


@bot.command(name="مكافأة", aliases=["daily", "bonus"])
async def cmd_daily(ctx: commands.Context):
    uid = str(ctx.author.id)
    data = _load_daily()
    now = time.time()

    if uid not in data:
        data[uid] = {"last_claim": 0, "streak": 0}

    last = data[uid]["last_claim"]
    streak = data[uid]["streak"]

    time_since = now - last
    # 20 ساعة كحد أدنى بين المكافآت
    if time_since < 20 * 3600:
        remaining = int(20 * 3600 - time_since)
        h, m = divmod(remaining // 60, 60)
        return await ctx.send(
            f"⏳ يمكنك المطالبة بمكافأتك اليومية بعد **{h}س {m}د**.",
            delete_after=15,
        )

    # تحقق من الاستمرارية (48 ساعة كحد أقصى للحفاظ على السلسلة)
    if time_since > 48 * 3600:
        streak = 0

    streak += 1
    bonus = DAILY_BONUS_BASE + min(streak - 1, 30) * DAILY_BONUS_STREAK_EXTRA

    data[uid] = {"last_claim": now, "streak": streak}
    _save_daily(data)

    ranks = _load_ranks()
    ranks[uid] = ranks.get(uid, INITIAL_POINTS) + bonus
    _save_ranks(ranks)

    embed = discord.Embed(
        title="🎁 مكافأة يومية!",
        description=(
            f"حصلت على **+{bonus}** نقطة!\n"
            f"🔥 السلسلة: {streak} يوم متواصل\n"
            f"💎 مجموع نقاطك: **{ranks[uid]:,}**"
        ),
        color=discord.Color.gold(),
    )
    if streak >= 7:
        embed.add_field(name="🌟 مكافأة الأسبوع!", value=f"استمررت {streak} أيام متواصلة!", inline=False)
    await ctx.send(embed=embed)


# ============================================================================
# نظام التصويت على الأدوار (Role Vote / Poll)
# ============================================================================

class RolePollView(discord.ui.View):
    """تصويت على أدوار اللعبة القادمة."""
    def __init__(self, roles_list: list[str], host_id: int):
        super().__init__(timeout=120)
        self.votes: dict[str, set[int]] = {r: set() for r in roles_list}
        self.host_id = host_id

        for role_name in roles_list[:5]:  # أقصى 5 أدوار
            r = ROLES.get(role_name)
            if not r:
                continue
            btn = discord.ui.Button(
                label=f"{r.emoji} {r.name}",
                style=discord.ButtonStyle.gray,
            )
            async def make_cb(rn=role_name):
                async def cb(interaction: discord.Interaction):
                    uid = interaction.user.id
                    for name, voters in self.votes.items():
                        voters.discard(uid)
                    self.votes[rn].add(uid)
                    await interaction.response.send_message(
                        f"صوّتت لـ **{rn}**.", ephemeral=True
                    )
                return cb
            btn.callback = await asyncio.coroutine(make_cb)() if False else make_cb()
            self.add_item(btn)

    @discord.ui.button(label="النتائج", style=discord.ButtonStyle.blurple, emoji="📊", row=2)
    async def results(self, interaction: discord.Interaction, button: discord.ui.Button):
        lines = [f"• **{r}**: {len(v)} صوت" for r, v in sorted(self.votes.items(), key=lambda x: -len(x[1]))]
        await interaction.response.send_message("\n".join(lines) or "لا أصوات بعد.", ephemeral=True)


# ============================================================================
# أوامر إدارية إضافية
# ============================================================================

@bot.command(name="طرد_من_اللعبة", aliases=["kick_player"])
@commands.has_permissions(administrator=True)
async def cmd_kick_player(ctx: commands.Context, member: discord.Member | None = None):
    """طرد لاعب من اللعبة الجارية."""
    if not member:
        return await ctx.send("استخدم: `&طرد_من_اللعبة @لاعب`")
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة جارية.")

    if game.phase == "waiting":
        if game.remove_lobby_player(member.id):
            return await ctx.send(f"✅ تم طرد {member.mention} من الردهة.")
        return await ctx.send("اللاعب ليس في الردهة.")

    player = game.get(member.id)
    if not player:
        return await ctx.send("اللاعب ليس في اللعبة.")

    player.alive = False
    player.killed_by = "admin_kick"
    await mute_player(game, member.id, reason="طُرد من اللعبة")
    await ctx.send(f"✅ تم طرد {member.mention} من اللعبة.")


@bot.command(name="فرض_دور", aliases=["force_role"])
@commands.has_permissions(administrator=True)
async def cmd_force_role(ctx: commands.Context, member: discord.Member | None = None, *, role_name: str = ""):
    """تغيير دور لاعب في اللعبة الجارية."""
    if not member or not role_name:
        return await ctx.send("استخدم: `&فرض_دور @لاعب <اسم الدور>`")
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة جارية.")

    role = ROLES.get(role_name.strip())
    if not role:
        return await ctx.send(f"❌ لا يوجد دور باسم **{role_name}**.")

    player = game.get(member.id)
    if not player:
        return await ctx.send("اللاعب ليس في اللعبة.")

    game.players[member.id].role = role
    await ctx.send(f"✅ تم تغيير دور {member.mention} إلى {role.emoji} **{role.name}**.")


@bot.command(name="مد_الليل", aliases=["extend_night"])
@commands.has_permissions(administrator=True)
async def cmd_extend_night(ctx: commands.Context, seconds: int = 30):
    """تمديد وقت الليل في اللعبة الجارية (يعمل بشكل محدود)."""
    await ctx.send(f"⏱️ تم تسجيل طلب التمديد بـ {seconds} ثانية. يؤثر في الليلة القادمة.")


@bot.command(name="كشف_أدوار", aliases=["reveal_all"])
@commands.has_permissions(administrator=True)
async def cmd_reveal_all(ctx: commands.Context):
    """كشف جميع أدوار اللاعبين (للمسؤول فقط)."""
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة جارية.")

    lines = [
        f"{'🟢' if p.alive else '💀'} {p.user.display_name}: {p.role.emoji} **{p.role.name}** ({p.role.team})"
        for p in game.players.values()
    ]
    embed = discord.Embed(
        title="🔍 كشف الأدوار (للمسؤول)",
        description="\n".join(lines) or "لا لاعبون.",
        color=discord.Color.dark_red(),
    )
    await ctx.send(embed=embed, ephemeral=True)


@bot.command(name="إعادة_ضبط_يومي", aliases=["reset_daily"])
@commands.has_permissions(administrator=True)
async def cmd_reset_daily(ctx: commands.Context, member: discord.Member | None = None):
    """إعادة ضبط المكافأة اليومية للاعب."""
    data = _load_daily()
    if member:
        data.pop(str(member.id), None)
        _save_daily(data)
        await ctx.send(f"✅ تم إعادة ضبط المكافأة اليومية لـ {member.mention}.")
    else:
        _save_daily({})
        await ctx.send("✅ تم إعادة ضبط المكافآت اليومية للجميع.")


# ============================================================================
# أوامر معلومات اللعبة
# ============================================================================

@bot.command(name="فريقي", aliases=["my_team", "team"])
async def cmd_my_team(ctx: commands.Context):
    """يعرض اللاعب فريقه (للمافيا فقط)."""
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة جارية.", delete_after=5)

    player = game.get(ctx.author.id)
    if not player or not player.alive:
        return await ctx.send("لست في اللعبة أو أنت ميت.", delete_after=5)

    if player.role.team != "mafia" and not (player.role.name == "قاتل" and player.joined_mafia):
        return await ctx.send("هذا الأمر للمافيا فقط.", delete_after=5)

    team = game.mafia_team_members(alive_only=True)
    lines = [f"• {p.user.display_name} — {p.role.emoji} {p.role.name}" for p in team]
    await ctx.send(
        embed=discord.Embed(
            title="🔪 فريق المافيا الأحياء",
            description="\n".join(lines) or "لا أحد في فريقك.",
            color=discord.Color.dark_red(),
        ),
        ephemeral=True,
    )


@bot.command(name="دوري", aliases=["my_role", "role_me"])
async def cmd_my_role(ctx: commands.Context):
    """يذكّر اللاعب بدوره سراً."""
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة جارية.", delete_after=5)

    player = game.get(ctx.author.id)
    if not player:
        return await ctx.send("لست في اللعبة.", delete_after=5)

    embed = build_role_embed(player.role)
    embed.title = f"🎭 دورك: {player.role.emoji} {player.role.name}"
    embed.add_field(
        name="📊 حالتك",
        value=f"{'🟢 حي' if player.alive else '💀 ميت'}",
        inline=True,
    )
    await ctx.send(embed=embed, ephemeral=True)


@bot.command(name="وقت", aliases=["timer", "time_left"])
async def cmd_time_left(ctx: commands.Context):
    """يعرض المرحلة الحالية للعبة."""
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة جارية.")

    phase_names = {
        "waiting": "⏳ انتظار اللاعبين",
        "night": f"🌙 ليل رقم {game.day_count}",
        "day": f"☀️ نهار {game.day_count}",
        "ended": "🏁 انتهت اللعبة",
    }
    await ctx.send(f"📍 المرحلة الحالية: **{phase_names.get(game.phase, game.phase)}** | الوقت: {game.elapsed_time()}")


# ============================================================================
# ألعاب نصية خفيفة (ثانوية)
# ============================================================================

ROLE_QUIZ: list[dict] = [
    {"q": "أي دور يظهر بريئاً عند تحقيق الشرطي؟", "a": "محتال", "choices": ["محتال", "مزوّر", "مخبر", "ساحرة"]},
    {"q": "أي دور يرث دور الطبيب تلقائياً؟", "a": "ممرضة", "choices": ["ممرضة", "نائب الشرطي", "حارسة", "كاهن"]},
    {"q": "أي دور يأخذ معه أول من صوّت ضده عند الإعدام؟", "a": "شهيد", "choices": ["شهيد", "فارس", "مجنون", "قنّاص"]},
    {"q": "أي دور يفوز إذا أُعدم بالتصويت؟", "a": "مجنون", "choices": ["مجنون", "شهيد", "قاتل", "سياسي"]},
    {"q": "أي دور يمنع لاعباً من التصويت في اليوم التالي؟", "a": "رجل عصابة", "choices": ["رجل عصابة", "محرّض", "مضيفة", "ساحرة"]},
    {"q": "أي دور يكشف دور لاعب ميت؟", "a": "عرافة", "choices": ["عرافة", "شرطي", "عميل سري", "مراقب"]},
    {"q": "أي دور يمتلك رصاصة واحدة تخترق الحماية؟", "a": "قنّاص", "choices": ["قنّاص", "شرطي", "حارسة", "فارس"]},
    {"q": "أي دور يعيد لاعباً ميتاً مرة واحدة؟", "a": "كاهن", "choices": ["كاهن", "طبيب", "ممرضة", "فارس"]},
    {"q": "أي دور صوته يُحسب مرتين ولا يُعدَم بالتصويت؟", "a": "سياسي", "choices": ["سياسي", "رجل عصابة", "شرطي", "محامي"]},
    {"q": "أي دور يموت مع مهاجمه؟", "a": "فارس", "choices": ["فارس", "شهيد", "جندي", "حارسة"]},
]

_active_quizzes: dict[int, asyncio.Task] = {}


class QuizView(discord.ui.View):
    def __init__(self, question: dict, timeout: float = 20):
        super().__init__(timeout=timeout)
        self.question = question
        self.answered: dict[int, bool] = {}
        self.correct_users: list[int] = []

        choices = question["choices"][:]
        random.shuffle(choices)
        for choice in choices:
            btn = discord.ui.Button(
                label=choice,
                style=discord.ButtonStyle.gray,
            )
            is_correct = (choice == question["a"])

            async def make_cb(c=choice, correct=is_correct):
                async def cb(interaction: discord.Interaction):
                    uid = interaction.user.id
                    if uid in self.answered:
                        return await interaction.response.send_message("أجبت من قبل!", ephemeral=True)
                    self.answered[uid] = correct
                    if correct:
                        self.correct_users.append(uid)
                        await interaction.response.send_message("✅ إجابة صحيحة!", ephemeral=True)
                    else:
                        await interaction.response.send_message(f"❌ خطأ! الإجابة هي **{self.question['a']}**", ephemeral=True)
                return cb

            btn.callback = make_cb()
            self.add_item(btn)


@bot.command(name="مسابقة", aliases=["quiz"])
async def cmd_quiz(ctx: commands.Context):
    """مسابقة سريعة عن أدوار المافيا مع نقاط للفائزين."""
    if not ctx.guild:
        return

    question = random.choice(ROLE_QUIZ)
    view = QuizView(question, timeout=20)

    embed = discord.Embed(
        title="🎯 مسابقة مافيا 42!",
        description=f"**السؤال:** {question['q']}\n\nلديك **20 ثانية**!",
        color=discord.Color.blurple(),
    )
    msg = await ctx.send(embed=embed, view=view)
    await asyncio.sleep(20)

    for child in view.children:
        child.disabled = True
    try:
        await msg.edit(view=view)
    except discord.HTTPException:
        pass

    if view.correct_users:
        # أعطِ نقاطاً
        ranks = _load_ranks()
        winner_mentions = []
        for uid in view.correct_users:
            key = str(uid)
            ranks[key] = ranks.get(key, INITIAL_POINTS) + 50
            winner_mentions.append(f"<@{uid}>")
        _save_ranks(ranks)

        result_embed = discord.Embed(
            title="🎉 نتيجة المسابقة",
            description=(
                f"الإجابة الصحيحة: **{question['a']}**\n\n"
                f"✅ **الفائزون (+50 نقطة):**\n" + "\n".join(winner_mentions)
            ),
            color=discord.Color.green(),
        )
    else:
        result_embed = discord.Embed(
            title="😢 لم يجب أحد بشكل صحيح",
            description=f"الإجابة الصحيحة كانت: **{question['a']}**",
            color=discord.Color.red(),
        )
    await ctx.send(embed=result_embed)


# ============================================================================
# أوامر تذكير وتنبيه
# ============================================================================

@bot.command(name="تذكير", aliases=["remind"])
@commands.has_permissions(administrator=True)
async def cmd_remind(ctx: commands.Context):
    """يرسل تذكيراً لجميع اللاعبين في الردهة."""
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game or game.phase != "waiting":
        return await ctx.send("لا توجد ردهة انتظار نشطة.")

    if not game.lobby_user_ids:
        return await ctx.send("لا يوجد لاعبون في الردهة.")

    mentions = " ".join(f"<@{uid}>" for uid in game.lobby_user_ids)
    await ctx.send(
        f"🔔 **تذكير!** اللعبة ستبدأ قريباً.\n{mentions}\n"
        f"الردهة تنتظر {MIN_PLAYERS - len(game.lobby_user_ids)} لاعب إضافي."
        if len(game.lobby_user_ids) < MIN_PLAYERS
        else f"🔔 **تذكير!** المنشئ سيبدأ اللعبة قريباً.\n{mentions}"
    )


# ============================================================================
# نظام المقارنة (Compare Players)
# ============================================================================

@bot.command(name="مقارنة", aliases=["compare"])
async def cmd_compare(ctx: commands.Context, member1: discord.Member | None = None, member2: discord.Member | None = None):
    if not member1:
        return await ctx.send("استخدم: `&مقارنة @لاعب1 @لاعب2`")
    if not member2:
        member2_target = ctx.author
        member1_target = member1
    else:
        member1_target = member1
        member2_target = member2

    s1 = get_stats(member1_target.id)
    s2 = get_stats(member2_target.id)
    p1 = ensure_rank(member1_target.id)
    p2 = ensure_rank(member2_target.id)

    def wr(s):
        return round(s.get("wins", 0) / max(s.get("games_played", 1), 1) * 100)

    embed = discord.Embed(
        title=f"⚔️ مقارنة: {member1_target.display_name} vs {member2_target.display_name}",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name=f"👤 {member1_target.display_name}",
        value=(
            f"💎 {p1:,} نقطة\n"
            f"🎮 {s1.get('games_played', 0)} لعبة\n"
            f"✅ {s1.get('wins', 0)} فوز ({wr(s1)}%)\n"
            f"🔥 سلسلة: {s1.get('max_win_streak', 0)}\n"
            f"🏆 {len(get_player_achievements(member1_target.id))} إنجاز"
        ),
        inline=True,
    )
    embed.add_field(
        name=f"👤 {member2_target.display_name}",
        value=(
            f"💎 {p2:,} نقطة\n"
            f"🎮 {s2.get('games_played', 0)} لعبة\n"
            f"✅ {s2.get('wins', 0)} فوز ({wr(s2)}%)\n"
            f"🔥 سلسلة: {s2.get('max_win_streak', 0)}\n"
            f"🏆 {len(get_player_achievements(member2_target.id))} إنجاز"
        ),
        inline=True,
    )

    if p1 > p2:
        embed.add_field(name="🏅 الأفضل نقاطاً", value=member1_target.mention, inline=False)
    elif p2 > p1:
        embed.add_field(name="🏅 الأفضل نقاطاً", value=member2_target.mention, inline=False)
    else:
        embed.add_field(name="🏅", value="تعادل!", inline=False)

    await ctx.send(embed=embed)


# ============================================================================
# نظام التبرع بالنقاط (Transfer Points)
# ============================================================================

@bot.command(name="تبرع", aliases=["transfer", "give"])
async def cmd_transfer(ctx: commands.Context, member: discord.Member | None = None, amount: int = 0):
    """تحويل نقاط إلى لاعب آخر."""
    if not member or amount <= 0:
        return await ctx.send("استخدم: `&تبرع @لاعب <كمية>`")
    if member.id == ctx.author.id:
        return await ctx.send("لا يمكنك التبرع لنفسك.")
    if amount < 100:
        return await ctx.send("الحد الأدنى للتبرع هو 100 نقطة.")

    ranks = _load_ranks()
    sender_key = str(ctx.author.id)
    receiver_key = str(member.id)

    sender_pts = ranks.get(sender_key, INITIAL_POINTS)
    if sender_pts < amount:
        return await ctx.send(f"❌ نقاطك غير كافية. لديك **{sender_pts:,}** فقط.")

    ranks[sender_key] = sender_pts - amount
    ranks[receiver_key] = ranks.get(receiver_key, INITIAL_POINTS) + amount
    _save_ranks(ranks)

    await ctx.send(
        embed=discord.Embed(
            title="💸 تم التحويل!",
            description=(
                f"{ctx.author.mention} تبرّع بـ **{amount:,}** نقطة لـ {member.mention}\n"
                f"رصيدك الآن: **{ranks[sender_key]:,}**"
            ),
            color=discord.Color.green(),
        )
    )


# ============================================================================
# لوحة الشرف (Hall of Fame)
# ============================================================================

HALL_OF_FAME_FILE = Path("mafia_hall_of_fame.json")


@bot.command(name="لوحة_الشرف", aliases=["hall_of_fame", "hof"])
async def cmd_hall_of_fame(ctx: commands.Context):
    history = _load_json(HISTORY_FILE) if HISTORY_FILE.exists() else []
    if not isinstance(history, list) or not history:
        return await ctx.send("لا يوجد بيانات بعد.")

    guild_games = [g for g in history if g.get("guild_id") == ctx.guild.id]

    # أكثر اللاعبين فوزاً
    win_count: dict[int, int] = defaultdict(int)
    for g in guild_games:
        w = g.get("winner")
        for p in g.get("players", []):
            pid = p.get("id")
            team = p.get("team")
            role = p.get("role")
            # المجنون
            if role == "مجنون" and p.get("killed_by") == "vote":
                win_count[pid] += 1
            elif (team == "citizens" and w == "citizens") or (team == "mafia" and w == "mafia"):
                win_count[pid] += 1

    top = sorted(win_count.items(), key=lambda x: -x[1])[:5]
    if not top:
        return await ctx.send("لا يوجد بيانات كافية.")

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [f"{medals[i]} <@{uid}>: **{cnt}** انتصار" for i, (uid, cnt) in enumerate(top)]

    embed = discord.Embed(
        title=f"🏆 لوحة شرف {ctx.guild.name}",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"بناءً على {len(guild_games)} لعبة")
    await ctx.send(embed=embed)


# ============================================================================
# أوامر متفرقة مفيدة
# ============================================================================

@bot.command(name="سرعة_اللعبة", aliases=["game_speed"])
async def cmd_game_speed(ctx: commands.Context):
    """عرض أوقات اللعبة الحالية."""
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if game:
        s = game.settings
        embed = discord.Embed(title="⚙️ أوقات اللعبة الجارية", color=discord.Color.blurple())
        embed.add_field(name="🌙 الليل", value=f"{s.night_seconds}ث", inline=True)
        embed.add_field(name="💬 النقاش", value=f"{s.discussion_seconds}ث", inline=True)
        embed.add_field(name="🗳️ التصويت", value=f"{s.vote_seconds}ث", inline=True)
        embed.add_field(name="⚖️ التأكيد", value=f"{s.confirm_seconds}ث", inline=True)
        embed.add_field(name="🎮 الوضع", value=game.mode, inline=True)
    else:
        embed = discord.Embed(title="⚙️ أوقات اللعبة الافتراضية", color=discord.Color.blurple())
        embed.add_field(name="🌙 الليل", value=f"{NIGHT_SECONDS}ث", inline=True)
        embed.add_field(name="💬 النقاش (عادي)", value=f"{DISCUSSION_SECONDS}ث", inline=True)
        embed.add_field(name="💬 النقاش (سريع)", value=f"{FAST_DISCUSSION_SECONDS}ث", inline=True)
        embed.add_field(name="🗳️ التصويت", value=f"{VOTE_SECONDS}ث", inline=True)
    await ctx.send(embed=embed)


@bot.command(name="رتبي", aliases=["my_rank"])
async def cmd_my_rank(ctx: commands.Context):
    """عرض رتبة اللاعب مع شريط تقدم للرتبة التالية."""
    uid = ctx.author.id
    pts = ensure_rank(uid)
    current_rank = get_rank_title(pts)

    # الرتبة التالية
    next_rank_name = None
    next_threshold = None
    for threshold, name, _ in RANKS:
        if pts < threshold:
            next_rank_name = name
            next_threshold = threshold
            break

    embed = discord.Embed(
        title=f"🏅 رتبتك: {current_rank}",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.add_field(name="💎 نقاطك", value=f"**{pts:,}**", inline=True)

    if next_rank_name and next_threshold:
        needed = next_threshold - pts
        # شريط تقدم
        prev_threshold = 0
        for t, n, _ in RANKS:
            if t < next_threshold:
                prev_threshold = t
        progress = pts - prev_threshold
        total_needed = next_threshold - prev_threshold
        pct = min(100, int(progress / max(total_needed, 1) * 100))
        bar_filled = int(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        embed.add_field(name="🎯 الرتبة التالية", value=next_rank_name, inline=True)
        embed.add_field(name="📊 التقدم", value=f"`{bar}` {pct}%\n{needed:,} نقطة متبقية", inline=False)
    else:
        embed.add_field(name="🌟 وصلت للرتبة الأعلى!", value="أنت في القمة!", inline=False)

    await ctx.send(embed=embed)


@bot.command(name="أدوار_الفريق", aliases=["team_roles"])
async def cmd_team_roles(ctx: commands.Context, team: str = ""):
    """عرض أدوار فريق معين."""
    team_map = {"مافيا": "mafia", "مواطنون": "citizens", "محايد": "neutral"}
    team_en = team_map.get(team, "")
    if not team_en:
        return await ctx.send("استخدم: `&أدوار_الفريق مافيا` أو `مواطنون` أو `محايد`")

    roles = [(n, r) for n, r in ROLES.items() if r.team == team_en]
    if not roles:
        return await ctx.send("لا أدوار لهذا الفريق.")

    color = {"mafia": discord.Color.dark_red(), "citizens": discord.Color.green(), "neutral": discord.Color.purple()}.get(team_en)
    embed = discord.Embed(
        title=f"📋 أدوار {team}",
        color=color or discord.Color.blurple(),
    )
    for name, role in roles:
        rarity = {"common": "⚪", "rare": "🔵", "legendary": "⭐"}.get(role.rarity, "")
        embed.add_field(
            name=f"{role.emoji} {name} {rarity}",
            value=role.description[:100] + ("..." if len(role.description) > 100 else ""),
            inline=False,
        )
    await ctx.send(embed=embed)


@bot.command(name="نقاط_الإنجازات", aliases=["ach_points"])
async def cmd_ach_points(ctx: commands.Context, member: discord.Member | None = None):
    """عرض النقاط المكتسبة من الإنجازات."""
    target = member or ctx.author
    ach_ids = get_player_achievements(target.id)
    total = sum(ACHIEVEMENTS[aid].points for aid in ach_ids if aid in ACHIEVEMENTS)
    count = len(ach_ids)
    await ctx.send(
        embed=discord.Embed(
            title=f"🏆 نقاط إنجازات {target.display_name}",
            description=f"**{count}** إنجاز | **{total:,}** نقطة من الإنجازات",
            color=discord.Color.gold(),
        )
    )


# ============================================================================
# إشعارات خاصة عند الانضمام لقناة اللعبة
# ============================================================================

@bot.event
async def on_member_join(member: discord.Member):
    """رسالة ترحيب تلقائية تشمل معلومات اللعبة."""
    # يمكن تخصيصه لاحقاً
    pass


# ============================================================================
# معالجة الأخطاء
# ============================================================================

@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ وسيط خاطئ: {error}", delete_after=10)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ ينقص وسيط: `{error.param.name}`", delete_after=10)
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ ليس لديك صلاحية لهذا الأمر.", delete_after=10)
    else:
        log.error("خطأ في الأمر %s: %s", ctx.command, error)


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
