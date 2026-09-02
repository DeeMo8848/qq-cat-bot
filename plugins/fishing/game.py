# -*- coding: utf-8 -*-
"""钓鱼玩法数据与抽取逻辑（纯函数，便于测试；命令在 core.py）。"""

import datetime
import random

RARITIES = {
    "common": "常见",
    "fine": "优良",
    "rare": "稀有",
    "epic": "史诗",
    "legend": "传说",
}
RARITY_ORDER = ["common", "fine", "rare", "epic", "legend"]

# 钓竿等级：(名称, 价格)
RODS = {
    1: ("木钓竿", 0),
    2: ("竹钓竿", 1200),
    3: ("碳素钓竿", 5000),
    4: ("钛合金钓竿", 20000),
    5: ("传说钓竿", 80000),
}
_ROD_RARITY = {
    1: {"common": .50, "fine": .30, "rare": .14, "epic": .055, "legend": .005},
    2: {"common": .42, "fine": .32, "rare": .18, "epic": .07, "legend": .010},
    3: {"common": .34, "fine": .32, "rare": .22, "epic": .10, "legend": .020},
    4: {"common": .26, "fine": .30, "rare": .26, "epic": .14, "legend": .040},
    5: {"common": .18, "fine": .28, "rare": .30, "epic": .18, "legend": .060},
}

# 鱼钩等级：(名称, 价格, 稀有度加成)
HOOKS = {
    1: ("木质鱼钩", 0, {}),
    2: ("骨质鱼钩", 1500, {"rare": .02}),
    3: ("铁质鱼钩", 6000, {"rare": .04}),
    4: ("金质鱼钩", 20000, {"rare": .03, "epic": .02}),
    5: ("钻石鱼钩", 60000, {"epic": .04, "legend": .01}),
    6: ("秘银鱼钩", 150000, {"epic": .05, "legend": .02}),
}

# 鱼线等级：(名称, 价格, 重量倍率)
LINES = {
    1: ("棉线", 0, 1.0),
    2: ("麻线", 1500, 1.1),
    3: ("尼龙线", 6000, 1.25),
    4: ("钢丝线", 20000, 1.4),
    5: ("碳素线", 60000, 1.6),
    6: ("秘银线", 150000, 1.8),
}

# 鱼漂等级：(名称, 价格, 上钩率加成)
FLOATS = {
    1: ("橡皮鸭", 0, 0.0),
    2: ("乒乓球", 1200, 0.02),
    3: ("木浮漂", 5000, 0.05),
    4: ("工艺漂", 15000, 0.08),
    5: ("夜光漂", 45000, 0.10),
    6: ("自动钩鱼漂", 120000, 0.12),
}

# 附魔等级罗马数字
ENCHANT_LV_CN = ("", "I", "II", "III")

# 饵料：(名称, 单价, 稀有度加成)
BAITS = {
    "bait1": ("蚯蚓", 30, {"rare": .03}),
    "bait2": ("高级鱼饵", 120, {"epic": .04, "legend": .01}),
}

