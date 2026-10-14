// Rasterise the tab icons from the SVGs tools/extract_doormark.py writes.
//
// Through a headless browser rather than a rasteriser library: the artwork is
// an SVG with a mask, and the browser is the renderer the icons will actually
// be seen through. One dependency fewer, and no surprises between what this
// draws and what a tab draws.
//
// Needs playwright. This repo has no package.json, so install it next to the
// script and run from the repo root (node_modules/ is gitignored):
//   npm install playwright
//   node tools/render_favicons.mjs
//
// Paths resolve against the repo rather than the cwd, so it also runs from
// anywhere by absolute path. Run it after extract_doormark.py, which writes
// the SVGs it reads.

import { chromium } from 'playwright';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

const JOBS = [
  // The small sizes take the solid cut: at 16px the line work inside the
  // doors is thinner than a pixel and reads as a smudge.
  { svg: 'images/favicon-small.svg', out: 'images/favicon-16.png', size: 16, bg: 'transparent' },
  { svg: 'images/favicon-small.svg', out: 'images/favicon-32.png', size: 32, bg: 'transparent' },
  // Apple renders this one large, and wants it opaque.
  { svg: 'images/favicon.svg', out: 'images/favicon-180.png', size: 180, bg: '#faf8f5' },
];

const browser = await chromium.launch();
for (const { svg, out, size, bg } of JOBS) {
  const art = readFileSync(join(ROOT, svg), 'utf8');
  const page = await browser.newPage({
    viewport: { width: size, height: size },
    deviceScaleFactor: 1,
  });
  await page.setContent(`<!doctype html><style>
      html, body { margin: 0; padding: 0; width: ${size}px; height: ${size}px;
                   background: ${bg}; }
      svg { width: ${size}px; height: ${size}px; display: block; }
    </style>${art}`);
  await page.waitForTimeout(200);
  await page.screenshot({ path: join(ROOT, out), omitBackground: bg === 'transparent' });
  await page.close();
  console.log(`${out}  ${size}x${size}`);
}
await browser.close();
