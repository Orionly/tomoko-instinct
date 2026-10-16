/**
 * Tomoko — Pair Scanner (visual redesign)
 * ----------------------------------------------------------------------------
 * Same data, same logic as cardHTML()/renderCards() in ui/web_dashboard.html.
 * Only the visual system changed.
 *
 * Requires: React 18, Tailwind CSS 3.4+, shadcn/ui (card, badge, button,
 * tooltip, table), lucide-react.
 *
 *   npx shadcn@latest add card badge button tooltip table
 *
 * Tailwind config (fontFamily):
 *   sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"]
 */
"use client";

import * as React from "react";
import { ArrowUpRight } from "lucide-react";

import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

/* ============================================================================
 * 1. DATA SHAPE — unchanged from the existing API payload
 * ========================================================================== */

export type PairData = {
  symbol: string;
  price: number | null;
  score: number;
  action?: string;
  reason?: string;
  h4?: { gate?: string; direction?: string; adx?: number; atr_ratio?: number };
  h1?: { dist_ema21_atr?: number; bias?: string };
  m15_bias?: string;
  levels?: { liq_high?: number };
};

export const PAIRS = [
  "EURUSD",
  "GBPUSD",
  "USDJPY",
  "GBPJPY",
  "EURJPY",
  "EURGBP",
  "XAUUSD",
] as const;

/** Static display label for the identity column (not data). */
const PAIR_NAMES: Record<string, string> = {
  EURUSD: "Euro · US Dollar",
  GBPUSD: "Pound · US Dollar",
  USDJPY: "US Dollar · Yen",
  GBPJPY: "Pound · Yen",
  EURJPY: "Euro · Yen",
  EURGBP: "Euro · Pound",
  XAUUSD: "Gold · US Dollar",
};

/* ============================================================================
 * 2. LOGIC — ported 1:1 from web_dashboard.html (do not edit)
 * ========================================================================== */

function fmt(n: unknown, d?: number): string {
  if (n === undefined || n === null || isNaN(n as number)) return "—";
  return Number(n).toFixed(d);
}
const isGold = (s: string) => s.indexOf("XAU") >= 0;
function priceTxt(s: string, p: number | null): string {
  if (p == null || isNaN(p)) return "—";
  return isGold(s) ? p.toFixed(3) : p.toFixed(5);
}

type Dir = "BULL" | "BEAR" | "NEUTRAL";
function parseDir(raw?: string): Dir {
  if (!raw) return "NEUTRAL";
  const s = String(raw).toUpperCase();
  if (s === "UP" || s === "BULLISH" || s === "BULL") return "BULL";
  if (s === "DOWN" || s === "BEARISH" || s === "BEAR") return "BEAR";
  return "NEUTRAL";
}
const dirArrow = (w: Dir) => (w === "BULL" ? "↑" : w === "BEAR" ? "↓" : "→");

type Level = "HIGH" | "MEDIUM" | "LOW";
type Alignment = { level: Level; label: string; tooltip: string };

function getAlignment(h4d?: string, h1d?: string, m15d?: string): Alignment {
  const a = parseDir(h4d), b = parseDir(h1d), c = parseDir(m15d);
  const cnt = { BULL: 0, BEAR: 0, NEUTRAL: 0 };
  [a, b, c].forEach((x) => cnt[x]++);
  if (a === b && b === c && a !== "NEUTRAL")
    return { level: "HIGH", label: "ALIGNED", tooltip: "HIGH CONFLUENCE - 3/3 aligned" };
  if (cnt.BULL === 2 || cnt.BEAR === 2)
    return { level: "MEDIUM", label: "2/3", tooltip: "MEDIUM - 2/3 aligned" };
  return { level: "LOW", label: "LOW", tooltip: "LOW CONFLUENCE" };
}
const alignmentFor = (d: PairData) =>
  getAlignment(d.h4?.direction, d.h1?.bias, d.m15_bias);