# 鱼：id -> (名称, emoji, 稀有度, 基础价, 重量区间[克], 昼/夜)
FISH = {
    # ===== 常见（70） =====
    "f_tilapia": ("罗非鱼", "🐠", "common", 30, (400, 1500), "day"),
    "f_carp": ("鲤鱼", "🐟", "common", 30, (800, 4000), "day"),
    "f_grass": ("草鱼", "🐬", "common", 35, (1000, 6000), "day"),
    "f_bream": ("鲫鱼", "🐟", "common", 25, (150, 800), "day"),
    "f_loach": ("泥鳅", "🪱", "common", 15, (80, 200), "day"),
    "f_snail": ("田螺", "🐚", "common", 10, (20, 60), "day"),
    "f_guppy": ("孔雀鱼", "🐠", "common", 20, (50, 150), "day"),
    "f_molly": ("摩利鱼", "🐠", "common", 22, (60, 180), "day"),
    "f_tetra": ("灯鱼", "🐟", "common", 18, (10, 40), "day"),
    "f_zebra": ("斑马鱼", "🐟", "common", 20, (20, 60), "day"),
    "f_betta": ("斗鱼", "🐠", "common", 25, (30, 100), "day"),
    "f_goldfish2": ("金鱼", "🐟", "common", 28, (100, 400), "day"),
    "f_whitebait": ("白条鱼", "🐟", "common", 15, (30, 120), "day"),
    "f_chinese": ("餐条鱼", "🐟", "common", 15, (20, 100), "day"),
    "f_topmouth": ("麦穗鱼", "🐟", "common", 12, (10, 50), "day"),
    "f_culter": ("翘嘴鱼", "🐟", "common", 30, (200, 1200), "day"),
    "f_mudcarp": ("鲮鱼", "🐟", "common", 25, (300, 1500), "day"),
    "f_blackcarp": ("青鱼", "🐟", "common", 40, (1500, 8000), "day"),
    "f_bighead": ("鳙鱼", "🐟", "common", 38, (1500, 7000), "day"),
    "f_silvercarp": ("白鲢", "🐟", "common", 32, (1000, 5000), "day"),
    "f_wuchang": ("武昌鱼", "🐟", "common", 30, (400, 2000), "day"),
    "f_yellowcat": ("黄颡鱼", "🐟", "common", 28, (100, 400), "day"),
    "f_snakehead": ("黑鱼", "🐟", "common", 35, (500, 3000), "day"),
    "f_mandarin": ("鳜鱼", "🐟", "common", 45, (300, 1500), "day"),
    "f_giantprawn": ("罗氏沼虾", "🦐", "common", 20, (30, 150), "day"),
    "f_crayfish": ("小龙虾", "🦞", "common", 25, (40, 150), "day"),
    "f_mussel": ("河蚌", "🐚", "common", 12, (50, 300), "day"),
    "f_clam": ("蛤蜊", "🐚", "common", 15, (30, 120), "day"),
    "f_oyster": ("牡蛎", "🦪", "common", 18, (50, 200), "day"),
    "f_conch": ("海螺", "🐚", "common", 20, (60, 300), "day"),
    "f_scallop": ("扇贝", "🐚", "common", 22, (40, 150), "day"),
    "f_seaurchin": ("海胆", "🦔", "common", 25, (100, 400), "day"),
    "f_jellyfish": ("水母", "🪼", "common", 15, (100, 500), "day"),
    "f_starfish": ("海星", "⭐", "common", 18, (50, 200), "day"),
    "f_seacucumber": ("海参", "🥒", "common", 30, (100, 400), "day"),
    "f_mantisshrimp": ("皮皮虾", "🦐", "common", 35, (50, 200), "day"),
    "f_lanternfish": ("灯笼鱼", "🐟", "common", 20, (100, 500), "night"),
    "f_electric_eel": ("电鳗", "🐍", "common", 40, (500, 2500), "night"),
    "f_deepshrimp": ("深海虾", "🦐", "common", 25, (20, 80), "night"),
    "f_krill": ("磷虾", "🦐", "common", 15, (5, 20), "night"),
    "f_blindfish": ("盲鱼", "🐟", "common", 20, (50, 200), "night"),
    "f_deepcod": ("深海鳕鱼", "🐟", "common", 25, (300, 1500), "night"),
    "f_sardine": ("沙丁鱼", "🐟", "common", 18, (50, 150), "night"),
    "f_anchovy": ("凤尾鱼", "🐟", "common", 15, (20, 80), "night"),
    "f_saury": ("秋刀鱼", "🐟", "common", 22, (100, 300), "night"),
    "f_hairtail": ("带鱼", "🐍", "common", 30, (300, 1500), "night"),
    "f_yellowcroaker": ("黄花鱼", "🐟", "common", 28, (200, 1000), "night"),
    "f_pomfret": ("鲳鱼", "🐟", "common", 30, (300, 1500), "night"),
    "f_swimmingcrab": ("梭子蟹", "🦀", "common", 30, (100, 400), "night"),
    "f_mudcrab": ("青蟹", "🦀", "common", 35, (150, 600), "night"),
    "f_silverfish": ("银鱼", "🐟", "common", 18, (20, 80), "day"),
    "f_ayu": ("香鱼", "🐟", "common", 22, (50, 200), "day"),
    "f_goby": ("虾虎鱼", "🐟", "common", 18, (20, 80), "day"),
    "f_butterflyfish": ("蝴蝶鱼", "🦋", "common", 25, (50, 200), "day"),
    "f_angelfish": ("神仙鱼", "🐠", "common", 28, (80, 300), "day"),
    "f_discus": ("七彩神仙", "🐠", "common", 30, (100, 400), "day"),
    "f_parrotfish": ("鹦鹉鱼", "🦜", "common", 30, (200, 800), "day"),
    "f_clownfish": ("小丑鱼", "🐠", "common", 25, (30, 120), "day"),
    "f_bluegill": ("蓝鳃太阳鱼", "🐟", "common", 22, (100, 400), "day"),
    "f_turtle2": ("小乌龟", "🐢", "common", 30, (100, 500), "day"),
    "f_lamprey": ("七鳃鳗", "🐍", "common", 25, (200, 800), "night"),
    "f_moray": ("海鳝", "🐍", "common", 35, (500, 2000), "night"),
    "f_flyingfish": ("飞鱼", "🐟", "common", 25, (100, 400), "night"),
    "f_deepcrab": ("深海蟹", "🦀", "common", 28, (100, 400), "night"),
    "f_brittlestar": ("海蛇尾", "⭐", "common", 20, (20, 80), "night"),
    "f_seaworm": ("沙蚕", "🪱", "common", 15, (10, 40), "night"),
    "f_rockfish": ("岩鱼", "🐟", "common", 25, (200, 800), "night"),
    "f_lionfish": ("蓑鲉", "🦁", "common", 32, (200, 800), "night"),
    "f_frogfish": ("躄鱼", "🐟", "common", 28, (100, 400), "night"),
    "f_remora": ("吸盘鱼", "🐟", "common", 25, (200, 800), "night"),
    # ===== 优良（55） =====
    "f_shrimp": ("河虾", "🦐", "fine", 45, (20, 80), "day"),
    "f_bass": ("鲈鱼", "🎣", "fine", 60, (600, 3000), "day"),
    "f_eel": ("鳗鱼", "🐍", "fine", 75, (500, 2500), "day"),
    "f_trout": ("虹鳟", "🐡", "fine", 70, (400, 2000), "day"),
    "f_catfish": ("鲶鱼", "🐲", "fine", 55, (700, 3500), "day"),
    "f_crab": ("河蟹", "🦀", "fine", 50, (100, 500), "day"),
    "f_redsnapper": ("红鲷鱼", "🐟", "fine", 55, (400, 2000), "day"),
    "f_sea_bass": ("海鲈鱼", "🎣", "fine", 65, (800, 3500), "day"),
    "f_garfish": ("针鱼", "🐟", "fine", 50, (200, 1000), "day"),
    "f_pike": ("狗鱼", "🐟", "fine", 60, (800, 4000), "day"),
    "f_zander": ("梭鲈", "🐟", "fine", 58, (600, 3000), "day"),
    "f_perch": ("河鲈", "🐟", "fine", 55, (200, 1000), "day"),
    "f_tench": ("丁鱥", "🐟", "fine", 50, (500, 2500), "day"),
    "f_roach": ("拟鲤", "🐟", "fine", 45, (100, 500), "day"),
    "f_rudd": ("红眼鱼", "🐟", "fine", 45, (150, 600), "day"),
    "f_gudgeon": ("棒花鱼", "🐟", "fine", 40, (50, 200), "day"),
    "f_loach2": ("花鳅", "🪱", "fine", 40, (60, 250), "day"),
    "f_amur": ("雅罗鱼", "🐟", "fine", 48, (300, 1500), "day"),
    "f_grayling": ("茴鱼", "🐟", "fine", 52, (200, 1000), "day"),
    "f_char": ("红点鲑", "🐟", "fine", 62, (500, 2500), "day"),
    "f_whitefish": ("白鲑", "🐟", "fine", 60, (600, 3000), "day"),
    "f_herring": ("鲱鱼", "🐟", "fine", 50, (200, 800), "day"),
    "f_mullet2": ("鲻鱼", "🐟", "fine", 55, (400, 2000), "day"),
    "f_amberjack": ("琥珀鱼", "🐟", "fine", 70, (1000, 5000), "day"),
    "f_snapper": ("真鲷", "🐟", "fine", 65, (500, 2500), "day"),
    "f_abalone": ("鲍鱼", "🐚", "fine", 60, (50, 200), "day"),
    "f_whelk": ("香螺", "🐚", "fine", 45, (40, 150), "day"),
    "f_razorclam": ("蛏子", "🐚", "fine", 40, (20, 80), "day"),
    "f_octopus": ("章鱼", "🐙", "fine", 80, (500, 3000), "night"),
    "f_grouper2": ("石斑鱼", "🐟", "fine", 70, (500, 3000), "night"),
    "f_mackerel2": ("蓝点马鲛", "🐟", "fine", 60, (400, 2000), "night"),
    "f_seabream2": ("黑鲷", "🐟", "fine", 58, (300, 1500), "night"),
    "f_croaker2": ("白姑鱼", "🐟", "fine", 50, (200, 1000), "night"),
    "f_lobster2": ("龙虾", "🦞", "fine", 75, (200, 800), "night"),
    "f_flowercrab": ("花蟹", "🦀", "fine", 60, (100, 500), "night"),
    "f_deepshrimp2": ("深海红虾", "🦐", "fine", 55, (30, 120), "night"),
    "f_viperfish": ("蝰鱼", "🐍", "fine", 65, (200, 900), "night"),
    "f_gulper": ("吞噬鳗", "🐍", "fine", 68, (300, 1200), "night"),
    "f_hatchetfish": ("斧头鱼", "🐟", "fine", 52, (20, 80), "night"),
    "f_dragonfish": ("深海龙鱼", "🐉", "fine", 72, (400, 1800), "night"),
    "f_gar": ("雀鳝", "🐟", "fine", 65, (800, 3500), "day"),
    "f_bowfin": ("弓鳍鱼", "🐟", "fine", 60, (600, 3000), "day"),
    "f_steelhead": ("硬头鳟", "🐟", "fine", 68, (700, 3000), "day"),
    "f_brooktrout": ("溪红点鲑", "🐟", "fine", 62, (400, 2000), "day"),
    "f_grasscarp2": ("鲩鱼", "🐟", "fine", 58, (1000, 5000), "day"),
    "f_yellowperch": ("黄鲈", "🐟", "fine", 55, (200, 1000), "day"),
    "f_amberjack2": ("黄尾鰤", "🐟", "fine", 72, (800, 3500), "day"),
    "f_skate": ("鳐鱼", "🦈", "fine", 75, (1500, 6000), "day"),
    "f_bluefish": ("蓝鱼", "🐟", "fine", 60, (500, 2500), "night"),
    "f_bonito": ("鲣鱼", "🐟", "fine", 65, (800, 3000), "night"),
    "f_kingfish": ("马鲛鱼", "🐟", "fine", 62, (600, 2500), "night"),
    "f_sablefish": ("黑鳕鱼", "🐟", "fine", 70, (800, 3500), "night"),
    "f_pollock": ("狭鳕", "🐟", "fine", 58, (500, 2500), "night"),
    "f_hake": ("无须鳕", "🐟", "fine", 60, (500, 2500), "night"),
    "f_squid2": ("鱿鱼", "🦑", "fine", 55, (200, 900), "night"),
    # ===== 稀有（40） =====
    "f_sturgeon": ("中华鲟", "🐉", "rare", 150, (2000, 5000), "day"),
    "f_bass2": ("大嘴鲈", "🐠", "rare", 140, (900, 4000), "day"),
    "f_salmon": ("三文鱼", "🐟", "rare", 130, (2500, 7000), "day"),
    "f_flatfish": ("比目鱼", "🦈", "rare", 160, (600, 2500), "day"),
    "f_swordfish": ("剑鱼", "🐟", "rare", 200, (3000, 10000), "day"),
    "f_marlin": ("旗鱼", "🐟", "rare", 220, (4000, 12000), "day"),
    "f_halibut": ("大比目鱼", "🦈", "rare", 180, (2000, 8000), "day"),
    "f_cod": ("鳕鱼", "🐟", "rare", 160, (1500, 6000), "day"),
    "f_haddock": ("黑线鳕", "🐟", "rare", 150, (1000, 4000), "day"),
    "f_turbot": ("多宝鱼", "🐟", "rare", 170, (800, 3000), "day"),
    "f_sole": ("龙利鱼", "🐟", "rare", 165, (500, 2000), "day"),
    "f_sea_urchin2": ("紫海胆", "🦔", "rare", 155, (50, 200), "day"),
    "f_abalone2": ("大鲍鱼", "🐚", "rare", 175, (100, 400), "day"),
    "f_prawn": ("大明虾", "🦐", "rare", 160, (80, 300), "day"),
    "f_kingcrab": ("皇帝蟹", "🦀", "rare", 190, (1000, 4000), "day"),
    "f_arowana": ("金龙鱼", "🐉", "rare", 200, (1500, 5000), "day"),
    "f_pearlfish": ("珍珠鱼", "🐟", "rare", 170, (200, 800), "day"),
    "f_golden_carp": ("金鲤", "🐟", "rare", 185, (800, 3000), "day"),
    "f_squid": ("荧光鱿", "🦑", "rare", 170, (300, 900), "night"),
    "f_redcrab": ("帝王蟹", "🦀", "rare", 180, (900, 3500), "night"),
    "f_viperfish2": ("毒蛇鱼", "🐍", "rare", 180, (300, 1200), "night"),
    "f_angler2": ("深海鮟鱇", "🐟", "rare", 190, (800, 3000), "night"),
    "f_fangtooth": ("尖牙鱼", "🐟", "rare", 175, (200, 800), "night"),
    "f_barreleye": ("大眼鱼", "🐟", "rare", 185, (100, 400), "night"),
    "f_blackseadevil": ("黑魔鬼鱼", "🐟", "rare", 195, (500, 2000), "night"),
    "f_giantsquid2": ("巨枪乌贼", "🦑", "rare", 210, (5000, 20000), "night"),
    "f_octopus2": ("蓝环章鱼", "🐙", "rare", 185, (100, 400), "night"),
    "f_cuttlefish2": ("大王墨鱼", "🦑", "rare", 180, (2000, 8000), "night"),
    "f_seaspider": ("海蜘蛛", "🕷️", "rare", 170, (50, 200), "night"),
    "f_glowfish": ("发光鱼", "💡", "rare", 175, (100, 500), "night"),
    "f_grouper": ("东星斑", "🐟", "rare", 175, (800, 3000), "day"),
    "f_redgrouper": ("红斑鱼", "🐟", "rare", 170, (600, 2500), "day"),
    "f_trevally": ("鲹鱼", "🐟", "rare", 165, (800, 3500), "day"),
    "f_amberjack3": ("鰤鱼", "🐟", "rare", 180, (2000, 8000), "day"),
    "f_bluecrab": ("蓝蟹", "🦀", "rare", 160, (150, 600), "day"),
    "f_viperfish3": ("毒鲉", "🐟", "rare", 175, (200, 800), "night"),
    "f_blackmouth": ("黑口鱼", "🐟", "rare", 170, (300, 1200), "night"),
    "f_seabass2": ("狼鲈", "🐟", "rare", 180, (1000, 4000), "night"),
    "f_bluewhiting": ("蓝鳕", "🐟", "rare", 165, (400, 1800), "night"),
    "f_giantcrab": ("巨型蜘蛛蟹", "🦀", "rare", 200, (2000, 8000), "night"),
    # ===== 史诗（25） =====
    "f_koi": ("锦鲤", "🎐", "epic", 500, (1500, 6000), "day"),
    "f_goldfish": ("龙睛金鱼", "🧡", "epic", 300, (200, 900), "day"),
    "f_tuna": ("金枪鱼", "🐳", "epic", 380, (4000, 12000), "day"),
    "f_shark": ("小鲨鱼", "🦈", "epic", 420, (3000, 10000), "day"),
    "f_sailfish": ("帆鱼", "🐟", "epic", 450, (5000, 15000), "day"),
    "f_wahoo": ("刺鲅", "🐟", "epic", 400, (3000, 10000), "day"),
    "f_barracuda": ("梭鱼", "🦈", "epic", 380, (2000, 8000), "day"),
    "f_stingray": ("魔鬼鱼", "🦈", "epic", 420, (3000, 12000), "day"),
    "f_manatee": ("海牛", "🐬", "epic", 500, (10000, 30000), "day"),
    "f_seal": ("海豹", "🦭", "epic", 480, (8000, 25000), "day"),
    "f_anglerfish": ("鮟鱇鱼", "👹", "epic", 320, (1200, 4000), "night"),
    "f_seahorse": ("海马", "🦄", "epic", 310, (10, 40), "night"),
    "f_batfish": ("蝙蝠鱼", "🦇", "epic", 280, (800, 3000), "night"),
    "f_deeplamp": ("深海灯笼", "💡", "epic", 350, (600, 2500), "night"),
    "f_moonfish": ("月光鱼", "🌙", "epic", 330, (1500, 5000), "night"),
    "f_giantisopod": ("大王具足虫", "🪲", "epic", 400, (300, 1000), "night"),
    "f_dumbo": ("小飞象章鱼", "🐙", "epic", 420, (200, 800), "night"),
    "f_gulpereel": ("深海鳗", "🐍", "epic", 430, (1500, 6000), "night"),
    "f_ghostshark": ("幽灵鲨", "🦈", "epic", 460, (2000, 8000), "night"),
    "f_abyssal": ("深渊鱼", "🐟", "epic", 440, (1000, 4000), "night"),
    "f_sunfish": ("翻车鱼", "🐟", "epic", 450, (10000, 30000), "day"),
    "f_mola": ("月鱼", "🌙", "epic", 430, (8000, 25000), "day"),
    "f_hammerhead": ("锤头鲨", "🦈", "epic", 470, (5000, 15000), "day"),
    "f_oarfish": ("皇带鱼", "🐍", "epic", 480, (10000, 30000), "night"),
    "f_goblin": ("欧氏尖吻鲛", "🦈", "epic", 460, (3000, 10000), "night"),
    # ===== 传说（10） =====
    "f_bluefin": ("蓝鳍金枪鱼", "🐋", "legend", 1000, (15000, 40000), "day"),
    "f_dolphin": ("海豚", "🐬", "legend", 1500, (20000, 50000), "day"),
    "f_whale": ("蓝鲸", "🐋", "legend", 2200, (50000, 120000), "day"),
    "f_whaleshark": ("鲸鲨", "🦈", "legend", 2000, (30000, 80000), "day"),
    "f_orca": ("虎鲸", "🐋", "legend", 2500, (40000, 100000), "day"),
    "f_humpback": ("座头鲸", "🐋", "legend", 2300, (45000, 110000), "day"),
    "f_kraken": ("大王乌贼", "🦑", "legend", 1800, (30000, 90000), "night"),
    "f_violin": ("小提琴鱼", "🎻", "legend", 1200, (1000, 4000), "night"),
    "f_leviathan": ("利维坦", "🐲", "legend", 2800, (60000, 150000), "night"),
    "f_abyss_king": ("深渊之王", "🐙", "legend", 2600, (40000, 100000), "night"),
}


