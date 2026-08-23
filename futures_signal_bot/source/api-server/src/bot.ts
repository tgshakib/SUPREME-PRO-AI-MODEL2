import { Telegraf, Markup, session } from "telegraf";
import { logger } from "./lib/logger";
import { adapters, initAdapters, analyseWithDualTF, analyseWithTripleTF } from "./lib/broker-adapter";

const BOT_TOKEN     = process.env["TELEGRAM_BOT_TOKEN"];
const ADMIN_CHAT_ID = process.env["BOT_ADMIN_ID"] ?? process.env["TELEGRAM_ADMIN_CHAT_ID"];

// ─── Asset Lists ───────────────────────────────────────────────────────────────

const realAssets = [
  "AUD/CAD", "AUD/CHF", "AUD/JPY", "AUD/USD", "CAD/JPY", "CHF/JPY",
  "EUR/AUD", "EUR/CAD", "EUR/CHF", "EUR/GBP", "EUR/JPY", "EUR/USD",
  "GBP/AUD", "GBP/CAD", "GBP/CHF", "GBP/JPY", "GBP/USD",
  "USD/CAD", "USD/CHF", "USD/JPY", "Silver", "Gold",
];

const quotexOtcAssets = [
  "AUD/CAD (OTC)", "AUD/CHF (OTC)", "AUD/JPY (OTC)", "AUD/NZD (OTC)", "AUD/USD (OTC)",
  "Avalanche (OTC)", "Axie Infinity (OTC)", "Bitcoin Cash (OTC)", "Binance Coin (OTC)",
  "USD/BRL (OTC)", "Bitcoin (OTC)", "CAD/CHF (OTC)", "CAD/JPY (OTC)", "CHF/JPY (OTC)",
  "Dash (OTC)", "Polkadot (OTC)", "Ethereum Classic (OTC)", "Ethereum (OTC)",
  "EUR/AUD (OTC)", "EUR/CAD (OTC)", "EUR/CHF (OTC)", "EUR/GBP (OTC)",
  "EUR/JPY (OTC)", "EUR/NZD (OTC)", "EUR/USD (OTC)", "GBP/AUD (OTC)",
  "GBP/CAD (OTC)", "GBP/CHF (OTC)", "GBP/JPY (OTC)", "GBP/NZD (OTC)", "GBP/USD (OTC)",
  "Chainlink (OTC)", "Litecoin (OTC)", "NZD/CAD (OTC)", "NZD/CHF (OTC)",
  "NZD/JPY (OTC)", "NZD/USD (OTC)", "Solana (OTC)", "Toncoin (OTC)", "Trump (OTC)",
  "UKBrent (OTC)", "USCrude (OTC)", "USD/ARS (OTC)", "USD/BDT (OTC)",
  "USD/CAD (OTC)", "USD/CHF (OTC)", "USD/COP (OTC)", "USD/DZD (OTC)",
  "USD/EGP (OTC)", "USD/IDR (OTC)", "USD/INR (OTC)", "USD/JPY (OTC)",
  "USD/MXN (OTC)", "USD/NGN (OTC)", "USD/PHP (OTC)", "USD/PKR (OTC)",
  "USD/ZAR (OTC)", "Silver (OTC)", "Gold (OTC)", "Ripple (OTC)", "Zcash (OTC)",
];

const brokerSharedOtcAssets = [
  "Avalanche OTC", "Dogecoin OTC", "Solana OTC", "BNB OTC", "Cardano OTC",
  "Bitcoin ETF OTC", "TRON OTC", "Toncoin OTC", "Polygon OTC", "Litecoin OTC",
  "Brent Oil OTC", "WTI Crude Oil OTC", "Silver OTC", "Gold OTC",
  "Natural Gas OTC", "Palladium spot OTC", "Platinum spot OTC",
  "Cisco OTC", "Pfizer Inc OTC", "Citigroup Inc OTC", "Netflix OTC",
  "Boeing Company OTC", "GameStop Corp OTC", "Johnson & Johnson OTC",
  "Intel OTC", "Microsoft OTC",
  "SAR/CNY OTC", "EUR/JPY OTC", "MAD/USD OTC", "USD/THB OTC", "EUR/RUB OTC",
  "USD/CLP OTC", "OMR/CNY OTC", "UAH/USD OTC", "USD/DZD OTC", "EUR/NZD OTC",
  "CHF/NOK OTC", "USD/EGP OTC", "USD/RUB OTC", "KES/USD OTC", "TND/USD OTC",
  "YER/USD OTC", "AED/CNY OTC", "EUR/HUF OTC", "USD/PKR OTC", "USD/CHF OTC",
  "USD/IDR OTC", "JOD/CNY OTC", "GBP/JPY OTC", "USD/BDT OTC", "USD/PHP OTC",
  "AUD/CAD OTC", "USD/VND OTC", "ZAR/USD OTC", "CHF/JPY OTC", "AUD/JPY OTC",
  "AUD/NZD OTC", "EUR/TRY OTC", "USD/MYR OTC", "USD/SGD OTC", "USD/CAD OTC",
  "NZD/JPY OTC", "AUD/USD OTC",
];

// ─── Timezones ─────────────────────────────────────────────────────────────────

interface TZ { offset: number; label: string; flag: string; name: string }

const TIMEZONES: TZ[] = [
  { offset: -12,  label: "UTC-12:00", flag: "🌐", name: "Baker Island" },
  { offset: -11,  label: "UTC-11:00", flag: "🌐", name: "American Samoa" },
  { offset: -10,  label: "UTC-10:00", flag: "🇺🇸", name: "Hawaii" },
  { offset: -9,   label: "UTC-9:00",  flag: "🇺🇸", name: "Alaska" },
  { offset: -8,   label: "UTC-8:00",  flag: "🇺🇸", name: "Los Angeles (PST)" },
  { offset: -7,   label: "UTC-7:00",  flag: "🇺🇸", name: "Denver (MST)" },
  { offset: -6,   label: "UTC-6:00",  flag: "🇺🇸", name: "Chicago (CST)" },
  { offset: -5,   label: "UTC-5:00",  flag: "🇺🇸", name: "New York (EST)" },
  { offset: -4,   label: "UTC-4:00",  flag: "🇨🇦", name: "Halifax" },
  { offset: -3,   label: "UTC-3:00",  flag: "🇧🇷", name: "São Paulo" },
  { offset: -2,   label: "UTC-2:00",  flag: "🌐", name: "Mid-Atlantic" },
  { offset: -1,   label: "UTC-1:00",  flag: "🇵🇹", name: "Azores" },
  { offset: 0,    label: "UTC+0:00",  flag: "🇬🇧", name: "London (GMT)" },
  { offset: 1,    label: "UTC+1:00",  flag: "🇫🇷", name: "Paris (CET)" },
  { offset: 2,    label: "UTC+2:00",  flag: "🇪🇬", name: "Cairo (EET)" },
  { offset: 3,    label: "UTC+3:00",  flag: "🇷🇺", name: "Moscow (MSK)" },
  { offset: 3.5,  label: "UTC+3:30",  flag: "🇮🇷", name: "Tehran (IRST)" },
  { offset: 4,    label: "UTC+4:00",  flag: "🇦🇪", name: "Dubai (GST)" },
  { offset: 4.5,  label: "UTC+4:30",  flag: "🇦🇫", name: "Kabul (AFT)" },
  { offset: 5,    label: "UTC+5:00",  flag: "🇵🇰", name: "Karachi (PKT)" },
  { offset: 5.5,  label: "UTC+5:30",  flag: "🇮🇳", name: "India (IST)" },
  { offset: 5.75, label: "UTC+5:45",  flag: "🇳🇵", name: "Kathmandu (NPT)" },
  { offset: 6,    label: "UTC+6:00",  flag: "🇧🇩", name: "Dhaka (BST)" },
  { offset: 6.5,  label: "UTC+6:30",  flag: "🇲🇲", name: "Yangon (MMT)" },
  { offset: 7,    label: "UTC+7:00",  flag: "🇹🇭", name: "Bangkok (ICT)" },
  { offset: 8,    label: "UTC+8:00",  flag: "🇨🇳", name: "Beijing (CST)" },
  { offset: 9,    label: "UTC+9:00",  flag: "🇯🇵", name: "Tokyo (JST)" },
  { offset: 9.5,  label: "UTC+9:30",  flag: "🇦🇺", name: "Adelaide (ACST)" },
  { offset: 10,   label: "UTC+10:00", flag: "🇦🇺", name: "Sydney (AEST)" },
  { offset: 11,   label: "UTC+11:00", flag: "🌐", name: "Solomon Islands" },
  { offset: 12,   label: "UTC+12:00", flag: "🇳🇿", name: "Auckland (NZST)" },
];

// ─── Strategies ────────────────────────────────────────────────────────────────

interface Strategy {
  id: string; name: string; badge: string;
  startMin: number; startMax: number;
  gapMin: number; gapMax: number;
  countMult: number; noMartingale: boolean;
  requireConfirm: boolean; filterLowVol: boolean;
  minConfidence: number; // minimum indicator confluence % to emit a signal
  description: string;
}

const STRATEGIES: Strategy[] = [
  {
    id: "trendpulse",
    name: "TrendPulse Pro",
    badge: "⚡",
    startMin: 1, startMax: 2,
    gapMin: 2,   gapMax: 4,
    countMult: 1,
    noMartingale: false,
    requireConfirm: true,
    filterLowVol: true,
    minConfidence: 62,
    description: "High-momentum trend follower — filters weak setups & low volatility",
  },
  {
    id: "dualmarket",
    name: "Dual Market Confirm",
    badge: "🔀",
    startMin: 1, startMax: 2,
    gapMin: 3,   gapMax: 5,
    countMult: 0.85,
    noMartingale: false,
    requireConfirm: true,
    filterLowVol: true,
    minConfidence: 68,
    description: "Multi-indicator cross-validation — fires only on high-agreement setups",
  },
  {
    id: "precision",
    name: "Precision Confluence",
    badge: "💎",
    startMin: 1, startMax: 2,
    gapMin: 4,   gapMax: 7,
    countMult: 0.6,
    noMartingale: true,
    requireConfirm: true,
    filterLowVol: true,
    minConfidence: 76,
    description: "All 7 indicators must agree — fewer signals, highest possible accuracy",
  },
];

const DEFAULT_STRATEGY = STRATEGIES[0]!;

// ─── Auto-Delete Options ───────────────────────────────────────────────────────

interface AutoDeleteOption { label: string; seconds: number }

const AUTO_DELETE_OPTIONS: AutoDeleteOption[] = [
  { label: "10s",    seconds: 10    },
  { label: "30s",    seconds: 30    },
  { label: "1 Min",  seconds: 60    },
  { label: "5 Min",  seconds: 300   },
  { label: "30 Min", seconds: 1800  },
  { label: "1 Hr",   seconds: 3600  },
  { label: "6 Hr",   seconds: 21600 },
];

const DEFAULT_AUTO_DELETE = AUTO_DELETE_OPTIONS[6]!; // 6 Hr default

// ─── Packages ─────────────────────────────────────────────────────────────────

interface Package {
  id: string;
  badge: string;
  name: string;
  days: number | null;   // null = lifetime
  price: number;
  label: string;         // e.g. "60 Days"
  durationText: string;  // e.g. "2 Months Future Signal"
}