/** Same thresholds as scoreColor(): <40, <70, ≥70. */
type Tier = "low" | "mid" | "high";
const scoreTier = (s: number): Tier => (s < 40 ? "low" : s < 70 ? "mid" : "high");

/* ============================================================================
 * 3. VISUAL TOKENS — the whole color system lives here
 *    Neutral = zinc/white opacity. Accent = green / red / blue only.
 * ========================================================================== */

const T = {
  // Text hierarchy: whisper / talk / shout
  label: "text-[12px] font-medium text-white/40",
  value: "text-[14px] font-medium tabular-nums text-white/80",
  strong: "text-white/95",

  // Surfaces
  surface: "bg-zinc-900/60 ring-1 ring-white/[0.06]",
  cluster: "rounded-lg bg-white/[0.025]",
  divider: "border-white/[0.06]",
} as const;

const BIAS_STYLE: Record<Dir, string> = {
  BULL: "bg-emerald-400/10 text-emerald-300",
  BEAR: "bg-rose-400/10 text-rose-300",
  NEUTRAL: "bg-white/[0.04] text-white/50",
};

const ALIGN_DOT: Record<Level, string> = {
  HIGH: "bg-sky-400 ring-4 ring-sky-400/15",
  MEDIUM: "bg-sky-400/50",
  LOW: "bg-white/20",
};
const ALIGN_TEXT: Record<Level, string> = {
  HIGH: "text-sky-400",
  MEDIUM: "text-sky-400/70",
  LOW: "text-white/40",
};

const RING_STROKE: Record<Tier, string> = {
  high: "stroke-sky-400",
  mid: "stroke-white/50",
  low: "stroke-rose-400",
};

/* ============================================================================
 * 4. PRIMITIVES
 * ========================================================================== */

function BiasPill({ tf, raw }: { tf: string; raw?: string }) {
  const w = parseDir(raw);
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded-md px-2 text-[12px] font-medium tabular-nums",
        BIAS_STYLE[w],
      )}
    >
      <span className="opacity-60">{tf}</span>
      <span>{w}</span>
      <span aria-hidden>{dirArrow(w)}</span>
    </span>
  );
}

function AlignmentIndicator({ al }: { al: Alignment }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center gap-2">
          <span className={cn("size-2 rounded-full transition-colors", ALIGN_DOT[al.level])} />
          <span className={cn("text-[12px] font-medium tracking-wide", ALIGN_TEXT[al.level])}>
            {al.label}
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="text-[12px]">
        {al.tooltip}
      </TooltipContent>
    </Tooltip>
  );
}

function ScoreRing({ score }: { score: number | null }) {
  const r = 17;
  const c = 2 * Math.PI * r;
  const tier = score == null ? null : scoreTier(score);
  const pct = score == null ? 0 : Math.max(0, Math.min(100, score)) / 100;

  return (
    <div className="relative size-10 shrink-0" aria-label={`Score ${score ?? "--"}`}>
      <svg viewBox="0 0 40 40" className="size-10 -rotate-90">
        <circle cx="20" cy="20" r={r} fill="none" strokeWidth="2.5" className="stroke-white/[0.06]" />
        <circle
          cx="20"
          cy="20"
          r={r}
          fill="none"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
          className={cn(
            "transition-[stroke-dashoffset] duration-700 ease-out",
            tier && RING_STROKE[tier],
          )}
        />
      </svg>
      <span className="absolute inset-0 grid place-items-center text-[14px] font-semibold tabular-nums text-white/90">
        {score ?? "--"}
      </span>
    </div>
  );
}

function Stat({ label, value, unit }: { label: string; value: React.ReactNode; unit?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className={T.label}>{label}</dt>
      <dd className={T.value}>
        {value}
        {unit && <span className="ml-1 text-[12px] font-normal text-white/40">{unit}</span>}
      </dd>
    </div>
  );
}

function Cluster({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className={cn(T.cluster, "p-3")}>
      <h4 className={cn(T.label, "mb-2 text-white/30")}>{title}</h4>
      <dl className="space-y-1.5">{children}</dl>
    </section>
  );
}

