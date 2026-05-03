# Workspace

## Overview

This project is a **Mafia 42 Discord Bot** written in Python, plus a pnpm TypeScript monorepo for supporting artifacts.

---

## Mafia 42 Bot (`mafia_bot.py`)

### Version: 3.1.0 (~4640 lines)

A full-featured Arabic-language Mafia game bot for Discord.

### Key Features
- **30+ Roles** across three teams: Mafia, Citizens, Neutral
- **Achievement System** — 25+ achievements, automatically granted at game end
- **Statistics Tracking** — per-player win rate, streaks, kills, saves, etc.
- **Spectator Mode** — join as spectator (`&تفرج`)
- **Fast Mode** — shortened timers (`&مافيا سريع`)
- **Custom Settings** — admin configurable timers, min players
- **Whisper System** — mafia team secret chat (`&همس`)
- **Anti-AFK** — warn idle players during night phase
- **Tournament System** — registration lobby with Discord buttons (`&بطولة`)
- **Game History** — auto-saved after every game (`mafia_history.json`)
- **Confession System** — dead players reveal role voluntarily (`&اعترف`)
- **Daily Bonus** — streak-based daily point reward (`&مكافأة`)
- **Quiz System** — role knowledge trivia with point rewards (`&مسابقة`)
- **Player Comparison** — side-by-side stats (`&مقارنة`)
- **Point Transfer** — gift points to other players (`&تبرع`)
- **Hall of Fame** — most wins leaderboard (`&لوحة_الشرف`)
- **Server Stats** — win rates per team across all recorded games (`&إحصائيات_السيرفر`)
- **Rank Progress Bar** — visual tier progression (`&رتبي`)

### Bot Prefix
`&`

### Data Files (auto-created)
| File | Contents |
|------|----------|
| `mafia_stats.json` | Per-player statistics |
| `mafia_ranks.json` | Point balances |
| `mafia_achievements.json` | Unlocked achievements |
| `mafia_history.json` | Game history log (last 500 games) |
| `mafia_daily.json` | Daily bonus claim timestamps |
| `mafia_tournaments.json` | Tournament data |

### Deployment
- **Railway**: `Procfile` → `python3 mafia_bot.py`
- **GitHub repo**: `mohammedsj2008-sketch/mafia-bot`
- **Replit workflow**: `Mafia Bot` → `python3 mafia_bot.py`
- **Required secret**: `DISCORD_TOKEN` — must be set in environment

### Token Fix (if bot shows 401 Unauthorized)
1. Go to https://discord.com/developers/applications
2. Select your app → Bot → Reset Token
3. Copy the new token
4. Update the `DISCORD_TOKEN` secret in Replit (Tools → Secrets)
5. Restart the `Mafia Bot` workflow

---

## pnpm Monorepo (TypeScript)

### Stack
- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)

### Key Commands
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks from OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- `pnpm --filter @workspace/api-server run dev` — run API server locally