def is_night(now=None):
    h = (now or datetime.datetime.now()).hour
    return h >= 20 or h < 6


def pool_ids():
    night = is_night()
    return [k for k, (_, _, _, _, _, z) in FISH.items() if z == ("night" if night else "day")]


def _shift(w, target, plus):
    """从更低稀有度向 target 转移 plus 权重。"""
    if plus <= 0:
        return
    moved = 0.0
    for r in RARITY_ORDER:
        if r == target:
            break
        take = min(w.get(r, 0), plus - moved)
        if take <= 0:
            continue
        w[r] = w.get(r, 0) - take
        moved += take
        if moved >= plus:
            break
    w[target] = w.get(target, 0) + moved


def rarity_chance(rod, has_bait=None, hook_lv=1, fortune_lv=0, luck_lv=0):
    """返回按稀有度排序的权重表，受钓竿、鱼饵、鱼钩与附魔影响。"""
    w = dict(_ROD_RARITY.get(rod, _ROD_RARITY[1]))
    for b in (has_bait or []):
        for r, plus in BAITS[b][2].items():
            _shift(w, r, plus)
    for r, plus in HOOKS.get(hook_lv, HOOKS[1])[2].items():
        _shift(w, r, plus)
    for _ in range(fortune_lv):          # 时运：稀有度提升
        _shift(w, "rare", 0.03)
        _shift(w, "epic", 0.02)
    for _ in range(luck_lv):             # 海之眷顾：传说提升
        _shift(w, "legend", 0.02)
    s = sum(w.values()) or 1.0
    return {k: v / s for k, v in w.items()}


