// Renders Instagram content for לוזי from a plan file.
//   node marketing/studio/render.mjs <plan.json> <out-dir>
// Posts → 1080×1350 JPGs (one per slide; several slides = carousel). Stories → 1080×1920 JPGs (one per story).
// Reels → 1080×1920 MP4 (animated scenes + synthesized music via music.py).
// See README.md for the plan format and the slide templates.
import { createRequire } from 'module';
import { spawn, execFileSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import os from 'os';

const require = createRequire(fs.existsSync('/opt/node-tools/node_modules/') ? '/opt/node-tools/node_modules/' : import.meta.url);
const { chromium } = require('playwright');
const HERE = path.dirname(new URL(import.meta.url).pathname);
const [planFile, OUT] = process.argv.slice(2);
if (!planFile || !OUT) { console.error('usage: render.mjs plan.json out-dir'); process.exit(1); }
const plan = JSON.parse(fs.readFileSync(planFile, 'utf8'));
fs.mkdirSync(OUT, { recursive: true });
const FPS = 30;

const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
// Text fields allow **bold** and line breaks (\n); nothing else.
const rich = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\n/g, '<br>');
const shotCache = {};
const shot = name => {
  if (!name) return '';
  if (!shotCache[name]) {
    const f = [path.join(HERE, 'shots', name), path.join(HERE, 'shots', name + '.jpg'), name].find(x => fs.existsSync(x));
    if (!f) throw new Error('missing screenshot ' + name);
    shotCache[name] = 'data:image/jpeg;base64,' + fs.readFileSync(f).toString('base64');
  }
  return shotCache[name];
};

const THEMES = {
  blue:  { bg: 'radial-gradient(120% 90% at 30% 0%, #60a5fa 0%, #2563eb 45%, #1e3a8a 100%)', fg: '#fff', accent: '#fff', sub: 'rgba(255,255,255,.92)', chip: 'rgba(255,255,255,.18)', chipFg: '#fff', dark: true },
  light: { bg: '#eff6ff', fg: '#0f172a', accent: '#1e3a8a', sub: '#475569', chip: '#2563eb', chipFg: '#fff', dark: false },
  white: { bg: '#ffffff', fg: '#0f172a', accent: '#1e3a8a', sub: '#475569', chip: '#2563eb', chipFg: '#fff', dark: false },
  warm:  { bg: 'radial-gradient(120% 90% at 30% 0%, #f9a8d4 0%, #db2777 50%, #831843 100%)', fg: '#fff', accent: '#fff', sub: 'rgba(255,255,255,.94)', chip: 'rgba(255,255,255,.2)', chipFg: '#fff', dark: true },
  dark:  { bg: 'radial-gradient(120% 90% at 30% 0%, #475569 0%, #1e293b 55%, #0f172a 100%)', fg: '#fff', accent: '#fff', sub: 'rgba(255,255,255,.9)', chip: 'rgba(255,255,255,.14)', chipFg: '#fff', dark: true },
  mint:  { bg: 'radial-gradient(120% 90% at 30% 0%, #6ee7b7 0%, #059669 50%, #064e3b 100%)', fg: '#fff', accent: '#fff', sub: 'rgba(255,255,255,.94)', chip: 'rgba(255,255,255,.2)', chipFg: '#fff', dark: true },
  sun:   { bg: 'radial-gradient(120% 90% at 30% 0%, #fde68a 0%, #f59e0b 50%, #b45309 100%)', fg: '#fff', accent: '#fff', sub: 'rgba(255,255,255,.95)', chip: 'rgba(255,255,255,.22)', chipFg: '#fff', dark: true },
};

// a = animated entrance (reels); order of appearance follows document order.
const A = 'class="a"';
const chip = (t, th) => t ? `<div ${A} style="display:inline-block;background:${th.chip};color:${th.chipFg};font:700 38px Rubik;padding:14px 34px;border-radius:40px;margin-bottom:34px">${rich(t)}</div>` : '';
const phone = (src, w, scroll) => `<div class="a phone" style="width:${w}px;border-radius:${w * 0.14}px;background:#0f172a;padding:${w * 0.035}px;box-shadow:0 40px 90px rgba(5,15,50,.45);flex:none">
  <div style="border-radius:${w * 0.11}px;overflow:hidden;aspect-ratio:1170/2532"><img src="${src}" class="${scroll ? 'scroll' : ''}" style="display:block;width:100%"></div></div>`;
