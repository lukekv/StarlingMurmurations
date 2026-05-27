// build_presentation.js
// Generates Texture_Library_Pipeline_Presentation.pptx
// Run: node build_presentation.js

const pptxgen = require("pptxgenjs");

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";   // 10" × 5.625"
pres.title  = "Texture Library Image Sorter";

// ─── Palette ────────────────────────────────────────────────────────────────
const BG   = "0F1628";   // very dark navy
const CARD = "1A2540";   // card / panel background
const DIV  = "243050";   // subtle divider / border
const ACC  = "4F8FD4";   // blue  (primary accent)
const GRN  = "5AAF6A";   // green
const AMB  = "E8A338";   // amber
const PRP  = "9B7AD4";   // purple
const RED  = "CF6060";   // red
const WHT  = "FFFFFF";
const TXT  = "DDE3F5";   // light text
const MUT  = "7A8AAE";   // muted / secondary text

const STAGE_COLORS = [ACC, GRN, AMB, PRP, RED];
const STAGES = [
  { num: "01", name: "Scan",        sub: "Find & group PBR sets",       color: ACC },
  { num: "02", name: "Deduplicate", sub: "Remove identical files",       color: GRN },
  { num: "03", name: "Quality",     sub: "Test every texture",           color: AMB },
  { num: "04", name: "AI Tag",      sub: "Categorize with local AI",     color: PRP },
  { num: "05", name: "Output",      sub: "Write organized library",      color: RED },
];

// ─── Shared helpers ──────────────────────────────────────────────────────────

function bg(slide) {
  slide.background = { color: BG };
}

// Standard dark title + subtle rule underneath
function titleBar(slide, text) {
  bg(slide);
  slide.addText(text, {
    x: 0.5, y: 0.22, w: 9, h: 0.62,
    fontFace: "Georgia", fontSize: 27, bold: true, color: WHT, margin: 0,
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 0.88, w: 9, h: 0.025,
    fill: { color: DIV }, line: { color: DIV },
  });
}

// Colored card with optional left-side accent bar
function card(slide, x, y, w, h, accentColor) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h, fill: { color: CARD }, line: { color: DIV },
  });
  if (accentColor) {
    slide.addShape(pres.shapes.RECTANGLE, {
      x, y, w: 0.07, h,
      fill: { color: accentColor }, line: { color: accentColor },
    });
  }
}

// Top-colour-bar card (no left bar)
function topCard(slide, x, y, w, h, accentColor) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h, fill: { color: CARD }, line: { color: DIV },
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h: 0.07,
    fill: { color: accentColor }, line: { color: accentColor },
  });
}