function DeepDiveLink({ symbol }: { symbol: string }) {
  return (
    <Button
      asChild
      variant="ghost"
      size="sm"
      onClick={(e) => e.stopPropagation()}
      className="group/dd h-8 gap-1 rounded-lg px-2.5 text-[12px] font-medium text-white/50 hover:bg-white/[0.06] hover:text-white"
    >
      <a href={`/pair/${symbol}`}>
        Deep Dive
        <ArrowUpRight className="size-3.5 transition-transform duration-200 group-hover/dd:-translate-y-0.5 group-hover/dd:translate-x-0.5" />
      </a>
    </Button>
  );
}

/* ============================================================================
 * 5. CARD — identity (top) → decision (middle) → context (bottom)
 * ========================================================================== */

export function PairCard({
  data,
  selected,
  onSelect,
}: {
  data: PairData;
  selected?: boolean;
  onSelect?: (symbol: string) => void;
}) {
  const h = data.h4 ?? {};
  const h1 = data.h1 ?? {};
  const lv = data.levels ?? {};
  const al = alignmentFor(data);
  const isLimit = !!data.action && data.action.startsWith("LIMIT AT LIQ");

  return (
    <Card
      role="button"
      tabIndex={0}
      data-symbol={data.symbol}
      onClick={() => onSelect?.(data.symbol)}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect?.(data.symbol)}
      className={cn(
        "group flex cursor-pointer flex-col gap-5 rounded-xl border-0 p-5 text-white shadow-none",
        T.surface,
        "transition-all duration-200 ease-out hover:-translate-y-0.5 hover:bg-zinc-900/90 hover:ring-white/[0.12]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/50",
        selected && "bg-zinc-900/90 ring-sky-400/40 hover:ring-sky-400/50",
      )}
    >
      {/* IDENTITY ─────────────────────────────────────── */}
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className={cn("text-[15px] font-semibold tracking-tight", T.strong)}>
            {data.symbol}
          </h3>
          <p className="mt-0.5 truncate text-[12px] text-white/40">
            {PAIR_NAMES[data.symbol] ?? ""}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <AlignmentIndicator al={al} />
          <ScoreRing score={data.score} />
        </div>
      </header>

      {/* DECISION (shouts) ────────────────────────────── */}
      <div className="space-y-3">
        <div className="text-[26px] font-semibold leading-none tracking-tight tabular-nums text-white">
          {priceTxt(data.symbol, data.price)}
        </div>

        <div className="space-y-1">
          <p
            className={cn(
              "text-[14px] font-semibold tracking-tight",
              isLimit ? "text-sky-400" : "text-white/90",
            )}
          >
            {data.action || "WAIT"}
          </p>
          {data.reason && (
            <p className="text-[12px] leading-relaxed text-white/40">{data.reason}</p>
          )}
        </div>

        <div className="flex flex-wrap gap-1.5">
          <BiasPill tf="H4" raw={h.direction} />
          <BiasPill tf="H1" raw={h1.bias} />
          <BiasPill tf="M15" raw={data.m15_bias} />
        </div>
      </div>

      {/* CONTEXT (whispers) ───────────────────────────── */}
      <div className="grid grid-cols-2 gap-2">
        <Cluster title="H4">
          <Stat label="Gate" value={h.gate || "--"} />
          <Stat label="Dir" value={h.direction || "--"} />
          <Stat label="ADX" value={fmt(h.adx, 1)} />
          <Stat label="ATRx" value={fmt(h.atr_ratio, 1)} />
        </Cluster>
        <Cluster title="H1 · M15 · Levels">
          <Stat label="H1 Dist" value={fmt(h1.dist_ema21_atr, 2)} unit="ATR" />
          <Stat label="M15" value={data.m15_bias || "--"} />
          <Stat label="Liq High" value={fmt(lv.liq_high)} />
        </Cluster>
      </div>

      {/* FOOTER ───────────────────────────────────────── */}
      <footer className={cn("-mx-5 -mb-2 flex justify-end border-t px-3 pt-3", T.divider)}>
        <DeepDiveLink symbol={data.symbol} />
      </footer>
    </Card>
  );
}

