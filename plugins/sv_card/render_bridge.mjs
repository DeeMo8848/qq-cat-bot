#!/usr/bin/env node
/**
 * 影之诗制卡器 —— Node 渲染桥（bot 插件用）
 *
 * 职责：加载原项目的 wasm 渲染内核，按请求渲染卡图并落盘。
 * 设计：**只读引用原项目，绝不修改它**；本脚本自身可独立部署。
 *
 * 用法：
 *   node render_bridge.mjs <request.json>
 *
 * request.json：
 * {
 *   "assets": "/abs/path/to/plugin/assets",   // 本插件的资源目录
 *   "config": { ...CardConfig... },           // 渲染参数
 *   "art":    "/abs/path/to/art.png" | null,  // 立绘
 *   "out":    "/abs/path/to/out.png",         // 输出
 *   "style":  "wb" | "diy"                    // 单卡图 / 效果图
 * }
 *
 * 成功：stdout 输出一行 JSON（含耗时信息），exit 0
 * 失败：stderr 输出原因，exit 1
 *
 * 资源目录结构（由 deploy 脚本从原项目复制而来）：
 *   assets/pkg/wbmaker.js, wbmaker_bg.wasm
 *   assets/fonts/arweibeigbpro_bd.otf          (标题·中文)
 *   assets/fonts/FOT-TsukuAOldMin-Pr6-E.digits.otf  (数字)
 *   assets/fonts/NotoSansSC-Regular.otf        (署名，仅效果图需要)
 *   assets/backgrounds/<class>-2.jpg           (职业背景，仅效果图需要)
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const T0 = Date.now();

function fail(msg) {
  process.stderr.write(String(msg) + '\n');
  process.exit(1);
}

async function main() {
  const reqPath = process.argv[2];
  if (!reqPath) fail('usage: node render_bridge.mjs <request.json>');
  if (!fs.existsSync(reqPath)) fail('request file not found: ' + reqPath);

  let req;
  try {
    req = JSON.parse(fs.readFileSync(reqPath, 'utf8'));
  } catch (e) {
    fail('request parse error: ' + e.message);
  }

  const assets = req.assets;
  const cfg = req.config || {};
  const style = req.style === 'diy' ? 'diy' : 'wb';
  const outPath = req.out;
  if (!assets) fail('missing "assets"');
  if (!outPath) fail('missing "out"');

  const pkgDir = path.join(assets, 'pkg');
  const wasmPath = path.join(pkgDir, 'wbmaker_bg.wasm');
  if (!fs.existsSync(wasmPath)) fail('wasm not found: ' + wasmPath);

  const tLoad = Date.now();
  const mod = await import(pathToFileURL(path.join(pkgDir, 'wbmaker.js')).href);
  const { initSync, register_font, render_card, render_diy_card } = mod;

  // 兼容新旧两种 wasm-bindgen 初始化签名
  const wasmBytes = fs.readFileSync(wasmPath);
  try {
    initSync({ module: wasmBytes });
  } catch (e) {
    initSync(wasmBytes);
  }

  // ---- 字体：按需注册（单卡图不需要署名字体，省掉 11MB 解析）----
  const lang = cfg.language || 'chs';
  const FONT_DIR = path.join(assets, 'fonts');
  const TITLE_FONT = {
    chs: 'arweibeigbpro_bd.otf',
    cht: 'DFT_W7-930.ttf',
    jpn: 'MOC-KaiminTsuki-B.otf',
    kor: 'NanumGothic-ExtraBold.ttf',
    eng: 'Memento-SemiBold.ttf',
  };
  const SIGNATURE_FONT = {
    chs: 'NotoSansSC-Regular.otf',
    cht: 'NotoSansTC-Regular.otf',
    jpn: 'NotoSansJP-Regular.otf',
    kor: 'NotoSansKR-Regular.otf',
    eng: 'NotoSansSC-Regular.otf',
  };
  const readFont = (name) => {
    const p = path.join(FONT_DIR, name);
    if (!fs.existsSync(p)) return null;
    return fs.readFileSync(p);
  };
  const reg = (key, buf) => {
    if (buf && !register_font(key, buf)) {
      throw new Error('register_font failed: ' + key);
    }
  };

  reg('number', readFont('FOT-TsukuAOldMin-Pr6-E.digits.otf'));
  const titleBuf = readFont(TITLE_FONT[lang] || TITLE_FONT.chs);
  if (!titleBuf) fail('title font missing for lang=' + lang);
  reg('title_' + lang, titleBuf);

  // 效果图要画署名行（画师 / 脚注），需要常规体
  if (style === 'diy') {
    const sigBuf = readFont(SIGNATURE_FONT[lang] || SIGNATURE_FONT.chs);
    reg('illus_' + lang, sigBuf);
    reg('footnote_' + lang, sigBuf);
  }
  const tFont = Date.now() - tLoad;

  // ---- 立绘 ----
  let art = new Uint8Array(0);
  if (req.art && fs.existsSync(req.art)) {
    art = new Uint8Array(fs.readFileSync(req.art));
  }

  // ---- 渲染 ----
  const tRender = Date.now();
  let png;
  if (style === 'diy') {
    // 效果图需要职业背景图：assets/backgrounds/<class>-<gen>.jpg
    const DIY_BG_CLASS = ['neutral', 'forestcraft', 'swordcraft', 'runecraft',
      'dragoncraft', 'abysscraft', 'havencraft', 'portalcraft'];
    const cls = DIY_BG_CLASS[cfg.class] || 'neutral';
    const gen = cfg.bg_type === 1 ? 1 : 2;
    const bgPath = path.join(assets, 'backgrounds', `${cls}-${gen}.jpg`);
    const bg = fs.existsSync(bgPath) ? new Uint8Array(fs.readFileSync(bgPath)) : new Uint8Array(0);
    png = render_diy_card(JSON.stringify(cfg), art, bg);
  } else {
    png = render_card(JSON.stringify(cfg), art);
  }
  const renderMs = Date.now() - tRender;

  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, Buffer.from(png));

  process.stdout.write(JSON.stringify({
    ok: true,
    out: outPath,
    bytes: png.length,
    fontMs: tFont,
    renderMs,
    totalMs: Date.now() - T0,
  }) + '\n');
}

main().catch((e) => fail(e && e.stack ? e.stack : String(e)));
