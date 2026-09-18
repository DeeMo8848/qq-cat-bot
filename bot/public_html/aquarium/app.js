/* 我的鱼缸 —— 演示观察版（182 鱼种，来自钓鱼插件的 fish_data.js）
 * 图层：背景 < Water3 < Water2 < 鱼食 < 鱼
 * 交互：左键点鱼看信息 / 点空白关闭 / 右键或长按喂食
 */
(function () {
  "use strict";

  /* ---------------- 常量 ---------------- */
  let MAX_CAP = 60;   // 渲染上限（API 返回容量后动态调整）
  const TANK = document.getElementById("tank");
  const FISH_LAYER = document.getElementById("fish");
  const FOOD_LAYER = document.getElementById("food");
  const PANEL = document.getElementById("panel");
  const capEl = document.getElementById("cap");
  const WATER_HI = document.getElementById("water3");   // 上层水纹（略小）
  const WATER_LO = document.getElementById("water2");   // 下层水纹（主）

  const RARITY_CN = { common: "普通", rare: "稀有", epic: "史诗", legend: "传说", myth: "神话" };
  const RARITY_CLS = { common: "rarity-common", rare: "rarity-rare", epic: "rarity-epic", legend: "rarity-legend", myth: "rarity-myth" };
  const RARITY_SPEED = { common: 1.10, rare: 1.00, epic: 0.92, legend: 0.78, myth: 0.62 };

  // 鱼图片尺寸总缩放：最小鱼与最大鱼同步放大（改这一个数即可整体调）
  // 只影响「鱼在缸里的显示大小」，与重量/鱼线无关。
  // 历史：1.0 → 1.5 → 2.5 → 1.6
  //   2.5 时神话级最重鱼可达 527px（屏高 59%），缸里上限 200 条时大鱼太挤、
  //   视觉压迫感强、也更容易挡住旁边的小鱼，故大幅降到 1.6
  //   （最大鱼 337px / 屏高 37%，最小金鱼仍有 56px 可点）。
  const FISH_SIZE_K = 1.6;

  /* 同种鱼内部的尺寸跨度（重量比例 → 大小的两条曲线，ratio ∈ [0,1]）。
     ⚠️ 乘积关系：同种跨度 = (BL+BH)(DL+DH) / (BL·DL)，不是简单相加。
        改这两个数即调力度；BL/DL 调小 → 小鱼更小，BH/DH 调大 → 大鱼更大。
     历史：曾把区间拉大到 [0.75,1.25]×[0.62,1.37]（同种跨度 3.68x），
     但大鱼过大导致「点不到旁边小鱼」、整体观感失衡，已回退到下面这组。
     现在同种跨度 2.26x（这个比值与 FISH_SIZE_K 无关，只由曲线决定）。 */
  const SIZE_BASE_LO = 0.90, SIZE_BASE_HI = 0.30;   // 基础系数 = LO + ratio * HI
  const SIZE_DEPTH_LO = 0.72, SIZE_DEPTH_HI = 0.50; // 深度系数 = LO + ratio * HI

  // 稀有度基准：金鱼小、鲨鱼大（跨鱼种体型差异）。
  // 配合 K=1.6 后：金鱼 56~127px，鲟鱼 91~207px，神话级最重 337px（屏高 37%）。
  const RARITY_SIZE = { common: 6.0, rare: 7.8, epic: 9.8, legend: 12.8, myth: 16.0 };

  /* 渲染尺寸硬上限（vmin 倍数）：纯保险 —— 当前曲线下理论最大
     16.0×1.20×1.22×1.6 ≈ 37vmin（屏高约 21%），碰不到这个上限。
     留着是为了以后有人再调大曲线/倍数时不至于把画面撑爆。 */
  const MAX_FISH_VMIN = 50.0;

  const SAND_NRM = 0.80;
  const BORDER = { top: 0.04, bottom: 0.93, left: 0.04, right: 0.96 };

  // 物理：平滑加减速（lerp 趋近巡航速度；减速段短、smoothstep 缓动避免"刹车感"）
  const ACCEL = 0.16;         // 每帧速度向目标速度趋近的比例（越小越柔）
  const DECEL_DIST = 3.0;     // vmin，接近终点该距离内才开始减速
  const ARRIVE_DIST = 0.7;    // vmin，距目标足够近即到达（此时速度已很低）
  const DASH_K = 0.020;       // 巡航速度 = min(burstCap, dist*DASH_K + base)
  const DASH_BASE = 0.30;
  const BURST_CAP = 0.85;     // 巡航速度上限(vmin/帧)
  const ARRIVE_CHARGE = 0.10; // 到达后蓄力衔接下一目标的时间（无长停顿）

  // 果冻伸缩（略微）：蓄力时左右略收缩、上下略拉伸；发射后弹簧回弹（欠阻尼轻微过冲，Q弹不抽搐）
  const SQUASH_X = 0.90;
  const SQUASH_Y = 1.10;
  const SPRING_K = 0.28;      // 弹簧刚度（每帧）
  const SPRING_D = 0.86;      // 弹簧阻尼（每帧衰减）

  // 觅食
  const PERCEIVE = 28.0;      // vmin，感知食物半径（加大 → 追食过程有足够距离展开加减速）
  const FEED_TIMEOUT = 2.0;   // 冲刺超过该秒数还没吃到就放弃追食 → 滑行减速恢复休闲（避免多鱼抢食"断线风筝"停不下来）
  const FOOD_SINK = 0.0015;   // 食物下落加速度(vmin/帧^2) —— 极缓慢
  const FOOD_MAXFALL = 0.08;  // 食物最大下落速度(vmin/帧)

  // 掉头判定：当前速度方向与目标方向点积低于该值 → 视为冲过头/掉头（约 60° 夹角）
  const TURN_DOT = 0.5;

  // 追食冲刺：线性加减速模型（像车的起步/刹车，速度均匀变化，有长时间过渡）：
  //   加速：每帧 +HUNT_ACC_RATE，慢慢提到峰值后匀速巡航；
  //   刹车：冲过头（到食物的距离由近转远）后每帧 -HUNT_DEC_RATE，
  //         沿原方向惯性滑行、越来越慢，刹停后才转向再重新加速；
  //   吃到食物则进入 glide 惯性滑行（短促减速后停下休息）
  const HUNT_VMAX = 1.2;      // 巡航峰值 = HUNT_VMAX * vmin * speedK（vmin/帧）
  const HUNT_ACC_RATE = 0.10; // 线性加速（px/帧²）：约 0.6s 平滑提速
  const HUNT_DEC_RATE = 0.16; // 线性刹车（px/帧²）：全速时约 1s 滑行减速停下、滑行约 0.3~0.4 屏（台球惯性感；0.35 时太短几乎看不出滑动）

  // 移动端小屏：vmin 小导致鱼/鱼食整体偏小，放大保证看得清、点得中
  const MOBILE_VMIN = 5.5;     // vmin 低于该值视为小屏（手机竖屏约 3.5~4.5、横屏约 3.6）
  const MOBILE_FISH_K = 1.7;   // 鱼尺寸放大系数
  const MOBILE_FOOD_K = 1.3;   // 鱼食放大系数

  /* ---------------- 状态 ---------------- */
  let W = 0, H = 0, vmin = 0;
  let waterPx = 0;    // 锁定的水纹高度（px，启动时按视口计算）
  let waterDPR = 1;   // 锁定水纹时的 devicePixelRatio
  let panelPos = null;   // 信息框打开时的视口比例位置（缩放/改窗口时按比例重算，位置不漂移）
  const fishes = [];
  const foods = [];
  const FOOD_IMAGES = [];
  const imgCache = {};

  function rnd(a, b) { return a + Math.random() * (b - a); }
  function rndInt(a, b) { return Math.floor(rnd(a, b + 1)); }
  function mobileOn() { return vmin < MOBILE_VMIN; }
  function foodSize() { return 4.2 * vmin * (mobileOn() ? MOBILE_FOOD_K : 1); }

  // 鱼的尺寸比例：优先按重量在种内区间的位置归一化到 [0,1]；
  // 没有重量数据（演示鱼/兜底）时用 slug 哈希得到稳定伪随机 → 每次打开页面尺寸一致
  function hashRatio(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 10000) / 10000;
  }
  /* 重量 → 尺寸比例。
     注意：抽鱼时重量会被鱼线倍率放大（棉线 1.0 → 秘银线 1.8），可能超过种内区间上限，
     实测约 55% 的鱼都超出上限。若直接截断，这些鱼会全部顶格成同一个尺寸
     —— 表现为「1000g 和 3000g 的鱼差不多大」。所以先除以鱼线倍率还原成"基准重量"
     再归一化，让重量真正拉开尺寸差距。mult 由后端按用户的鱼线等级给出。 */
  function weightRatio(w, wmin, wmax, slug, mult) {
    if (typeof w === "number" && w > 0 && wmax > wmin) {
      const base = w / (mult > 0 ? mult : 1);
      return Math.max(0, Math.min(1, (base - wmin) / (wmax - wmin)));
    }
    return hashRatio(slug || "fish");
  }

  // 每鱼速度系数：贴底爬行类极慢，普通鱼按稀有度（大鱼慢、小鱼快）
  function speedFactor(spec) {
    if (spec.bottom) return 0.20;
    return (RARITY_SPEED[spec.rarity] || 1.0) * rnd(0.9, 1.1);
  }

  function resize() {
    W = window.innerWidth;
    H = window.innerHeight;
    vmin = Math.min(W, H) / 100;
    for (const f of fishes) {
      f.size = Math.min(f.baseSize * f.depth, MAX_FISH_VMIN) * vmin * FISH_SIZE_K *
               (mobileOn() ? MOBILE_FISH_K : 1);
      apply(f);
    }
    for (const g of foods) g.size = foodSize();
    // 信息框按视口比例重新定位：页面缩放/改窗口时物理位置不变（固定 px 会随缩放漂移）
    if (panelPos) {
      let px = panelPos.fx * W, py = panelPos.fy * H;
      const pw = PANEL.offsetWidth || 200, ph = PANEL.offsetHeight || 140;
      const m = 0.75 * vmin, gap = 1.9 * vmin;
      if (px + pw > W - m) px = Math.max(m, px - pw - gap);
      if (py + ph > H - m) py = Math.max(m, py - ph - gap);
      PANEL.style.left = px + "px";
      PANEL.style.top = py + "px";
    }
  }

  /* 水纹高度：启动时按当前视口锁定为固定 px —— 拖动窗口边缘不随之变化
     （与背景图/鱼一致，不再因 vw/vh 单位随视口跳动）；
     仅当浏览器页面缩放（Ctrl +/-）使 devicePixelRatio 变化时才按新视口重算，
     物理尺寸保持原样（页面缩放不放大水纹）。 */
  function lockWaterHeight() {
    const w = window.innerWidth, h = window.innerHeight;
    waterDPR = window.devicePixelRatio || 1;
    // 移动端竖屏时水纹下方仍会漏出底图，整体再放大 1.12 倍覆盖更充分
    waterPx = Math.max(w * 0.26, h * 0.30) * 1.12;
    WATER_LO.style.height = waterPx + "px";
    WATER_HI.style.height = (waterPx * 0.94) + "px";
  }

  function preload(url) {
    if (imgCache[url]) return imgCache[url];
    const im = new Image();
    im.src = url;
    imgCache[url] = im;
    return im;
  }

  function fishSpec(spec) {
    const gold = !!spec.gold;
    const rel = "images/" + spec.slug + "/" + (gold ? "gold.png" : "Adult.png");
    return { spec, gold, src: rel, img: preload(rel) };
  }

  /* ---------------- 鱼体碰撞形状 ----------------
     每张素材都是 256×256 方画布，但真实鱼体只占中间一条（纵向填充中位仅 0.33），
     且各鱼种形状差异极大：管口鱼内容宽高比 5.00、海马 0.48。
     所以吃食判定不能按渲染尺寸取圆（那会让扁长的鱼"隔着老远空气吃食"），
     改用沿长轴的胶囊体：两端圆 + 中间圆柱。

     window.FISH_BODIES[slug(+_gold)] = [lx, rx, r]，均为渲染尺寸 f.size 的比例。
     数据由 tmp/build_fish_bodies.py 量取素材 alpha 包围盒生成，改素材后重跑脚本。 */
  const FISH_BODIES = (typeof window !== "undefined" && window.FISH_BODIES) || {};

  // 材质兜底：没有量到数据的鱼（素材被换/新增未跑脚本）用一个保守的小圆
  const BODY_DEFAULT = [0.30, 0.70, 0.12];
  const BODY_K = 1.0;   // 整体微调旋钮：>1 判定更宽松（更易吃到），<1 更严格

  /* 尾部空出：让"尾巴扫到食物"不再算吃掉。
     素材里鱼【头在左、尾在右】（已逐个看图确认：鲟鱼/金鱼/锦鲤/海马皆如此），
     所以"尾部"= 素材的右段（rx 侧），把"判定的最远可及点"
     （= 该端圆心 + 半径）按比例往回收。

     ⚠️ 坑一：判定半径会往外鼓出 r，圆胖鱼（r 大）如果只收缩圆心坐标，
        鼓出的部分会把收缩量整个抵消 —— 实测鲟鱼 r=0.492 时收缩 0.28 后，
        "最远可及点"从 1.492 只降到 1.212，尾巴根本没空出来。
     正确做法：先定"最远可及点"的目标位置，再减去 r 反推圆心。
        目标 = lx + (rx - lx) * K        （K 即尾部收窄系数）
        圆心 = 目标 - r
     若圆心退到左端之前（半径比身体还大，如鲸鲨 r=0.50），胶囊退化成单个圆，
     此时把圆心并到身体中点，保证仍有有效判定区，不会整个失效。
     头部不收缩 —— 嘴是主要进食部位，必须能吃到。
     只影响"吃食判定"；鱼的显示、游动、重量都不受影响。 */
  const BODY_TAIL_K = 0.72;

  /* 吃食判定的松紧：定义在 eatHits 旁边（见下方 "吃食判定的松紧" 注释），
     EAT_K = 1.0 / FOOD_BITE = 0.35。这里不再重复声明，避免两处各有一份。 */

  /* 鱼体两端在渲染框内的横向位置 + 胶囊半径（px）。全部随 f.size 线性缩放，
     所以鱼图放大后判定范围同步放大。

     ⚠️ 坑二（曾导致"点头不吃、点尾才中"）：【坐标基准】。
        lx/rx 是"占整张素材宽度的比例"（0 = 素材左边缘，1 = 素材右边缘），
        而 f.nx * W 是【鱼的中心】在屏幕上的 x。两者基准不同，
        必须先把比例平移到以中心为原点： (lx - 0.5) * S。
        早期写成 f.nx*W + lx*S（漏了 -0.5）→ 整条胶囊朝右偏了半个 size，
        恰好盖住尾巴那一侧，于是"点尾巴中、点头不中"。

     ⚠️ 坑三：【必须跟随 scaleX 翻转】。
        素材默认头在左；scaleX = -1 时图片水平镜像（头翻到右侧），
        判定也必须一起镜像。做法是把"以中心为原点"的比例再乘 scaleX：
            screenX = f.nx * W + (u - 0.5) * S * f.scaleX
        scaleX=1 保持原样，scaleX=-1 时左右端自动互换（头尾对应关系永远正确）。

     headOnly=true 时按上面的规则空出尾部（用于吃食判定）。
     注意：尾部收缩是在【素材坐标系】里做的（t 侧），做完再统一翻转，
     这样不论鱼朝左还是朝右，"空出的都是尾巴"这一语义都不变。 */
  function fishBodySize(f, headOnly) {
    const S = f.size || 0;
    const sx = f.scaleX < 0 ? -1 : 1;
    const rec = FISH_BODIES[f.spec.slug + (f.gold ? "_gold" : "")] || BODY_DEFAULT;
    const r = Math.max(1, rec[2] * S * BODY_K);
    const lx = rec[0], rx = rec[1];          // 素材坐标系（头在 lx 侧、尾在 rx 侧）
    let tail = rx;                            // 尾部端点的归一化坐标
    if (headOnly) {
      const target = lx + (rx - lx) * BODY_TAIL_K;
      tail = target - (r / S);                // 反推圆心（半径换算回归一化单位）
      if (tail < lx) tail = (lx + rx) / 2;    // 半径过大 → 退化成单圆
    }
    // 归一化 → 屏幕：先平移到中心原点，再按朝向翻转
    const toScreen = u => f.nx * W + (u - 0.5) * S * sx;
    const hx = toScreen(lx), tx = toScreen(tail);
    // 翻转后左端坐标可能大于右端，统一成 [小, 大] 交给 segDist（它本身与端点顺序无关，
    // 这里排序只是让调试输出/断言好读）
    return hx <= tx ? [hx, tx, r] : [tx, hx, r];
  }

  // 点到线段最短距离（胶囊体的核心：圆心到中轴线段）
  function segDist(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const L = dx * dx + dy * dy;
    let t = L > 1e-9 ? ((px - ax) * dx + (py - ay) * dy) / L : 0;
    t = t < 0 ? 0 : (t > 1 ? 1 : t);
    return Math.hypot(px - (ax + dx * t), py - (ay + dy * t));
  }

  /* 吃食判定的松紧。语义拆分得更细，避免"一个系数背锅"：

     EAT_K     —— 作用在【鱼体半径】上的整体缩放。1.0 = 严格贴合鱼图边缘。
     FOOD_BITE —— 食物自身半径参与判定的比例（0~1）。
                  含义："食物圆心进到鱼体边缘外多少，才算鱼咬到了它"。
                  取 1.0 → 食物边缘碰到鱼身即算（最宽松，食物圆心可在鱼身外 foodR）；
                  取 0.0 → 食物圆心必须落进鱼身（最严格）。
                  0.35 表示"食物至少要压进鱼身一半多"，不会出现明显空气。

     ⚠️ 历史教训：早前判定写成 `b[2] * 1.15 + foodR`，两个量同时放大 ——
        foodR 是**与鱼大小无关的常量**（vmin=9 时约 19px），
        对小鱼（半径仅 6.6px）相当于把判定放大到 3 倍多，
        观感就是"隔着空气就吃到"。所以 foodR 必须按比例打折参与。
     只放宽半径、绝不放宽长轴端点 —— 端点一往外扩立刻变成远距离空气吃食。 */
  const EAT_K = 1.0;
  const FOOD_BITE = 0.35;

  // 食物圆心是否落进鱼体胶囊（+食物自身半径）内
  function bodyHits(f, gx, gy, foodR) {
    const b = fishBodySize(f);
    return segDist(gx, gy, b[0], f.ny * H, b[1], f.ny * H) < b[2] + foodR * FOOD_BITE;
  }

  // 吃食判定专用：空出尾巴（尾巴扫到不算吃到）
  function eatHits(f, gx, gy, foodR) {
    const b = fishBodySize(f, true);
    return segDist(gx, gy, b[0], f.ny * H, b[1], f.ny * H) < b[2] * EAT_K + foodR * FOOD_BITE;
  }

  /* ---------------- 鱼对象 ---------------- */
  function addFish(spec) {
    if (fishes.length >= MAX_CAP) return;
    const el = document.createElement("div");
    el.className = "fish-el";
    const im = document.createElement("img");
    im.src = spec.src; im.alt = "";
    el.appendChild(im);
    FISH_LAYER.appendChild(el);
    el.style.zIndex = 1000 + fishes.length;   // 固定递增，绝不交换 → 无频闪

    const bottom = !!spec.spec.bottom;
    // 先算尺寸再定初始位置 —— 大鱼要按半尺寸收缩可用区间（见 clampFish）
    const ratio = weightRatio(spec.spec.weight, spec.spec.wmin, spec.spec.wmax,
                             spec.spec.slug, spec.spec.mult);
    const size0 = Math.min(RARITY_SIZE[spec.spec.rarity || "common"] *
                           (SIZE_BASE_LO + ratio * SIZE_BASE_HI) * (SIZE_DEPTH_LO + ratio * SIZE_DEPTH_HI),
                           MAX_FISH_VMIN) * vmin * FISH_SIZE_K * (mobileOn() ? MOBILE_FISH_K : 1);
    const half0 = size0 / 2;
    const nyLo = bottom ? Math.max(SAND_NRM + 0.015, BORDER.top + half0 / H) : BORDER.top + half0 / H;
    const nyHi = BORDER.bottom - half0 / H;
    const yTop = nyLo < nyHi ? nyLo : BORDER.top;
    const yBot = nyLo < nyHi ? nyHi : BORDER.bottom;
    const ny = bottom ? rnd(Math.max(SAND_NRM + 0.015, yTop), Math.max(SAND_NRM + 0.035, yBot))
                      : rnd(yTop, yBot);
    const xLo = BORDER.left + half0 / W, xHi = BORDER.right - half0 / W;
    const f = {
      spec: spec.spec, el, im, bottom,
      gold: spec.gold,
      nx: rnd(xLo < xHi ? xLo : BORDER.left, xLo < xHi ? xHi : BORDER.right),
      ny,
      vx: 0, vy: 0,
      state: "rest", restT: rnd(0.15, 0.35), charge: 0,
      tx: 0, ty: 0, burst: 0,
      // 果冻伸缩弹簧（当前值/速度）
      kx: 1, ky: 1, kxv: 0, kyv: 0,
      baseSize: RARITY_SIZE[spec.spec.rarity || "common"] * (SIZE_BASE_LO + ratio * SIZE_BASE_HI),
      depth: SIZE_DEPTH_LO + ratio * SIZE_DEPTH_HI,
      size: 0,
      scaleX: 1,             // 左右翻转（朝向）
      speedK: speedFactor(spec.spec),
      feedT: 0,
      turnT: 0,                  // 转身冷却（秒）：刹停/方向不对后先停住缓冲再重新加速，防原地反复转头
      foodTarget: null,        // 锁定的食物目标（多个鱼食时不乱换目标）
    };
    f.scaleX = Math.random() < 0.5 ? -1 : 1;
    f.size = size0;
    fishes.push(f);
    apply(f);
    return f;
  }

  /* 定位在 el（translate），果冻伸缩+翻转在 img（每帧弹簧平滑过渡，无抽搐） */
  function apply(f) {
    const px = f.nx * W, py = f.ny * H;
    let s = f.size;
    if (!s || s <= 0) {   // 兜底：异步添加（API 替换）的鱼还没经过 resize()
      s = f.size = Math.min(f.baseSize * f.depth, MAX_FISH_VMIN) * vmin * FISH_SIZE_K *
                   (mobileOn() ? MOBILE_FISH_K : 1);
    }
    f.el.style.transform = "translate(" + px + "px," + py + "px)translate(-50%,-50%)";
    f.el.style.width = s + "px";
    f.el.style.height = s + "px";
    // 目标缩放：蓄力时左右略收缩/上下略拉伸，其余状态为正常
    let tX = 1, tY = 1;
    if (f.state === "charge") { tX = SQUASH_X; tY = SQUASH_Y; }
    // 欠阻尼弹簧：蓄力缓慢压扁、发射后回弹轻微过冲再复位（Q弹，无突变）
    f.kxv += (tX - f.kx) * SPRING_K;
    f.kyv += (tY - f.ky) * SPRING_K;
    f.kxv *= SPRING_D;
    f.kyv *= SPRING_D;
    f.kx += f.kxv;
    f.ky += f.kyv;
    f.im.style.transform = "scale(" + (f.kx * f.scaleX) + "," + f.ky + ")";
  }

  function pickTarget(f) {
    /* 竖直可用区间按鱼体半尺寸收缩：
       鱼是「中心定位 + 上下各占 size/2」渲染的，若 ny 只夹到固定边界，
       大鱼会有一截探出缸底（旧曲线下鲟鱼就超出约 98px，新曲线放大到 126px）。
       所以边界必须随鱼大小走 —— 大鱼自动往中间收，小鱼才能贴到近底。
       区间塌陷保护：鱼比缸还高时退回固定边界，防止 max/min 反转让鱼卡死。 */
    const half = (f.size || 0) / 2;
    let topLim = BORDER.top + half / H;
    let botLim = BORDER.bottom - half / H;
    if (topLim > botLim) { topLim = BORDER.top; botLim = BORDER.bottom; }
    if (f.bottom) {
      // 贴底生物（蜗牛等）：目标限制在当前位置附近，一次只爬一小段
      f.tx = Math.max(BORDER.left + 0.04, Math.min(BORDER.right - 0.04,
        f.nx + rnd(-0.16, 0.16)));
      const sTop = Math.max(SAND_NRM + 0.015, topLim);
      const sBot = Math.max(sTop, Math.min(botLim, SAND_NRM + 0.10));
      f.ty = Math.max(sTop, Math.min(sBot, f.ny + rnd(-0.05, 0.05)));
    } else {
      // 普通鱼：目标限制在当前位置附近，每次最多移动 MAX_STEP，避免突然横穿整缸
      // 横向同样按半宽收缩（鱼是方的，左右各占 size/2）
      const halfW = (f.size || 0) / 2 / W;
      let leftLim = BORDER.left + halfW;
      let rightLim = BORDER.right - halfW;
      if (leftLim > rightLim) { leftLim = BORDER.left; rightLim = BORDER.right; }
      const MAX_STEP = 0.15;
      f.tx = Math.max(leftLim, Math.min(rightLim, f.nx + rnd(-MAX_STEP, MAX_STEP)));
      f.ty = Math.max(topLim, Math.min(botLim, f.ny + rnd(-MAX_STEP, MAX_STEP)));
    }
  }

  function launch(f) {
    pickTarget(f);
    const dx = (f.tx - f.nx) * W, dy = (f.ty - f.ny) * H;
    const dist = Math.max(Math.hypot(dx, dy) / vmin, 0.0001);
    f.cruise = Math.min(BURST_CAP, dist * DASH_K + DASH_BASE) * f.speedK;
    if (Math.abs(dx) > 1) f.scaleX = dx < 0 ? 1 : -1;
    f.state = "dash";                // 弹簧自动回弹（轻微过冲），无需额外变量
    f.vx = 0; f.vy = 0;              // 从静止平滑加速，避免突兀弹射
  }

  /* ---------------- 食物 ---------------- */
  function dropFood(x, y) {
    const el = document.createElement("img");
    el.className = "food-el";
    el.src = FOOD_IMAGES[rndInt(0, FOOD_IMAGES.length - 1)];
    el.alt = "";
    const sz = foodSize();
    el.style.width = el.style.height = sz + "px";
    FOOD_LAYER.appendChild(el);
    foods.push({ el, x, y, vy: 0, size: sz });
    playSfx(SND_DROP, 2, 120, DROP_VOLUME);   // 放置鱼食音效（独立音量档）
  }

  function eatFood(f, g) {
    g.el.remove();
    const i = foods.indexOf(g);
    if (i >= 0) foods.splice(i, 1);
    f.foodTarget = null;
    f.feedT = 0;
    // 吃到后沿当前方向惯性滑行减速（不骤停；短促滑行停稳后休息）
    f.state = "glide";
    f.hspd = Math.max(Math.hypot(f.vx, f.vy), 0.3);
    playSfx(SND_EAT, 3, 90);   // 吃掉鱼食音效（限并发 + 最小间隔，多条鱼抢食时不糊）
  }

  /* ---------------- 逻辑步进 ---------------- */
  function step() {
    // 食物下落（极其缓慢；沉出缸底即消失）
    for (let i = foods.length - 1; i >= 0; i--) {
      const g = foods[i];
      g.vy = Math.min(g.vy + FOOD_SINK, FOOD_MAXFALL);
      g.y += g.vy;
      if (g.y - g.size / 2 > H) {
        g.el.remove();
        foods.splice(i, 1);
        continue;
      }
      g.el.style.left = g.x + "px";
      g.el.style.top = g.y + "px";
    }

    // 鱼
    for (let i = 0; i < fishes.length; i++) {
      const f = fishes[i];

      // 觅食：先追已锁定的目标（不因新食物出现而乱换），目标没了才重新选最近
      let targetFood = f.foodTarget;
      if (targetFood && !foods.includes(targetFood)) {
        // 目标被别的鱼吃掉：立即放弃，滑行减速恢复休闲（否则会以冲刺速度一直飞——"断线风筝"）
        targetFood = null; f.foodTarget = null; f.feedT = 0;
        if (f.state === "hunt") { f.state = "glide"; f.hspd = Math.max(Math.hypot(f.vx, f.vy), 0.3); }
      }
      if (!targetFood && f.state !== "glide") {
        // 多个鱼食时：优先吃最近的目标；距离相近（≤最近者的 1.35 倍）时优先选
        // "朝向/速度方向"的鱼食，避免为身后一点点距离的鱼食原地掉头，看起来像愣住
        let best = PERCEIVE * vmin, bestDot = -2;
        const sp = Math.hypot(f.vx, f.vy);
        for (const g of foods) {
          if (f.bottom && g.y < SAND_NRM * H) continue;
          const d = Math.hypot(g.x - f.nx * W, g.y - f.ny * H);
          if (d < best * 1.35 && d < PERCEIVE * vmin) {
            let dot = 1;
            if (sp > 1e-6 && d > 1e-6) {
              dot = ((g.x - f.nx * W) * f.vx + (g.y - f.ny * H) * f.vy) / (sp * d);
            }
            if (dot > bestDot) { bestDot = dot; best = d; targetFood = g; }
          }
        }
        f.foodTarget = targetFood;
      }

      if (targetFood) {
        // 追食物计时：2s 还没吃到就沿原方向惯性滑行减速（台球感），滑停后重选目标续追
        f.feedT += 1 / 60;
        if (f.feedT > FEED_TIMEOUT) {
          // 2s 没吃到：转 glide 沿原方向滑行减速（速度均匀下降，像球打出去后的台面滑动），
          // 滑停后（rest 一帧）立即重选最近/最顺路的鱼食续追，不进入休闲发呆；
          // 目标真没了时同样滑行停下，不会以冲刺速度一直飞（防断线风筝）
          f.feedT = 0;
          f.foodTarget = null;
          f.state = "glide";
          f.hspd = Math.max(Math.hypot(f.vx, f.vy), 0.3);
        } else {
          const gx = targetFood.x, gy = targetFood.y;
          const fx = f.nx * W, fy = f.ny * H;
          /* 吃食判定：沿鱼体长轴的胶囊体（两端圆 + 中间圆柱）+ 食物半径的一部分。
             形状随该鱼的真实鱼体包围盒（fish_bodies.js）走，并随渲染尺寸 f.size 线性缩放
             —— 鱼图放大 → 判定范围同步放大。
             只用胶囊体是不够细的：尾摆幅度大，尾巴扫过食物时会被误判成"吃到"，
             所以 eatHits 会把右端按 BODY_TAIL_K 往回收，空出尾部不参与判定
             （嘴是主要进食部位，头部不收缩）。
             松紧由 EAT_K / FOOD_BITE 控制（见其定义处注释）。 */
          const foodR = targetFood.size * 0.5;
          let hit = eatHits(f, gx, gy, foodR);
          if (!hit && f._px != null) {
            // 高速冲过：本帧移动路径扫过食物也算命中（两端各划一条线段）。
            // 注意直接把上一帧的胶囊【整体平移】过来 —— 只改中心坐标，
            // 端点由 fishBodySize 按旧的 nx/ny 重新算，避免手动拼两个坐标系。
            const ddx = fx - f._px, ddy = fy - f._py;
            if (ddx * ddx + ddy * ddy > 1e-9) {
              const q = { spec: f.spec, gold: f.gold, size: f.size, scaleX: f.scaleX,
                          nx: f._px / W, ny: f._py / H };
              const pq = fishBodySize(q, true);       // 上一帧位置的胶囊
              const dxq = fx - f._px;                 // 整体平移量
              // 半径同样按 EAT_K 缩放、foodR 同样打折 —— 与 eatHits 保持同一口径，
              // 否则高速路径会成为"偷偷放宽"的后门。
              const rr = Math.max(fishBodySize(f, true)[2], pq[2]) * EAT_K + foodR * FOOD_BITE;
              hit = segDist(gx, gy, pq[0] + dxq, fy, pq[1] + dxq, fy) < rr;
            }
          }
          if (hit) {
            eatFood(f, targetFood);
          } else {
            // 追食：线性加减速模型（像车起步/刹车，速度均匀变化）。
            // 方向每帧指向"食物后方一点"的冲过点；未掉头时速度线性加速至峰值后匀速巡航；
            // 冲过头（方向反转）进入刹车：速度沿原方向线性下降、惯性滑行越来越慢，
            // 刹停后才转向（scaleX 翻转）再缓慢加速。途中嘴的移动路径命中即吃。
            const dirX = gx - fx, dirY = gy - fy;
            const dirLen = Math.max(Math.hypot(dirX, dirY), 1e-6);
            const foodDist = Math.max(dirLen / vmin, 0.0001);
            const over = Math.max(f.size * 0.5, vmin) + targetFood.size * 0.4;
            // 冲过点限制在缸内，避免鱼追出边界撞墙反弹
            const tx = Math.max(BORDER.left * W, Math.min(BORDER.right * W,
              gx + (dirX / dirLen) * over));
            const ty = Math.max(BORDER.top * H, Math.min(BORDER.bottom * H,
              gy + (dirY / dirLen) * over));
            const dx = tx - fx, dy = ty - fy;
            const dist = Math.max(Math.hypot(dx, dy) / vmin, 0.0001);
            const inv = 1 / (dist * vmin);
            const ddx = dx * inv, ddy = dy * inv;
            if (f.state !== "hunt") {
              // 进入追食：从当前速度继续加速（不瞬变）；给最小起步速度避免静止时龟速
              f.hspd = Math.max(Math.hypot(f.vx, f.vy), 2.0);
              f.hCruise = HUNT_VMAX * vmin * f.speedK;
              f.braking = false;
              f.huntD0 = foodDist;   // 记住刚追时离食物的距离，用于判断"是否冲过头"
              f.lastFoodDist = foodDist;
              f.feedT = 0;           // 重新计 2s 放弃计时
              f.turnT = 0;           // 刚起步不用转身缓冲
              f.state = "hunt";
            }
            const curSp = Math.hypot(f.vx, f.vy);
            const dot = curSp > 1e-6 ? (f.vx * ddx + f.vy * ddy) / curSp : 1;
            if (!f.braking) {
              if (dot < TURN_DOT) {
                // 还没接近就方向不对（食物在身后）→ 转身：清空速度，留 0.3~0.5s 冷却再重新加速（防原地反复转头）
                f.vx = 0; f.vy = 0; f.hspd = 0;
                f.scaleX = ddx > 0 ? -1 : 1;
                f.turnT = rnd(0.3, 0.5);
              } else if (foodDist > f.lastFoodDist && f.lastFoodDist < f.huntD0 * 0.85) {
                // 已冲过食物（到食物的距离由近转远）→ 进入惯性刹车滑行
                f.braking = true;
              }
            }
            f.lastFoodDist = foodDist;
            if (f.braking) {
              // 线性刹车：速度沿原方向均匀下降（惯性滑行，越来越慢）
              const old = f.hspd;
              f.hspd = Math.max(0, f.hspd - HUNT_DEC_RATE);
              const k = old > 0 ? f.hspd / old : 0;
              f.vx *= k; f.vy *= k;
              if (f.hspd <= 0.05) {
                f.hspd = 0; f.braking = false;
                f.scaleX = ddx > 0 ? -1 : 1;   // 刹停后才转向
                f.turnT = rnd(0.3, 0.5);       // 转身冷却：先停住缓冲再掉头冲，防原地反复转头
              }
            } else if (f.turnT > 0) {
              // 转身冷却中：原地停住计时（清空速度），到点后再重新线性加速
              f.turnT -= 1 / 60;
              f.hspd = 0; f.vx = 0; f.vy = 0;
            } else {
              // 线性加速 → 峰值后匀速巡航
              f.hspd = Math.min(f.hspd + HUNT_ACC_RATE, f.hCruise);
              f.vx = ddx * f.hspd;
              f.vy = ddy * f.hspd * 0.9;
              if (Math.abs(dx) > 1) f.scaleX = dx < 0 ? 1 : -1;
            }
          }
        }
      } else {
        f.feedT = 0;
        // 目标没了却还处于冲刺速度（断线风筝兜底）→ 转滑行减速恢复休闲
        if (f.state === "hunt") {
          f.state = "glide";
          f.hspd = Math.max(Math.hypot(f.vx, f.vy), 0.3);
        }
        // 吃食后的惯性滑行：沿原方向线性减速越来越慢，停稳后休息
        if (f.state === "glide") {
          const old = f.hspd;
          f.hspd = Math.max(0, f.hspd - HUNT_DEC_RATE);
          const k = old > 0 ? f.hspd / old : 0;
          f.vx *= k; f.vy *= k;
          if (f.hspd <= 0.05) {
            f.state = "rest"; f.restT = rnd(0.15, 0.3);
            f.vx = 0; f.vy = 0;
          }
        } else if (f.state === "rest") {
          f.restT -= 1 / 60;
          if (f.restT <= 0) { f.state = "charge"; f.charge = 0.25; }
        } else if (f.state === "charge") {
          f.charge -= 1 / 60;
          if (f.charge <= 0) launch(f);
        } else if (f.state === "dash") {
          const dx = (f.tx - f.nx) * W, dy = (f.ty - f.ny) * H;
          const dist = Math.max(Math.hypot(dx, dy) / vmin, 0.0001);
          // 方向实时朝目标（窗口缩放后也能重新对准）；仅接近终点 3vmin 才开始减速，
          // smoothstep 缓动让速度从巡航值平缓过渡到 0，无"刹车感"
          const t = Math.max(0, Math.min(1, dist / DECEL_DIST));
          const ease = t * t * (3 - 2 * t);
          const target = f.cruise * ease;
          const inv = 1 / (dist * vmin);
          f.vx += (dx * inv * target - f.vx) * ACCEL;
          f.vy += (dy * inv * target - f.vy) * ACCEL;
          if (dist < ARRIVE_DIST) {
            // 到达后短暂蓄力即衔接下一目标，无长停顿
            f.state = "charge"; f.charge = ARRIVE_CHARGE;
            f.vx = 0; f.vy = 0;
          }
        }
      }

      // 积分（dash 漫游、hunt 追食、glide 滑行都移动；其余状态静止）
      if (f.state === "dash" || f.state === "hunt" || f.state === "glide") {
        f._px = f.nx * W; f._py = f.ny * H;
        f.nx += f.vx / W;
        f.ny += f.vy / H;
      }
      // 边界（按鱼体半尺寸收缩，大鱼不会探出缸外；塌陷时退回固定边界）
      const halfH = (f.size || 0) / 2 / H;
      const halfW = (f.size || 0) / 2 / W;
      let tLim = BORDER.top + halfH, bLim = BORDER.bottom - halfH;
      if (tLim > bLim) { tLim = BORDER.top; bLim = BORDER.bottom; }
      let lLim = BORDER.left + halfW, rLim = BORDER.right - halfW;
      if (lLim > rLim) { lLim = BORDER.left; rLim = BORDER.right; }
      if (f.bottom) {
        f.ny = Math.max(Math.max(SAND_NRM + 0.015, tLim), Math.min(bLim, f.ny));
        f.nx = Math.max(lLim, Math.min(rLim, f.nx));
      } else {
        if (f.nx < lLim) { f.nx = lLim; f.vx = Math.abs(f.vx); }
        if (f.nx > rLim) { f.nx = rLim; f.vx = -Math.abs(f.vx); }
        if (f.ny < tLim) { f.ny = tLim; f.vy = Math.abs(f.vy); }
        if (f.ny > bLim) { f.ny = bLim; f.vy = -Math.abs(f.vy); }
      }
      if (Math.abs(f.vx) > 0.008) f.scaleX = f.vx < 0 ? 1 : -1;
      apply(f);
    }
  }

  function loop() { requestAnimationFrame(loop); step(); }

  /* 测试用探针（无头回归测试需要观察闭包内的状态；生产环境不读它，
     但保留不会影响运行：只是把引用挂到 window 上，无副作用、无内存增长）。
     暴露只读快照函数，避免测试脚本直接改内部数组。 */
  if (typeof window !== "undefined") {
    window.__AQUA_DEBUG = {
      fishes: () => fishes,
      foods: () => foods,
      box: () => ({ W, H, vmin }),
      bodyOf: f => fishBodySize(f),
      hit: (f, x, y, fr) => bodyHits(f, x, y, fr),
      eat: (f, x, y, fr) => eatHits(f, x, y, fr),
      at: (x, y) => fishAt(x, y),
    };
  }

  /* ---------------- 信息面板 ---------------- */
  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function clearSelect() {
    for (const f of fishes) f.el.classList.remove("selected");
  }

  function openPanel(f, x, y) {
    const s = f.spec;
    const cls = RARITY_CLS[s.rarity] || "";
    const rarityN = RARITY_CN[s.rarity] || s.rarity;
    const price = f.gold ? (s.price || 0) * 5 : (s.price || 0);
    clearSelect();
    f.el.classList.add("selected");
    PANEL.classList.remove("hidden");
    PANEL.innerHTML =
      "<h3>" + (f.gold && s.name.indexOf("✨黄金") !== 0 ? "✨黄金 " : "") + escapeHtml(s.name) + "</h3>" +
      "<div class=\"p-rarity " + cls + "\">" + rarityN + "</div>" +
      (f.bottom ? "<div class=\"p-badge\">🏖️ 沙地住户</div>" : "") +
      (f.spec.weight ? "<div class=\"p-weight\">⚖️ 重量 " + f.spec.weight + " g</div>" : "") +
      "<img src=\"" + f.spec.src + "\" alt=\"\">" +
      "<div class=\"p-price\">🐚 价值 " + price + " 喵喵币</div>";
    let px = x, py = y;
    const pw = PANEL.offsetWidth || 200, ph = PANEL.offsetHeight || 140;
    const m = 0.75 * vmin, gap = 1.9 * vmin;
    if (px + pw > W - m) px = Math.max(m, px - pw - gap);
    if (py + ph > H - m) py = Math.max(m, py - ph - gap);
    PANEL.style.left = px + "px";
    PANEL.style.top = py + "px";
    panelPos = { fx: px / W, fy: py / H };
  }
  function closePanel() { PANEL.classList.add("hidden"); panelPos = null; clearSelect(); }

  /* 命中哪条鱼（点击/长按选鱼）。
     原来用正方形包围盒 |Δx| < size/2 && |Δy| < size/2 —— 对横向的鱼来说方框四角全是空的，
     一条大鱼会凭空盖住周围一大片，把旁边的 Clicked 全吃掉（"点不到旁边小鱼"）。
     改成随真实鱼体形状走的胶囊体后，四角立刻释放；鱼越大释放得越多。
     注意这里用完整鱼体（含尾巴）—— 点尾巴也算选中这条鱼，与吃食判定不同。 */
  function fishAt(x, y) {
    for (let i = fishes.length - 1; i >= 0; i--) {
      const f = fishes[i];
      const b = fishBodySize(f);
      /* 廉价包围盒剔除（性能）。
         ⚠️ 不能用 f.size / 2 —— lx/rx 是素材比例，胶囊两端的实际屏幕位置是
            (u - 0.5) * size * scaleX，只有 lx=0 且 rx=1 时才恰好等于 ±size/2。
            像鲟鱼 [0,1,0.492] 这种端点顶到素材边缘的鱼，胶囊再加上判定半径后
            会超出 ±size/2，用 size/2 做粗筛会把合法的点击【提前剔除掉】
            （表现为"鱼靠缸边时点头不中"，且随位置随机复现）。
            正确做法：直接用胶囊自身的两端算包围盒，再并上半径。 */
      const fx = f.nx * W, fy = f.ny * H;
      const boxL = Math.min(b[0], b[1]) - b[2] - fx;   // 相对中心的包围盒半宽
      const boxR = Math.max(b[0], b[1]) + b[2] - fx;
      const halfX = Math.max(Math.abs(boxL), Math.abs(boxR), 1);
      if (Math.abs(x - fx) > halfX || Math.abs(y - fy) > b[2] + 1) continue;
      if (segDist(x, y, b[0], fy, b[1], fy) < b[2]) return f;
    }
    return null;
  }

  /* ---------------- 交互 ---------------- */
  let down = null;
  let lastTap = null;   // 触摸单击记录（双击判定 + 延迟开面板）
  let tapSeq = 0;       // 单击序号：过期 timeout 不误开面板
  const LONG_PRESS = 420;
  const MOVE_ALLOW = 12;
  const DBL_TAP_MS = 300;   // 两次点击间隔上限
  const DBL_TAP_PX = 45;    // 两次点击位移上限

  TANK.addEventListener("contextmenu", function (e) {
    e.preventDefault();
    dropFood(e.clientX, e.clientY);
    down = null; closePanel();
  }, false);

  TANK.addEventListener("pointerdown", function (e) {
    if (e.button !== 0) return;
    closePanel();
    const hit = fishAt(e.clientX, e.clientY);
    down = { x: e.clientX, y: e.clientY, t: performance.now(), hit, fed: false, moved: false };
    const d = down;
    d.timer = setTimeout(function () {
      if (down === d && !d.moved && !d.fed) { d.fed = true; dropFood(d.x, d.y); }
    }, LONG_PRESS);
  }, { passive: false });

  TANK.addEventListener("pointermove", function (e) {
    if (down && !down.moved) {
      const dx = e.clientX - down.x, dy = e.clientY - down.y;
      if (Math.hypot(dx, dy) > MOVE_ALLOW) { down.moved = true; TANK.classList.add("dragging"); }
    }
  });

  TANK.addEventListener("pointerup", function (e) {
    TANK.classList.remove("dragging");
    const d = down;
    down = null;
    if (!d || e.button !== 0) return;
    clearTimeout(d.timer);
    if (d.fed) return;
    if (d.moved) { closePanel(); return; }
    if (performance.now() - d.t >= 300) { closePanel(); return; }
    const touch = e.pointerType !== "mouse";
    // 双击喂食（触摸/触控笔）：300ms 内、45px 内的第二次快速点击 → 在点击处撒鱼食
    if (touch && lastTap && performance.now() - lastTap.t < DBL_TAP_MS &&
        Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < DBL_TAP_PX) {
      lastTap = null;
      closePanel();
      dropFood(e.clientX, e.clientY);
      return;
    }
    if (touch) {
      // 触摸单击：先关面板，等 320ms 确认不是双击再开信息框（避免双击时面板闪现）
      const seq = ++tapSeq;
      lastTap = { x: e.clientX, y: e.clientY, t: performance.now(), hit: d.hit, seq };
      closePanel();
      setTimeout(function () {
        if (lastTap && lastTap.seq === seq) {
          const tap = lastTap;
          lastTap = null;
          if (tap.hit) openPanel(tap.hit, tap.x, tap.y);
        }
      }, 320);
      return;
    }
    if (d.hit) openPanel(d.hit, e.clientX, e.clientY);
    else closePanel();
  });

  TANK.addEventListener("pointercancel", function () { down = null; TANK.classList.remove("dragging"); });

  /* ---------------- 声音（BGM 随机播放 + 喂食音效） ----------------
     浏览器不允许页面在用户操作前出声 → BGM 在「首次点击 / 触摸 / 按键」后才起播；
     右上角喇叭按钮可随时静音（状态记在 localStorage，下次打开沿用）。 */
  // 音频统一用 .m4a（AAC/MP4 容器）：体积比裸 ADTS .aac 小一半、比未压缩 WAV 小十倍，
  // 且裸 ADTS 在 Firefox 上支持差 → 转封装后所有主流浏览器都能播。
  const SND_BGM = ["audio/song_1.m4a", "audio/song_2.m4a", "audio/song_3.m4a"];
  const SND_EAT = "audio/eat_sfx.m4a";
  const SND_DROP = "audio/food_cluck.m4a";
  const SND_KEY = "aquarium-sound";
  // 音量（0~1）。各音效独立一档，避免调一个牵连另一个：
  //   BGM 0.35 → 0.525（×1.5）
  //   放置鱼食 0.75 → 1.0（×2.5）→ 0.5（×0.5，用户实测觉得偏吵）
  //     ⚠️ 历史：曾为了加响把前端音量顶到 1.0（HTMLMediaElement 物理上限），
  //        并在 food_cluck.m4a 源文件上做了 +6dB（2 倍）增益（见 tmp/boost_drop_sfx.py）。
  //        现在回落到 0.5，**源文件增益保留不动** —— 若要继续调整，
  //        改这里即可（0.5 × 源增益仍比最初版本响一倍）。
  const BGM_VOLUME = 0.525;
  const DROP_VOLUME = 0.5;    // 放置鱼食（food_cluck）：用户要求 ×0.5
  const SFX_VOLUME = 0.75;    // 吃掉鱼食（eat_sfx）

  let sndOn = true;
  try { sndOn = localStorage.getItem(SND_KEY) !== "off"; } catch (e) { sndOn = true; }
  let bgmEl = null, bgmIdx = -1, bgmStarted = false, bgmFails = 0;

  /* 音效：限制同时发声条数 + 最小间隔，避免多鱼抢食时糊成一片 */
  const sfxLive = [];
  let sfxLastAt = -1e9;
  function playSfx(src, maxLive, minGap, vol) {
    if (!sndOn) return;
    const now = performance.now();
    if (minGap && now - sfxLastAt < minGap) return;
    sfxLastAt = now;
    for (let i = sfxLive.length - 1; i >= 0; i--) if (sfxLive[i].ended) sfxLive.splice(i, 1);
    if (sfxLive.length >= maxLive) return;
    const a = new Audio(src);
    a.volume = Math.max(0, Math.min(1, vol === undefined ? SFX_VOLUME : vol));
    sfxLive.push(a);
    const p = a.play();
    if (p && p.catch) p.catch(function () {});   // 自动播放被拦截 / 文件缺失 → 静默忽略
  }

  function nextBgm() {
    if (!sndOn) return;
    if (!bgmEl) {
      bgmEl = new Audio();
      bgmEl.addEventListener("ended", nextBgm);          // 一首放完 → 随机下一首
      bgmEl.addEventListener("error", function () {      // 格式不支持/文件缺失 → 换一首，别卡死
        bgmFails += 1;
        if (bgmFails <= SND_BGM.length) nextBgm();
      });
    }
    let i = Math.floor(Math.random() * SND_BGM.length);
    if (SND_BGM.length > 1 && i === bgmIdx) i = (i + 1) % SND_BGM.length;   // 不连播同一首
    bgmIdx = i;
    bgmFails = 0;
    bgmEl.src = SND_BGM[i];
    bgmEl.volume = BGM_VOLUME;
    const p = bgmEl.play();
    if (p && p.catch) p.catch(function () {});
  }

  const sndBtn = document.getElementById("snd");
  function renderSnd() {
    if (!sndBtn) return;
    sndBtn.textContent = sndOn ? "🔊" : "🔇";
    sndBtn.classList.toggle("off", !sndOn);
    sndBtn.title = sndOn ? "点击静音" : "点击开启声音";
  }

  if (sndBtn) {
    // 按钮上的操作不要冒泡到鱼缸（否则点静音会顺手喂食/选鱼）
    ["pointerdown", "pointerup", "click", "dblclick", "contextmenu"].forEach(function (t) {
      sndBtn.addEventListener(t, function (e) { e.stopPropagation(); }, false);
    });
    sndBtn.addEventListener("click", function () {
      sndOn = !sndOn;
      try { localStorage.setItem(SND_KEY, sndOn ? "on" : "off"); } catch (e) {}
      if (sndOn) {
        if (bgmEl && bgmEl.src) { const p = bgmEl.play(); if (p && p.catch) p.catch(function () {}); }
        else nextBgm();
        bgmStarted = true;
      } else if (bgmEl) {
        bgmEl.pause();
      }
      renderSnd();
    }, false);
    renderSnd();
  }

  // 首次用户操作 → 起播 BGM（点喇叭按钮时交给按钮自己处理）
  function wakeSound(e) {
    if (bgmStarted || !sndOn) return;
    if (e && e.target && sndBtn && sndBtn.contains(e.target)) return;
    bgmStarted = true;
    nextBgm();
    ["pointerdown", "keydown", "touchstart"].forEach(function (t) {
      window.removeEventListener(t, wakeSound);
    });
  }
  ["pointerdown", "keydown", "touchstart"].forEach(function (t) {
    window.addEventListener(t, wakeSound, { passive: true });
  });

  /* ---------------- 启动 ---------------- */
  function byRarity(r) {
    const pool = window.FISH_DATA.filter(x => x.rarity === r);
    if (!pool.length) return pool;
    return [pool[Math.floor(Math.random() * pool.length)]];
  }

  function clearFishes() {
    for (const f of fishes) f.el.remove();
    fishes.length = 0;
  }

  // 用 API 返回的用户鱼缸数据渲染鱼缸：每条鱼按它自己的重量放一条（同种多条各自出现，
  // 尺寸由重量决定 → 画面条数 = 计数，不再出现「计数 13 / 画面只有 7 条」的错位）
  function addFromData(data) {
    clearFishes();
    const cap = data.capacity || {};
    const max = cap.max || 0;
    MAX_CAP = Math.max(max, 60);
    // 该用户的鱼线重量倍率（抽鱼时重量会被放大，算尺寸时要还原，见 weightRatio）
    const wmult = data.mult > 0 ? data.mult : 1;
    let total = 0;
    for (const f of data.fish || []) {
      let spec = window.FISH_DATA.find(d => d.id === f.id);
      if (!spec) spec = window.FISH_DATA.find(d => d.name === f.name);
      if (!spec) spec = { slug: "goldfish", rarity: f.rarity, bottom: false, price: 0 };
      const base = Object.assign({}, spec,
        { name: f.name, gold: !!(f.gold || spec.gold) });
      // 每条重量放一条鱼；没有重量数据时放一条兜底（尺寸用 slug 哈希，稳定不变）
      const ws = (Array.isArray(f.weights) && f.weights.length) ? f.weights : [null];
      for (const w of ws) {
        if (fishes.length >= MAX_CAP) break;
        addFish(fishSpec(Object.assign({}, base,
          { weight: w, wmin: f.wmin, wmax: f.wmax, mult: wmult })));
        total += 1;
      }
    }
    const hint = document.getElementById("hint");
    if (max <= 0) {
      capEl.textContent = "🐠 尚未购买鱼缸";
      if (hint) hint.textContent = "鱼具店发「买鱼缸」买一个，就能把鱼存进来防偷啦";
    } else if (!data.fish || !data.fish.length) {
      capEl.textContent = "🐟 0 / " + max;
      if (hint) hint.textContent = "鱼缸空空如也，发「存鱼 <鱼名> <数量>」把鱼存进来防偷";
    } else {
      capEl.textContent = "🐟 " + total + " / " + max;
      if (hint) hint.textContent = "点鱼看信息 · 右键 / 长按 / 双击喂食";
    }
    resize();   // API 替换的鱼需要重算尺寸/位置（否则 0×0 不可见）
  }

  // 演示鱼（无 key 直接打开页面 / 接口连不上时兜底；不再作为真实鱼缸的占位闪现）
  function addDemoFishes() {
    const picks = [];
    for (let i = 0; i < 7; i++) picks.push(...byRarity("common"));
    for (let i = 0; i < 6; i++) picks.push(...byRarity("rare"));
    for (let i = 0; i < 4; i++) picks.push(...byRarity("epic"));
    for (let i = 0; i < 2; i++) picks.push(...byRarity("legend"));
    for (let i = 0; i < 1; i++) picks.push(...byRarity("myth"));
    const goldPool = window.FISH_DATA.filter(x => x.gold);
    for (let i = 0; i < 3 && goldPool.length; i++)
      picks.push(goldPool[Math.floor(Math.random() * goldPool.length)]);
    const bottomPool = window.FISH_DATA.filter(x => x.bottom);
    for (let i = 0; i < Math.min(3, bottomPool.length); i++)
      picks.push(bottomPool[i]);
    const seen = {};
    for (const s of picks) {
      const k = s.slug + (s.gold ? "-g" : "");
      if (seen[k]) continue;
      seen[k] = 1;
      addFish(fishSpec(s));
    }
    capEl.textContent = "🐟 " + fishes.length + " / " + MAX_CAP;
    const hint = document.getElementById("hint");
    if (hint) hint.textContent = "演示鱼缸 · 点鱼看信息 · 右键 / 长按 / 双击喂食";
    resize();
  }

  function loadFromAPI() {
    const key = window.location.pathname.split("/").pop();
    if (!key) { addDemoFishes(); return; }
    fetch("/api/aquarium/" + encodeURIComponent(key))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.ok) {
          addFromData(data);
        } else {
          const hint = document.getElementById("hint");
          if (hint) hint.textContent = "没有找到这个鱼缸喵";
        }
      })
      .catch(function () { addDemoFishes(); });   // 接口连不上时兜底演示鱼，避免空缸
  }

  function boot() {
    for (let i = 1; i <= 6; i++) FOOD_IMAGES.push("images/Food" + i + ".png");
    resize();
    lockWaterHeight();
    requestAnimationFrame(loop);
    // 有鱼缸 key：先只显示「加载中」，接口返回后直接出现真实鱼
    // （不再先闪一堆演示鱼再替换）；无 key（直接打开页面）才进演示模式
    const key = window.location.pathname.split("/").pop();
    if (key) {
      const hint = document.getElementById("hint");
      capEl.textContent = "🐟 加载中…";
      if (hint) hint.textContent = "正在打开鱼缸喵…";
      loadFromAPI();
    } else {
      addDemoFishes();
    }
  }

  window.addEventListener("resize", function () {
    // 页面缩放（Ctrl +/-）时 dpr 变化 → 重锁水纹保持物理大小；纯窗口拖动则保持锁定不变
    if ((window.devicePixelRatio || 1) !== waterDPR) lockWaterHeight();
    resize();
  });
  boot();
})();