// Small numbered badge (stage indicator)
function badge(slide, stageIdx, x, y) {
  const s = STAGES[stageIdx];
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w: 0.7, h: 0.7,
    fill: { color: s.color }, line: { color: s.color },
  });
  slide.addText(s.num, {
    x, y, w: 0.7, h: 0.7,
    fontFace: "Georgia", fontSize: 20, bold: true, color: WHT,
    align: "center", valign: "middle", margin: 0,
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 1 — Title
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  bg(s);

  // Full-height left accent bar
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 0.22, h: 5.625,
    fill: { color: ACC }, line: { color: ACC },
  });

  s.addText("Texture Library\nImage Sorter", {
    x: 0.55, y: 1.1, w: 9.1, h: 2.0,
    fontFace: "Georgia", fontSize: 52, bold: true, color: WHT, align: "left",
  });

  s.addText("An AI-powered pipeline that automatically sorts, names,\nand organizes 20,000+ architectural texture files", {
    x: 0.55, y: 3.2, w: 8.0, h: 0.95,
    fontFace: "Calibri", fontSize: 18, color: MUT, align: "left",
  });

  // Bottom rule + label
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.55, y: 4.75, w: 3.8, h: 0.035,
    fill: { color: ACC }, line: { color: ACC },
  });
  s.addText("2026  ·  Architectural Visualization", {
    x: 0.55, y: 4.9, w: 5, h: 0.3,
    fontFace: "Calibri", fontSize: 11, color: MUT,
  });
  s.addText("TEXTURE PIPELINE", {
    x: 6.5, y: 4.9, w: 3, h: 0.3,
    fontFace: "Calibri", fontSize: 11, color: DIV, align: "right", charSpacing: 3,
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 2 — The Problem
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "The Problem at Scale");

  s.addText(
    "An architect's texture library grows for years, accumulating files from " +
    "many sources. Without automation, finding a single material means manually " +
    "browsing thousands of unsorted, inconsistently named files.",
    { x: 0.5, y: 1.0, w: 9, h: 0.62,
      fontFace: "Calibri", fontSize: 14, color: MUT }
  );

  const stats = [
    { num: "20,000+",  label: "Texture files\nto organize",               color: ACC },
    { num: "55+",      label: "Hours of manual\nreview avoided",           color: GRN },
    { num: "23",       label: "Material categories\nauto-assigned by AI",  color: AMB },
  ];

  stats.forEach((st, i) => {
    const x = 0.55 + i * 3.05;
    topCard(s, x, 1.78, 2.85, 2.85, st.color);
    s.addText(st.num, {
      x, y: 2.1, w: 2.85, h: 1.2,
      fontFace: "Georgia", fontSize: 54, bold: true, color: st.color,
      align: "center", valign: "middle", margin: 0,
    });
    s.addText(st.label, {
      x, y: 3.35, w: 2.85, h: 0.9,
      fontFace: "Calibri", fontSize: 14, color: TXT, align: "center", valign: "top",
    });
  });

  s.addText(
    "The pipeline also handles PBR texture sets — groups of 4–6 related files " +
    "that must be recognized as one unit, deduplicated together, and copied as a set.",
    { x: 0.5, y: 4.9, w: 9, h: 0.4,
      fontFace: "Calibri", fontSize: 12, color: MUT }
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 3 — What is a PBR Texture Set?
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "What Is a PBR Texture Set?");

  // Left column
  s.addText(
    "A texture is a flat image applied to a 3D surface to make it look like a " +
    "real material. PBR (Physically Based Rendering) textures come in sets of " +
    "4–6 coordinated files that together simulate how light behaves on a surface.",
    { x: 0.5, y: 1.02, w: 4.5, h: 1.3,
      fontFace: "Calibri", fontSize: 13, color: TXT }
  );

  s.addText(
    "The pipeline must detect that these files belong together, keep them as one " +
    "group, and copy the full set to the output folder.",
    { x: 0.5, y: 2.4, w: 4.5, h: 0.75,
      fontFace: "Calibri", fontSize: 13, color: MUT }
  );

  // Code block — example group
  topCard(s, 0.5, 3.25, 4.5, 1.98, DIV);
  s.addText(
    "ConcreteWall_Albedo.jpg\nConcreteWall_Normal.jpg\nConcreteWall_Roughness.jpg\nConcreteWall_Metallic.jpg\nConcreteWall_Displace.jpg",
    { x: 0.65, y: 3.42, w: 4.2, h: 1.65,
      fontFace: "Consolas", fontSize: 12, color: ACC }
  );

  // Right column — map cards
  const maps = [
    { name: "Albedo",        desc: "The base surface color",              color: GRN },
    { name: "Normal",        desc: "Microscopic surface bumps",           color: ACC },
    { name: "Roughness",     desc: "How shiny or matte the surface is",   color: AMB },
    { name: "Metallic",      desc: "Metal vs. non-metal areas",           color: PRP },
    { name: "Displacement",  desc: "Physical depth and surface relief",   color: RED },
  ];

  maps.forEach((m, i) => {
    const y = 1.02 + i * 0.89;
    card(s, 5.3, y, 4.2, 0.76, m.color);
    s.addText(m.name, {
      x: 5.52, y: y + 0.08, w: 3.8, h: 0.27,
      fontFace: "Calibri", fontSize: 13, bold: true, color: WHT, margin: 0,
    });
    s.addText(m.desc, {
      x: 5.52, y: y + 0.4, w: 3.8, h: 0.26,
      fontFace: "Calibri", fontSize: 12, color: MUT, margin: 0,
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 4 — Pipeline Overview
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "The Five-Stage Pipeline");

  s.addText(
    "Every texture passes through five automated stages — like an assembly line. " +
    "Progress is saved after each step so the pipeline can be safely stopped and resumed at any time.",
    { x: 0.5, y: 0.98, w: 9, h: 0.62,
      fontFace: "Calibri", fontSize: 14, color: MUT }
  );

  const cardW = 1.7, gap = 0.125, startX = 0.55;
  STAGES.forEach((stage, i) => {
    const x = startX + i * (cardW + gap);
    topCard(s, x, 1.8, cardW, 3.1, stage.color);
    s.addText(stage.num, {
      x, y: 2.05, w: cardW, h: 0.65,
      fontFace: "Georgia", fontSize: 34, bold: true, color: stage.color,
      align: "center", margin: 0,
    });
    s.addText(stage.name, {
      x, y: 2.78, w: cardW, h: 0.42,
      fontFace: "Calibri", fontSize: 13, bold: true, color: WHT, align: "center", margin: 0,
    });
    s.addText(stage.sub, {
      x: x + 0.07, y: 3.28, w: cardW - 0.14, h: 0.8,
      fontFace: "Calibri", fontSize: 11, color: MUT, align: "center",
    });
    if (i < STAGES.length - 1) {
      s.addText("›", {
        x: x + cardW, y: 3.0, w: gap + 0.08, h: 0.5,
        fontFace: "Georgia", fontSize: 24, color: DIV, align: "center", margin: 0,
      });
    }
  });

  s.addText(
    "All decisions are recorded in a database. A crashed run resumes from exactly where it stopped — nothing is repeated.",
    { x: 0.5, y: 5.12, w: 9, h: 0.35,
      fontFace: "Calibri", fontSize: 12, color: MUT, align: "center" }
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 5 — Stage 01: Scan
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "Stage 01  ·  Scan & Group");
  badge(s, 0, 9.0, 0.15);

  const points = [
    ["Walks every folder",          "Finds all image files (JPG, PNG, TIF, EXR, etc.) in the entire input directory tree."],
    ["Groups PBR sets",             "Files sharing the same base name in the same folder become one group: three files → one texture."],
    ["Skips known junk folders",    "Folder names like 'ChaosGroupTextureCache' or 'Single planks' are excluded at scan time before any processing begins."],
    ["Reads keywords from names",   "Words like 'sky,' 'rug,' 'seamless,' or 'hdri' in a filename pre-classify the texture before the AI is called."],
    ["Parses real-world dimensions", "Measurements in filenames ('600x300mm,' '48x36in') are extracted and stored. Ambiguous units default to inches."],
  ];

  points.forEach(([hdr, body], i) => {
    const y = 1.03 + i * 0.88;
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y, w: 0.06, h: 0.68,
      fill: { color: ACC }, line: { color: ACC },
    });
    s.addText(hdr, {
      x: 0.7, y: y + 0.03, w: 8.7, h: 0.27,
      fontFace: "Calibri", fontSize: 13, bold: true, color: WHT, margin: 0,
    });
    s.addText(body, {
      x: 0.7, y: y + 0.33, w: 8.7, h: 0.32,
      fontFace: "Calibri", fontSize: 12, color: MUT, margin: 0,
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 6 — Stage 02: Deduplication
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "Stage 02  ·  Deduplication");
  badge(s, 1, 9.0, 0.15);

  s.addText(
    "A perceptual hash (pHash) is computed for every base image. Think of it as a fingerprint for " +
    "what an image looks like — not its exact bytes. Two visually identical images in different " +
    "formats or sizes produce nearly identical fingerprints.",
    { x: 0.5, y: 1.02, w: 9, h: 0.82,
      fontFace: "Calibri", fontSize: 14, color: TXT }
  );

  // Process flow
  const steps = [
    { label: "Load base\ncolor image",          color: ACC },
    { label: "Compute 64-bit\nperceptual hash", color: GRN },
    { label: "Search BK-tree\nfor near-matches", color: AMB },
    { label: "Distance ≤ 4?\nMark duplicate",   color: RED },
  ];

  steps.forEach((st, i) => {
    const x = 0.42 + i * 2.38;
    topCard(s, x, 2.1, 2.12, 1.4, st.color);
    s.addText(st.label, {
      x: x + 0.05, y: 2.28, w: 2.02, h: 1.1,
      fontFace: "Calibri", fontSize: 12, color: TXT, align: "center", valign: "middle",
    });
    if (i < steps.length - 1) {
      s.addText("→", {
        x: x + 2.12, y: 2.58, w: 0.26, h: 0.5,
        fontFace: "Georgia", fontSize: 22, color: MUT, align: "center", margin: 0,
      });
    }
  });

  // Two notes
  card(s, 0.5, 3.75, 4.25, 1.55, GRN);
  s.addText("What gets saved", {
    x: 0.72, y: 3.84, w: 3.9, h: 0.28,
    fontFace: "Calibri", fontSize: 13, bold: true, color: GRN, margin: 0,
  });
  s.addText(
    "The alphabetically first copy is kept. The duplicate is copied to _recycle_bin/duplicates/ " +
    "for reference. A full duplicate report is written so every decision can be audited.",
    { x: 0.72, y: 4.17, w: 3.9, h: 0.98,
      fontFace: "Calibri", fontSize: 12, color: MUT }
  );

  card(s, 5.25, 3.75, 4.25, 1.55, AMB);
  s.addText("Known limitation", {
    x: 5.47, y: 3.84, w: 3.9, h: 0.28,
    fontFace: "Calibri", fontSize: 13, bold: true, color: AMB, margin: 0,
  });
  s.addText(
    "Colorway variants — the same fabric pattern in two different dye colors — can be falsely " +
    "flagged as duplicates. This is a known limitation and an area for future refinement.",
    { x: 5.47, y: 4.17, w: 3.9, h: 0.98,
      fontFace: "Calibri", fontSize: 12, color: MUT }
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 7 — Stage 03: Quality Checks
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "Stage 03  ·  Five Quality Checks");
  badge(s, 2, 9.0, 0.15);

  s.addText("Five automated checks run on every texture. Failures go to a review folder — not the bin.", {
    x: 0.5, y: 0.98, w: 9, h: 0.44,
    fontFace: "Calibri", fontSize: 14, color: MUT,
  });

  const checks = [
    { n: "1", label: "Minimum Resolution",    color: ACC,
      desc: "Base map must be at least 512 px on the shortest side. Too small to be useful in professional rendering." },
    { n: "2", label: "Blank / Solid Color",   color: GRN,
      desc: "Measures pixel variation. Near-zero variation means a solid export or an empty layer — sent to recycle bin." },
    { n: "3", label: "Product Photo",         color: AMB,
      desc: "All four edge strips near-uniform = object photographed on a studio background, not a material texture." },
    { n: "4", label: "Line Art / Drawings",   color: PRP,
      desc: "If 60%+ of pixels are near-white, it's a CAD drawing, floor plan, or technical document — not a texture." },
    { n: "5", label: "Tileability",           color: RED,
      desc: "Two signals: (1) edge gradient spike ratio and (2) opposite-edge pixel similarity. Both must pass." },
  ];

  checks.forEach((c, i) => {
    const y = 1.52 + i * 0.8;
    slide_card_row(s, c.n, c.label, c.desc, c.color, y);
  });

  function slide_card_row(slide, num, label, desc, color, y) {
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y, w: 0.44, h: 0.44,
      fill: { color }, line: { color },
    });
    slide.addText(num, {
      x: 0.5, y, w: 0.44, h: 0.44,
      fontFace: "Georgia", fontSize: 18, bold: true, color: WHT,
      align: "center", valign: "middle", margin: 0,
    });
    slide.addText(label, {
      x: 1.08, y: y + 0.02, w: 8.3, h: 0.26,
      fontFace: "Calibri", fontSize: 13, bold: true, color: WHT, margin: 0,
    });
    slide.addText(desc, {
      x: 1.08, y: y + 0.3, w: 8.3, h: 0.4,
      fontFace: "Calibri", fontSize: 12, color: MUT, margin: 0,
    });
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 8 — Stage 04: AI Tagging
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "Stage 04  ·  AI Tagging");
  badge(s, 3, 9.0, 0.15);

  s.addText(
    "A local AI vision model (Gemma 4, via Ollama) looks at each texture image " +
    "and answers structured questions — entirely on-device, no internet, no cost.",
    { x: 0.5, y: 0.98, w: 9, h: 0.62,
      fontFace: "Calibri", fontSize: 14, color: TXT }
  );

  // Table: what the AI determines
  const rows = [
    [
      { text: "What the AI decides", options: { bold: true, color: WHT, fill: { color: DIV }, align: "center" } },
      { text: "Example",             options: { bold: true, color: WHT, fill: { color: DIV }, align: "center" } },
    ],
    ["Category (from fixed list of 23)", "Wood"],
    ["Material",                          "Oak"],
    ["Material Type",                     "Planks"],
    ["Dominant Color",                    "Beige"],
    ["Tags",                              "wood, oak, planks, natural, woodgrain"],
    ["Is Tileable?",                      "true"],
    ["Real-World Size Estimate",          "2m × 2m  (filename-parsed value takes priority)"],
  ];

  s.addTable(rows, {
    x: 0.5, y: 1.72, w: 5.45, h: 3.55,
    fontFace: "Calibri", fontSize: 12, color: TXT,
    fill: { color: CARD },
    border: { pt: 0.5, color: DIV },
    rowH: 0.43,
    colW: [3.05, 2.4],
  });

  // Right side info cards
  const facts = [
    { label: "Local & private",   desc: "No data leaves the machine. No cloud fees. No rate limits.",                 color: GRN },
    { label: "23 categories",     desc: "Wood, Stone, Tile, Fabric, Concrete, Metal, Brick, Art, Sky, Water...",     color: ACC },
    { label: "Auto-retry",        desc: "Up to 3 retries with backoff on timeout. Failures resume on next run.",     color: AMB },
    { label: "Filename hints",    desc: "Keywords pre-classify textures — AI validates rather than guesses cold.",   color: PRP },
    { label: "Secondary guard",   desc: "AI disagreement with geometry test → _needs_review/ai_not_tileable/.",      color: RED },
  ];

  facts.forEach((f, i) => {
    const y = 1.72 + i * 0.73;
    card(s, 6.2, y, 3.3, 0.64, f.color);
    s.addText(f.label, {
      x: 6.42, y: y + 0.07, w: 2.95, h: 0.24,
      fontFace: "Calibri", fontSize: 12, bold: true, color: WHT, margin: 0,
    });
    s.addText(f.desc, {
      x: 6.42, y: y + 0.35, w: 2.95, h: 0.24,
      fontFace: "Calibri", fontSize: 11, color: MUT, margin: 0,
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 9 — The Override Pass
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "The Override Pass — Rescuing Non-Tileable Textures");

  s.addText(
    "Some genuinely useful textures are not tileable by design: artwork, sky images, water photos, " +
    "grunge overlays. The tileability check correctly rejects them — but they still belong in the library.",
    { x: 0.5, y: 0.98, w: 9, h: 0.72,
      fontFace: "Calibri", fontSize: 14, color: TXT }
  );

  // Flow boxes
  const flowSteps = [
    { label: "Tileability\nFailed folder",      color: RED },
    { label: "AI is asked:\n\"What is this?\"", color: PRP },
    { label: "Art / Sky /\nUtility / Water?",   color: AMB },
  ];

  flowSteps.forEach((st, i) => {
    const x = 0.5 + i * 2.75;
    topCard(s, x, 2.0, 2.4, 1.2, st.color);
    s.addText(st.label, {
      x: x + 0.05, y: 2.18, w: 2.3, h: 0.9,
      fontFace: "Calibri", fontSize: 13, color: TXT, align: "center", valign: "middle",
    });
    if (i < flowSteps.length - 1) {
      s.addText("→", {
        x: x + 2.4, y: 2.42, w: 0.35, h: 0.5,
        fontFace: "Georgia", fontSize: 24, color: MUT, align: "center", margin: 0,
      });
    }
  });

  // Yes → rescued
  s.addText("YES →", {
    x: 8.1, y: 2.07, w: 1.3, h: 0.38,
    fontFace: "Calibri", fontSize: 12, bold: true, color: GRN,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 8.1, y: 2.47, w: 1.6, h: 0.55,
    fill: { color: CARD }, line: { color: GRN },
  });
  s.addText("→ Library", {
    x: 8.1, y: 2.47, w: 1.6, h: 0.55,
    fontFace: "Calibri", fontSize: 12, bold: true, color: GRN,
    align: "center", valign: "middle",
  });

  // No → confirmed
  s.addText("NO →", {
    x: 8.1, y: 3.07, w: 1.3, h: 0.38,
    fontFace: "Calibri", fontSize: 12, bold: true, color: RED,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 8.1, y: 3.47, w: 1.6, h: 0.55,
    fill: { color: CARD }, line: { color: RED },
  });
  s.addText("→ Review", {
    x: 8.1, y: 3.47, w: 1.6, h: 0.55,
    fontFace: "Calibri", fontSize: 12, bold: true, color: RED,
    align: "center", valign: "middle",
  });

  // Resumable card
  card(s, 0.5, 3.42, 7.3, 1.88, AMB);
  s.addText("Designed for safe interruption and resume", {
    x: 0.72, y: 3.57, w: 6.9, h: 0.3,
    fontFace: "Calibri", fontSize: 13, bold: true, color: AMB, margin: 0,
  });
  s.addText(
    "Pressing Ctrl+C saves progress cleanly. Textures the AI has already decided on receive a " +
    "terminal database status (tileability_override_confirmed) so they are never re-processed on " +
    "a resumed run. Only genuinely unfinished work is retried. AI call fees are not wasted.",
    { x: 0.72, y: 3.92, w: 6.9, h: 1.25,
      fontFace: "Calibri", fontSize: 13, color: MUT }
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 10 — The Output
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "Stage 05  ·  Organized Output");
  badge(s, 4, 9.0, 0.15);

  s.addText(
    "Files are copied — never moved or deleted from the source. " +
    "Every texture gets a consistent, descriptive name and its own folder:",
    { x: 0.5, y: 1.0, w: 9, h: 0.55,
      fontFace: "Calibri", fontSize: 14, color: MUT }
  );

  // Naming convention highlight
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 1.63, w: 9, h: 0.72,
    fill: { color: CARD }, line: { color: ACC },
  });
  s.addText("Category  _  Material  _  MaterialType  _  Color  _  NN", {
    x: 0.5, y: 1.63, w: 9, h: 0.72,
    fontFace: "Consolas", fontSize: 20, bold: true, color: ACC,
    align: "center", valign: "middle", margin: 0,
  });

  // Folder tree
  topCard(s, 0.5, 2.52, 9, 2.45, DIV);
  const tree =
    "_output/\n" +
    "  Wood/\n" +
    "    Wood_Oak_Planks_Beige_01/\n" +
    "      Wood_Oak_Planks_Beige_01.jpg         ← base map, renamed\n" +
    "      Wood_Oak_Planks_Beige_01_NRM.jpg     ← normal map\n" +
    "      Wood_Oak_Planks_Beige_01_RGH.jpg     ← roughness map\n" +
    "      Wood_Oak_Planks_Beige_01.json        ← all metadata stored here\n" +
    "      Wood_Oak_Planks_Beige_01.pat         ← Revit pattern file";
  s.addText(tree, {
    x: 0.7, y: 2.68, w: 8.6, h: 2.15,
    fontFace: "Consolas", fontSize: 12, color: TXT,
  });

  s.addText(
    "The JSON sidecar stores AI metadata, parsed real-world dimensions, source path, and map list — " +
    "making the library fully self-describing for any future tool.",
    { x: 0.5, y: 5.1, w: 9, h: 0.35,
      fontFace: "Calibri", fontSize: 12, color: MUT }
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 11 — Preview Browser
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "The Preview Browser");

  s.addText(
    "After the pipeline runs, one script generates a single HTML file you open in any browser. " +
    "No installation, no server, no internet — just open and use.",
    { x: 0.5, y: 1.0, w: 9, h: 0.6,
      fontFace: "Calibri", fontSize: 14, color: MUT }
  );

  const features = [
    { label: "Category tabs",           desc: "Browse 23 material categories with live texture counts",           color: ACC },
    { label: "Real-time text search",   desc: "Filter by material, color, tag, or source filename instantly",    color: GRN },
    { label: "PBR filter checkbox",     desc: "Show only textures that have a full set of PBR maps",             color: AMB },
    { label: "Thumbnail grid",          desc: "256 px preview image generated for every texture",                color: PRP },
    { label: "Pixel & size dimensions", desc: "Parsed from filenames — always displayed in inches",              color: RED },
    { label: "Click-to-copy path",      desc: "Click any texture name to copy its Windows folder path",         color: ACC },
    { label: "Review & bin tabs",       desc: "See exactly what was set aside and why — transparent decisions",  color: GRN },
    { label: "Batch selection",         desc: "Multi-select and move or delete groups of textures at once",      color: AMB },
  ];

  features.forEach((f, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = col === 0 ? 0.5 : 5.1;
    const y = 1.75 + row * 0.87;
    card(s, x, y, 4.35, 0.74, f.color);
    s.addText(f.label, {
      x: x + 0.18, y: y + 0.08, w: 4.0, h: 0.27,
      fontFace: "Calibri", fontSize: 13, bold: true, color: WHT, margin: 0,
    });
    s.addText(f.desc, {
      x: x + 0.18, y: y + 0.39, w: 4.0, h: 0.27,
      fontFace: "Calibri", fontSize: 12, color: MUT, margin: 0,
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 12 — Reliability & Crash Recovery
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "Built for Reliability — Crash Recovery");

  s.addText(
    "Every decision is recorded in a local SQLite database. The pipeline can be stopped at any time " +
    "and restarted from exactly where it left off. Nothing is ever repeated.",
    { x: 0.5, y: 0.98, w: 9, h: 0.72,
      fontFace: "Calibri", fontSize: 14, color: TXT }
  );

  // Status progression
  const statuses = [
    { name: "pending",     color: MUT },
    { name: "dedup_check", color: ACC },
    { name: "tileability", color: AMB },
    { name: "ai_tagging",  color: PRP },
    { name: "completed",   color: GRN },
  ];
  statuses.forEach((st, i) => {
    const x = 0.4 + i * 1.88;
    s.addShape(pres.shapes.RECTANGLE, {
      x, y: 1.92, w: 1.65, h: 0.54,
      fill: { color: CARD }, line: { color: st.color },
    });
    s.addText(st.name, {
      x, y: 1.92, w: 1.65, h: 0.54,
      fontFace: "Consolas", fontSize: 11, color: st.color,
      align: "center", valign: "middle", margin: 0,
    });
    if (i < statuses.length - 1) {
      s.addText("→", {
        x: x + 1.65, y: 2.07, w: 0.23, h: 0.3,
        fontFace: "Georgia", fontSize: 16, color: MUT, align: "center", margin: 0,
      });
    }
  });

  // Terminal states
  card(s, 0.5, 2.65, 9, 1.35, GRN);
  s.addText("Terminal States — once reached, never re-processed on resume", {
    x: 0.72, y: 2.78, w: 8.6, h: 0.3,
    fontFace: "Calibri", fontSize: 13, bold: true, color: GRN, margin: 0,
  });
  s.addText(
    "completed  ·  duplicate  ·  binned_resolution  ·  binned_blank  ·  tileability_failed\n" +
    "tileability_override_confirmed  ·  review_no_base_map  ·  review_line_art  ·  review_ai_not_tileable",
    { x: 0.72, y: 3.13, w: 8.6, h: 0.75,
      fontFace: "Consolas", fontSize: 11, color: MUT }
  );

  // Writer thread
  card(s, 0.5, 4.18, 9, 1.13, ACC);
  s.addText("Thread-safe writes — no data corruption under parallel processing", {
    x: 0.72, y: 4.3, w: 8.6, h: 0.3,
    fontFace: "Calibri", fontSize: 13, bold: true, color: ACC, margin: 0,
  });
  s.addText(
    "Six CPU cores run image analysis in parallel. All database writes are queued through a single " +
    "dedicated writer thread so cores never compete — eliminating any risk of database corruption.",
    { x: 0.72, y: 4.65, w: 8.6, h: 0.55,
      fontFace: "Calibri", fontSize: 12, color: MUT }
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 13 — Technology Stack
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "Technology Stack");

  const tech = [
    { name: "Python 3.14",           role: "Core language",        why: "Cross-platform, rich image processing ecosystem, readable code",            color: ACC },
    { name: "Pillow (PIL)",           role: "Image processing",     why: "Resize, hash, statistical analysis — all standard image operations",       color: GRN },
    { name: "SQLite",                 role: "State database",       why: "Zero-installation, single file, crash-safe with WAL journal mode",          color: AMB },
    { name: "Ollama + Gemma 4",       role: "Local AI model",       why: "Runs fully on-device — no internet, no per-call cost, no data sharing",   color: PRP },
    { name: "OpenAI-compatible API",  role: "AI communication",     why: "Standard interface — swap any vision model by editing one config line",    color: RED },
    { name: "HTML + JavaScript",      role: "Preview browser",      why: "Single static file — works offline, no server, shareable immediately",    color: ACC },
  ];

  tech.forEach((t, i) => {
    const y = 1.05 + i * 0.75;
    card(s, 0.5, y, 9, 0.66, t.color);
    s.addText(t.name, {
      x: 0.72, y: y + 0.07, w: 2.3, h: 0.26,
      fontFace: "Calibri", fontSize: 13, bold: true, color: WHT, margin: 0,
    });
    s.addText(t.role, {
      x: 0.72, y: y + 0.37, w: 2.3, h: 0.22,
      fontFace: "Calibri", fontSize: 11, color: t.color, margin: 0,
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x: 3.2, y: y + 0.12, w: 0.03, h: 0.42,
      fill: { color: DIV }, line: { color: DIV },
    });
    s.addText(t.why, {
      x: 3.35, y: y + 0.14, w: 5.95, h: 0.42,
      fontFace: "Calibri", fontSize: 12, color: MUT, valign: "middle",
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 14 — Design Principles
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  titleBar(s, "Key Design Principles");

  const principles = [
    {
      title: "Never touch the source files",
      body: "The pipeline only ever copies — never moves or deletes. The worst case is deleting the output folder and starting fresh. Source files are always safe.",
      color: GRN,
    },
    {
      title: "Everything configurable in one place",
      body: "Every threshold, category, AI model, and format lives in config.py. Nothing is hardcoded. Tune the tool without touching any processing logic.",
      color: ACC,
    },
    {
      title: "Failures go to review, not the bin",
      body: "Tileability failures, AI disagreements, and edge cases go to _needs_review folders for human judgment. Nothing is lost quietly without a record.",
      color: AMB,
    },
    {
      title: "Local AI — private, free, and swappable",
      body: "20,000 cloud AI calls would cost money and send proprietary assets off-site. The local model runs free, stays private, and is replaced by editing one line.",
      color: PRP,
    },
  ];

  principles.forEach((p, i) => {
    const y = 1.06 + i * 1.1;
    card(s, 0.5, y, 9, 0.98, p.color);
    s.addText(p.title, {
      x: 0.72, y: y + 0.1, w: 8.6, h: 0.28,
      fontFace: "Calibri", fontSize: 14, bold: true, color: WHT, margin: 0,
    });
    s.addText(p.body, {
      x: 0.72, y: y + 0.44, w: 8.6, h: 0.46,
      fontFace: "Calibri", fontSize: 13, color: MUT, margin: 0,
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// SLIDE 15 — Summary
// ═══════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  bg(s);

  s.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 0.22, h: 5.625,
    fill: { color: ACC }, line: { color: ACC },
  });

  s.addText("What Was Built", {
    x: 0.55, y: 0.32, w: 9, h: 0.62,
    fontFace: "Georgia", fontSize: 30, bold: true, color: WHT,
  });

  const items = [
    { text: "A 5-stage pipeline processing 20,000+ texture files end-to-end, with full crash recovery",                    color: ACC },
    { text: "pHash deduplication that finds visually identical images across different names and formats",                  color: GRN },
    { text: "5 automated quality gates — resolution, blank detection, product photos, line art, tileability",              color: AMB },
    { text: "Local AI categorization into 23 material categories with consistent, human-readable naming",                  color: PRP },
    { text: "An override pass that rescues Art, Sky, Utility, and Water textures from tileability rejection",              color: RED },
    { text: "A single-file HTML preview browser with real-time search, PBR filter, thumbnails, and batch actions",        color: ACC },
    { text: "A database-backed architecture where every decision is saved and every run is safely resumable",              color: GRN },
  ];

  items.forEach((item, i) => {
    const y = 1.1 + i * 0.59;
    s.addShape(pres.shapes.RECTANGLE, {
      x: 0.6, y: y + 0.15, w: 0.14, h: 0.14,
      fill: { color: item.color }, line: { color: item.color },
    });
    s.addText(item.text, {
      x: 0.9, y, w: 8.6, h: 0.52,
      fontFace: "Calibri", fontSize: 13, color: TXT, valign: "middle",
    });
  });

  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.55, y: 5.16, w: 4.2, h: 0.03,
    fill: { color: ACC }, line: { color: ACC },
  });
  s.addText("Texture Library Image Sorter  ·  2026", {
    x: 0.55, y: 5.23, w: 4.5, h: 0.28,
    fontFace: "Calibri", fontSize: 11, color: MUT,
  });
}

// ─── Write file ──────────────────────────────────────────────────────────────
pres.writeFile({ fileName: "Texture_Library_Pipeline_Presentation.pptx" })
  .then(() => console.log("Done: Texture_Library_Pipeline_Presentation.pptx"))
  .catch(err => { console.error(err); process.exit(1); });