def hook_rate(float_lv, lure_lv):
    """上钩率：基础 85% + 鱼漂加成 + 饵钓附魔（每级 +5%）。"""
    base = 0.85 + FLOATS.get(float_lv, FLOATS[1])[2] + lure_lv * 0.05
    return min(base, 1.0)


def roll_fish(rod, has_bait=None, rng=None, hook_lv=1, line_lv=1, fortune_lv=0, luck_lv=0):
    """抽取一条鱼。返回 dict {id,name,emoji,rarity,weight_g,value}。"""
    rng = rng or random
    has_bait = has_bait or []
    chances = rarity_chance(rod, has_bait, hook_lv, fortune_lv, luck_lv)
    rar = rng.random()
    acc = 0.0
    chosen_rar = "common"
    for r in RARITY_ORDER:
        acc += chances[r]
        if rar <= acc:
            chosen_rar = r
            break
    pool = set(pool_ids())
    candidates = [k for k, (_, _, rr, _, _, _) in FISH.items() if rr == chosen_rar and k in pool]
    if not candidates:
        chosen_rar = "common"
        candidates = [k for k, (_, _, rr, _, _, _) in FISH.items() if rr == "common"]
    fid = rng.choice(candidates)
    name, emoji, rr, base, (wmin, wmax), _z = FISH[fid]
    mult = LINES.get(line_lv, LINES[1])[2]
    wg = int(rng.randint(wmin, wmax) * mult)
    value = base + int(wg / 8)
    return {"id": fid, "name": name, "emoji": emoji,
            "rarity": rr, "weight_g": wg, "value": value}


def fmt_weight(g):
    return f"{g/1000:g}kg" if g >= 1000 else f"{g}g"


# ---------- 扭蛋（喵币回收口） ----------
# 比传说钓竿更易出稀有；不限昼夜池，方便集图鉴
GACHA_CHANCE = {"common": 0.26, "fine": 0.30, "rare": 0.24, "epic": 0.15, "legend": 0.05}


def roll_gacha(rng=None):
    rng = rng or random
    rar = rng.random()
    acc = 0.0
    chosen_rar = "common"
    for r in RARITY_ORDER:
        acc += GACHA_CHANCE[r]
        if rar <= acc:
            chosen_rar = r
            break
    candidates = [k for k, (_, _, rr, _, _, _) in FISH.items() if rr == chosen_rar]
    if not candidates:
        chosen_rar = "common"
        candidates = [k for k, (_, _, rr, _, _, _) in FISH.items() if rr == "common"]
    fid = rng.choice(candidates)
    name, emoji, rr, base, (wmin, wmax), _z = FISH[fid]
    wg = rng.randint(wmin, wmax)
    return {"id": fid, "name": name, "emoji": emoji,
            "rarity": rr, "weight_g": wg, "value": base + int(wg / 8)}