const coupon = (th, big) => `<div ${A} style="margin:${big ? 60 : 50}px auto 0;background:${th.dark ? '#fff' : THEMES.blue.bg};color:${th.dark ? '#1e3a8a' : '#fff'};border-radius:44px;padding:44px 80px;box-shadow:0 30px 70px rgba(5,15,50,.3)">
  <div style="font:600 40px Rubik;opacity:.85">קוד הנחה למצטרפים ראשונים</div>
  <div style="font:800 ${big ? 120 : 104}px Rubik;letter-spacing:6px;direction:ltr">LUZI29</div>
  <div style="font:800 56px Rubik">₪29 לחודש במקום ₪39</div></div>`;

function slideHtml(s, th, reel) {
  const T = s.type || 'hook';
  const titleSize = s.titleSize || (reel ? 104 : 88);
  const title = s.title ? `<div ${A} style="font:800 ${titleSize}px/1.15 Rubik;color:${th.accent}">${rich(s.title)}</div>` : '';
  const sub = s.sub ? `<div ${A} style="font:500 ${reel ? 48 : 44}px/1.5 Rubik;color:${th.sub};margin-top:30px">${rich(s.sub)}</div>` : '';
  const emoji = s.emoji ? `<div ${A} style="font-size:${reel ? 170 : 140}px;line-height:1.1;margin-bottom:20px">${esc(s.emoji)}</div>` : '';
  const foot = s.foot ? `<div ${A} style="font:700 44px Rubik;color:${th.dark ? '#bfdbfe' : '#2563eb'};margin-top:56px">${rich(s.foot)}</div>` : '';
  switch (T) {
    case 'hook':
      return `<div class="c center">${chip(s.tag, th)}${emoji}${title}${sub}${foot}</div>`;
    case 'phone':
      if (reel || s.stack) return `<div class="c center" style="justify-content:flex-start;padding-top:${reel ? 250 : 150}px">${chip(s.tag, th)}
        <div ${A} style="font:800 ${s.titleSize || (reel ? 88 : 70)}px/1.15 Rubik;color:${th.accent}">${rich(s.title)}</div>
        ${s.sub ? `<div ${A} style="font:500 ${reel ? 44 : 38}px/1.45 Rubik;color:${th.sub};margin:22px 0 0">${rich(s.sub)}</div>` : ''}
        <div style="margin-top:${reel ? 60 : 40}px">${phone(shot(s.shot), reel ? 560 : 400, reel)}</div></div>`;
      return `<div class="c" style="flex-direction:row;align-items:center;gap:56px;padding:0 70px">
        <div style="flex:1;text-align:right">${chip(s.tag, th)}
          <div ${A} style="font:800 ${s.titleSize || 76}px/1.15 Rubik;color:${th.accent}">${rich(s.title)}</div>
          ${s.sub ? `<div ${A} style="font:500 40px/1.6 Rubik;color:${th.sub};margin-top:30px">${rich(s.sub)}</div>` : ''}
        </div>${phone(shot(s.shot), 420)}</div>`;
    case 'list':
      return `<div class="c center">${chip(s.tag, th)}${title}
        <div style="display:flex;flex-direction:column;gap:22px;width:880px;margin-top:50px">${(s.items || []).map(([e, t, d]) =>
          `<div ${A} style="background:#fff;border-radius:36px;padding:34px 40px;display:flex;gap:28px;align-items:center;text-align:right;box-shadow:0 18px 40px rgba(15,23,42,.12)">
            <div style="font-size:70px">${esc(e)}</div><div><div style="font:800 46px Rubik;color:#1e3a8a">${rich(t)}</div>
            ${d ? `<div style="font:500 34px/1.4 Rubik;color:#475569;margin-top:6px">${rich(d)}</div>` : ''}</div></div>`).join('')}</div>${foot}</div>`;
    case 'bubble':
      return `<div class="c center">${chip(s.tag, th)}${emoji}${title}${sub}
        <div ${A} style="margin-top:56px;background:#fff;color:#0f172a;border-radius:36px;padding:30px 44px;text-align:right;width:800px;box-shadow:0 30px 70px rgba(5,15,50,.3)">
          <div style="font:700 32px Rubik;color:#64748b">${rich(s.label || '🔔 תזכורת')}</div>
          <div style="font:600 42px/1.4 Rubik;margin-top:8px">${rich(s.bubble)}</div></div>${foot}</div>`;
    case 'stat':
      return `<div class="c center">${chip(s.tag, th)}
        <div ${A} style="font:800 ${reel ? 260 : 220}px/1 Rubik;color:${th.accent}">${rich(s.big)}</div>
        ${title.replace(`${titleSize}px`, `${Math.round(titleSize * 0.75)}px`)}${sub}${foot}</div>`;
    case 'vs':
      return `<div class="c center">${chip(s.tag, th)}${title}
        <div style="display:flex;gap:28px;margin-top:56px;width:920px">
          <div ${A} style="flex:1;background:#fee2e2;color:#991b1b;border-radius:36px;padding:36px;text-align:right">
            <div style="font:800 50px Rubik;margin-bottom:18px">${rich(s.beforeTitle || 'בלי לוזי 😩')}</div>
            <div style="font:500 40px/1.7 Rubik">${(s.before || []).map(x => '✕ ' + rich(x)).join('<br>')}</div></div>
          <div ${A} style="flex:1;background:#dcfce7;color:#166534;border-radius:36px;padding:36px;text-align:right">
            <div style="font:800 50px Rubik;margin-bottom:18px">${rich(s.afterTitle || 'עם לוזי 😌')}</div>
            <div style="font:500 40px/1.7 Rubik">${(s.after || []).map(x => '✓ ' + rich(x)).join('<br>')}</div></div></div>${foot}</div>`;
    case 'offer':
      return `<div class="c center">${chip(s.tag, th)}${title}${sub}${coupon(th, reel)}${foot}</div>`;
    case 'cta':
      return `<div class="c center">
        <div ${A} style="width:${reel ? 280 : 230}px;height:${reel ? 280 : 230}px;border-radius:64px;background:#fff;display:flex;align-items:center;justify-content:center;font:800 ${reel ? 190 : 160}px Rubik;color:#2563eb;box-shadow:0 30px 70px rgba(5,15,50,.4)">ל</div>
        <div ${A} style="font:800 ${reel ? 120 : 100}px Rubik;color:${th.accent};margin-top:46px">${rich(s.title || 'לוזי')}</div>
        ${sub}<div ${A} style="font:700 50px Rubik;color:${th.dark ? '#bfdbfe' : '#2563eb'};margin-top:56px">${rich(s.foot || '🔗 הקישור בביו')}</div></div>`;
    case 'html':
      return s.html;
    default:
      throw new Error('unknown slide type ' + T);
  }
}