/** Placeholder when a symbol has no data yet (mirrors renderCards fallback). */
export function PairCardEmpty({ symbol }: { symbol: string }) {
  return (
    <Card className={cn("flex flex-col gap-5 rounded-xl border-0 p-5 text-white shadow-none", T.surface)}>
      <header className="flex items-start justify-between">
        <div>
          <h3 className="text-[15px] font-semibold tracking-tight text-white/60">{symbol}</h3>
          <p className="mt-0.5 text-[12px] text-white/30">{PAIR_NAMES[symbol] ?? ""}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className={cn("size-2 rounded-full", ALIGN_DOT.LOW)} />
          <ScoreRing score={null} />
        </div>
      </header>
      <div className="text-[26px] font-semibold leading-none tabular-nums text-white/30">—</div>
      <div className="space-y-2">
        <div className="h-3 w-2/3 animate-pulse rounded bg-white/[0.04]" />
        <div className="h-3 w-1/2 animate-pulse rounded bg-white/[0.04]" />
      </div>
    </Card>
  );
}

/* ============================================================================
 * 6. GRID — same responsive breakpoints as the current #grid
 * ========================================================================== */

export function ScannerGrid({
  data,
  selected,
  onSelect,
}: {
  data: Record<string, PairData | undefined>;
  selected: string;
  onSelect: (symbol: string) => void;
}) {
  return (
    <TooltipProvider delayDuration={200}>
      <div
        className={cn(
          "font-sans antialiased [font-feature-settings:'cv11','ss01']",
          // <900px: horizontal snap scroller (unchanged behavior)
          "flex snap-x snap-mandatory gap-4 overflow-x-auto pb-3",
          "[&>*]:min-w-[320px] [&>*]:snap-start",
          // ≥900px: grid 2 / 3 / 4
          "min-[900px]:grid min-[900px]:grid-cols-2 min-[900px]:overflow-visible min-[900px]:pb-0 min-[900px]:[&>*]:min-w-0",
          "min-[1200px]:grid-cols-3 min-[1600px]:grid-cols-4",
        )}
      >
        {PAIRS.map((s) =>
          data[s] ? (
            <PairCard key={s} data={data[s]!} selected={s === selected} onSelect={onSelect} />
          ) : (
            <PairCardEmpty key={s} symbol={s} />
          ),
        )}
      </div>
    </TooltipProvider>
  );
}

/* ============================================================================
 * 7. ROW VARIANT — Left = identity · Middle = price + signal · Right = context
 *    Same fields as the card, for a <1s top-to-bottom scan.
 * ========================================================================== */

const th = "h-10 px-4 text-[12px] font-medium text-white/40";
const ctxCell = "bg-white/[0.015]"; // subtle band groups the context columns

