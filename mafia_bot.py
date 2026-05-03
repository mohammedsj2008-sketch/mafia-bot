"""Mafia 42 — بوت ديسكورد كامل في ملف واحد.

نظام مستوحى من لعبة Mafia42 الأصلية.
كل تفاعلات الأدوار تتم عبر رسائل مخفية (ephemeral) داخل قناة اللعبة.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import discord
from discord.ext import commands


# ============================================================================
# الإعدادات
# ============================================================================

NIGHT_SECONDS = 30
DISCUSSION_SECONDS = 90
VOTE_SECONDS = 20
CONFIRM_SECONDS = 20
MIN_PLAYERS = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mafia-bot")


# ============================================================================
# الأدوار
# ============================================================================

@dataclass(frozen=True)
class Role:
    name: str
    team: str  # "mafia" | "citizens"
    description: str
    emoji: str
    has_night_action: bool


ROLES: dict[str, Role] = {
    # ----- فريق المافيا -----
    "مافيا": Role("مافيا", "mafia", "يقتل لاعباً كل ليلة بالتنسيق مع باقي المافيا.", "🔪", True),
    "وحش": Role(
        "وحش", "mafia",
        "يبحث عن المافيا كل ليلة. إذا وقع اختياره على مافيا، تخترق ضربتهم حماية الطبيب والجندي. وإذا لم يبقَ مافيا أحياء، يقتل هدفه بنفسه.",
        "👹", True,
    ),
    "مضيفة": Role("مضيفة", "mafia", "تختار لاعباً وتمنعه من تنفيذ دوره الليلة.", "💋", True),
    "ساحرة": Role(
        "ساحرة", "mafia",
        "تسحر لاعباً كل ليلة فتمنع قدرته (لا يعمل سحرها على المافيا).",
        "🧙‍♀️", True,
    ),
    "محتال": Role("محتال", "mafia", "يظهر بريئاً عند تحقيق الشرطي معه.", "🎭", False),
    "جاسوسة": Role(
        "جاسوسة", "mafia",
        "تكشف هوية لاعب كل ليلة، وتشاركها مع باقي المافيا.",
        "🕵️‍♀️", True,
    ),
    # ----- فريق المواطنين -----
    "شرطي": Role("شرطي", "citizens", "يحقق مع لاعب ليكشف هل هو مافيا أم لا.", "🚓", True),
    "طبيب": Role("طبيب", "citizens", "يحمي لاعباً من القتل (يستطيع حماية نفسه).", "💉", True),
    "حارسة": Role("حارسة", "citizens", "تحرس لاعباً — إذا هاجمته المافيا، تقتل المهاجم.", "🛡️", True),
    "جندي": Role("جندي", "citizens", "ينجو من أول هجوم عليه (مرة واحدة فقط).", "💂", False),
    "ممرضة": Role("ممرضة", "citizens", "ترث دور الطبيب تلقائياً إذا مات.", "🏥", False),
    "عميل سري": Role(
        "عميل سري", "citizens",
        "ابتداءً من الليلة الثانية، يكشف دور مواطن عشوائي كل ليلة.",
        "🕴️", True,
    ),
    "كاهن": Role("كاهن", "citizens", "يعيد لاعباً ميتاً للحياة — مرة واحدة فقط.", "⛪", True),
    "عرافة": Role("عرافة", "citizens", "تكشف دور لاعب ميت كل ليلة.", "🔮", True),
    "مراسلة": Role("مراسلة", "citizens", "تنشر دور لاعب علناً في الصباح — مرة واحدة فقط.", "📰", True),
    "شهيد": Role(
        "شهيد", "citizens",
        "إذا أُعدم أو قُتل ليلاً، يأخذ معه أحداً (أول من صوّت ضده، أو قاتله الليلي).",
        "💀", False,
    ),
    "رجل عصابة": Role("رجل عصابة", "citizens", "يمنع لاعباً من التصويت في النهار التالي.", "🚫", True),
    "سياسي": Role(
        "سياسي", "citizens",
        "صوته يُحسب مرتين، ولا يمكن إعدامه بالتصويت — يكشف البوت أنه شخص عادي.",
        "🎩", False,
    ),
    "مواطن": Role("مواطن", "citizens", "لا قدرات خاصة. يصوت فقط في النهار.", "👤", False),
    # ----- مساعد محايد -----
    "قاتل": Role(
        "قاتل", "killer",
        "ينتمي لفريق المافيا لكن لا يعرفهم. كل ليلة يختار شخصين (الطبيب مستثنى) ويخمّن دور كلٍ منهما: إن كان فيهم مافيا انضمّ لهم وعرَفهم، وإن كانا مواطنَين وخمّن دوريهما بدقة قتَلهما معاً.",
        "🗡️", True,
    ),
}


def distribute_roles(player_ids: list[int]) -> dict[int, Role]:
    n = len(player_ids)
    # جدول التوزيع المعتمد:
    # 4 → 1 مافيا + 0 مساعد   |   5 → 1 + 0
    # 6 → 1 مافيا + 1 مساعد   |   7 → 1 + 1
    # 8 → 2 مافيا + 1 مساعد   |   9 → 2 + 1
    # 10 → 2 + 2              |   11 → 3 + 2 فما فوق
    if n <= 5:
        mafia_count, helper_count = 1, 0
    elif n <= 7:
        mafia_count, helper_count = 1, 1
    elif n <= 9:
        mafia_count, helper_count = 2, 1
    elif n == 10:
        mafia_count, helper_count = 2, 2
    else:
        mafia_count, helper_count = 3, 2

    pool: list[Role] = [ROLES["مافيا"]] * mafia_count
    helpers = ["وحش", "مضيفة", "ساحرة", "محتال", "جاسوسة", "قاتل"]
    random.shuffle(helpers)
    pool += [ROLES[name] for name in helpers[:helper_count]]
    pool += [ROLES["شرطي"], ROLES["طبيب"]]

    specials = [
        "حارسة", "جندي", "ممرضة", "عميل سري", "كاهن",
        "عرافة", "مراسلة", "شهيد", "رجل عصابة", "سياسي",
    ]
    random.shuffle(specials)
    # عدد الأدوار الخاصة المواطنية = ما تبقّى بعد المافيا والمساعدين والشرطي والطبيب
    special_count = max(0, n - len(pool))
    pool += [ROLES[name] for name in specials[:special_count]]
    # لو ما زال هناك نقص (لاعبون أكثر من الأدوار)، عبّئ بمواطنين عاديين
    while len(pool) < n:
        pool.append(ROLES["مواطن"])
    pool = pool[:n]
    random.shuffle(pool)

    shuffled = player_ids[:]
    random.shuffle(shuffled)
    return dict(zip(shuffled, pool))


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
    blocked_from_voting_today: bool = False
    killed_by: str | None = None
    killed_by_player: int | None = None
    first_vote_against: int | None = None
    pending_notices: list[str] = field(default_factory=list)
    joined_mafia: bool = False  # يصبح True إذا القاتل خمّن مافيا صح


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
    killer_role_guesses: dict[int, str] = field(default_factory=dict)  # target_id -> guessed role name


class MafiaGame:
    def __init__(self, guild: discord.Guild, channel: discord.TextChannel):
        self.guild = guild
        self.channel = channel
        self.players: dict[int, PlayerState] = {}
        self.lobby_user_ids: list[int] = []
        self.phase: str = "waiting"
        self.day_count: int = 0
        self.night_actions: NightActions = NightActions()
        self.day_votes: dict[int, int] = {}
        self.lobby_message: discord.Message | None = None
        self.phase_task: asyncio.Task | None = None
        # تخزين صلاحيات اللاعبين الأصلية على قناة اللعبة لاسترجاعها بعد انتهاء اللعبة
        self.original_overwrites: dict[int, discord.PermissionOverwrite | None] = {}

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

    def alive_players(self) -> list[PlayerState]:
        return [p for p in self.players.values() if p.alive]

    def dead_players(self) -> list[PlayerState]:
        return [p for p in self.players.values() if not p.alive]

    def alive_mafia(self) -> list[PlayerState]:
        # القاتل أصلاً من فريق المافيا (لشروط الفوز/الموازنة) حتى لو لم ينضم بعد
        return [p for p in self.alive_players()
                if p.role.team == "mafia" or p.role.name == "قاتل"]

    def alive_citizens(self) -> list[PlayerState]:
        return [p for p in self.alive_players() if p.role.team == "citizens"]

    def mafia_team_members(self, *, alive_only: bool = True) -> list[PlayerState]:
        """كل أعضاء فريق المافيا للشات السري (مافيا + قاتل المنضم)."""
        pool = self.alive_players() if alive_only else list(self.players.values())
        return [p for p in pool
                if p.role.team == "mafia" or (p.role.name == "قاتل" and p.joined_mafia)]

    def get(self, user_id: int) -> PlayerState | None:
        return self.players.get(user_id)

    def check_winner(self) -> str | None:
        mafia = len(self.alive_mafia())
        alive_c = self.alive_citizens()
        citizens = len(alive_c)
        # السياسي يُعدّ بشخصين لأن صوته مزدوج
        has_politician = any(p.role.name == "سياسي" for p in alive_c)
        effective_citizens = citizens + (1 if has_politician else 0)
        if mafia == 0 and citizens > 0:
            return "citizens"
        if mafia >= effective_citizens and citizens > 0:
            return "mafia"
        if mafia == 0 and citizens == 0:
            return "citizens"
        return None


# ============================================================================
# نظام النقاط (التصنيف)
# ============================================================================

RANKS_FILE = Path("mafia_ranks.json")
INITIAL_POINTS = 4000


def _load_ranks() -> dict[str, int]:
    try:
        with RANKS_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_ranks(ranks: dict[str, int]) -> None:
    try:
        with RANKS_FILE.open("w", encoding="utf-8") as f:
            json.dump(ranks, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("فشل حفظ ملف النقاط")


def ensure_rank(user_id: int) -> int:
    ranks = _load_ranks()
    key = str(user_id)
    if key not in ranks:
        ranks[key] = INITIAL_POINTS
        _save_ranks(ranks)
    return ranks[key]


def get_rank(user_id: int) -> int:
    return _load_ranks().get(str(user_id), INITIAL_POINTS)


def _delta_for(player: PlayerState, winner: str) -> int:
    """يحسب التغيير في النقاط للاعب بناء على الفريق والنتيجة."""
    role = player.role
    # القاتل: ينتمي للمافيا (يفوز معهم) لكنه مساعد
    if role.name == "قاتل":
        return 50 if winner == "mafia" else -40
    # المافيا الأساسية
    if role.name == "مافيا":
        return 70 if winner == "mafia" else -60
    # مساعدو المافيا (وحش، مضيفة، ساحرة، محتال، جاسوسة)
    if role.team == "mafia":
        return 50 if winner == "mafia" else -40
    # المواطنون
    return 40 if winner == "citizens" else -35


def update_ranks_after_game(game: "MafiaGame", winner: str) -> tuple[dict[int, int], dict[str, int]]:
    """يحدّث ملف النقاط ويعيد (تغييرات_لكل_لاعب، النقاط_الجديدة)."""
    ranks = _load_ranks()
    deltas: dict[int, int] = {}
    for p in game.players.values():
        key = str(p.user.id)
        if key not in ranks:
            ranks[key] = INITIAL_POINTS
        delta = _delta_for(p, winner)
        ranks[key] += delta
        deltas[p.user.id] = delta
    _save_ranks(ranks)
    return deltas, ranks


# ============================================================================
# قنوات اللعبة المسموح بها
# ============================================================================

ALLOWED_CHANNELS_FILE = Path("mafia_allowed_channels.json")


def _load_allowed_channels() -> dict[str, list[int]]:
    try:
        with ALLOWED_CHANNELS_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_allowed_channels(data: dict[str, list[int]]) -> None:
    try:
        with ALLOWED_CHANNELS_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        log.exception("فشل حفظ ملف القنوات المسموحة")


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
# إدارة صلاحيات الكلام أثناء اللعبة
# ============================================================================

async def _get_member(game: "MafiaGame", uid: int) -> discord.Member | None:
    m = game.guild.get_member(uid)
    if m is None:
        try:
            m = await game.guild.fetch_member(uid)
        except discord.HTTPException:
            return None
    return m


async def snapshot_player_perms(game: "MafiaGame") -> None:
    """يحفظ صلاحيات كل لاعب الحالية على قناة اللعبة قبل تعديلها."""
    for uid in game.players:
        member = await _get_member(game, uid)
        if member is None:
            game.original_overwrites[uid] = None
            continue
        existing = game.channel.overwrites_for(member)
        # is_empty تعيد True لو ما فيه أي صلاحية مخصصة
        if existing.is_empty():
            game.original_overwrites[uid] = None
        else:
            game.original_overwrites[uid] = existing


async def restore_player_perms(game: "MafiaGame", uid: int) -> None:
    """يرجّع صلاحيات لاعب لما كانت عليه قبل اللعبة."""
    member = await _get_member(game, uid)
    if member is None:
        return
    original = game.original_overwrites.get(uid, None)
    try:
        await game.channel.set_permissions(member, overwrite=original, reason="انتهت اللعبة — استرجاع الصلاحيات")
    except discord.HTTPException:
        log.exception("فشل استرجاع صلاحيات %s", uid)


async def mute_player(game: "MafiaGame", uid: int, reason: str = "") -> None:
    """يمنع لاعب من الكلام في قناة اللعبة (مع الحفاظ على بقية صلاحياته الأصلية)."""
    member = await _get_member(game, uid)
    if member is None:
        return
    original = game.original_overwrites.get(uid, None)
    if original is None:
        new_ow = discord.PermissionOverwrite()
    else:
        # نسخ الصلاحيات الأصلية ثم تعديل الكلام فقط
        new_ow = discord.PermissionOverwrite(**{k: v for k, v in original})
    new_ow.send_messages = False
    new_ow.add_reactions = False
    new_ow.send_messages_in_threads = False
    new_ow.create_public_threads = False
    new_ow.create_private_threads = False
    try:
        await game.channel.set_permissions(member, overwrite=new_ow, reason=reason or "كتم لاعب أثناء المافيا")
    except discord.HTTPException:
        log.exception("فشل كتم اللاعب %s", uid)


async def unmute_player(game: "MafiaGame", uid: int) -> None:
    """يفك الكتم عن لاعب (يرجّعه لصلاحياته الأصلية)."""
    await restore_player_perms(game, uid)


async def mute_all_alive(game: "MafiaGame") -> None:
    for p in game.alive_players():
        await mute_player(game, p.user.id, reason="الليل — صمت اللاعبين")


async def unmute_all_alive(game: "MafiaGame") -> None:
    for p in game.alive_players():
        await unmute_player(game, p.user.id)


async def mute_all_dead(game: "MafiaGame") -> None:
    for p in game.dead_players():
        await mute_player(game, p.user.id, reason="لاعب ميت")


EVENT_IMAGES = {
    "mafia_kill": "event_mafia_kill.png",
    "doctor_save": "event_doctor_save.png",
    "execution": "event_execution.png",
    "killer_success": "event_killer_success.png",
    "journalist_reveal": "event_journalist_reveal.png",
    "quiet": "event_quiet_night.png",
}


def _event_file(event_name: str) -> tuple[discord.File | None, str | None]:
    """يعيد ملف الصورة + رابط attachment لحدث معين، أو (None, None) إن لم تكن الصورة موجودة."""
    fname = EVENT_IMAGES.get(event_name)
    if not fname:
        return None, None
    path = Path(__file__).parent / "attached_assets" / fname
    if not path.exists():
        return None, None
    return discord.File(str(path), filename=fname), f"attachment://{fname}"


def _pick_morning_event(events: set[str]) -> str:
    """يختار أهم حدث للصورة الصباحية حسب الأولوية."""
    for ev in ("killer_success", "journalist_reveal", "mafia_kill", "doctor_save"):
        if ev in events:
            return ev
    return "quiet"


async def restore_all_perms(game: "MafiaGame") -> None:
    for uid in list(game.original_overwrites.keys()):
        await restore_player_perms(game, uid)


# ============================================================================
# مساعدات الأزرار
# ============================================================================

def _player_options(candidates: list[PlayerState]) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(label=p.user.display_name[:80] or str(p.user.id), value=str(p.user.id))
        for p in candidates
    ]


# ============================================================================
# واجهة الردهة
# ============================================================================

class LobbyView(discord.ui.View):
    def __init__(self, game: MafiaGame, host_id: int, on_start, on_cancel):
        super().__init__(timeout=600)
        self.game = game
        self.host_id = host_id
        self.on_start = on_start
        self.on_cancel = on_cancel

    async def _refresh(self, interaction: discord.Interaction):
        ids = self.game.lobby_user_ids
        names = "\n".join(f"• <@{uid}>" for uid in ids) if ids else "_لا يوجد لاعبون بعد._"
        embed = discord.Embed(
            title="🕵️‍♂️ غرفة انتظار مافيا 42",
            description=(
                f"👑 **منشئ اللعبة:** <@{self.host_id}> (هو فقط من يستطيع البدء أو الإلغاء)\n"
                f"اضغط **انضمام** للدخول. الحد الأدنى **{MIN_PLAYERS} لاعبين**.\n\n"
                f"**اللاعبون ({len(ids)}):**\n{names}"
            ),
            color=discord.Color.gold(),
        )
        embed.set_image(url="attachment://mafia_lobby.png")
        if interaction.message:
            await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="انضمام", style=discord.ButtonStyle.green, emoji="✅")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game.add_lobby_player(interaction.user.id):
            pts = ensure_rank(interaction.user.id)
            await interaction.response.send_message(
                f"تم انضمامك! 🎉 (نقاطك: {pts})", ephemeral=True
            )
            await self._refresh(interaction)
        else:
            await interaction.response.send_message("أنت موجود بالفعل في اللعبة.", ephemeral=True)

    @discord.ui.button(label="خروج", style=discord.ButtonStyle.gray, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game.remove_lobby_player(interaction.user.id):
            await interaction.response.send_message("غادرتَ غرفة الانتظار.", ephemeral=True)
            await self._refresh(interaction)
        else:
            await interaction.response.send_message("أنت لست في غرفة الانتظار.", ephemeral=True)

    @discord.ui.button(label="بدء اللعبة", style=discord.ButtonStyle.blurple, emoji="▶️")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message(
                f"🚫 فقط <@{self.host_id}> (منشئ اللعبة) يستطيع بدء اللعبة.",
                ephemeral=True,
            )
        if len(self.game.lobby_user_ids) < MIN_PLAYERS:
            return await interaction.response.send_message(
                f"يجب أن يكون هناك {MIN_PLAYERS} لاعبين على الأقل (حالياً {len(self.game.lobby_user_ids)}).",
                ephemeral=True,
            )
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        if interaction.message:
            await interaction.message.edit(view=self)
        await self.on_start(interaction)

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.red, emoji="🛑")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message(
                f"🚫 فقط <@{self.host_id}> (منشئ اللعبة) يستطيع إلغاء اللعبة.",
                ephemeral=True,
            )
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        if interaction.message:
            await interaction.message.edit(view=self)
        await self.on_cancel(interaction)


# ============================================================================
# واجهات الأدوار (ephemeral selectors)
# ============================================================================

class _RoleTargetSelect(discord.ui.Select):
    def __init__(self, candidates: list[PlayerState], placeholder: str, callback):
        opts = _player_options(candidates) or [
            discord.SelectOption(label="لا يوجد هدف متاح", value="none")
        ]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=opts)
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


def _make_action_view(candidates, placeholder, callback, *, allow_skip=True) -> discord.ui.View:
    view = discord.ui.View(timeout=NIGHT_SECONDS + 10)
    view.add_item(_RoleTargetSelect(candidates, placeholder, callback))
    if allow_skip:
        view.add_item(_SkipButton(callback))
    return view


class _MultiTargetSelect(discord.ui.Select):
    def __init__(self, candidates: list[PlayerState], placeholder: str, callback, count: int):
        opts = _player_options(candidates) or [
            discord.SelectOption(label="لا يوجد هدف متاح", value="none")
        ]
        super().__init__(
            placeholder=placeholder,
            min_values=count,
            max_values=count if len(opts) >= count else len(opts),
            options=opts,
        )
        self._callback = callback

    async def callback(self, interaction: discord.Interaction):
        if any(v == "none" for v in self.values):
            return await interaction.response.send_message("لا يوجد هدف.", ephemeral=True)
        ids = [int(v) for v in self.values]
        await self._callback(interaction, ids)


def _make_multi_action_view(candidates, placeholder, callback, count: int, *, allow_skip=True) -> discord.ui.View:
    view = discord.ui.View(timeout=NIGHT_SECONDS + 10)
    view.add_item(_MultiTargetSelect(candidates, placeholder, callback, count))
    if allow_skip:
        async def skip_cb(interaction, _):
            await callback(interaction, [])
        view.add_item(_SkipButton(skip_cb))
    return view


# ---- واجهة القاتل المخصّصة (هدفان + تخمين دور لكل واحد) ----

class _KillerView(discord.ui.View):
    def __init__(self, candidates: list[PlayerState], role_names: list[str], on_submit):
        super().__init__(timeout=NIGHT_SECONDS + 10)
        self.target1: int | None = None
        self.target2: int | None = None
        self.role1: str | None = None
        self.role2: str | None = None
        self._on_submit = on_submit

        player_opts = _player_options(candidates) or [
            discord.SelectOption(label="لا يوجد", value="none")
        ]
        role_opts = [
            discord.SelectOption(label=f"{ROLES[r].emoji} {r}", value=r)
            for r in role_names
        ]

        sel_t1 = discord.ui.Select(placeholder="🎯 الهدف الأول", options=player_opts, row=0)
        sel_r1 = discord.ui.Select(placeholder="🎭 تخمين دور الهدف الأول", options=role_opts, row=1)
        sel_t2 = discord.ui.Select(placeholder="🎯 الهدف الثاني", options=player_opts, row=2)
        sel_r2 = discord.ui.Select(placeholder="🎭 تخمين دور الهدف الثاني", options=role_opts, row=3)

        async def cb_t1(interaction):
            self.target1 = None if sel_t1.values[0] == "none" else int(sel_t1.values[0])
            await interaction.response.defer()
        async def cb_t2(interaction):
            self.target2 = None if sel_t2.values[0] == "none" else int(sel_t2.values[0])
            await interaction.response.defer()
        async def cb_r1(interaction):
            self.role1 = sel_r1.values[0]
            await interaction.response.defer()
        async def cb_r2(interaction):
            self.role2 = sel_r2.values[0]
            await interaction.response.defer()

        sel_t1.callback = cb_t1
        sel_t2.callback = cb_t2
        sel_r1.callback = cb_r1
        sel_r2.callback = cb_r2
        self.add_item(sel_t1)
        self.add_item(sel_r1)
        self.add_item(sel_t2)
        self.add_item(sel_r2)

        confirm = discord.ui.Button(label="تأكيد", style=discord.ButtonStyle.danger, emoji="✅", row=4)
        async def cb_confirm(interaction):
            if self.target1 is None or self.target2 is None or not self.role1 or not self.role2:
                return await interaction.response.send_message(
                    "⚠️ اختر الهدفين وخمّن دوريهما قبل التأكيد.", ephemeral=True
                )
            if self.target1 == self.target2:
                return await interaction.response.send_message(
                    "⚠️ يجب أن يكونا شخصين مختلفين.", ephemeral=True
                )
            await self._on_submit(interaction, self.target1, self.role1, self.target2, self.role2)
        confirm.callback = cb_confirm
        self.add_item(confirm)

        skip = discord.ui.Button(label="تخطّي الليلة", style=discord.ButtonStyle.gray, emoji="⏭️", row=4)
        async def cb_skip(interaction):
            await self._on_submit(interaction, None, None, None, None)
        skip.callback = cb_skip
        self.add_item(skip)


def _build_night_menu(game: MafiaGame, player: PlayerState) -> tuple[discord.Embed, discord.ui.View | None]:
    """يبني الـ embed والـ view المناسبين لدور اللاعب الليلة."""
    role = player.role
    candidates = [p for p in game.alive_players() if p.user.id != player.user.id]

    # أدوار بلا تنفيذ ليلي
    if not role.has_night_action:
        embed = discord.Embed(
            title=f"{role.emoji} {role.name}",
            description="ليس لك تنفيذ هذه الليلة. نم بسلام.",
            color=discord.Color.dark_gray(),
        )
        return embed, None

    # تحقق من الاستخدام لمرة واحدة
    if role.name == "كاهن" and player.priest_used:
        return discord.Embed(title="⛪ كاهن", description="استهلكت قدرتك.", color=discord.Color.gold()), None
    if role.name == "مراسلة" and player.journalist_used:
        return discord.Embed(title="📰 مراسلة", description="استهلكت قدرتك.", color=discord.Color.gold()), None

    na = game.night_actions

    # ----- المافيا -----
    if role.name == "مافيا":
        targets = [p for p in candidates if p.role.team != "mafia"]

        async def cb(interaction, target_id):
            if target_id is None:
                na.mafia_votes.pop(player.user.id, None)
                msg = "تخطيت التصويت."
            else:
                na.mafia_votes[player.user.id] = target_id
                msg = f"🔪 صوّتت لقتل **{game.get(target_id).user.display_name}**."
            await interaction.response.send_message(msg, ephemeral=True)

        embed = discord.Embed(
            title="🔪 وقت المافيا",
            description="اختر الضحية. الأكثر تصويتاً يُقتل.",
            color=discord.Color.dark_red(),
        )
        return embed, _make_action_view(targets, "اختر هدفاً", cb)

    if role.name == "وحش":
        async def cb(interaction, target_id):
            na.beast_target = target_id
            if target_id is None:
                msg = "لن تبحث الليلة."
            else:
                msg = (
                    f"👹 ستبحث في **{game.get(target_id).user.display_name}** عن المافيا.\n"
                    "إن كان مافيا، تخترق ضربة المافيا الحماية. وإن لم يبقَ مافيا، تقتله بنفسك."
                )
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="👹 وقت الوحش",
            description=(
                "اختر لاعباً تبحث فيه عن المافيا.\n"
                "• إن كان مافيا → ضربة المافيا الليلة تخترق الطبيب والجندي.\n"
                "• إن لم يبقَ مافيا (الدور الأساسي) → تقتله بنفسك."
            ),
            color=discord.Color.dark_red(),
        ), _make_action_view(candidates, "اختر هدفاً", cb)

    if role.name == "مضيفة":
        async def cb(interaction, target_id):
            na.hostess_block = target_id
            msg = "لن تمنعي أحداً." if target_id is None else f"💋 منعتِ **{game.get(target_id).user.display_name}**."
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="💋 وقت المضيفة",
            description="اختاري لاعباً لمنعه من تنفيذ دوره الليلة.",
            color=discord.Color.magenta(),
        ), _make_action_view(candidates, "اختاري هدفاً", cb)

    if role.name == "ساحرة":
        async def cb(interaction, target_id):
            if target_id is None:
                na.witch_block = None
                msg = "لن تسحري أحداً الليلة."
            else:
                na.witch_block = target_id
                msg = f"🧙‍♀️ ستسحرين **{game.get(target_id).user.display_name}** فيفقد قدرته (لا يعمل على المافيا)."
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="🧙‍♀️ وقت الساحرة",
            description="اختاري لاعباً لتعطيل قدرته الليلة. تستطيعين السحر كل ليلة، لكن السحر لا يؤثر على المافيا.",
            color=discord.Color.purple(),
        ), _make_action_view(candidates, "اختاري من تسحرين", cb)

    if role.name == "جاسوسة":
        async def cb(interaction, target_id):
            na.spy_target = (player.user.id, target_id) if target_id else None
            if target_id is None:
                msg = "لن تتجسسي."
            else:
                msg = "🕵️‍♀️ تم تسجيل التجسس. النتيجة في الصباح (وستصل لباقي المافيا أيضاً)."
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="🕵️‍♀️ وقت الجاسوسة",
            description="اختاري لاعباً لكشف دوره — النتيجة تصلك أنتِ وتصل باقي المافيا.",
            color=discord.Color.dark_red(),
        ), _make_action_view(candidates, "اختاري هدف التجسس", cb)

    # ----- المواطنون -----
    if role.name == "شرطي":
        async def cb(interaction, target_id):
            na.cop_target = (player.user.id, target_id) if target_id else None
            msg = "لن تحقق الليلة." if target_id is None else "🚓 تم تسجيل التحقيق. النتيجة في الصباح."
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="🚓 وقت الشرطي",
            description="اختر لاعباً للتحقيق معه.",
            color=discord.Color.blue(),
        ), _make_action_view(candidates, "اختر من تحقق معه", cb)

    if role.name == "طبيب":
        # الطبيب يستطيع حماية نفسه
        doctor_targets = list(game.alive_players())

        async def cb(interaction, target_id):
            na.doctor_save = target_id
            if target_id is None:
                msg = "لن تحمي أحداً."
            elif target_id == player.user.id:
                msg = "💉 ستحمي نفسك الليلة."
            else:
                msg = f"💉 ستحمي **{game.get(target_id).user.display_name}**."
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="💉 وقت الطبيب",
            description="اختر لاعباً لحمايته (يمكنك حماية نفسك).",
            color=discord.Color.green(),
        ), _make_action_view(doctor_targets, "اختر من تحمي", cb)

    if role.name == "حارسة":
        async def cb(interaction, target_id):
            na.guardian_target = target_id
            msg = "لن تحرسي أحداً." if target_id is None else f"🛡️ ستحرسين **{game.get(target_id).user.display_name}**."
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="🛡️ وقت الحارسة",
            description="إذا هاجمت المافيا من تحرسينه، تقتلين المهاجم.",
            color=discord.Color.dark_teal(),
        ), _make_action_view(candidates, "اختاري من تحرسين", cb)

    if role.name == "عميل سري":
        na.secret_agent = player.user.id
        if game.day_count < 2:
            return discord.Embed(
                title="🕴️ وقت العميل السري",
                description="ستبدأ كشف هويات المواطنين ابتداءً من الليلة الثانية.",
                color=discord.Color.dark_blue(),
            ), None
        return discord.Embed(
            title="🕴️ وقت العميل السري",
            description="ستحصل على دور مواطن عشوائي تلقائياً في الصباح. لا حاجة للاختيار.",
            color=discord.Color.dark_blue(),
        ), None

    if role.name == "كاهن":
        dead = game.dead_players()
        if not dead:
            return discord.Embed(
                title="⛪ وقت الكاهن", description="لا يوجد موتى لإحيائهم.", color=discord.Color.gold(),
            ), None

        async def cb(interaction, target_id):
            if target_id is None:
                na.priest_revive = None
                msg = "احتفظت بقدرتك."
            else:
                na.priest_revive = target_id
                msg = f"⛪ ستعيد **{game.get(target_id).user.display_name}** للحياة (تستهلك قدرتك)."
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="⛪ وقت الكاهن",
            description="اختر ميتاً لإعادته للحياة — مرة واحدة فقط.",
            color=discord.Color.gold(),
        ), _make_action_view(dead, "اختر من تعيد", cb)

    if role.name == "عرافة":
        dead = game.dead_players()
        if not dead:
            return discord.Embed(
                title="🔮 وقت العرافة", description="لا يوجد موتى لاستجوابهم.", color=discord.Color.dark_purple(),
            ), None

        async def cb(interaction, target_id):
            na.oracle_target = (player.user.id, target_id) if target_id else None
            msg = "لن تستجوبي الموتى." if target_id is None else "🔮 سترين الدور في الصباح."
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="🔮 وقت العرافة",
            description="اختاري لاعباً ميتاً لتعرفي دوره.",
            color=discord.Color.dark_purple(),
        ), _make_action_view(dead, "اختاري ميتاً", cb)

    if role.name == "مراسلة":
        # المراسلة لا تنشر في الليلة الأولى — تبدأ من الليلة الثانية
        if game.day_count < 2:
            return discord.Embed(
                title="📰 وقت المراسلة",
                description="لا يمكنك النشر في الليلة الأولى. ستتمكنين من الليلة الثانية فما فوق.",
                color=discord.Color.gold(),
            ), None

        async def cb(interaction, target_id):
            if target_id is None:
                na.journalist_reveal = None
                msg = "احتفظت بمنشورك."
            else:
                na.journalist_reveal = target_id
                msg = f"📰 ستنشرين دور **{game.get(target_id).user.display_name}** صباح الغد (تستهلك قدرتك)."
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="📰 وقت المراسلة",
            description="انشري دور لاعب علناً — مرة واحدة فقط.",
            color=discord.Color.gold(),
        ), _make_action_view(candidates, "اختاري من تنشرين دوره", cb)

    if role.name == "رجل عصابة":
        async def cb(interaction, target_id):
            na.gangster_block = target_id
            msg = "لن تمنع أحداً." if target_id is None else f"🚫 ستمنع **{game.get(target_id).user.display_name}** من التصويت غداً."
            await interaction.response.send_message(msg, ephemeral=True)

        return discord.Embed(
            title="🚫 وقت رجل العصابة",
            description="اختر لاعباً لمنعه من التصويت في النهار التالي.",
            color=discord.Color.dark_gold(),
        ), _make_action_view(candidates, "اختر من تمنع", cb)

    if role.name == "قاتل":
        # القاتل بعد ما ينضم للمافيا يصوّت معهم على القتل (لم يعد يخمّن)
        if player.joined_mafia:
            mafia_targets = [
                p for p in candidates
                if p.role.team != "mafia" and not (p.role.name == "قاتل")
            ]

            async def cb_join(interaction, target_id):
                if target_id is None:
                    na.mafia_votes.pop(player.user.id, None)
                    msg = "تخطيت التصويت."
                else:
                    na.mafia_votes[player.user.id] = target_id
                    msg = f"🔪 صوّتت لقتل **{game.get(target_id).user.display_name}**."
                await interaction.response.send_message(msg, ephemeral=True)

            return discord.Embed(
                title="🗡️🔪 قاتل (انضممت للمافيا)",
                description="صوّت مع المافيا لاختيار الضحية الليلة.",
                color=discord.Color.dark_red(),
            ), _make_action_view(mafia_targets, "اختر هدفاً", cb_join)

        # قبل الانضمام: يختار شخصين (الطبيب مستثنى) ويخمّن دور كل واحد
        killer_candidates = [p for p in candidates if p.role.name != "طبيب"]
        guessable_roles = [name for name in ROLES.keys() if name != "قاتل"]

        async def on_killer_submit(interaction, t1, r1, t2, r2):
            if t1 is None:
                na.killer_guesses = []
                na.killer_role_guesses = {}
                msg = "🗡️ تخطّيت الليلة."
            else:
                na.killer_guesses = [t1, t2]
                na.killer_role_guesses = {t1: r1, t2: r2}
                n1 = game.get(t1).user.display_name
                n2 = game.get(t2).user.display_name
                msg = (
                    f"🗡️ سجّلت اختياراتك:\n"
                    f"• **{n1}** → خمّنته **{r1}**\n"
                    f"• **{n2}** → خمّنته **{r2}**\n\n"
                    "إن كان أحدهما مافيا → تنضم لهم.\n"
                    "إن كانا مواطنَين **والتخمينان صحيحان** → يُقتلان."
                )
            await interaction.response.send_message(msg, ephemeral=True)

        embed = discord.Embed(
            title="🗡️ وقت القاتل",
            description=(
                "أنت من **فريق المافيا** لكنك لا تعرفهم بعد.\n"
                "اختر **شخصين** (الطبيب مستثنى) وخمّن **دور** كل واحد:\n"
                "• إن كان أحدهما/كلاهما مافيا → تنضم لهم وتعرفهم\n"
                "• إن كانا مواطنَين **وخمّنت دوريهما بدقة** → يُقتلان الليلة\n"
                "• وإلا لا يحدث شيء"
            ),
            color=discord.Color.dark_gray(),
        )
        return embed, _KillerView(killer_candidates, guessable_roles, on_killer_submit)

    # احتياط
    return discord.Embed(title=role.name, description="لا تنفيذ متاح."), None



# ============================================================================
# واجهة الليل — زر "افتح دورك"
# ============================================================================

class _NightRoleView(discord.ui.View):
    """زر في embed الليل يُظهر لكل لاعب دوره + مهارته بشكل مخفي (ephemeral)."""

    def __init__(self, game: MafiaGame):
        super().__init__(timeout=NIGHT_SECONDS + 10)
        self.game = game

    @discord.ui.button(label="🌙 افتح دورك", style=discord.ButtonStyle.blurple)
    async def open_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self.game.get(interaction.user.id)
        if not player:
            return await interaction.response.send_message("لست في هذه اللعبة.", ephemeral=True)
        if not player.alive:
            return await interaction.response.send_message("💀 أنت ميت — لا تستطيع التصرف.", ephemeral=True)
        embed, view = _build_night_menu(self.game, player)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


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
        super().__init__(timeout=DISCUSSION_SECONDS * 4)
        self.game = game
        self.timer = timer
        self.message_ref = message_ref

    def _can_use(self, interaction: discord.Interaction) -> bool:
        p = self.game.get(interaction.user.id)
        return p is not None and p.alive

    async def _refresh_msg(self):
        msg = self.message_ref.get("msg")
        if msg is None:
            return
        embed = msg.embeds[0] if msg.embeds else discord.Embed()
        if embed.fields:
            embed.set_field_at(
                0, name="⏱️ الوقت المتبقي", value=f"{int(self.timer.remaining())} ثانية", inline=False
            )
        else:
            embed.add_field(
                name="⏱️ الوقت المتبقي", value=f"{int(self.timer.remaining())} ثانية", inline=False
            )
        try:
            await msg.edit(embed=embed)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="+١٥ ثانية", style=discord.ButtonStyle.green, emoji="➕")
    async def add_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_use(interaction):
            return await interaction.response.send_message("فقط اللاعبون الأحياء يستطيعون التحكم بالوقت.", ephemeral=True)
        self.timer.extend(15)
        await interaction.response.send_message(
            f"⏱️ تم تمديد النقاش ١٥ ثانية. (متبقي ≈ {int(self.timer.remaining())}s)", ephemeral=True
        )
        await self._refresh_msg()

    @discord.ui.button(label="-١٥ ثانية", style=discord.ButtonStyle.red, emoji="➖")
    async def cut_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_use(interaction):
            return await interaction.response.send_message("فقط اللاعبون الأحياء يستطيعون التحكم بالوقت.", ephemeral=True)
        self.timer.reduce(15)
        await interaction.response.send_message(
            f"⏱️ تم تقليل النقاش ١٥ ثانية. (متبقي ≈ {int(self.timer.remaining())}s)", ephemeral=True
        )
        await self._refresh_msg()


class DayVoteSelect(discord.ui.Select):
    def __init__(self, game: MafiaGame):
        self.game = game
        options = _player_options(game.alive_players())
        options.append(discord.SelectOption(label="امتناع عن التصويت", value="abstain", emoji="🚫"))
        super().__init__(placeholder="صوّت لإعدام لاعب", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        voter = self.game.get(interaction.user.id)
        if not voter or not voter.alive:
            return await interaction.response.send_message("لا يمكنك التصويت.", ephemeral=True)
        if voter.blocked_from_voting_today:
            return await interaction.response.send_message("🚫 رجل عصابة منعك من التصويت اليوم.", ephemeral=True)
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
        super().__init__(timeout=VOTE_SECONDS + 10)
        self.add_item(DayVoteSelect(game))


class ConfirmExecutionView(discord.ui.View):
    def __init__(self, game: MafiaGame, target: PlayerState, state: dict):
        super().__init__(timeout=CONFIRM_SECONDS + 10)
        self.game = game
        self.target = target
        self.state = state  # {"approve": set[int], "reject": set[int]}

    def _validate(self, interaction: discord.Interaction) -> bool:
        p = self.game.get(interaction.user.id)
        return p is not None and p.alive

    @discord.ui.button(label="موافق على الإعدام", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._validate(interaction):
            return await interaction.response.send_message("لا يحق لك التصويت.", ephemeral=True)
        self.state["approve"].add(interaction.user.id)
        self.state["reject"].discard(interaction.user.id)
        await interaction.response.send_message("✅ سُجلت موافقتك.", ephemeral=True)

    @discord.ui.button(label="اعتراض", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._validate(interaction):
            return await interaction.response.send_message("لا يحق لك التصويت.", ephemeral=True)
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
# دورة اللعبة
# ============================================================================

async def run_game(game: MafiaGame):
    try:
        async def member_lookup(uid: int):
            m = game.guild.get_member(uid)
            if m is None:
                try:
                    m = await game.guild.fetch_member(uid)
                except discord.HTTPException:
                    return None
            return m

        # توزيع الأدوار (بدون رسائل خاصة)
        assignments = distribute_roles(game.lobby_user_ids)
        for user_id, role in assignments.items():
            member = await member_lookup(user_id)
            if member is None:
                continue
            game.players[user_id] = PlayerState(user=member, role=role)

        roster = "\n".join(f"• {p.user.mention}" for p in game.players.values())
        await game.channel.send(
            embed=discord.Embed(
                title="🎬 بدأت اللعبة!",
                description=(
                    f"تم توزيع **{len(game.players)} دور**.\n"
                    f"📩 ستصلك رسالتك الخاصة عند بدء كل ليلة.\n\n"
                    f"**اللاعبون:**\n{roster}"
                ),
                color=discord.Color.gold(),
            ),
        )

        # حفظ صلاحيات اللاعبين الأصلية قبل التعديل عليها
        await snapshot_player_perms(game)

        while True:
            winner = game.check_winner()
            if winner:
                await announce_winner(game, winner)
                return
            await run_night(game)
            winner = game.check_winner()
            if winner:
                await announce_winner(game, winner)
                return
            await run_day(game)
    except asyncio.CancelledError:
        log.info("Game in #%s was cancelled.", game.channel.name)
        raise
    except Exception:
        log.exception("Game crashed in #%s", game.channel.name)
        try:
            await game.channel.send("❌ حدث خطأ غير متوقع وانتهت اللعبة.")
        except discord.HTTPException:
            pass
    finally:
        # استرجاع صلاحيات الكلام لجميع اللاعبين كما كانت قبل اللعبة
        try:
            await restore_all_perms(game)
        except Exception:
            log.exception("فشل استرجاع الصلاحيات في نهاية اللعبة")
        games.pop(game_key(game.guild.id, game.channel.id), None)


async def announce_winner(game: MafiaGame, winner: str):
    game.phase = "ended"
    titles = {
        "citizens": "🏆 فوز فريق المواطنين!",
        "mafia": "🔪 فوز فريق المافيا!",
    }
    colors = {
        "citizens": discord.Color.green(),
        "mafia": discord.Color.dark_red(),
    }
    title = titles.get(winner, "🏁 انتهت اللعبة")
    color = colors.get(winner, discord.Color.blurple())
    lines = [
        f"{'🟢' if p.alive else '💀'} {p.user.mention} — {p.role.emoji} {p.role.name}"
        for p in game.players.values()
    ]
    embed = discord.Embed(title=title, description="\n".join(lines), color=color)

    # تحديث النقاط
    deltas, ranks = update_ranks_after_game(game, winner)
    rank_lines = []
    for p in game.players.values():
        d = deltas.get(p.user.id, 0)
        new_pts = ranks.get(str(p.user.id), INITIAL_POINTS)
        sign = "+" if d >= 0 else ""
        rank_lines.append(f"{p.user.mention}: **{sign}{d}** → الإجمالي: **{new_pts}**")
    if rank_lines:
        embed.add_field(name="🏅 تحديث النقاط", value="\n".join(rank_lines), inline=False)

    await game.channel.send(embed=embed)


# ============================================================================
# الليل
# ============================================================================

async def run_night(game: MafiaGame):
    game.phase = "night"
    game.day_count += 1
    game.night_actions = NightActions()

    for p in game.players.values():
        p.blocked_from_voting_today = False
        p.first_vote_against = None

    # كتم جميع اللاعبين الأحياء + الميتين أثناء الليل
    await mute_all_alive(game)
    await mute_all_dead(game)

    # إعلان الليل في القناة العامة مع زر "افتح دورك"
    night_view = _NightRoleView(game)
    night_embed = discord.Embed(
        title=f"🌙 الليلة رقم {game.day_count}",
        description=(
            f"حلّ الظلام. لديكم **{NIGHT_SECONDS} ثانية**.\n\n"
            "👇 اضغط الزر لمعرفة دورك وتنفيذ مهارتك (يظهر لك فقط).\n"
            "⚠️ المافيا: إذا لم تختر ضحية، لن يُقتل أحد الليلة."
        ),
        color=discord.Color.dark_blue(),
    )
    night_msg = await game.channel.send(embed=night_embed, view=night_view)

    # العميل السري ينفذ تلقائياً
    for p in game.alive_players():
        if p.role.name == "عميل سري":
            game.night_actions.secret_agent = p.user.id

    await asyncio.sleep(NIGHT_SECONDS)

    for child in night_view.children:
        child.disabled = True
    try:
        await night_msg.edit(view=night_view)
    except discord.HTTPException:
        pass

    log_lines, events = await resolve_night(game)

    # إرسال النتائج الخاصة (تحقيق/تجسس/...) عبر DM تلقائياً
    for p in game.players.values():
        if p.pending_notices:
            text = "\n\n".join(p.pending_notices)
            p.pending_notices.clear()
            try:
                dm_channel = await p.user.create_dm()
                await dm_channel.send(text)
            except discord.HTTPException:
                pass

    # --- الصباح + النقاش في embed واحد ---
    game.phase = "day"
    game.day_votes = {}
    await unmute_all_alive(game)
    await mute_all_dead(game)

    alive = game.alive_players()
    blocked_voters = [p for p in alive if p.blocked_from_voting_today]

    alive_mentions = " • ".join(p.user.mention for p in alive)
    desc_lines = list(log_lines) if log_lines else ["🌅 مرّت الليلة بسلام، لم يُقتل أحد."]
    desc_lines.append(f"\n👥 **المتبقون ({len(alive)}):** {alive_mentions}")
    if blocked_voters:
        desc_lines.append("🚫 **ممنوعون من التصويت:** " + ", ".join(p.user.mention for p in blocked_voters))
    desc_lines.append("_استخدم الأزرار للتحكم بمدة النقاش._")

    timer = DiscussionTimer(DISCUSSION_SECONDS)
    msg_ref: dict = {}
    discussion_view = DiscussionView(game, timer, msg_ref)

    morning = discord.Embed(
        title=f"🌅 صباح اليوم {game.day_count}",
        description="\n".join(desc_lines),
        color=discord.Color.orange(),
    )
    morning.add_field(name="⏱️ الوقت المتبقي", value=f"{int(timer.remaining())} ثانية", inline=False)

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


# ============================================================================
# معالجة الليل
# ============================================================================

async def resolve_night(game: MafiaGame) -> tuple[list[str], set[str]]:
    log_lines: list[str] = []
    events: set[str] = set()
    na = game.night_actions

    # 1. تحديد المحظورين (المضيفة + الساحرة على غير المافيا)
    blocked: set[int] = set()
    if na.hostess_block is not None:
        blocked.add(na.hostess_block)
    if na.witch_block is not None:
        witch = next((p for p in game.alive_players() if p.role.name == "ساحرة"), None)
        if witch and witch.user.id not in blocked:
            target = game.get(na.witch_block)
            # سحر الساحرة لا يعمل على المافيا
            if target and target.role.team != "mafia":
                blocked.add(na.witch_block)

    def is_blocked(uid: int) -> bool:
        return uid in blocked

    # 2. أدوار المعلومات (تخزين النتائج)
    _process_info_roles(game, blocked)

    # 3. حساب هدف المافيا
    valid_mafia_votes = {mid: tid for mid, tid in na.mafia_votes.items() if not is_blocked(mid)}
    mafia_target: int | None = None
    if valid_mafia_votes:
        tally = Counter(valid_mafia_votes.values())
        mafia_target = tally.most_common(1)[0][0]

    # 4. حساب فعل الوحش
    beast = next((p for p in game.alive_players() if p.role.name == "وحش"), None)
    beast_blocked = beast is not None and is_blocked(beast.user.id)
    beast_pierces = False  # هل تخترق ضربة المافيا الحماية؟
    beast_solo_target: int | None = None  # هل يقتل الوحش بنفسه؟
    if beast and not beast_blocked and na.beast_target is not None:
        beast_pick = game.get(na.beast_target)
        mafia_main_alive = any(
            p.role.name == "مافيا" for p in game.alive_players()
        )
        if beast_pick and beast_pick.role.team == "mafia":
            # وجد المافيا → ضربتهم تخترق الحماية
            beast_pierces = True
            beast.pending_notices.append(
                f"👹 وجدت أن **{beast_pick.user.display_name}** من المافيا — ضربتهم الليلة تخترق الحماية!"
            )
        else:
            beast.pending_notices.append(
                f"👹 لم تجد مافيا في **{beast_pick.user.display_name if beast_pick else 'الهدف'}**."
                + ("" if mafia_main_alive else " ولأن المافيا الأساسي ميت، ستقتله بنفسك.")
            )
        if not mafia_main_alive and beast_pick is not None:
            beast_solo_target = na.beast_target

    # 5. حماية الطبيب
    save_target = None
    if na.doctor_save is not None:
        doctor = next((p for p in game.alive_players() if p.role.name == "طبيب"), None)
        if doctor and not is_blocked(doctor.user.id):
            save_target = na.doctor_save

    # 6. هدف الحارسة
    guardian_target = None
    if na.guardian_target is not None:
        guardian = next((p for p in game.alive_players() if p.role.name == "حارسة"), None)
        if guardian and not is_blocked(guardian.user.id):
            guardian_target = na.guardian_target

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
            log_lines.append(f"💂 صدّ الجندي الهجوم! ({target.user.mention})")
            return
        if target_id == guardian_target and attacker_id is not None and cause in ("mafia", "beast"):
            attacker = game.get(attacker_id)
            if attacker and attacker.alive:
                killed[attacker_id] = "guardian"
                killed_by_player[attacker_id] = target_id
                log_lines.append(
                    f"🛡️ الحارسة قتلت المهاجم! ({attacker.user.mention})"
                )
                return
        killed[target_id] = cause
        if attacker_id is not None:
            killed_by_player[target_id] = attacker_id

    # ضربة المافيا الأساسية (مع احتمال اختراق الوحش)
    if mafia_target is not None:
        attacker = next((p for p in game.alive_mafia() if p.role.name == "مافيا"), None)
        attempt_kill(
            mafia_target, "mafia",
            attacker.user.id if attacker else None,
            pierce=beast_pierces,
        )
    # ضربة الوحش المنفردة (فقط إذا لم يبقَ مافيا أساسي)
    if beast_solo_target is not None and beast is not None:
        attempt_kill(beast_solo_target, "beast", beast.user.id)

    # تطبيق الوفيات الليلية (لا نكشف الدور)
    for uid, cause in killed.items():
        p = game.get(uid)
        if p and p.alive:
            p.alive = False
            p.killed_by = cause
            p.killed_by_player = killed_by_player.get(uid)
            log_lines.append(f"☠️ {p.user.mention} قُتل في الليل.")
            await mute_player(game, uid, reason="لاعب قُتل في الليل")
            if cause in ("mafia", "beast"):
                events.add("mafia_kill")

    # ----- انتقام الشهيد ليلاً -----
    martyr_kills: list[tuple[int, int]] = []
    for uid in list(killed.keys()):
        victim = game.get(uid)
        if victim and victim.role.name == "شهيد" and victim.killed_by_player is not None:
            martyr_kills.append((uid, victim.killed_by_player))
    for martyr_id, killer_id in martyr_kills:
        killer_p = game.get(killer_id)
        if killer_p and killer_p.alive:
            killer_p.alive = False
            killer_p.killed_by = "martyr"
            log_lines.append(
                f"💀 **انتقام الشهيد:** أخذ {game.get(martyr_id).user.mention} معه "
                f"{killer_p.user.mention}!"
            )
            await mute_player(game, killer_id, reason="انتقام الشهيد")

    # كاهن (لا نكشف دور المُحيا)
    if na.priest_revive is not None:
        priest = next((p for p in game.alive_players() if p.role.name == "كاهن"), None)
        if priest and not is_blocked(priest.user.id):
            target = game.get(na.priest_revive)
            if target and not target.alive:
                target.alive = True
                target.killed_by = None
                priest.priest_used = True
                log_lines.append(
                    f"⛪ الكاهن أعاد {target.user.mention} للحياة!"
                )
                # المُحيا يستعيد كلامه (سيُكتم تلقائياً مع باقي الأحياء في الليل التالي)
                await unmute_player(game, target.user.id)

    # ممرضة ترث الطبيب
    dead_doctor = next((p for p in game.players.values() if p.role.name == "طبيب" and not p.alive), None)
    if dead_doctor:
        nurse = next((p for p in game.alive_players() if p.role.name == "ممرضة"), None)
        if nurse:
            game.players[nurse.user.id] = PlayerState(user=nurse.user, role=ROLES["طبيب"], alive=True)
            game.players[nurse.user.id].pending_notices.append(
                "🏥 **ورثتِ دور الطبيب!** ابدئي بحماية اللاعبين كل ليلة."
            )
            log_lines.append("🏥 الممرضة ورثت دور الطبيب!")

    # رجل عصابة (تطبيق غداً)
    if na.gangster_block is not None:
        gangster = next((p for p in game.alive_players() if p.role.name == "رجل عصابة"), None)
        if gangster and not is_blocked(gangster.user.id):
            target = game.get(na.gangster_block)
            if target and target.alive:
                target.blocked_from_voting_today = True

    # نشر المراسلة
    if na.journalist_reveal is not None:
        journalist = next((p for p in game.players.values() if p.role.name == "مراسلة"), None)
        if journalist and not is_blocked(journalist.user.id):
            target = game.get(na.journalist_reveal)
            if target:
                journalist.journalist_used = True
                log_lines.append(
                    f"📰 **خبر عاجل**: {target.user.mention} هو {target.role.emoji} **{target.role.name}**!"
                )
                events.add("journalist_reveal")

    # ----- معالجة اختيار القاتل -----
    killer = next(
        (p for p in game.alive_players() if p.role.name == "قاتل" and not p.joined_mafia),
        None,
    )
    if killer and na.killer_guesses:
        if is_blocked(killer.user.id):
            killer.pending_notices.append("💋 منعتك المضيفة من التحرّك هذه الليلة.")
        else:
            picks = [game.get(tid) for tid in na.killer_guesses]
            picks = [p for p in picks if p and p.alive]
            mafia_picks = [p for p in picks if p.role.team == "mafia"]
            citizen_picks = [p for p in picks if p.role.team == "citizens"]

            if mafia_picks:
                # ينضم للمافيا ويعرفهم
                killer.joined_mafia = True
                mate_names = ", ".join(
                    f"{m.user.display_name} ({m.role.emoji} {m.role.name})"
                    for m in game.mafia_team_members(alive_only=True)
                    if m.user.id != killer.user.id
                )
                killer.pending_notices.append(
                    f"🗡️ ✅ اكتشفت أحد أعضاء المافيا — انضممت إليهم!\n"
                    f"زملاؤك: {mate_names or '_لا أحد آخر_'}\n"
                    f"استخدم `&همس <رسالة>` للحديث معهم سراً."
                )
                for m in game.mafia_team_members(alive_only=True):
                    if m.user.id != killer.user.id:
                        m.pending_notices.append(
                            f"🗡️ القاتل **{killer.user.display_name}** انضم إليكم الآن."
                        )
            elif len(citizen_picks) >= 2:
                # تحقق من صحة تخمين الدور للاثنين
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
                            log_lines.append(
                                f"🗡️ {victim.user.mention} قُتل في الليل."
                            )
                            await mute_player(game, victim.user.id, reason="قتله القاتل")
                    killer.pending_notices.append(
                        "🗡️ ✅ خمّنت دوري المواطنَين بدقة — قتلتهما الليلة!"
                    )
                    events.add("killer_success")
                else:
                    killer.pending_notices.append(
                        "🗡️ ❌ تخمينك خاطئ — لم يحدث شيء هذه الليلة."
                    )
            else:
                killer.pending_notices.append(
                    "🗡️ لم يحدث شيء هذه الليلة."
                )

    if not killed and not log_lines:
        log_lines.append("🌅 مرّت الليلة بسلام، لم يُقتل أحد.")

    return log_lines, events


def _process_info_roles(game: MafiaGame, blocked: set[int]):
    na = game.night_actions

    if na.cop_target:
        cop_id, target_id = na.cop_target
        cop = game.get(cop_id)
        target = game.get(target_id)
        if cop and target:
            if cop_id in blocked:
                cop.pending_notices.append("💋 منعتك المضيفة من التحقيق هذه الليلة.")
            else:
                if target.role.name == "محتال":
                    verdict = "👤 **بريء**"
                elif target.role.team == "mafia":
                    verdict = "🔪 **مافيا**"
                else:
                    verdict = "👤 **بريء**"
                cop.pending_notices.append(f"🚓 نتيجة التحقيق مع **{target.user.display_name}**: {verdict}")

    if na.spy_target:
        spy_id, target_id = na.spy_target
        spy = game.get(spy_id)
        target = game.get(target_id)
        if spy and target:
            if spy_id in blocked:
                spy.pending_notices.append("💋 منعتك المضيفة من التجسس هذه الليلة.")
            else:
                # نتيجة تصل للجاسوسة
                spy.pending_notices.append(
                    f"🕵️‍♀️ دور **{target.user.display_name}** هو: {target.role.emoji} **{target.role.name}**"
                )
                # وتُرسَل النتيجة لباقي المافيا الأحياء
                for mate in game.alive_mafia():
                    if mate.user.id == spy_id:
                        continue
                    mate.pending_notices.append(
                        f"🕵️‍♀️ كشفت الجاسوسة دور **{target.user.display_name}**: "
                        f"{target.role.emoji} **{target.role.name}**"
                    )

    # العميل السري يكشف ابتداءً من الليلة الثانية فقط
    if na.secret_agent and game.day_count >= 2:
        agent = game.get(na.secret_agent)
        if agent:
            if na.secret_agent in blocked:
                agent.pending_notices.append("💋 منعتك المضيفة من جمع المعلومات.")
            else:
                citizens = [
                    p for p in game.alive_citizens()
                    if p.user.id != agent.user.id and p.role.name != "محتال"
                ]
                if citizens:
                    pick = random.choice(citizens)
                    agent.pending_notices.append(
                        f"🕴️ كشفت أن **{pick.user.display_name}** هو {pick.role.emoji} **{pick.role.name}**"
                    )
                else:
                    agent.pending_notices.append("🕴️ لا يوجد مواطن للكشف عنه.")

    if na.oracle_target:
        oracle_id, dead_id = na.oracle_target
        oracle = game.get(oracle_id)
        dead = game.get(dead_id)
        if oracle and dead:
            if oracle_id in blocked:
                oracle.pending_notices.append("💋 منعتك المضيفة من استجواب الموتى.")
            else:
                oracle.pending_notices.append(
                    f"🔮 الميت **{dead.user.display_name}** كان دوره: {dead.role.emoji} **{dead.role.name}**"
                )


# ============================================================================
# النهار
# ============================================================================

async def run_day(game: MafiaGame):
    if game.check_winner():
        return

    # مرحلة التصويت
    vote_view = DayVoteView(game)
    vote_msg = await game.channel.send(
        embed=discord.Embed(
            title="🗳️ التصويت",
            description=f"لديكم **{VOTE_SECONDS} ثانية** للتصويت. السياسي صوته يُحسب مرتين.",
            color=discord.Color.red(),
        ),
        view=vote_view,
    )
    await asyncio.sleep(VOTE_SECONDS)
    for c in vote_view.children:
        c.disabled = True
    try:
        await vote_msg.edit(view=vote_view)
    except discord.HTTPException:
        pass

    counts: Counter[int] = Counter()
    for voter_id, target_id in game.day_votes.items():
        voter = game.get(voter_id)
        target = game.get(target_id)
        if not voter or not voter.alive or voter.blocked_from_voting_today:
            continue
        if not target or not target.alive:
            continue
        weight = 2 if voter.role.name == "سياسي" else 1
        counts[target_id] += weight

    if not counts:
        return await game.channel.send("🤐 لم يصوّت أحد. لا يوجد إعدام اليوم.")

    breakdown = "\n".join(
        f"• {game.get(uid).user.mention}: {n}"
        for uid, n in sorted(counts.items(), key=lambda x: -x[1])
    )
    top_target, top_votes = counts.most_common(1)[0]
    ties = [uid for uid, v in counts.items() if v == top_votes]
    if len(ties) > 1:
        return await game.channel.send(
            embed=discord.Embed(
                title="⚖️ تعادل في التصويت",
                description=f"تعادل في الأصوات — لا يوجد إعدام اليوم.\n\n{breakdown}",
                color=discord.Color.light_gray(),
            )
        )

    candidate = game.get(top_target)

    # السياسي محمي من الإعدام بالتصويت
    if candidate and candidate.role.name == "سياسي":
        return await game.channel.send(
            embed=discord.Embed(
                title="🎩 لا، هذا شخص عادي!",
                description=(
                    f"حصل {candidate.user.mention} على أعلى الأصوات، "
                    f"لكنه شخص عادي ولا يمكن إعدامه بالتصويت.\n\n"
                    f"**نتائج التصويت:**\n{breakdown}"
                ),
                color=discord.Color.gold(),
            )
        )

    # مرحلة التأكيد
    confirm_state = {"approve": set(), "reject": set()}
    confirm_view = ConfirmExecutionView(game, candidate, confirm_state)
    confirm_msg = await game.channel.send(
        embed=discord.Embed(
            title=f"⚖️ تأكيد إعدام {candidate.user.display_name}",
            description=(
                f"حصل {candidate.user.mention} على أعلى الأصوات.\n"
                f"لديكم **{CONFIRM_SECONDS} ثانية** للموافقة أو الاعتراض.\n"
                f"**عند تساوي الأصوات: يُنفّذ الإعدام.**\n\n"
                f"**نتائج التصويت:**\n{breakdown}"
            ),
            color=discord.Color.orange(),
        ),
        view=confirm_view,
    )
    await asyncio.sleep(CONFIRM_SECONDS)
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
                title="❌ تم رفض الإعدام",
                description=f"الاعتراضات ({r}) أكثر من الموافقات ({a}). نجا {candidate.user.mention}.",
                color=discord.Color.green(),
            )
        )

    # تنفيذ الإعدام (الموافقة ≥ الاعتراض) — لا نكشف الدور
    candidate.alive = False
    candidate.killed_by = "vote"
    await mute_player(game, candidate.user.id, reason="تم إعدامه بالتصويت")
    msg = (
        f"تم إعدام {candidate.user.mention}.\n"
        f"موافقون: **{a}** | معترضون: **{r}**"
    )
    if candidate.role.name == "شهيد" and candidate.first_vote_against is not None:
        martyr_target = game.get(candidate.first_vote_against)
        if martyr_target and martyr_target.alive:
            martyr_target.alive = False
            martyr_target.killed_by = "martyr"
            await mute_player(game, martyr_target.user.id, reason="انتقام الشهيد")
            msg += (
                f"\n\n💀 **انتقام الشهيد:** أخذ معه {martyr_target.user.mention}!"
            )
    exec_embed = discord.Embed(title="⚖️ تم الإعدام", description=msg, color=discord.Color.dark_red())
    exec_file, exec_url = _event_file("execution")
    if exec_url:
        exec_embed.set_image(url=exec_url)
    if exec_file:
        await game.channel.send(embed=exec_embed, file=exec_file)
    else:
        await game.channel.send(embed=exec_embed)


# ============================================================================
# الأوامر
# ============================================================================

@bot.event
async def on_ready():
    log.info("✅ %s متصل وجاهز! (id=%s)", bot.user.name, bot.user.id)


@bot.command(name="مافيا")
async def cmd_start(ctx: commands.Context):
    if not ctx.guild:
        return await ctx.send("هذا الأمر يعمل في السيرفرات فقط.")

    # تحقق من أن القناة مسموح فيها لعب المافيا
    if not is_channel_allowed(ctx.guild.id, ctx.channel.id):
        allowed = get_allowed_channels(ctx.guild.id)
        if not allowed:
            await ctx.send(
                "⚠️ **لم يتم تحديد قناة للعبة بعد.**\n"
                "يجب على المسؤول أولاً تحديد قناة عبر:\n"
                "`&اضافه_قناة <ID القناة>`"
            )
        else:
            await ctx.send(
                "❌ **هذه القناة غير مضافة للعبة المافيا.**\n"
                "اطلب من المسؤول إضافتها عبر: `&اضافه_قناة <ID القناة>`\n"
                "أو انتقل إلى قناة مضافة (شاهد القنوات: `&قنوات`)."
            )
        return

    key = game_key(ctx.guild.id, ctx.channel.id)
    if key in games:
        return await ctx.send("توجد لعبة جارية بالفعل في هذه القناة. استخدم `&إنهاء` لإيقافها.")

    game = MafiaGame(ctx.guild, ctx.channel)
    games[key] = game
    host_id = ctx.author.id
    # المنشئ ينضم تلقائياً للردهة
    game.add_lobby_player(host_id)

    async def on_start(interaction: discord.Interaction):
        game.phase_task = asyncio.create_task(run_game(game))

    async def on_cancel(interaction: discord.Interaction):
        games.pop(key, None)
        await ctx.send("🛑 تم إلغاء غرفة الانتظار.")

    view = LobbyView(game, host_id, on_start, on_cancel)
    embed = discord.Embed(
        title="🕵️‍♂️ غرفة انتظار مافيا 42",
        description=(
            f"👑 **منشئ اللعبة:** {ctx.author.mention} (هو فقط من يستطيع البدء أو الإلغاء)\n"
            f"اضغط **انضمام** للدخول. الحد الأدنى **{MIN_PLAYERS} لاعبين**.\n\n"
            f"**اللاعبون (1):**\n• {ctx.author.mention}"
        ),
        color=discord.Color.gold(),
    )
    embed.set_image(url="attachment://mafia_lobby.png")
    lobby_image_path = Path(__file__).parent / "attached_assets" / "mafia_lobby.png"
    if lobby_image_path.exists():
        file = discord.File(str(lobby_image_path), filename="mafia_lobby.png")
        game.lobby_message = await ctx.send(embed=embed, view=view, file=file)
    else:
        game.lobby_message = await ctx.send(embed=embed, view=view)


@bot.command(name="إنهاء")
async def cmd_end(ctx: commands.Context):
    if not ctx.guild:
        return
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.pop(key, None)
    if not game:
        return await ctx.send("لا توجد لعبة جارية في هذه القناة.")
    if game.phase_task and not game.phase_task.done():
        game.phase_task.cancel()
    # استرجاع الصلاحيات يدوياً (block finally قد يتأخر)
    try:
        await restore_all_perms(game)
    except Exception:
        log.exception("فشل استرجاع الصلاحيات عند الإنهاء اليدوي")
    await ctx.send("🛑 تم إنهاء اللعبة وأُعيدت الصلاحيات.")


@bot.command(name="اضافه_قناة", aliases=["إضافه_قناة", "إضافة_قناة", "اضافة_قناة"])
@commands.has_permissions(administrator=True)
async def cmd_add_channel(ctx: commands.Context, channel_id: int | None = None):
    if not ctx.guild:
        return await ctx.send("هذا الأمر يعمل في السيرفرات فقط.")
    if channel_id is None:
        return await ctx.send(
            "استخدم: `&اضافه_قناة <ID القناة>`\n"
            "(احصل على الـ ID بتفعيل وضع المطور ثم النقر بالزر الأيمن على القناة → نسخ المعرف)"
        )
    channel = ctx.guild.get_channel(channel_id)
    if channel is None:
        return await ctx.send("❌ لم أجد قناة بهذا الـ ID في هذا السيرفر.")
    if not isinstance(channel, discord.TextChannel):
        return await ctx.send("❌ يجب أن تكون قناة نصية.")
    if add_allowed_channel(ctx.guild.id, channel_id):
        await ctx.send(f"✅ تم إضافة {channel.mention} إلى قنوات لعبة المافيا.")
    else:
        await ctx.send(f"⚠️ {channel.mention} موجودة بالفعل في القائمة.")


@cmd_add_channel.error
async def cmd_add_channel_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ هذا الأمر للمسؤولين (Administrator) فقط.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ ID القناة يجب أن يكون رقماً صحيحاً.")


@bot.command(name="حذف_قناة", aliases=["حذف_القناة"])
@commands.has_permissions(administrator=True)
async def cmd_remove_channel(ctx: commands.Context, channel_id: int | None = None):
    if not ctx.guild:
        return await ctx.send("هذا الأمر يعمل في السيرفرات فقط.")
    if channel_id is None:
        return await ctx.send("استخدم: `&حذف_قناة <ID القناة>`")
    if remove_allowed_channel(ctx.guild.id, channel_id):
        channel = ctx.guild.get_channel(channel_id)
        name = channel.mention if channel else f"`{channel_id}`"
        await ctx.send(f"✅ تم حذف {name} من قنوات لعبة المافيا.")
    else:
        await ctx.send("⚠️ هذه القناة ليست في قائمة قنوات اللعبة.")


@cmd_remove_channel.error
async def cmd_remove_channel_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ هذا الأمر للمسؤولين (Administrator) فقط.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ ID القناة يجب أن يكون رقماً صحيحاً.")


@bot.command(name="قنوات_اللعبة", aliases=["قنوات"])
async def cmd_list_channels(ctx: commands.Context):
    if not ctx.guild:
        return
    chans = get_allowed_channels(ctx.guild.id)
    if not chans:
        return await ctx.send("⚠️ لا توجد قنوات لعبة مضافة في هذا السيرفر.")
    lines = []
    for cid in chans:
        ch = ctx.guild.get_channel(cid)
        lines.append(f"• {ch.mention if ch else f'`{cid}` (غير موجودة)'}")
    await ctx.send(embed=discord.Embed(
        title="🎮 قنوات لعبة المافيا",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    ))


@bot.command(name="حالة")
async def cmd_status(ctx: commands.Context):
    if not ctx.guild:
        return
    key = game_key(ctx.guild.id, ctx.channel.id)
    game = games.get(key)
    if not game:
        return await ctx.send("لا توجد لعبة جارية.")
    if game.phase == "waiting":
        return await ctx.send(f"📋 في غرفة الانتظار: {len(game.lobby_user_ids)} لاعبين.")
    alive = game.alive_players()
    dead = game.dead_players()
    alive_list = "\n".join(f"🟢 {p.user.mention}" for p in alive) or "_لا أحد._"
    dead_list = "\n".join(f"💀 {p.user.mention}" for p in dead) or "_لا أحد._"
    embed = discord.Embed(
        title=f"📋 حالة اللعبة — اليوم {game.day_count} ({game.phase})",
        color=discord.Color.blurple(),
    )
    embed.add_field(name=f"الأحياء ({len(alive)})", value=alive_list, inline=False)
    embed.add_field(name=f"الأموات ({len(dead)})", value=dead_list, inline=False)
    await ctx.send(embed=embed)


@bot.command(name="مساعدة")
async def cmd_help(ctx: commands.Context):
    mafia_lines = "\n".join(
        f"{r.emoji} **{r.name}** — {r.description}" for r in ROLES.values() if r.team == "mafia"
    )
    citizen_lines = "\n".join(
        f"{r.emoji} **{r.name}** — {r.description}" for r in ROLES.values() if r.team == "citizens"
    )
    neutral_lines = "\n".join(
        f"{r.emoji} **{r.name}** — {r.description}" for r in ROLES.values() if r.team == "killer"
    )
    embed = discord.Embed(
        title="📖 دليل مافيا 42",
        description=(
            "**الأوامر:**\n"
            "`&مافيا` — فتح غرفة انتظار جديدة (في قناة مسموحة فقط)\n"
            "`&إنهاء` — إنهاء اللعبة الحالية\n"
            "`&حالة` — عرض حالة اللعبة\n"
            "`&همس <رسالة>` — شات سري بين المافيا (وأي قاتل انضم)\n"
            "`&نقاط [@لاعب]` — عرض نقاطك أو نقاط لاعب\n"
            "`&تصنيف` — أعلى 10 لاعبين\n"
            "`&قنوات` — عرض قنوات اللعبة المسموحة\n"
            "`&مساعدة` — عرض هذا الدليل\n\n"
            "**أوامر المسؤولين:**\n"
            "`&اضافه_قناة <ID القناة>` — تفعيل قناة للعبة\n"
            "`&حذف_قناة <ID القناة>` — إلغاء تفعيل قناة\n\n"
            "**ملاحظة:** أثناء الليل تُكتم أصوات اللاعبين، وفي النهار يتكلم الأحياء فقط، "
            "والميتون يبقون مكتومين حتى نهاية اللعبة. تُسترجع جميع الصلاحيات تلقائياً عند انتهاء اللعبة.\n\n"
            f"⏱️ ليل: {NIGHT_SECONDS}s • نقاش: {DISCUSSION_SECONDS}s (قابل للتعديل) • تصويت: {VOTE_SECONDS}s • تأكيد: {CONFIRM_SECONDS}s\n"
            "كل تنفيذ الأدوار يتم برسائل **مخفية** داخل قناة اللعبة.\n"
            f"🏅 كل لاعب جديد يبدأ بـ **{INITIAL_POINTS}** نقطة. "
            "مافيا: +70/-60 • مساعد مافيا/قاتل: +50/-40 • مواطن: +40/-35"
        ),
        color=discord.Color.blue(),
    )
    embed.add_field(name="🔪 فريق المافيا", value=mafia_lines, inline=False)
    embed.add_field(name="👥 فريق المواطنين", value=citizen_lines, inline=False)
    if neutral_lines:
        embed.add_field(name="🗡️ محايد", value=neutral_lines, inline=False)
    embed.set_footer(text="نظام مستوحى من Mafia42 الأصلية")
    await ctx.send(embed=embed)


# ============================================================================
# أوامر الشات السري والنقاط
# ============================================================================

@bot.command(name="همس")
async def cmd_whisper(ctx: commands.Context, *, message: str = ""):
    if not ctx.guild:
        return
    if not message.strip():
        return await ctx.send("استخدم: `&همس <رسالة>` لإرسال رسالة سرية لزملاء المافيا.", delete_after=8)

    # اعثر على لعبة فيها المستخدم كعضو مافيا حي
    sender_game = None
    sender_player = None
    for g in games.values():
        if g.guild.id != ctx.guild.id:
            continue
        p = g.get(ctx.author.id)
        if not p or not p.alive:
            continue
        if p.role.team == "mafia" or (p.role.name == "قاتل" and p.joined_mafia):
            sender_game = g
            sender_player = p
            break

    if not sender_game or not sender_player:
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        return await ctx.send("لست عضو مافيا حياً في أي لعبة جارية.", delete_after=6)

    # احذف الرسالة الأصلية حتى لا يراها بقية اللاعبين
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass

    teammates = [m for m in sender_game.mafia_team_members(alive_only=True)
                 if m.user.id != ctx.author.id]
    sent = 0
    for mate in teammates:
        try:
            await mate.user.send(
                f"🤫 **شات المافيا** ({sender_game.channel.guild.name} #{sender_game.channel.name})\n"
                f"من **{ctx.author.display_name}**: {message}"
            )
            sent += 1
        except discord.HTTPException:
            pass

    # أرسل تأكيد سري للمرسل
    try:
        await ctx.author.send(f"✅ أُرسلت رسالتك السرية إلى {sent} من زملائك في المافيا.")
    except discord.HTTPException:
        pass


@bot.command(name="نقاط")
async def cmd_points(ctx: commands.Context, member: discord.Member | None = None):
    target = member or ctx.author
    pts = ensure_rank(target.id)
    await ctx.send(f"🏅 نقاط {target.mention}: **{pts}**")


@bot.command(name="تصنيف")
async def cmd_leaderboard(ctx: commands.Context):
    ranks = _load_ranks()
    if not ranks:
        return await ctx.send("لا يوجد لاعبون مسجلون بعد.")
    sorted_ranks = sorted(ranks.items(), key=lambda x: -x[1])[:10]
    lines = []
    for i, (uid, pts) in enumerate(sorted_ranks, 1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"**{i}.**"
        lines.append(f"{medal} <@{uid}>: **{pts}**")
    await ctx.send(embed=discord.Embed(
        title="🏆 ترتيب أفضل اللاعبين",
        description="\n".join(lines),
        color=discord.Color.gold(),
    ))


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