function page(s, reel, idx, total) {
  const th = THEMES[s.theme] || THEMES.blue;
  const W = 1080, H = reel ? 1920 : 1350;
  const logoC = th.dark ? '#fff' : '#1e3a8a';
  const dots = !reel && total > 1 ? `<div style="position:absolute;bottom:56px;left:0;right:0;display:flex;justify-content:center;gap:12px">${
    Array.from({ length: total }, (_, k) => `<span style="width:${k === idx ? 34 : 12}px;height:12px;border-radius:6px;background:${k === idx ? (th.dark ? '#fff' : '#2563eb') : (th.dark ? 'rgba(255,255,255,.35)' : 'rgba(37,99,235,.25)')}"></span>`).join('')}</div>` : '';
  const logo = s.type === 'cta' ? '' : `<div style="position:absolute;top:${reel ? 150 : 56}px;right:64px;font:800 46px Rubik;color:${logoC}">🗓️ לוזי</div>`;
  return `<html dir="rtl"><head><link href="https://fonts.googleapis.com/css2?family=Rubik:wght@500;600;700;800&display=block" rel="stylesheet">
  <style>
    body{margin:0;background:#000}
    .s{position:relative;width:${W}px;height:${H}px;overflow:hidden;font-family:Rubik;background:${th.bg};color:${th.fg}}
    .c{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;
       padding:${reel ? '250px 70px 420px' : '120px 70px 110px'}}
    .center{text-align:center}
    b{font-weight:800}
    ${reel ? `
    @keyframes rise{from{opacity:0;transform:translateY(60px) scale(.97)}to{opacity:1;transform:none}}
    @keyframes scroll{0%,15%{transform:translateY(0)}100%{transform:translateY(var(--scroll,-14%))}}
    @keyframes drift{from{transform:scale(1.0)}to{transform:scale(1.06)}}
    .bgz{position:absolute;inset:-4%;background:${th.bg};animation:drift ${s.dur || 3}s linear both}
    .a{animation:rise .55s cubic-bezier(.2,.8,.2,1) both}
    .scroll{animation:scroll ${s.dur || 3}s ease-in-out both}` : ''}
  </style></head><body><div class="s">${reel ? '<div class="bgz"></div>' : ''}${logo}${slideHtml(s, th, reel)}${dots}</div>
  ${reel ? `<script>document.querySelectorAll('.a').forEach((e,i)=>e.style.animationDelay=(0.12+i*0.16)+'s')</script>` : ''}</body></html>`;
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1080, height: 1920 } });
const p = await ctx.newPage();
async function load(html, h) {
  await p.setViewportSize({ width: 1080, height: h });
  await p.setContent(html, { waitUntil: 'networkidle' });
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(150);
}