export function ScannerTable({
  data,
  selected,
  onSelect,
}: {
  data: Record<string, PairData | undefined>;
  selected: string;
  onSelect: (symbol: string) => void;
}) {
  return (
    <TooltipProvider delayDuration={200}>
      <div className={cn("overflow-x-auto rounded-xl font-sans antialiased", T.surface)}>
        <Table className="text-white">
          <TableHeader>
            <TableRow className={cn("hover:bg-transparent", T.divider)}>
              {/* identity */}
              <TableHead className={th}>Pair</TableHead>
              {/* what matters */}
              <TableHead className={cn(th, "text-right")}>Price</TableHead>
              <TableHead className={th}>Action</TableHead>
              <TableHead className={th}>Bias</TableHead>
              <TableHead className={th}>Align</TableHead>
              <TableHead className={cn(th, "text-center")}>Score</TableHead>
              {/* context */}
              <TableHead className={cn(th, ctxCell)}>H4</TableHead>
              <TableHead className={cn(th, ctxCell, "text-right")}>H1 Dist</TableHead>
              <TableHead className={cn(th, ctxCell)}>M15</TableHead>
              <TableHead className={cn(th, ctxCell, "text-right")}>Liq High</TableHead>
              <TableHead className={th} />
            </TableRow>
          </TableHeader>

          <TableBody>
            {PAIRS.map((s) => {
              const d = data[s];
              const h = d?.h4 ?? {};
              const h1 = d?.h1 ?? {};
              const lv = d?.levels ?? {};
              const al = d ? alignmentFor(d) : null;
              const isLimit = !!d?.action && d.action.startsWith("LIMIT AT LIQ");

              return (
                <TableRow
                  key={s}
                  data-symbol={s}
                  onClick={() => d && onSelect(s)}
                  className={cn(
                    "cursor-pointer transition-colors duration-150 hover:bg-white/[0.03]",
                    T.divider,
                    s === selected && "bg-sky-400/[0.05] hover:bg-sky-400/[0.07]",
                  )}
                >
                  {/* Identity */}
                  <TableCell className="px-4 py-3.5">
                    <div className="flex items-center gap-3">
                      <span
                        className={cn(
                          "h-8 w-0.5 rounded-full transition-colors",
                          s === selected ? "bg-sky-400" : "bg-transparent",
                        )}
                      />
                      <div>
                        <div className={cn("text-[14px] font-semibold tracking-tight", T.strong)}>{s}</div>
                        <div className="text-[12px] text-white/40">{PAIR_NAMES[s]}</div>
                      </div>
                    </div>
                  </TableCell>

                  {/* Price */}
                  <TableCell className="px-4 text-right text-[16px] font-semibold tabular-nums text-white">
                    {d ? priceTxt(s, d.price) : "—"}
                  </TableCell>

                  {/* Action + reason */}
                  <TableCell className="max-w-[280px] px-4">
                    <div className={cn("text-[14px] font-semibold", isLimit ? "text-sky-400" : "text-white/90")}>
                      {d?.action || "WAIT"}
                    </div>
                    {d?.reason && (
                      <div className="truncate text-[12px] text-white/40" title={d.reason}>
                        {d.reason}
                      </div>
                    )}
                  </TableCell>

                  {/* Bias */}
                  <TableCell className="px-4">
                    <div className="flex gap-1">
                      <BiasPill tf="H4" raw={h.direction} />
                      <BiasPill tf="H1" raw={h1.bias} />
                      <BiasPill tf="M15" raw={d?.m15_bias} />
                    </div>
                  </TableCell>

                  {/* Alignment */}
                  <TableCell className="px-4">{al && <AlignmentIndicator al={al} />}</TableCell>

                  {/* Score */}
                  <TableCell className="px-4">
                    <div className="flex justify-center">
                      <ScoreRing score={d ? d.score : null} />
                    </div>
                  </TableCell>

                  {/* Context: H4 stacked */}
                  <TableCell className={cn("px-4", ctxCell)}>
                    <div className="text-[14px] font-medium text-white/80">
                      {h.gate || "--"} <span className="text-white/30">·</span> {h.direction || "--"}
                    </div>
                    <div className="text-[12px] tabular-nums text-white/40">
                      ADX <span className="text-white/70">{fmt(h.adx, 1)}</span>
                      <span className="mx-1.5 text-white/20">·</span>
                      ATRx <span className="text-white/70">{fmt(h.atr_ratio, 1)}</span>
                    </div>
                  </TableCell>

                  <TableCell className={cn("px-4 text-right", ctxCell)}>
                    <span className={T.value}>{fmt(h1.dist_ema21_atr, 2)}</span>
                    <span className="ml-1 text-[12px] text-white/40">ATR</span>
                  </TableCell>

                  <TableCell className={cn("px-4", ctxCell, T.value)}>{d?.m15_bias || "--"}</TableCell>

                  <TableCell className={cn("px-4 text-right", ctxCell, T.value)}>
                    {fmt(lv.liq_high)}
                  </TableCell>

                  <TableCell className="px-2 text-right">
                    <DeepDiveLink symbol={s} />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </TooltipProvider>
  );
}