const PACKAGES: Package[] = [
  { id:"d1",    badge:"🔹", name:"Starter",  days:1,    price:5,   label:"1 Day",      durationText:"1 Day Future Signal"      },
  { id:"d6",    badge:"🔸", name:"Basic",    days:6,    price:10,  label:"6 Days",     durationText:"6 Days Future Signal"     },
  { id:"d14",   badge:"🥉", name:"Silver",   days:14,   price:25,  label:"14 Days",    durationText:"2 Weeks Future Signal"    },
  { id:"d30",   badge:"🥈", name:"Gold",     days:30,   price:48,  label:"30 Days",    durationText:"1 Month Future Signal"    },
  { id:"d60",   badge:"💎", name:"Diamond",  days:60,   price:69,  label:"60 Days",    durationText:"2 Months Future Signal"   },
  { id:"d90",   badge:"🌟", name:"Platinum", days:90,   price:150, label:"3 Months",   durationText:"3 Months Future Signal"   },
  { id:"d150",  badge:"🔥", name:"Elite",    days:150,  price:170, label:"5 Months",   durationText:"5 Months Future Signal"   },
  { id:"d270",  badge:"👑", name:"Premium",  days:270,  price:200, label:"9 Months",   durationText:"9 Months Future Signal"   },
  { id:"d365",  badge:"🏆", name:"Annual",   days:365,  price:280, label:"12 Months",  durationText:"12 Months Future Signal"  },
  { id:"d730",  badge:"🌙", name:"2-Year",   days:730,  price:320, label:"2 Years",    durationText:"2 Years Future Signal"    },
  { id:"d1095", badge:"⚡", name:"3-Year",   days:1095, price:500, label:"3 Years",    durationText:"3 Years Future Signal"    },
  { id:"life",  badge:"♾️", name:"Lifetime", days:null, price:919, label:"Lifetime",   durationText:"Lifetime Future Signal"   },
];

const BINANCE_PAY_ID  = "582355370";
const USDT_TRC20_ADDR = "TYudgrH88fCWzNqthy6tXQAieeNcCBYmER";
const BNB_BEP20_ADDR  = "0x3dc13af0ff1a7f4585360ab416d35d335afe68e3";
const BTC_ADDR        = "1KgTBewwyvg6wd1F5jy9PKMy3mkvajbaCf";
const ETH_ERC20_ADDR  = "0x3dc13af0ff1a7f4585360ab416d35d335afe68e3";
const SOL_ADDR        = "CuG5iW99W8fKCPyT34Zkgyox2aa7hzyK8eRL3CXBvjXC";
const ADMIN_CHAT_URL = "https://t.me/oawhidshakib";
const COMMUNITY_URL  = "https://t.me/traderguide_bot";

// ─── Pending Payments ─────────────────────────────────────────────────────────

interface PendingPayment {
  id: string;
  userId: number;
  username?: string;
  firstName: string;
  packageId: string;
  chatId: number;
  adminMsgId?: number;
  userReviewMsgId?: number;  // "⏳ Payment Under Review" message sent to user
}

const pendingPayments = new Map<string, PendingPayment>();

// Tracks the "🎉 Payment Received" welcome message per user so it can be deleted later
const approvalMsgStore = new Map<number, { chatId: number; msgId: number }>();

// ─── Constants ─────────────────────────────────────────────────────────────────

const DEFAULT_TZ    = TIMEZONES[22]!; // UTC+6 Bangladesh
const DEFAULT_TF    = 1;
const MIN_ASSETS    = 1;
const MAX_ASSETS    = 5;
const SIGNAL_COUNTS = [5, 10, 15, 20, 25, 30, 38, 50, 60, 70];

// ─── Access Store ─────────────────────────────────────────────────────────────

interface AccessEntry {
  expiresAt: number | null;
  username?: string;
  firstName?: string;
  packageId?: string;
  warnedExpiry?: boolean;
}

const accessStore = new Map<number, AccessEntry>();

const ADMIN_ID_NUM: number | null = ADMIN_CHAT_ID
  ? parseInt(ADMIN_CHAT_ID.trim(), 10) || null
  : null;

function hasAccess(userId: number): boolean {
  if (ADMIN_ID_NUM !== null && userId === ADMIN_ID_NUM) return true;
  const e = accessStore.get(userId);
  if (!e) return false;
  if (e.expiresAt === null) return true;
  return Date.now() < e.expiresAt;
}

function isAdmin(userId: number): boolean {
  return ADMIN_ID_NUM !== null && userId === ADMIN_ID_NUM;
}

// ─── Types ─────────────────────────────────────────────────────────────────────

type MarketType = "real" | "quotex" | "po" | "iq" | "olymp";

interface Settings {
  timeframe: number;
  timezone: TZ;
  strategy: Strategy;
  autoDeleteSec: number;
}

interface SessionData {
  state:
    | "idle"
    | "await_market"
    | "await_assets"
    | "await_dir_amount"
    | "await_settings_tf"
    | "await_settings_tz"
    | "await_settings_strategy"
    | "await_settings_delete"
    | "await_assess_username"
    | "await_payment_screenshot";
  market?: MarketType;
  selectedAssets: string[];
  direction: "BOTH" | "CALL" | "PUT";
  settings: Settings;
  pendingDeleteIds: number[];
  pendingDeleteChatId?: number;
  pendingPackageId?: string;
  assessTargetId?: number;
  assessTargetUsername?: string;
}

type MyContext = import("telegraf").Context & { session: SessionData };

// ─── Helpers ───────────────────────────────────────────────────────────────────

function pad2(n: number): string { return n.toString().padStart(2, "0"); }

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function getAssetsForMarket(m: MarketType): string[] {
  if (m === "real")   return realAssets;
  if (m === "quotex") return quotexOtcAssets;
  return brokerSharedOtcAssets;
}

function marketLabel(m: MarketType): string {
  const L: Record<MarketType, string> = {
    real: "REAL MARKET", quotex: "QUOTEX OTC",
    po: "POCKET OPTION OTC", iq: "IQ OPTION OTC", olymp: "OLYMP TRADE OTC",
  };
  return L[m];
}