async function renderPost(item) {
  const files = [];
  for (const [i, s] of item.slides.entries()) {
    await load(page(s, false, i, item.slides.length), 1350);
    const f = path.join(OUT, item.slides.length > 1 ? `${item.id}-${i + 1}.jpg` : `${item.id}.jpg`);
    await p.screenshot({ path: f, type: 'jpeg', quality: 90 });
    files.push(path.basename(f));
  }
  return files;
}

// Stories: one 1080×1920 image per slide, laid out with the reel templates (safe margins for
// Instagram's top/bottom UI), with the entrance animations jumped to their end state.
async function renderStory(item) {
  const files = [];
  for (const [i, s] of item.slides.entries()) {
    await load(page({ ...s, dur: 1 }, true, i, item.slides.length), 1920);
    await p.evaluate(() => document.getAnimations().forEach(a => a.finish()));
    const f = path.join(OUT, item.slides.length > 1 ? `${item.id}-${i + 1}.jpg` : `${item.id}.jpg`);
    await p.screenshot({ path: f, type: 'jpeg', quality: 90 });
    files.push(path.basename(f));
  }
  return files;
}

async function renderReel(item) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'reel-'));
  const silent = path.join(tmp, 'v.mp4');
  const ff = spawn('ffmpeg', ['-loglevel', 'error', '-y', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'medium', '-crf', '20', '-r', String(FPS), silent], { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((res, rej) => ff.on('close', c => c ? rej(new Error('ffmpeg ' + c)) : res()));
  const cuts = []; let t = 0;
  for (const s of item.scenes) {
    const dur = s.dur || 3;
    await load(page({ ...s, dur }, true, 0, 1), 1920);
    await p.evaluate(() => document.getAnimations().forEach(a => a.pause()));
    const n = Math.round(dur * FPS);
    for (let f = 0; f < n; f++) {
      await p.evaluate(ms => document.getAnimations().forEach(a => { a.currentTime = ms; }), (f / FPS) * 1000);
      const buf = await p.screenshot({ type: 'jpeg', quality: 88 });
      if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    }
    t += dur; cuts.push(+t.toFixed(2));
  }
  ff.stdin.end(); await done;
  const total = t; cuts.pop();
  const m = item.music || {};
  const wav = path.join(tmp, 'm.wav');
  const py = process.env.PYTHON || 'python3';
  execFileSync(py, [path.join(HERE, 'music.py'), wav, String(total), cuts.join(','), m.ding != null ? String(m.ding) : '-',
    String(m.key ?? 0), String(m.style ?? 0), String(m.bpm ?? 120)], { stdio: ['ignore', 'ignore', 'inherit'] });
  const out = path.join(OUT, `${item.id}.mp4`);
  execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-i', silent, '-i', wav, '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k',
    '-af', 'loudnorm=I=-14:TP=-1.5:LRA=11', '-shortest', '-movflags', '+faststart', out]);
  fs.rmSync(tmp, { recursive: true, force: true });
  return [path.basename(out)];
}

const manifest = [];
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
for (const item of plan.items.filter(i => !ONLY.length || ONLY.includes(i.id))) {
  const files = item.kind === 'reel' ? await renderReel(item) : item.kind === 'story' ? await renderStory(item) : await renderPost(item);
  manifest.push({ id: item.id, kind: item.kind, files });
  console.log(item.id, files.join(' '));
}
if (!ONLY.length) fs.writeFileSync(path.join(OUT, 'manifest.json'), JSON.stringify(manifest, null, 2));
await browser.close();