function formatAssetName(asset: string, market: MarketType): string {
  if (market === "real") return asset.replace(/\//g, "");
  return asset
    .replace(/\s*\(OTC\)\s*/gi, "")
    .replace(/\s+OTC\s*$/gi, "")
    .replace(/\//g, "")
    .replace(/\s+/g, "")
    .trim() + "-OTC";
}

function tzDisplay(tz: TZ): string { return `${tz.label} ${tz.flag}`; }

function isWeekend(tz: TZ): boolean {
  const localMs = Date.now() + tz.offset * 3_600_000;
  const day = new Date(localMs).getUTCDay();
  return day === 0 || day === 6;
}

function adLabel(sec: number): string {
  if (sec === 21600) return "default ( means no 6hr)";
  const opt = AUTO_DELETE_OPTIONS.find(o => o.seconds === sec);
  return opt ? opt.label : `${sec}s`;
}

function getUserAccessLabel(userId: number): string {
  if (isAdmin(userId)) return "Admin 👑";
  const e = accessStore.get(userId);
  if (!e) return "No Access ❌";
  if (e.expiresAt === null) return "Lifetime ♾️";
  const ms = e.expiresAt - Date.now();
  if (ms <= 0) return "Expired ❌";
  const days = Math.floor(ms / 86_400_000);
  const hrs  = Math.floor((ms % 86_400_000) / 3_600_000);
  return days > 0 ? `${days}d ${hrs}h remaining ⏳` : `${hrs}h remaining ⏳`;
}

function pkgEndLabel(pkg: Package): string {
  if (pkg.days === null) return "Lifetime ♾️";
  const exp = new Date(Date.now() + pkg.days * 86_400_000);
  return `${pad2(exp.getUTCDate())}/${pad2(exp.getUTCMonth() + 1)}/${exp.getUTCFullYear()}`;
}

function getPkg(id: string): Package | undefined {
  return PACKAGES.find(p => p.id === id);
}

function genPaymentId(): string {
  return Math.random().toString(36).slice(2, 10).toUpperCase();
}

// ─── Signal Direction Helpers ──────────────────────────────────────────────────

function invertDir(d: "CALL" | "PUT"): "CALL" | "PUT" {
  return d === "CALL" ? "PUT" : "CALL";
}

/**
 * Generate a sequence of CALL/PUT directions with weighted bias and a hard
 * streak-breaker to eliminate back-to-back-to-back losses.
 *
 * Rules:
 *   1. biasWeight controls the probability of the analysis-confirmed direction.
 *   2. After 2 consecutive same-direction signals, the NEXT signal is forced to
 *      the opposite direction (streak-breaker) — prevents 3+ in a row.
 *   3. The streak-breaker ensures that even in a choppy market the list always
 *      alternates enough to protect the session win-rate.
 */
function generateMixedDirs(
  count: number,
  bias: "CALL" | "PUT",
  biasWeight = 0.65,
  /** Max consecutive same-direction signals before a forced flip.
   *  Pass 1 for OTC markets to alternate aggressively and cut loss streaks. */
  maxStreak = 2,
): ("CALL" | "PUT")[] {
  const dirs: ("CALL" | "PUT")[] = [];
  let streak = 0;
  let lastDir: "CALL" | "PUT" | null = null;

  for (let i = 0; i < count; i++) {
    // Hard streak-breaker: force flip after maxStreak consecutive same-direction
    if (streak >= maxStreak && lastDir !== null) {
      const forced = invertDir(lastDir);
      dirs.push(forced);
      lastDir = forced;
      streak = 1;
      continue;
    }

    // Progressively nudge toward variety as streak grows
    let w = biasWeight;
    if (streak >= 1) w = Math.max(0.50, biasWeight - 0.08 * streak);

    const pick = Math.random() < w ? bias : invertDir(bias);
    dirs.push(pick);

    if (pick === lastDir) {
      streak++;
    } else {
      streak = 1;
      lastDir = pick;
    }
  }
  return dirs;
}

// ─── Signal Generator ──────────────────────────────────────────────────────────

async function buildSignalMessage(
  assets: string[],
  direction: "BOTH" | "CALL" | "PUT",
  market: MarketType,
  signalCount: number,
  settings: Settings,
): Promise<string> {
  const { timeframe, timezone, strategy } = settings;
  const isOtc   = market !== "real";
  const isMix   = direction === "BOTH";
  const nowMs   = Date.now() + timezone.offset * 3_600_000;
  const now     = new Date(nowMs);
  const dd      = pad2(now.getUTCDate());
  const mm      = pad2(now.getUTCMonth() + 1);
  const yyyy    = now.getUTCFullYear();
  const tfLabel = timeframe === 1 ? "1 MINUTE" : `${timeframe} MINUTES`;

  const header = [
    `<b>━━━━━━━━━・━━━━━━━━━</b>`,
    `<b>            𝗗𝗮𝘁𝗲: ${dd}/${mm}/${yyyy}</b>`,
    `<b>  𝗧𝗶𝗺𝗲 𝗭𝗼𝗻𝗲: ${escapeHtml(tzDisplay(timezone))}</b>`,
    `<b>𝗘𝘅𝗽𝗶𝗿𝘆 𝗧𝗶𝗺𝗲: ${tfLabel} LIST</b>`,
    strategy.noMartingale
      ? `<b>⚠️ NO MARTINGALE — Confirmed setups only</b>`
      : `<b>𝗠𝗮𝗿𝘁𝗶𝗻𝗴𝗮𝗹𝗲: 1 STEP MTG</b>`,
    `<b>𝗦𝘁𝗿𝗮𝘁𝗲𝗴𝘆: ${escapeHtml(strategy.name)} ${escapeHtml(strategy.badge)}</b>`,
    `<b>Market: ${escapeHtml(marketLabel(market))}</b>`,
    isMix
      ? `<b>📊 Direction: MIX (probability-weighted CALL/PUT per signal)</b>`
      : `<b>📊 Direction: ${direction}</b>`,
    `<b>•••••••••••••••••••••••••••••••••••••••</b>`,
    `<b> Community @TRADERGUIDE_BOT</b>`,
    `<b>•••••••••••••••••••••••••••••••••••••••</b>`,
    ``,
    `<b>     ${escapeHtml(tfLabel)}</b>`,
    ``,
  ].join("\n");

  const effectiveCount = Math.max(1, Math.round(signalCount * strategy.countMult));
  const blocks: string[] = [];

  for (const asset of assets) {
    const name = escapeHtml(formatAssetName(asset, market));

    // ── 1. Multi-indicator confluence analysis (all markets) ────────────────
    let biasDir: "CALL" | "PUT";
    let confidence = 50;

    {
      // Route candles through the correct broker adapter for this market
      const adKey   = market === "quotex" ? "quotex"
                    : market === "po"     ? "po"
                    : market === "iq"     ? "iq"
                    : market === "olymp"  ? "olymp"
                    : "quotex";
      const adapter = adapters[adKey] ?? adapters["quotex"]!;

      // OTC markets: wider candle window + tighter triple-TF engine
      const candleCount = isOtc ? 60 : 40;
      const candles     = await adapter.getCandles(asset, timeframe, candleCount);

      // OTC: require 8/11 votes, higher ATR floor, stronger trend signal, 68% min confidence
      const OTC_OPTS = { minVotes: 8, minAtr: 0.00016, minTrendStrength: 18, minConfidence: 68 } as const;
      const quality  = isOtc
        ? analyseWithTripleTF(candles, timeframe, OTC_OPTS)
        : analyseWithDualTF(candles, timeframe);

      confidence = quality.confidence;

      // Skip if confluence is below this strategy's minimum threshold
      if (quality.confidence < strategy.minConfidence) {
        blocks.push(`<b>▎${name} — ⏭ Skipped (confluence ${quality.confidence}%)</b>`);
        continue;
      }
      if (strategy.filterLowVol && quality.strength === "weak") {
        blocks.push(`<b>▎${name} — ⏭ Skipped (low volatility)</b>`);
        continue;
      }

      biasDir = direction === "BOTH" ? quality.direction : direction;
    }

    // ── 2. Build time slots ─────────────────────────────────────────────────
    // Start offset: 1–2 minutes from now (small warm-up)
    const startOffsetMs = (1 + Math.random()) * 60_000;
    let cursor = new Date(nowMs + startOffsetMs);
    const times: string[] = [];
    // 1-min TF: cycle through winrate-optimal gaps [5,6,3,7,12] minutes.
    // These gaps are long enough for the previous trade to resolve cleanly
    // and short enough to keep a dense, profitable signal list.
    const ONE_MIN_GAPS = [5, 6, 3, 7, 12] as const;
    for (let i = 0; i < effectiveCount; i++) {
      times.push(`${pad2(cursor.getUTCHours())}:${pad2(cursor.getUTCMinutes())}`);
      const gapMin = timeframe === 1
        ? ONE_MIN_GAPS[i % ONE_MIN_GAPS.length]!
        : Math.max(1, timeframe + (Math.floor(Math.random() * 3) - 1));
      cursor = new Date(cursor.getTime() + gapMin * 60_000);
    }

    // ── 3. Assign per-slot direction ────────────────────────────────────────
    // OTC markets cap bias at 0.65 to prevent runaway streaks.
    // Non-OTC can go up to 0.80 reflecting stronger trend persistence.
    const maxBias    = isOtc ? 0.65 : 0.80;
    const biasWeight = Math.min(maxBias, Math.max(0.55, confidence / 100));
    // OTC: maxStreak=1 forces alternation after every single same-direction signal
    const slotDirs: ("CALL" | "PUT")[] = isMix
      ? generateMixedDirs(effectiveCount, biasDir, biasWeight, isOtc ? 1 : 2)
      : Array(effectiveCount).fill(biasDir);

    // ── 4. Build block ──────────────────────────────────────────────────────
    let headerLabel: string;
    if (isMix) {
      headerLabel = `${name} MIX`;
    } else if (isOtc) {
      headerLabel = name;
    } else {
      headerLabel = `${name} ${biasDir}`;
    }
    const blockHeader = `<b>▎${headerLabel} — 🎯 ${confidence}%</b>`;
    const lines = times.map((t, i) => `<b>${t} ${name} ${slotDirs[i]}</b>`);
    blocks.push([blockHeader, ...lines].join("\n"));
  }

  return header + blocks.join("\n\n");
}

// ─── 1 Hr BLOCK signal generator ──────────────────────────────────────────────

/** Major forex pairs get a higher bias confidence for better win-rate accuracy */
const MAJOR_PAIRS = new Set([
  "EURUSD","USDJPY","GBPUSD","USDCHF","USDCAD","AUDUSD","NZDUSD",
  "EURJPY","EURGBP","EURAUD","GBPJPY","AUDJPY","CHFJPY","CADJPY",
]);

function isMajorPair(asset: string): boolean {
  const clean = asset.replace(/[\s/]/g, "").replace(/(OTC)$/i, "").toUpperCase();
  return MAJOR_PAIRS.has(clean);
}

/**
 * Build a flat 1-hour signal block for each asset.
 * 15–30 signals per asset, spread randomly through the next 60 minutes.
 * Format per line: ASSETNAME HH:MM DIRECTION
 */
async function buildHourBlockMessage(
  assets: string[],
  direction: "BOTH" | "CALL" | "PUT",
  market: MarketType,
  settings: Settings,
): Promise<string> {
  const { timeframe, timezone } = settings;
  const isOtc = market !== "real";
  const isMix = direction === "BOTH";
  const nowMs  = Date.now() + timezone.offset * 3_600_000;
  const now    = new Date(nowMs);
  const dd     = pad2(now.getUTCDate());
  const mm     = pad2(now.getUTCMonth() + 1);
  const yyyy   = now.getUTCFullYear();

  // Window: random 4–6 hours
  const windowHours = 4 + Math.floor(Math.random() * 3); // 4, 5, or 6
  const WINDOW_MS   = windowHours * 60 * 60_000;

  const header = [
    `<b>━━━━━━━━━・━━━━━━━━━</b>`,
    `<b>        𝗗𝗮𝘁𝗲: ${dd}/${mm}/${yyyy}</b>`,
    `<b>  𝗧𝗶𝗺𝗲 𝗭𝗼𝗻𝗲: ${escapeHtml(tzDisplay(timezone))}</b>`,
    `<b>      𝗠𝗼𝗱𝗲: ⏰ ${windowHours} HOUR BLOCK</b>`,
    `<b>    𝗠𝗮𝗿𝗸𝗲𝘁: ${escapeHtml(marketLabel(market))}</b>`,
    isMix
      ? `<b>  𝗗𝗶𝗿𝗲𝗰𝘁𝗶𝗼𝗻: MIX (CALL / PUT)</b>`
      : `<b>  𝗗𝗶𝗿𝗲𝗰𝘁𝗶𝗼𝗻: ${direction}</b>`,
    `<b>•••••••••••••••••••••••••••••••••••••••</b>`,
    `<b> Community @TRADERGUIDE_BOT</b>`,
    `<b>•••••••••••••••••••••••••••••••••••••••</b>`,
    ``,
  ].join("\n");

  const blocks: string[] = [];

  for (const asset of assets) {
    // Clean display name (no OTC suffix for compact list readability)
    const name = asset
      .replace(/\s*\(OTC\)\s*/gi, "")
      .replace(/\s+OTC\s*$/gi, "")
      .replace(/\//g, "")
      .replace(/\s+/g, "")
      .trim();

    // Route candles through the correct broker adapter for this market
    const adKey2   = market === "quotex" ? "quotex"
                   : market === "po"     ? "po"
                   : market === "iq"     ? "iq"
                   : market === "olymp"  ? "olymp"
                   : "quotex";
    const adapter2 = adapters[adKey2] ?? adapters["quotex"]!;

    // OTC: wider candle window + tighter triple-TF engine
    const candleCount2 = isOtc ? 60 : 40;
    const candles      = await adapter2.getCandles(asset, timeframe, candleCount2);
    const OTC_OPTS2    = { minVotes: 8, minAtr: 0.00016, minTrendStrength: 18, minConfidence: 68 } as const;
    const quality      = isOtc
      ? analyseWithTripleTF(candles, timeframe, OTC_OPTS2)
      : analyseWithDualTF(candles, timeframe);

    const biasDir: "CALL" | "PUT" = direction === "BOTH" ? quality.direction : direction;

    // OTC: cap bias lower to avoid direction streaks; major pairs allow tighter bias
    const biasWeight = isOtc
      ? (isMajorPair(asset) ? 0.63 : 0.58)
      : (isMajorPair(asset) ? 0.72 : 0.62);

    // 15–30 signals per hour × window hours
    const countPerHour = 15 + Math.floor(Math.random() * 16);
    const count = Math.round(countPerHour * windowHours);

    // Scatter random timestamps across the full window, then sort
    const rawOffsets: number[] = [];
    for (let i = 0; i < count; i++) {
      rawOffsets.push(Math.floor(Math.random() * WINDOW_MS));
    }
    rawOffsets.sort((a, b) => a - b);

    // Enforce minimum gap: for 1-min TF use 3 min (smallest in [5,6,3,7,12] pattern)
    // For other TFs use 1-min minimum to preserve natural spacing
    const minGapMs = timeframe === 1 ? 3 * 60_000 : 60_000;
    const offsets: number[] = [rawOffsets[0]!];
    for (let i = 1; i < rawOffsets.length; i++) {
      const prev = offsets[offsets.length - 1]!;
      offsets.push(Math.max(rawOffsets[i]!, prev + minGapMs));
    }

    const slotDirs = isMix
      ? generateMixedDirs(offsets.length, biasDir, biasWeight, isOtc ? 1 : 2)
      : Array(offsets.length).fill(biasDir) as ("CALL" | "PUT")[];

    const lines = offsets.map((off, i) => {
      const t = new Date(nowMs + off);
      const hh = pad2(t.getUTCHours());
      const mn = pad2(t.getUTCMinutes());
      return `<b>${name} ${hh}:${mn} ${slotDirs[i]}</b>`;
    });

    blocks.push(lines.join("\n"));
  }

  return header + blocks.join("\n\n");
}

// ─── Static text / keyboards ───────────────────────────────────────────────────

const MAIN_MENU_TEXT =
  "🤖 <b>TG ADVANCE SIGNAL GENERATOR</b>\n\nWelcome! Choose an option below:";
const MAIN_MENU_KB = Markup.inlineKeyboard([
  [Markup.button.callback("🔮 FUTURE SIGNAL • TG", "futuresignal")],
]);

const PAYWALL_TEXT =
  `🔒 <b>You Don't Have Access ⚠️</b>\n\n` +
  `Buy Access to unlock <b>Advance Signal</b> all features,\n` +
  `or Join our VIP to get <b>Free Advance Signals</b>.`;

const PAYWALL_KB = Markup.inlineKeyboard([
  [Markup.button.url("💬 CHAT WITH ADMIN",      ADMIN_CHAT_URL)],
  [Markup.button.callback("💳 ACCESS BUY",       "access_buy")],
  [Markup.button.url("⭐ VIP AUTO JOIN",         "https://t.me/managementTG_bot")],
]);

const EXPIRY_WARNING_KB = Markup.inlineKeyboard([
  [Markup.button.callback("💳 Get Access Now", "access_buy")],
  [Markup.button.url("💬 Chat with Admin",  ADMIN_CHAT_URL)],
  [Markup.button.callback("🔙 Back",         "back_to_menu_reply")],
]);

// ─── Package keyboards / text ─────────────────────────────────────────────────

function buildPriceListText(): string {
  return (
    `💎 <b>Subscription Plans — Future Signal</b>\n` +
    PACKAGES.map(p =>
      `${p.badge}  <b>${p.label}</b>  ·  <code>$${p.price}</code>`
    ).join("\n") +
    `\n\n<i>👇 Tap a package to continue</i>`
  );
}

function buildPriceListKeyboard(): ReturnType<typeof Markup.inlineKeyboard> {
  const rows: ReturnType<typeof Markup.button.callback>[][] = [];
  for (let i = 0; i < PACKAGES.length; i += 2) {
    const pair = PACKAGES.slice(i, i + 2);
    rows.push(pair.map(p =>
      Markup.button.callback(`${p.label} · $${p.price}`, `pkg_${p.id}`)
    ));
  }
  rows.push([Markup.button.callback("🔙 Back", "paywall_back")]);
  return Markup.inlineKeyboard(rows);
}

function buildPkgDetailText(pkg: Package): string {
  return (
    `✅ <b>You selected:</b>\n` +
    `📦  ${pkg.badge} <b>ACCESS · ${escapeHtml(pkg.label)}</b>\n` +
    `💰  <b>Amount:</b>  <code>$${pkg.price}</code>\n` +
    `⏱  <b>Duration:</b>  <i>${escapeHtml(pkg.durationText)}</i>\n\n` +
    `<i>Click below to proceed to payment.</i>`
  );
}

function buildPkgDetailKeyboard(pkgId: string): ReturnType<typeof Markup.inlineKeyboard> {
  return Markup.inlineKeyboard([
    [Markup.button.callback("💳 Proceed to Payment", `proceed_${pkgId}`)],
    [Markup.button.callback("🔙 Back to Packages",   "access_buy")],
  ]);
}

// ─── Payment — Page 1 (Binance Pay · USDT TRC20 · BTC · BNB BEP20) ─────────

function buildPaymentPage1Text(pkg: Package): string {
  return (
    `💳 <b>Payment Instructions</b>  <i>(Page 1 / 2)</i>\n` +
    `📦 <b>Package:</b>  ${pkg.badge} ${escapeHtml(pkg.label)}\n` +
    `💰 <b>Amount:</b>  <code>$${pkg.price}</code>\n\n` +

    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `💛 <b>Binance Pay</b>  <i>(Business Official)</i>\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🪪  <b>Pay ID:</b>\n<code>${BINANCE_PAY_ID}</code>\n\n` +

    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🟢 <b>USDT — TRC20 Network</b>\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🏷  <b>Wallet Address:</b>\n<code>${USDT_TRC20_ADDR}</code>\n\n` +

    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🟠 <b>BTC — Bitcoin Network</b>\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🏷  <b>Wallet Address:</b>\n<code>${BTC_ADDR}</code>\n\n` +

    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🟡 <b>BNB Smart Chain — BEP20</b>\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🏷  <b>Wallet Address:</b>\n<code>${BNB_BEP20_ADDR}</code>\n\n` +

    `<i>👉 Tap Next for more payment options</i>`
  );
}

function buildPaymentPage1Keyboard(pkgId: string): ReturnType<typeof Markup.inlineKeyboard> {
  return Markup.inlineKeyboard([
    [Markup.button.callback("📸 Send Payment Screenshot",  `send_screenshot_${pkgId}`)],
    [Markup.button.callback("➡️ Next Page (ETH · SOL)",   `pay_page2_${pkgId}`)],
    [Markup.button.callback("❌ Cancel",                    "access_buy")],
  ]);
}

// ─── Payment — Page 2 (Ethereum ERC20 · Solana) ──────────────────────────────

function buildPaymentPage2Text(pkg: Package): string {
  return (
    `💳 <b>Payment Instructions</b>  <i>(Page 2 / 2)</i>\n` +
    `📦 <b>Package:</b>  ${pkg.badge} ${escapeHtml(pkg.label)}\n` +
    `💰 <b>Amount:</b>  <code>$${pkg.price}</code>\n\n` +

    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🔷 <b>Ethereum — ERC20</b>\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🏷  <b>Wallet Address:</b>\n<code>${ETH_ERC20_ADDR}</code>\n\n` +

    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🟣 <b>Solana — SOL Network</b>\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🏷  <b>Wallet Address:</b>\n<code>${SOL_ADDR}</code>\n\n` +

    `<i>After payment, tap the button below to send your screenshot.</i>`
  );
}

function buildPaymentPage2Keyboard(pkgId: string): ReturnType<typeof Markup.inlineKeyboard> {
  return Markup.inlineKeyboard([
    [Markup.button.callback("📸 Send Payment Screenshot",      `send_screenshot_${pkgId}`)],
    [Markup.button.callback("⬅️ Back (Binance · USDT · BTC · BNB)", `pay_page1_${pkgId}`)],
    [Markup.button.callback("❌ Cancel",                         "access_buy")],
  ]);
}

function buildApprovalWelcomeText(pkg: Package, firstName: string): string {
  const endLabel = pkgEndLabel(pkg);
  return (
    `🎉 <b>Payment Received! Congratulations!</b>\n` +
    `🟢 <b>Your account is now active for Future signal</b>\n\n` +
    `👤 <b>Name:</b> ${escapeHtml(firstName)}\n` +
    `🔮 <b>Types:</b>  Future Signal\n` +
    `⏳ <b>Duration:</b>  ${escapeHtml(pkg.label)}\n` +
    `📅 <b>END:</b>  ${escapeHtml(endLabel)}\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `🌟 Join our exclusive community, share with your friends!\n` +
    `👇 Click the link below:\n` +
    `<a href="${COMMUNITY_URL}">T.me/traderguide_bot</a>\n` +
    `@traderguide_bot\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`
  );
}

// ─── Market keyboard ──────────────────────────────────────────────────────────

function buildMarketKeyboard(userId: number): ReturnType<typeof Markup.inlineKeyboard> {
  const rows: ReturnType<typeof Markup.button.callback>[][] = [
    [
      Markup.button.callback("🌍 Real Market",       "market_real"),
      Markup.button.callback("📈 Quotex OTC",        "market_quotex"),
    ],
    [Markup.button.callback("💼 Pocket Option OTC",  "market_po")],
    [
      Markup.button.callback("📊 IQ Option OTC",     "market_iq"),
      Markup.button.callback("🏦 Olymp Trade OTC",   "market_olymp"),
    ],
    ...(isAdmin(userId) ? [[Markup.button.callback("👑 ASSESS USER", "assess_users")]] : []),
    [Markup.button.callback("🔙 Back", "back_to_menu")],
  ];
  return Markup.inlineKeyboard(rows);
}

// ─── Assess panel ─────────────────────────────────────────────────────────────

function buildAssessText(): string {
  let text =
    `👑 <b>ASSESS USER PANEL</b>\n` +
    `🔒 <code>${ADMIN_ID_NUM}</code>  —  <b>Admin</b>  <i>(LOCKED)</i>\n\n`;

  if (accessStore.size === 0) {
    text += `<i>No users granted access yet.</i>\n\n`;
  } else {
    for (const [id, e] of accessStore.entries()) {
      const badge = e.expiresAt === null
        ? "♾️"
        : Date.now() < e.expiresAt ? "✅" : "❌";
      const expLabel = e.expiresAt === null
        ? "Lifetime"
        : Date.now() < e.expiresAt
          ? `Exp ${new Date(e.expiresAt).toLocaleDateString()}`
          : "EXPIRED";
      const nameLabel = e.username ? `@${e.username}` : e.firstName ?? `ID:${id}`;
      text += `${badge} <b>${escapeHtml(nameLabel)}</b>  <code>${id}</code>  <i>${expLabel}</i>\n`;
    }
    text += `\n`;
  }

  text +=
    `<b>To grant:</b>  <code>/grant &lt;userId&gt; &lt;days|lifetime&gt;</code>\n` +
    `<b>Total active:</b>  ${[...accessStore.values()].filter(e => e.expiresAt === null || Date.now() < (e.expiresAt ?? Infinity)).length} users`;
  return text;
}

function buildAssessKeyboard(): ReturnType<typeof Markup.inlineKeyboard> {
  const rows: ReturnType<typeof Markup.button.callback>[][] = [];
  for (const [id, e] of accessStore.entries()) {
    const badge = e.expiresAt === null ? "♾️" : Date.now() < e.expiresAt ? "✅" : "❌";
    const nameLabel = e.username ? `@${e.username}` : e.firstName ?? `${id}`;
    rows.push([
      Markup.button.callback(`${badge} ${nameLabel}`, "assess_noop"),
      Markup.button.callback("🗑 Remove", `assess_remove_${id}`),
    ]);
  }
  rows.push([Markup.button.callback("➕ Add User", "assess_add_user")]);
  rows.push([Markup.button.callback("🔄 Refresh",         "assess_users")]);
  rows.push([Markup.button.callback("🔙 Back to Markets", "back_to_market_from_assess")]);
  return Markup.inlineKeyboard(rows);
}

function buildAssessPackageKeyboard(): ReturnType<typeof Markup.inlineKeyboard> {
  const rows: ReturnType<typeof Markup.button.callback>[][] = [];
  for (let i = 0; i < PACKAGES.length; i += 2) {
    const pair = PACKAGES.slice(i, i + 2);
    rows.push(pair.map(p =>
      Markup.button.callback(`${p.badge} ${p.label}`, `assess_pkg_${p.id}`)
    ));
  }
  rows.push([Markup.button.callback("🔙 Cancel", "assess_users")]);
  return Markup.inlineKeyboard(rows);
}

// ─── Other keyboards ──────────────────────────────────────────────────────────

function assetKeyboard(
  assets: string[], selected: string[],
): ReturnType<typeof Markup.inlineKeyboard> {
  const btns = assets.map(a =>
    Markup.button.callback(selected.includes(a) ? `✔ ${a}` : a, `asset_${a}`)
  );
  const rows: ReturnType<typeof Markup.button.callback>[][] = [];
  for (let i = 0; i < btns.length; i += 3) rows.push(btns.slice(i, i + 3));
  rows.push([
    Markup.button.callback("✅ Done",   "assets_done"),
    Markup.button.callback("🔙 Back",   "back_to_market"),
  ]);
  rows.push([Markup.button.callback("⚙️ Change Settings", "settings_hub")]);
  rows.push([Markup.button.callback("🌍 Timezone",        "settings_tz_open")]);
  return Markup.inlineKeyboard(rows);
}

function dirAmountKeyboard(dir: "BOTH" | "CALL" | "PUT"): ReturnType<typeof Markup.inlineKeyboard> {
  const ck = (d: string) => dir === d ? " ✓" : "";
  return Markup.inlineKeyboard([
    [
      Markup.button.callback(`↕ Both${ck("BOTH")}`, "setdir_BOTH"),
      Markup.button.callback(`📈 CALL${ck("CALL")}`, "setdir_CALL"),
      Markup.button.callback(`📉 PUT${ck("PUT")}`,   "setdir_PUT"),
    ],
    SIGNAL_COUNTS.slice(0, 5).map(n => Markup.button.callback(`${n}`, `sigcount_${n}`)),
    SIGNAL_COUNTS.slice(5).map(n =>   Markup.button.callback(`${n}`, `sigcount_${n}`)),
    [Markup.button.callback("⏰ 1 Hr BLOCK", "sigcount_1hr_block")],
    [Markup.button.callback("🔙 Back", "back_to_assets")],
  ]);
}

function settingsHubKeyboard(s: Settings): ReturnType<typeof Markup.inlineKeyboard> {
  return Markup.inlineKeyboard([
    [
      Markup.button.callback(`⏱ TF: ${s.timeframe}Min`,    "settings_open"),
      Markup.button.callback(`🌍 ${s.timezone.flag} ${s.timezone.label}`, "settings_tz_open"),
    ],
    [Markup.button.callback(`🎯 ${s.strategy.badge} ${s.strategy.name}`, "settings_strategy_open")],
    [Markup.button.callback(`⏰ Auto-Delete: ${adLabel(s.autoDeleteSec)}`, "settings_delete_open")],
    [Markup.button.callback("🔙 Back to Assets", "back_to_assets")],
  ]);
}

function tfKeyboard(currentTf: number): ReturnType<typeof Markup.inlineKeyboard> {
  const tfs = [1, 2, 3, 5, 10, 15, 30];
  const mk  = (tf: number) =>
    Markup.button.callback(tf === currentTf ? `✓ ${tf}Min` : `${tf}Min`, `tf_${tf}`);
  return Markup.inlineKeyboard([
    tfs.slice(0, 4).map(mk),
    tfs.slice(4).map(mk),
    [Markup.button.callback("🔙 Back", "back_to_settings_hub")],
  ]);
}

function tzKeyboard(): ReturnType<typeof Markup.inlineKeyboard> {
  const rows: ReturnType<typeof Markup.button.callback>[][] = [];
  for (let i = 0; i < TIMEZONES.length; i += 2) {
    const row = [TIMEZONES[i]!, TIMEZONES[i + 1]].filter(Boolean) as TZ[];
    rows.push(row.map((tz, j) =>
      Markup.button.callback(`${tz.flag} ${tz.label}`, `tz_${i + j}`)
    ));
  }
  rows.push([Markup.button.callback("🔙 Back", "back_to_settings_hub")]);
  return Markup.inlineKeyboard(rows);
}

function strategyKeyboard(currentId: string): ReturnType<typeof Markup.inlineKeyboard> {
  const rows: ReturnType<typeof Markup.button.callback>[][] = [];
  for (let i = 0; i < STRATEGIES.length; i += 2) {
    const pair = STRATEGIES.slice(i, i + 2);
    rows.push(pair.map(s =>
      Markup.button.callback(
        s.id === currentId ? `✓ ${s.badge} ${s.name}` : `${s.badge} ${s.name}`,
        `strategy_${s.id}`,
      )
    ));
  }
  rows.push([Markup.button.callback("🔙 Back", "back_to_settings_hub")]);
  return Markup.inlineKeyboard(rows);
}

function autoDeleteKeyboard(currentSec: number): ReturnType<typeof Markup.inlineKeyboard> {
  const rows: ReturnType<typeof Markup.button.callback>[][] = [];
  for (let i = 0; i < AUTO_DELETE_OPTIONS.length; i += 3) {
    rows.push(
      AUTO_DELETE_OPTIONS.slice(i, i + 3).map(o =>
        Markup.button.callback(
          o.seconds === currentSec ? `✓ ${o.label}` : o.label,
          `autodel_${o.seconds}`,
        )
      )
    );
  }
  rows.push([Markup.button.callback("🔙 Back", "back_to_settings_hub")]);
  return Markup.inlineKeyboard(rows);
}

// ─── Bot ───────────────────────────────────────────────────────────────────────

const MAX_RETRIES    = 5;
const RETRY_DELAY_MS = 3_000;
let   botRestartCount = 0;

export async function startBot(): Promise<void> {
  if (!BOT_TOKEN) {
    logger.warn("TELEGRAM_BOT_TOKEN not set — bot will not start");
    return;
  }
  logger.info({ adminId: ADMIN_ID_NUM, rawEnv: ADMIN_CHAT_ID ? "[set]" : "[not set]" }, "Bot admin config");
  await initAdapters();
  await launchWithRetry();
}

async function launchWithRetry(): Promise<void> {
  const bot = buildBot();

  try {
    await bot.launch();
    botRestartCount = 0;
    logger.info("Telegram bot started");

    if (ADMIN_CHAT_ID) {
      bot.telegram
        .sendMessage(
          ADMIN_CHAT_ID,
          `🤖 <b>TG ADVANCE SIGNAL GENERATOR is online!</b>\nBroker adapters: ${Object.entries(adapters)
            .map(([k, a]) => `${k}=${a.isExperimental ? "⚠️exp" : "✅"}`)
            .join(", ")}`,
          { parse_mode: "HTML" },
        )
        .catch(() => {});
    }

    // Start expiry watcher
    startExpiryWatcher(bot);
  } catch (err) {
    botRestartCount++;
    logger.error({ err, attempt: botRestartCount }, "Bot launch failed");
    if (botRestartCount <= MAX_RETRIES) {
      const delay = RETRY_DELAY_MS * botRestartCount;
      logger.info({ delay, attempt: botRestartCount }, "Retrying bot launch…");
      await new Promise(r => setTimeout(r, delay));
      return launchWithRetry();
    }
    logger.error("Max retries reached. Bot will not restart automatically.");
  }

  process.once("SIGINT",  () => bot.stop("SIGINT"));
  process.once("SIGTERM", () => bot.stop("SIGTERM"));
}

function startExpiryWatcher(bot: Telegraf<MyContext>): void {
  setInterval(() => {
    const now = Date.now();
    for (const [userId, entry] of accessStore.entries()) {
      if (entry.expiresAt === null) continue;
      const remaining = entry.expiresAt - now;
      if (remaining > 0 && remaining <= 3_600_000 && !entry.warnedExpiry) {
        entry.warnedExpiry = true;
        bot.telegram
          .sendMessage(
            userId,
            `⚠️ <b>Subscription Expiring Soon!</b>\n` +
            `⏰ Your <b>Future Signal Generator</b> subscription\n` +
            `expires in <b>less than 1 hour!</b>\n\n` +
            `🔴 Get access now before it's too late! 🔴`,
            { parse_mode: "HTML", ...EXPIRY_WARNING_KB },
          )
          .catch(() => {});
      }
    }
  }, 5 * 60_000); // check every 5 minutes
}

// ─── Bot Builder ──────────────────────────────────────────────────────────────

function buildBot(): Telegraf<MyContext> {
  const bot = new Telegraf<MyContext>(BOT_TOKEN!);

  bot.use(
    session({
      defaultSession: (): SessionData => ({
        state: "idle",
        selectedAssets: [],
        direction: "BOTH",
        pendingDeleteIds: [],
        settings: {
          timeframe: DEFAULT_TF,
          timezone: DEFAULT_TZ,
          strategy: DEFAULT_STRATEGY,
          autoDeleteSec: DEFAULT_AUTO_DELETE.seconds,
        },
      }),
    }),
  );

  bot.telegram.setMyCommands([
    { command: "start",        description: "Start the bot" },
    { command: "futuresignal", description: "Generate future signals" },
    { command: "help",         description: "Show help" },
  ]).catch(() => {});

  // ── Inner helpers ──────────────────────────────────────────────────────────

  async function sendMainMenu(ctx: MyContext): Promise<void> {
    await ctx.reply(MAIN_MENU_TEXT, { parse_mode: "HTML", ...MAIN_MENU_KB });
  }

  async function showPaywall(ctx: MyContext, edit: boolean): Promise<void> {
    if (edit) await ctx.editMessageText(PAYWALL_TEXT, { parse_mode: "HTML", ...PAYWALL_KB });
    else      await ctx.reply(PAYWALL_TEXT,            { parse_mode: "HTML", ...PAYWALL_KB });
  }

  async function clearPendingSignals(ctx: MyContext): Promise<void> {
    const { pendingDeleteIds, pendingDeleteChatId } = ctx.session;
    if (!pendingDeleteChatId || pendingDeleteIds.length === 0) return;
    for (const id of pendingDeleteIds) {
      await bot.telegram.deleteMessage(pendingDeleteChatId, id).catch(() => {});
    }
    ctx.session.pendingDeleteIds    = [];
    ctx.session.pendingDeleteChatId = undefined;
  }

  function assetText(ctx: MyContext): string {
    const uid  = ctx.from?.id ?? 0;
    const sel  = ctx.session.selectedAssets;
    const s    = ctx.session.settings;
    const tf   = s.timeframe === 1 ? "1Minutes" : `${s.timeframe}Minutes`;
    const accessLabel = getUserAccessLabel(uid);
    return (
      `🎯 <b>Strategy Active</b>  :  <b>${escapeHtml(s.strategy.name)}</b>\n` +
      `⚙️ <b>TIMEFRAME</b>  :  <b>${tf}</b>\n` +
      `🌍 <b>TIMEZONE</b>  :  <b>${escapeHtml(tzDisplay(s.timezone))}</b>\n` +
      `🔑 <b>Asses</b>  :  <b>${escapeHtml(accessLabel)}</b>\n` +
      `⏰ <b>Auto Delete List</b>  :  <b>${adLabel(s.autoDeleteSec)}</b>\n` +
      `📌 <b>Select assets</b>  (min ${MIN_ASSETS}, max ${MAX_ASSETS})\n` +
      `<b>Selected</b>  :  <i>${sel.length > 0 ? escapeHtml(sel.join(", ")) : "none"}</i>`
    );
  }

  async function showAssets(ctx: MyContext, edit: boolean): Promise<void> {
    const assets = getAssetsForMarket(ctx.session.market ?? "real");
    const kb     = assetKeyboard(assets, ctx.session.selectedAssets);
    ctx.session.state = "await_assets";
    if (edit) await ctx.editMessageText(assetText(ctx), { parse_mode: "HTML", ...kb });
    else      await ctx.reply(assetText(ctx),            { parse_mode: "HTML", ...kb });
  }

  async function showDirAmount(ctx: MyContext): Promise<void> {
    ctx.session.state = "await_dir_amount";
    const s = ctx.session.settings;
    await ctx.editMessageText(
      `📊 <b>Select Direction &amp; Signal Count</b>\n\n` +
      `🎯 Strategy: <b>${escapeHtml(s.strategy.name)}</b> — <i>${escapeHtml(s.strategy.description)}</i>\n` +
      `Direction: <b>${ctx.session.direction}</b>\nChoose signals per pair:`,
      { parse_mode: "HTML", ...dirAmountKeyboard(ctx.session.direction) },
    );
  }

  function showSettingsHub(ctx: MyContext, edit: boolean): Promise<unknown> {
    const s = ctx.session.settings;
    const text =
      `⚙️ <b>Settings</b>\n\n` +
      `⏱ Timeframe: <b>${s.timeframe} Min</b>\n` +
      `🌍 Timezone: <b>${escapeHtml(tzDisplay(s.timezone))}</b>\n` +
      `🎯 Strategy: <b>${escapeHtml(s.strategy.name)}</b> ${escapeHtml(s.strategy.badge)}\n` +
      `    <i>${escapeHtml(s.strategy.description)}</i>\n` +
      `⏰ Auto-Delete: <b>${adLabel(s.autoDeleteSec)}</b>`;
    const kb = settingsHubKeyboard(s);
    if (edit) return ctx.editMessageText(text, { parse_mode: "HTML", ...kb });
    return ctx.reply(text, { parse_mode: "HTML", ...kb });
  }

  // ── Commands ───────────────────────────────────────────────────────────────

  bot.command("myid", async ctx => {
    const uid = ctx.from?.id ?? 0;
    await ctx.reply(
      `🆔 Your Telegram ID: <code>${uid}</code>\n` +
      `${isAdmin(uid) ? "✅ You are the admin" : "❌ Not admin"}\n\n` +
      `Bot admin ID: <code>${ADMIN_ID_NUM ?? "not set"}</code>`,
      { parse_mode: "HTML" },
    );
  });

  bot.start(async ctx => {
    ctx.session.state = "idle";
    const uid = ctx.from?.id ?? 0;
    // Store user info on first contact
    const existing = accessStore.get(uid);
    if (existing) {
      existing.username  = ctx.from?.username;
      existing.firstName = ctx.from?.first_name;
    }
    // Delete any lingering approval welcome message
    const stored = approvalMsgStore.get(uid);
    if (stored) {
      bot.telegram.deleteMessage(stored.chatId, stored.msgId).catch(() => {});
      approvalMsgStore.delete(uid);
    }
    await sendMainMenu(ctx);
  });

  bot.command("help", async ctx => {
    await ctx.reply(
      "📋 <b>Commands</b>\n\n/start — Start\n/futuresignal — Generate signals\n/help — This message",
      { parse_mode: "HTML" },
    );
  });

  bot.command("futuresignal", async ctx => {
    const uid = ctx.from?.id ?? 0;
    if (!hasAccess(uid)) { await showPaywall(ctx, false); return; }
    const stored = approvalMsgStore.get(uid);
    if (stored) {
      bot.telegram.deleteMessage(stored.chatId, stored.msgId).catch(() => {});
      approvalMsgStore.delete(uid);
    }
    ctx.session.state = "await_market";
    await ctx.reply("📊 <b>Select Market Type:</b>", { parse_mode: "HTML", ...buildMarketKeyboard(uid) });
  });

  // ── Admin commands ─────────────────────────────────────────────────────────

  bot.command("grant", async ctx => {
    if (!isAdmin(ctx.from?.id ?? 0)) return;
    const [, rawId, param] = ctx.message.text.trim().split(/\s+/);
    const targetId = parseInt(rawId ?? "", 10);
    if (!targetId || !param) {
      await ctx.reply("Usage: /grant &lt;userId&gt; &lt;days|lifetime&gt;", { parse_mode: "HTML" });
      return;
    }
    if (param.toLowerCase() === "lifetime") {
      accessStore.set(targetId, { expiresAt: null });
      await ctx.reply(`✅ Lifetime access granted to <code>${targetId}</code>.`, { parse_mode: "HTML" });
    } else {
      const days = parseInt(param, 10);
      if (!days || days <= 0) { await ctx.reply("Days must be a positive number."); return; }
      accessStore.set(targetId, { expiresAt: Date.now() + days * 86_400_000 });
      await ctx.reply(
        `✅ <b>${days}-day</b> access granted to <code>${targetId}</code>.\n` +
        `Expires: <code>${new Date(Date.now() + days * 86_400_000).toUTCString()}</code>`,
        { parse_mode: "HTML" },
      );
    }
  });

  bot.command("revoke", async ctx => {
    if (!isAdmin(ctx.from?.id ?? 0)) return;
    const [, rawId] = ctx.message.text.trim().split(/\s+/);
    const targetId = parseInt(rawId ?? "", 10);
    if (!targetId) { await ctx.reply("Usage: /revoke &lt;userId&gt;", { parse_mode: "HTML" }); return; }
    accessStore.delete(targetId);
    await ctx.reply(`✅ Access revoked for <code>${targetId}</code>.`, { parse_mode: "HTML" });
  });

  bot.command("listaccess", async ctx => {
    if (!isAdmin(ctx.from?.id ?? 0)) return;
    if (accessStore.size === 0) { await ctx.reply("No users have been granted access."); return; }
    const lines = Array.from(accessStore.entries()).map(([id, e]) => {
      const exp = e.expiresAt === null
        ? "Lifetime ♾️"
        : `${new Date(e.expiresAt).toUTCString()} ${Date.now() < e.expiresAt ? "✅" : "❌ EXPIRED"}`;
      return `<code>${id}</code> — ${exp}`;
    });
    await ctx.reply(`<b>Access List (${accessStore.size}):</b>\n\n${lines.join("\n")}`, { parse_mode: "HTML" });
  });

  // ── Main actions ───────────────────────────────────────────────────────────

  bot.action("futuresignal", async ctx => {
    await ctx.answerCbQuery();
    const uid = ctx.from?.id ?? 0;
    if (!hasAccess(uid)) { await showPaywall(ctx, true); return; }
    // Delete any lingering approval welcome message
    const stored = approvalMsgStore.get(uid);
    if (stored) {
      bot.telegram.deleteMessage(stored.chatId, stored.msgId).catch(() => {});
      approvalMsgStore.delete(uid);
    }
    ctx.session.state = "await_market";
    await ctx.editMessageText("📊 <b>Select Market Type:</b>", { parse_mode: "HTML", ...buildMarketKeyboard(uid) });
  });

  bot.action("back_to_menu", async ctx => {
    await ctx.answerCbQuery();
    ctx.session.state = "idle";
    await clearPendingSignals(ctx);
    await ctx.editMessageText(MAIN_MENU_TEXT, { parse_mode: "HTML", ...MAIN_MENU_KB });
  });

  bot.action("back_to_menu_reply", async ctx => {
    await ctx.answerCbQuery();
    ctx.session.state = "idle";
    await ctx.reply(MAIN_MENU_TEXT, { parse_mode: "HTML", ...MAIN_MENU_KB });
  });

  bot.action("access_buy", async ctx => {
    await ctx.answerCbQuery();
    await ctx.editMessageText(buildPriceListText(), { parse_mode: "HTML", ...buildPriceListKeyboard() });
  });

  bot.action("paywall_back", async ctx => {
    await ctx.answerCbQuery();
    await ctx.editMessageText(PAYWALL_TEXT, { parse_mode: "HTML", ...PAYWALL_KB });
  });

  // ── Package selection (user purchase flow) ────────────────────────────────

  bot.action(/^pkg_(.+)$/, async ctx => {
    await ctx.answerCbQuery();
    const pkg = getPkg(ctx.match[1]);
    if (!pkg) return;
    ctx.session.pendingPackageId = pkg.id;
    await ctx.editMessageText(buildPkgDetailText(pkg), { parse_mode: "HTML", ...buildPkgDetailKeyboard(pkg.id) });
  });

  bot.action(/^proceed_(.+)$/, async ctx => {
    await ctx.answerCbQuery();
    const pkg = getPkg(ctx.match[1]);
    if (!pkg) return;
    ctx.session.pendingPackageId = pkg.id;
    await ctx.editMessageText(buildPaymentPage1Text(pkg), { parse_mode: "HTML", ...buildPaymentPage1Keyboard(pkg.id) });
  });

  bot.action(/^pay_page2_(.+)$/, async ctx => {
    await ctx.answerCbQuery();
    const pkg = getPkg(ctx.match[1]);
    if (!pkg) return;
    await ctx.editMessageText(buildPaymentPage2Text(pkg), { parse_mode: "HTML", ...buildPaymentPage2Keyboard(pkg.id) });
  });

  bot.action(/^pay_page1_(.+)$/, async ctx => {
    await ctx.answerCbQuery();
    const pkg = getPkg(ctx.match[1]);
    if (!pkg) return;
    await ctx.editMessageText(buildPaymentPage1Text(pkg), { parse_mode: "HTML", ...buildPaymentPage1Keyboard(pkg.id) });
  });

  bot.action(/^send_screenshot_(.+)$/, async ctx => {
    await ctx.answerCbQuery();
    const pkg = getPkg(ctx.match[1]);
    if (!pkg) return;
    ctx.session.pendingPackageId = pkg.id;
    ctx.session.state = "await_payment_screenshot";
    await ctx.editMessageText(
      `📸 <b>Send Payment Screenshot</b>\n` +
      `📦 <b>Package:</b>  ${pkg.badge} ${escapeHtml(pkg.label)}\n\n` +
      `Take a screenshot of your <b>completed payment</b>\n` +
      `and send it here as a <b>photo</b>.\n\n` +
      `<i>⏳ Waiting for your screenshot…</i>`,
      {
        parse_mode: "HTML",
        ...Markup.inlineKeyboard([[Markup.button.callback("❌ Cancel", "access_buy")]]),
      },
    );
  });

  // ── Market selection ──────────────────────────────────────────────────────

  const markets: MarketType[] = ["real", "quotex", "po", "iq", "olymp"];
  for (const m of markets) {
    bot.action(`market_${m}`, async ctx => {
      if (ctx.session.state !== "await_market") return;
      if (m === "real" && isWeekend(ctx.session.settings.timezone)) {
        await ctx.answerCbQuery(
          "🚫 Real Market Closed — Weekend!\nOpen Mon–Fri only. Use OTC instead.",
          { show_alert: true },
        );
        return;
      }
      await ctx.answerCbQuery();
      ctx.session.market = m;
      ctx.session.selectedAssets = [];
      await showAssets(ctx, true);
    });
  }

  bot.action("back_to_market", async ctx => {
    await ctx.answerCbQuery();
    ctx.session.state = "await_market";
    ctx.session.selectedAssets = [];
    const uid = ctx.from?.id ?? 0;
    await ctx.editMessageText("📊 <b>Select Market Type:</b>", { parse_mode: "HTML", ...buildMarketKeyboard(uid) });
  });

  // ── Assess User panel ──────────────────────────────────────────────────────

  bot.action("assess_users", async ctx => {
    await ctx.answerCbQuery();
    if (!isAdmin(ctx.from?.id ?? 0)) return;
    ctx.session.state = "idle";
    await ctx.editMessageText(buildAssessText(), { parse_mode: "HTML", ...buildAssessKeyboard() });
  });

  bot.action("assess_noop", async ctx => {
    await ctx.answerCbQuery();
  });

  bot.action(/^assess_remove_(\d+)$/, async ctx => {
    if (!isAdmin(ctx.from?.id ?? 0)) return;
    const targetId = parseInt(ctx.match[1], 10);
    if (ADMIN_ID_NUM !== null && targetId === ADMIN_ID_NUM) {
      await ctx.answerCbQuery("🔒 Cannot remove Admin!", { show_alert: true });
      return;
    }
    await ctx.answerCbQuery(`🗑 Removed ${targetId}`);
    accessStore.delete(targetId);

    // Notify removed user
    bot.telegram
      .sendMessage(
        targetId,
        `⚠️ <b>Access Removed</b>\n\n` +
        `You have been removed by admin.\n` +
        `If you think this is a misunderstanding,\n` +
        `just direct message the admin.`,
        {
          parse_mode: "HTML",
          ...Markup.inlineKeyboard([
            [Markup.button.url("💬 Chat with Admin", ADMIN_CHAT_URL)],
            [Markup.button.callback("💳 Access Buy",   "access_buy")],
          ]),
        },
      )
      .catch(() => {});

    await ctx.editMessageText(buildAssessText(), { parse_mode: "HTML", ...buildAssessKeyboard() });
  });

  bot.action("back_to_market_from_assess", async ctx => {
    await ctx.answerCbQuery();
    const uid = ctx.from?.id ?? 0;
    ctx.session.state = "await_market";
    await ctx.editMessageText("📊 <b>Select Market Type:</b>", { parse_mode: "HTML", ...buildMarketKeyboard(uid) });
  });

  bot.action("assess_add_user", async ctx => {
    await ctx.answerCbQuery();
    if (!isAdmin(ctx.from?.id ?? 0)) return;
    ctx.session.state = "await_assess_username";
    await ctx.editMessageText(
      `➕ <b>Add User — Step 1 of 2</b>\n` +
      `Send the <b>Telegram User ID</b> of the user\n` +
      `you want to grant access to.\n\n` +
      `<i>💡 The user can find their ID by sending\n/myid to this bot.</i>`,
      {
        parse_mode: "HTML",
        ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Cancel", "assess_users")]]),
      },
    );
  });

  // Admin selects a package in the assess flow
  bot.action(/^assess_pkg_(.+)$/, async ctx => {
    await ctx.answerCbQuery();
    if (!isAdmin(ctx.from?.id ?? 0)) return;
    const pkg = getPkg(ctx.match[1]);
    const targetId = ctx.session.assessTargetId;
    if (!pkg || !targetId) {
      await ctx.answerCbQuery("⚠️ Session lost. Please try again.", { show_alert: true });
      return;
    }

    const firstName = ctx.session.assessTargetUsername ?? String(targetId);
    const endLabel  = pkgEndLabel(pkg);

    // Grant access
    accessStore.set(targetId, {
      expiresAt: pkg.days === null ? null : Date.now() + pkg.days * 86_400_000,
      username:  ctx.session.assessTargetUsername,
      firstName,
      packageId: pkg.id,
    });
    ctx.session.state = "idle";
    ctx.session.assessTargetId = undefined;
    ctx.session.assessTargetUsername = undefined;

    // Notify user
    bot.telegram
      .sendMessage(targetId, buildApprovalWelcomeText(pkg, firstName), { parse_mode: "HTML" })
      .catch(() => {});

    // Show updated assess panel to admin
    await ctx.editMessageText(
      `✅ <b>Access granted!</b>\n\n` +
      `👤 User: <code>${targetId}</code>\n` +
      `📦 Package: ${pkg.badge} ${escapeHtml(pkg.label)}\n` +
      `📅 Until: ${escapeHtml(endLabel)}\n\n` +
      `<i>User has been notified.</i>`,
      {
        parse_mode: "HTML",
        ...Markup.inlineKeyboard([
          [Markup.button.callback("👑 Back to Assess Panel", "assess_users")],
        ]),
      },
    );
  });

  // ── Admin approve / reject payments ──────────────────────────────────────

  bot.action(/^approve_pay_(.+)$/, async ctx => {
    await ctx.answerCbQuery();
    if (!isAdmin(ctx.from?.id ?? 0)) return;
    const payId  = ctx.match[1];
    const payment = pendingPayments.get(payId);
    if (!payment) {
      await ctx.answerCbQuery("⚠️ Payment not found (may be expired).", { show_alert: true });
      return;
    }
    const pkg = getPkg(payment.packageId);
    if (!pkg) return;

    pendingPayments.delete(payId);

    // Grant access
    accessStore.set(payment.userId, {
      expiresAt: pkg.days === null ? null : Date.now() + pkg.days * 86_400_000,
      username:  payment.username,
      firstName: payment.firstName,
      packageId: pkg.id,
    });

    // Delete user's "Under Review" message
    if (payment.userReviewMsgId) {
      bot.telegram.deleteMessage(payment.chatId, payment.userReviewMsgId).catch(() => {});
    }

    // Send welcome & store its message ID so it can be deleted on next /start or futuresignal tap
    bot.telegram
      .sendMessage(payment.userId, buildApprovalWelcomeText(pkg, payment.firstName), { parse_mode: "HTML" })
      .then(sent => {
        approvalMsgStore.set(payment.userId, { chatId: payment.chatId, msgId: sent.message_id });
      })
      .catch(() => {});

    // Delete admin payment message (clean up chat)
    await ctx.deleteMessage().catch(() => {});
  });

  bot.action(/^reject_pay_(.+)$/, async ctx => {
    await ctx.answerCbQuery();
    if (!isAdmin(ctx.from?.id ?? 0)) return;
    const payId   = ctx.match[1];
    const payment = pendingPayments.get(payId);
    if (!payment) {
      await ctx.answerCbQuery("⚠️ Payment not found.", { show_alert: true });
      return;
    }

    pendingPayments.delete(payId);

    // Delete user's "Under Review" message
    if (payment.userReviewMsgId) {
      bot.telegram.deleteMessage(payment.chatId, payment.userReviewMsgId).catch(() => {});
    }

    // Notify user of rejection
    bot.telegram
      .sendMessage(
        payment.userId,
        `❌ <b>Payment Not Confirmed</b>\n` +
        `We could not confirm your payment.\n\n` +
        `If you think this is a mistake,\n` +
        `please contact admin directly:`,
        {
          parse_mode: "HTML",
          ...Markup.inlineKeyboard([
            [Markup.button.url("💬 Contact Admin", ADMIN_CHAT_URL)],
            [Markup.button.callback("🔄 Try Again", "access_buy")],
          ]),
        },
      )
      .catch(() => {});

    // Delete admin payment message (clean up chat)
    await ctx.deleteMessage().catch(() => {});
  });

  // ── Asset toggling ────────────────────────────────────────────────────────

  bot.action(/^asset_(.+)$/, async ctx => {
    if (ctx.session.state !== "await_assets") return;
    const asset = ctx.match[1];
    const sel   = ctx.session.selectedAssets;
    const idx   = sel.indexOf(asset);
    if (idx === -1) {
      if (sel.length >= MAX_ASSETS) {
        await ctx.answerCbQuery(`⚠️ Max ${MAX_ASSETS} assets!`, { show_alert: true });
        return;
      }
      sel.push(asset);
      await ctx.answerCbQuery(`✔ ${asset}`);
    } else {
      sel.splice(idx, 1);
      await ctx.answerCbQuery(`✖ ${asset}`);
    }
    const assets = getAssetsForMarket(ctx.session.market ?? "real");
    await ctx.editMessageText(assetText(ctx), { parse_mode: "HTML", ...assetKeyboard(assets, sel) });
  });

  bot.action("assets_done", async ctx => {
    if (ctx.session.selectedAssets.length < MIN_ASSETS) {
      await ctx.answerCbQuery(`⚠️ Select at least ${MIN_ASSETS} asset!`, { show_alert: true });
      return;
    }
    await ctx.answerCbQuery();
    await showDirAmount(ctx);
  });

  bot.action("back_to_assets", async ctx => {
    await ctx.answerCbQuery();
    await showAssets(ctx, true);
  });

  // ── Direction ──────────────────────────────────────────────────────────────

  for (const dir of ["BOTH", "CALL", "PUT"] as const) {
    bot.action(`setdir_${dir}`, async ctx => {
      if (ctx.session.state !== "await_dir_amount") return;
      ctx.session.direction = dir;
      await ctx.answerCbQuery(`Direction: ${dir}`);
      const s = ctx.session.settings;
      await ctx.editMessageText(
        `📊 <b>Select Direction &amp; Signal Count</b>\n\n` +
        `🎯 Strategy: <b>${escapeHtml(s.strategy.name)}</b> — <i>${escapeHtml(s.strategy.description)}</i>\n` +
        `Direction: <b>${dir}</b>\nChoose signals per pair:`,
        { parse_mode: "HTML", ...dirAmountKeyboard(dir) },
      );
    });
  }

  // ── Signal generation ──────────────────────────────────────────────────────

  for (const count of SIGNAL_COUNTS) {
    bot.action(`sigcount_${count}`, async ctx => {
      if (ctx.session.state !== "await_dir_amount") return;
      await ctx.answerCbQuery("⏳ Generating...");

      const { selectedAssets, direction, market, settings } = ctx.session;
      ctx.session.state = "idle";

      let msg: string;
      try {
        msg = await buildSignalMessage(selectedAssets, direction, market ?? "real", count, settings);
      } catch (err) {
        logger.error({ err }, "Signal generation error");
        await ctx.reply("⚠️ Error generating signals. Please try again.", { parse_mode: "HTML" });
        return;
      }

      const MAX_LEN = 4000;
      const chunks: string[] = [];
      let rem = msg;
      while (rem.length > 0) {
        if (rem.length <= MAX_LEN) { chunks.push(rem); break; }
        const cut = rem.lastIndexOf("\n\n", MAX_LEN);
        chunks.push(rem.slice(0, cut > 0 ? cut : MAX_LEN));
        rem = rem.slice(cut > 0 ? cut : MAX_LEN).trimStart();
      }

      const chatId      = ctx.chat!.id;
      const deleteMsgIds: number[] = [];
      const firstMsgId  = (ctx.callbackQuery as { message?: { message_id?: number } })?.message?.message_id;
      if (firstMsgId) deleteMsgIds.push(firstMsgId);
      await ctx.editMessageText(chunks[0]!, { parse_mode: "HTML" });

      for (let i = 1; i < chunks.length; i++) {
        const m = await ctx.reply(chunks[i]!, { parse_mode: "HTML" });
        deleteMsgIds.push(m.message_id);
      }

      const delLabel   = adLabel(settings.autoDeleteSec);
      const summaryMsg = await ctx.reply(
        `✅ <b>${count} signals × ${selectedAssets.length} pair(s)</b> | 🎯 ${escapeHtml(settings.strategy.name)}\n` +
        `⏱ <i>Auto-deleting in ${delLabel}…</i>`,
        {
          parse_mode: "HTML",
          ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Home", "go_home")]]),
        },
      );

      ctx.session.pendingDeleteIds    = deleteMsgIds;
      ctx.session.pendingDeleteChatId = chatId;

      setTimeout(() => {
        for (const id of deleteMsgIds) {
          bot.telegram.deleteMessage(chatId, id).catch(() => {});
        }
        bot.telegram
          .editMessageText(chatId, summaryMsg.message_id, undefined, MAIN_MENU_TEXT, {
            parse_mode: "HTML",
            ...MAIN_MENU_KB,
          })
          .catch(() => {});
        ctx.session.pendingDeleteIds    = [];
        ctx.session.pendingDeleteChatId = undefined;
        ctx.session.state = "idle";
      }, settings.autoDeleteSec * 1_000);
    });
  }

  // ── 1 Hr BLOCK ─────────────────────────────────────────────────────────────

  bot.action("sigcount_1hr_block", async ctx => {
    if (ctx.session.state !== "await_dir_amount") return;
    await ctx.answerCbQuery("⏳ Building 1 Hr block...");

    const { selectedAssets, direction, market, settings } = ctx.session;
    ctx.session.state = "idle";

    let msg: string;
    try {
      msg = await buildHourBlockMessage(selectedAssets, direction, market ?? "real", settings);
    } catch (err) {
      logger.error({ err }, "1Hr block generation error");
      await ctx.reply("⚠️ Error generating signals. Please try again.", { parse_mode: "HTML" });
      return;
    }

    const MAX_LEN = 4000;
    const chunks: string[] = [];
    let rem = msg;
    while (rem.length > 0) {
      if (rem.length <= MAX_LEN) { chunks.push(rem); break; }
      const cut = rem.lastIndexOf("\n", MAX_LEN);
      chunks.push(rem.slice(0, cut > 0 ? cut : MAX_LEN));
      rem = rem.slice(cut > 0 ? cut : MAX_LEN).trimStart();
    }

    const chatId     = ctx.chat!.id;
    const deleteMsgIds: number[] = [];
    const firstMsgId = (ctx.callbackQuery as { message?: { message_id?: number } })?.message?.message_id;
    if (firstMsgId) deleteMsgIds.push(firstMsgId);
    await ctx.editMessageText(chunks[0]!, { parse_mode: "HTML" });

    for (let i = 1; i < chunks.length; i++) {
      const m = await ctx.reply(chunks[i]!, { parse_mode: "HTML" });
      deleteMsgIds.push(m.message_id);
    }

    const delLabel   = adLabel(settings.autoDeleteSec);
    const summaryMsg = await ctx.reply(
      `✅ <b>⏰ 1 Hr BLOCK</b> — ${selectedAssets.length} pair(s) | 4–6 hr window\n` +
      `⏱ <i>Auto-deleting in ${delLabel}…</i>`,
      {
        parse_mode: "HTML",
        ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Home", "go_home")]]),
      },
    );

    ctx.session.pendingDeleteIds    = deleteMsgIds;
    ctx.session.pendingDeleteChatId = chatId;

    setTimeout(() => {
      for (const id of deleteMsgIds) {
        bot.telegram.deleteMessage(chatId, id).catch(() => {});
      }
      bot.telegram
        .editMessageText(chatId, summaryMsg.message_id, undefined, MAIN_MENU_TEXT, {
          parse_mode: "HTML",
          ...MAIN_MENU_KB,
        })
        .catch(() => {});
      ctx.session.pendingDeleteIds    = [];
      ctx.session.pendingDeleteChatId = undefined;
      ctx.session.state = "idle";
    }, settings.autoDeleteSec * 1_000);
  });

  // Home
  bot.action("go_home", async ctx => {
    await ctx.answerCbQuery();
    ctx.session.state = "idle";
    await clearPendingSignals(ctx);
    await ctx.editMessageText(MAIN_MENU_TEXT, { parse_mode: "HTML", ...MAIN_MENU_KB });
  });

  // ── Settings Hub ───────────────────────────────────────────────────────────

  bot.action("settings_hub",          async ctx => { await ctx.answerCbQuery(); await showSettingsHub(ctx, true); });
  bot.action("back_to_settings_hub",  async ctx => { await ctx.answerCbQuery(); await showSettingsHub(ctx, true); });

  bot.action("settings_open", async ctx => {
    await ctx.answerCbQuery();
    const tf = ctx.session.settings.timeframe;
    await ctx.editMessageText(
      `⏱ <b>Choose Timeframe</b>\n\nCurrent: <b>${tf} Min</b>`,
      { parse_mode: "HTML", ...tfKeyboard(tf) },
    );
  });

  for (const tf of [1, 2, 3, 5, 10, 15, 30]) {
    bot.action(`tf_${tf}`, async ctx => {
      ctx.session.settings.timeframe = tf;
      await ctx.answerCbQuery(`✓ ${tf}Min saved`);
      await ctx.editMessageText(
        `⏱ <b>Choose Timeframe</b>\n\nCurrent: <b>${tf} Min</b>`,
        { parse_mode: "HTML", ...tfKeyboard(tf) },
      );
    });
  }

  bot.action("settings_tz_open", async ctx => {
    await ctx.answerCbQuery();
    const cur = ctx.session.settings.timezone;
    await ctx.editMessageText(
      `🌍 <b>Select Timezone</b>\n\nCurrent: <b>${escapeHtml(tzDisplay(cur))}</b>`,
      { parse_mode: "HTML", ...tzKeyboard() },
    );
  });

  bot.action(/^tz_(\d+)$/, async ctx => {
    const tz = TIMEZONES[parseInt(ctx.match[1], 10)];
    if (!tz) return;
    ctx.session.settings.timezone = tz;
    await ctx.answerCbQuery(`✓ ${tzDisplay(tz)} saved`);
    await showSettingsHub(ctx, true);
  });

  bot.action("settings_strategy_open", async ctx => {
    await ctx.answerCbQuery();
    const cur = ctx.session.settings.strategy;
    await ctx.editMessageText(
      `🎯 <b>Select Strategy</b>\n\nCurrent: <b>${escapeHtml(cur.name)}</b>\n<i>${escapeHtml(cur.description)}</i>`,
      { parse_mode: "HTML", ...strategyKeyboard(cur.id) },
    );
  });

  bot.action(/^strategy_(.+)$/, async ctx => {
    const s = STRATEGIES.find(x => x.id === ctx.match[1]);
    if (!s) return;
    ctx.session.settings.strategy = s;
    await ctx.answerCbQuery(`✓ ${s.name} selected`);
    await showSettingsHub(ctx, true);
  });

  bot.action("settings_delete_open", async ctx => {
    await ctx.answerCbQuery();
    const cur = ctx.session.settings.autoDeleteSec;
    await ctx.editMessageText(
      `⏰ <b>Auto-Delete Timer</b>\n\nCurrent: <b>${adLabel(cur)}</b>\nSignal messages are deleted after this time:`,
      { parse_mode: "HTML", ...autoDeleteKeyboard(cur) },
    );
  });

  bot.action(/^autodel_(\d+)$/, async ctx => {
    const sec = parseInt(ctx.match[1], 10);
    const opt = AUTO_DELETE_OPTIONS.find(o => o.seconds === sec);
    if (!opt) return;
    ctx.session.settings.autoDeleteSec = sec;
    await ctx.answerCbQuery(`✓ Auto-delete set to ${opt.label}`);
    await showSettingsHub(ctx, true);
  });

  // ── Message handler (text + photo) ─────────────────────────────────────────

  bot.on("message", async ctx => {
    const uid   = ctx.from?.id ?? 0;
    const state = ctx.session.state;

    // ── Admin: waiting for user ID to grant access ───────────────────────────
    if (state === "await_assess_username" && isAdmin(uid)) {
      const text = ("text" in ctx.message ? ctx.message.text : "").trim();
      const targetId = parseInt(text, 10);

      if (!targetId || isNaN(targetId)) {
        await ctx.reply(
          `⚠️ <b>Invalid input.</b>\n\nPlease send a <b>numeric Telegram User ID</b>.\n<i>Example: <code>123456789</code></i>`,
          { parse_mode: "HTML" },
        );
        return;
      }

      ctx.session.assessTargetId       = targetId;
      ctx.session.assessTargetUsername = undefined;
      ctx.session.state = "idle";

      await ctx.reply(
        `📦 <b>Select Package — Step 2 of 2</b>\n` +
        `👤 <b>User ID:</b>  <code>${targetId}</code>\n\n` +
        `Choose the access package for this user:`,
        { parse_mode: "HTML", ...buildAssessPackageKeyboard() },
      );
      return;
    }

    // ── User: waiting to send payment screenshot ───────────────────────────
    if (state === "await_payment_screenshot") {
      const pkgId = ctx.session.pendingPackageId;
      const pkg   = pkgId ? getPkg(pkgId) : undefined;

      if (!pkg) {
        await ctx.reply("⚠️ Session expired. Please start again.", { parse_mode: "HTML" });
        ctx.session.state = "idle";
        return;
      }

      const hasPhoto = "photo" in ctx.message && ctx.message.photo && ctx.message.photo.length > 0;
      const hasDoc   = "document" in ctx.message && ctx.message.document;

      if (!hasPhoto && !hasDoc) {
        await ctx.reply(
          `📸 <b>Please send a photo</b> of your payment screenshot.\n<i>Not a file — send it as a photo.</i>`,
          { parse_mode: "HTML" },
        );
        return;
      }

      ctx.session.state = "idle";
      const payId: string = genPaymentId();
      const firstName = ctx.from?.first_name ?? "User";
      const username  = ctx.from?.username;

      pendingPayments.set(payId, {
        id: payId,
        userId: uid,
        username,
        firstName,
        packageId: pkg.id,
        chatId: ctx.chat!.id,
      });

      // Confirm to user — store message ID so we can delete it on approve/reject
      const reviewMsg = await ctx.reply(
        `⏳ <b>Payment Under Review</b>\n` +
        `✅ Your screenshot has been sent to admin.\n\n` +
        `📦 <b>Package:</b>  ${pkg.badge} ${escapeHtml(pkg.label)}\n` +
        `⏱ You will be notified once approved.\n\n` +
        `<i>Usually within a few hours.</i>`,
        { parse_mode: "HTML" },
      );
      const pp = pendingPayments.get(payId);
      if (pp) pp.userReviewMsgId = reviewMsg.message_id;

      // Forward to admin with approve/reject
      if (ADMIN_CHAT_ID && ADMIN_ID_NUM) {
        const caption =
          `💳 <b>New Payment Screenshot</b>\n` +
          `👤 <b>User:</b>  ${escapeHtml(firstName)}${username ? ` (@${escapeHtml(username)})` : ""}\n` +
          `🆔 <b>ID:</b>  <code>${uid}</code>\n` +
          `📦 <b>Package:</b>  ${pkg.badge} ${escapeHtml(pkg.label)}\n` +
          `💰 <b>Amount:</b>  <code>$${pkg.price}</code>\n` +
          `⏱ <b>Duration:</b>  ${escapeHtml(pkg.durationText)}\n` +
          `🔑 <b>Pay ID:</b>  <code>${payId}</code>`;

        const approveKb = Markup.inlineKeyboard([
          [
            Markup.button.callback("✅ Approve",  `approve_pay_${payId}`),
            Markup.button.callback("❌ Reject",   `reject_pay_${payId}`),
          ],
        ]);

        try {
          if (hasPhoto) {
            const photos = (ctx.message as { photo: Array<{ file_id: string }> }).photo;
            const fileId = photos[photos.length - 1]!.file_id;
            await bot.telegram.sendPhoto(ADMIN_ID_NUM, fileId, {
              caption,
              parse_mode: "HTML",
              ...approveKb,
            });
          } else if (hasDoc) {
            const doc = (ctx.message as { document: { file_id: string } }).document;
            await bot.telegram.sendDocument(ADMIN_ID_NUM, doc.file_id, {
              caption,
              parse_mode: "HTML",
              ...approveKb,
            });
          }
        } catch {
          // fallback: send text only
          await bot.telegram.sendMessage(
            ADMIN_ID_NUM,
            caption + `\n\n⚠️ <i>Could not forward screenshot.</i>`,
            { parse_mode: "HTML", ...approveKb },
          );
        }
      }
      return;
    }
  });

  // ── Global error handler ───────────────────────────────────────────────────

  bot.catch((err, ctx) => {
    logger.error({ err, updateType: ctx.updateType }, "Unhandled bot error");
    if (ctx.callbackQuery) {
      ctx.answerCbQuery("⚠️ Something went wrong. Please try again.").catch(() => {});
    }
  });

  return bot;
}
