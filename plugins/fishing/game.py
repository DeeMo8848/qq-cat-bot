# -*- coding: utf-8 -*-
"""钓鱼玩法数据与抽取逻辑（纯函数，便于测试；命令在 core.py）。

稀有度体系（按 Fish_Data_Updated.csv 的 Rarity 0-4 映射）：
  common 普通 / rare 稀有 / epic 史诗 / legend 传说 / myth 神话（黄金鱼为独立词条，不入稀有度）
"""

import datetime
import random

RARITIES = {
    "common": "普通",
    "rare": "稀有",
    "epic": "史诗",
    "legend": "传说",
    "myth": "神话",
}
RARITY_ORDER = ["common", "rare", "epic", "legend", "myth"]

# 黄金鱼：独立于稀有度的专属词条，每种鱼都有黄金版本，钓鱼时以 GOLD_CHANCE 概率触发
GOLD_CHANCE = 0.01   # 1%
GOLD_MULT = 5        # 黄金版价格倍率
GOLD_PREFIX = "✨黄金"

# 钓竿等级：(名称, 价格)
RODS = {
    1: ("木钓竿", 0),
    2: ("竹钓竿", 1200),
    3: ("碳素钓竿", 5000),
    4: ("钛合金钓竿", 20000),
    5: ("传说钓竿", 80000),
}
_ROD_RARITY = {
    1: {"common": .50, "rare": .30, "epic": .14, "legend": .055, "myth": .005},
    2: {"common": .42, "rare": .32, "epic": .18, "legend": .07, "myth": .010},
    3: {"common": .34, "rare": .32, "epic": .22, "legend": .10, "myth": .020},
    4: {"common": .26, "rare": .30, "epic": .26, "legend": .14, "myth": .040},
    5: {"common": .18, "rare": .28, "epic": .30, "legend": .18, "myth": .060},
}

# 鱼钩等级：(名称, 价格, 稀有度加成)
HOOKS = {
    1: ("木质鱼钩", 0, {}),
    2: ("骨质鱼钩", 1500, {"rare": .02}),
    3: ("铁质鱼钩", 6000, {"rare": .04}),
    4: ("金质鱼钩", 20000, {"rare": .03, "epic": .02}),
    5: ("钻石鱼钩", 60000, {"epic": .04, "legend": .01}),
    6: ("秘银鱼钩", 150000, {"legend": .03, "myth": .01}),
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

# 自动钓鱼机等级：(名称, 价格, 间隔分钟, 成功率, 最大小时)
AUTO_MACHINES = {
    1: ("基础钓鱼机", 500,   60, 0.40, 8),
    2: ("老式钓鱼机", 2000,  45, 0.55, 10),
    3: ("标准钓鱼机", 8000,  30, 0.65, 12),
    4: ("高级钓鱼机", 25000, 20, 0.75, 16),
    5: ("秘银钓鱼机", 80000, 10, 0.85, 24),
}

# 鱼缸等级：(名称, 价格, 容量条数)。第 1 级为购买基础缸，之后逐级升级；
# 容量 60 → 80(+20) → 100(+20) → 130(+30) → 160(+30) → 200(+40)，后三级高价
AQUARIUM_TANKS = {
    1: ("基础鱼缸", 500, 60),
    2: ("小型鱼缸", 2000, 80),
    3: ("中型鱼缸", 8000, 100),
    4: ("大型鱼缸", 50000, 130),
    5: ("巨型鱼缸", 150000, 160),
    6: ("豪华鱼缸", 300000, 200),
}

# 附魔等级罗马数字
ENCHANT_LV_CN = ("", "I", "II", "III")

# 饵料：(名称, 单价, 稀有度加成)
BAITS = {
    "bait1": ("蚯蚓", 30, {"rare": .03}),
    "bait2": ("高级鱼饵", 120, {"epic": .04, "legend": .01}),
}

# 鱼：id -> (名称, emoji, 稀有度, 基础价, 重量区间[克], 昼/夜)
# 依据 Fish_Data_Updated.csv（182 种）：英文名=id，Lifetime 反推价格与重量，昼夜随机分配
FISH = {
"f_goldfish": ('金鱼', '⚪', 'common', 21, (30, 203), 'day'),
"f_neon_tetra": ('霓虹灯鱼', '⚪', 'common', 25, (31, 206), 'night'),
"f_blue_grass_guppy": ('蓝草孔雀鱼', '⚪', 'common', 30, (32, 209), 'day'),
"f_clown_loach": ('三间鼠鱼', '⚪', 'common', 40, (34, 219), 'night'),
"f_corydoras": ('老鼠鱼', '⚪', 'common', 48, (36, 228), 'day'),
"f_swordtail": ('剑尾鱼', '⚪', 'common', 56, (38, 238), 'night'),
"f_wagtail_platy": ('花斑剑尾鱼', '⚪', 'common', 56, (38, 238), 'day'),
"f_acara": ('蓝宝丽鱼', '⚪', 'common', 68, (42, 257), 'night'),
"f_bala_shark": ('银鲨鲳', '⚪', 'common', 79, (46, 276), 'day'),
"f_betta": ('斗鱼', '⚪', 'common', 89, (50, 295), 'night'),
"f_dwarf_gourami": ('丽丽鱼', '⚪', 'common', 98, (54, 314), 'day'),
"f_chili_rasbora": ('红莲灯鱼', '⚪', 'common', 130, (70, 390), 'night'),
"f_ramshorn_snail": ('羊角螺', '🟢', 'rare', 115, (62, 352), 'day'),
"f_hillstream_loach": ('吸鳅', '🟢', 'rare', 144, (78, 428), 'night'),
"f_peacock_gudgeon": ('孔雀橘丽鱼', '🟢', 'rare', 170, (94, 504), 'day'),
"f_pleco": ('异型鱼', '🟢', 'rare', 214, (126, 656), 'night'),
"f_cherry_shrimp": ('樱桃虾', '🟢', 'rare', 252, (158, 808), 'day'),
"f_angelfish": ('神仙鱼', '🟢', 'rare', 319, (222, 1112), 'night'),
"f_flowerhorn_cichlid": ('罗汉鱼', '🟢', 'rare', 479, (414, 2024), 'day'),
"f_discus": ('七彩神仙', '🟣', 'epic', 319, (222, 1112), 'night'),
"f_oscar": ('地图鱼', '🟣', 'epic', 377, (286, 1416), 'day'),
"f_elephant_fish": ('象鼻鱼', '🟣', 'epic', 479, (414, 2024), 'night'),
"f_glassfish": ('玻璃鱼', '🟣', 'epic', 721, (798, 3848), 'day'),
"f_koi": ('锦鲤', '🔴', 'legend', 608, (606, 2936), 'night'),
"f_whiptail_catfish": ('鞭尾鲶鱼', '🔴', 'legend', 902, (1150, 5520), 'day'),
"f_polka_dot_stingray": ('斑点魟', '🟡', 'myth', 1005, (1374, 6584), 'night'),
"f_carp": ('鲤鱼', '⚪', 'common', 40, (34, 219), 'day'),
"f_redtail_catfish": ('红尾鲶鱼', '⚪', 'common', 48, (36, 228), 'night'),
"f_walleye": ('大眼梭鲈', '⚪', 'common', 56, (38, 238), 'day'),
"f_green_sunfish": ('绿太阳鱼', '⚪', 'common', 56, (38, 238), 'night'),
"f_mudskipper": ('弹涂鱼', '⚪', 'common', 68, (42, 257), 'day'),
"f_northern_pike": ('白斑狗鱼', '⚪', 'common', 79, (46, 276), 'night'),
"f_perch": ('鲈鱼', '⚪', 'common', 98, (54, 314), 'day'),
"f_rainbow_trout": ('虹鳟', '⚪', 'common', 115, (62, 352), 'night'),
"f_snakehead": ('乌鳢', '⚪', 'common', 144, (78, 428), 'day'),
"f_sockeye_salmon": ('红鲑鱼', '⚪', 'common', 170, (94, 504), 'night'),
"f_bullhead_catfish": ('大头鲶鱼', '⚪', 'common', 214, (126, 656), 'day'),
"f_tiger_trout": ('虎纹鳟', '⚪', 'common', 252, (158, 808), 'night'),
"f_axolotl": ('六角恐龙', '🟢', 'rare', 115, (62, 352), 'day'),
"f_tequila_splitfin": ('龙舌兰剑鱼', '🟢', 'rare', 170, (94, 504), 'night'),
"f_ghost_knifefish": ('幽灵刀鱼', '🟢', 'rare', 214, (126, 656), 'day'),
"f_fire_eel": ('火鳗', '🟢', 'rare', 252, (158, 808), 'night'),
"f_assassin_snail": ('杀手螺', '🟢', 'rare', 319, (222, 1112), 'day'),
"f_ornate_bichir": ('花恐龙鱼', '🟢', 'rare', 404, (318, 1568), 'night'),
"f_piranha": ('食人鱼', '🟢', 'rare', 479, (414, 2024), 'day'),
"f_african_butterflyfish": ('非洲蝴蝶鱼', '🟣', 'epic', 364, (270, 1340), 'night'),
"f_amazon_leaffish": ('亚马逊枯叶鱼', '🟣', 'epic', 524, (478, 2328), 'day'),
"f_alligator_gar": ('鳄雀鳝', '🟣', 'epic', 608, (606, 2936), 'night'),
"f_sturgeon": ('鲟鱼', '🟣', 'epic', 739, (830, 4000), 'day'),
"f_arowana": ('龙鱼', '🔴', 'legend', 721, (798, 3848), 'night'),
"f_arapaima": ('巨骨舌鱼', '🔴', 'legend', 902, (1150, 5520), 'day'),
"f_paddlefish": ('匙吻鲟', '🟡', 'myth', 1005, (1374, 6584), 'night'),
"f_clownfish": ('小丑鱼', '⚪', 'common', 40, (34, 219), 'day'),
"f_exquisite_fairy_wrasse": ('仙女隆头鱼', '⚪', 'common', 56, (38, 238), 'night'),
"f_copperband_butterflyfish": ('黄铜蝴蝶鱼', '⚪', 'common', 68, (42, 257), 'day'),
"f_falco_hawkfish": ('福尔科鹰鱼', '⚪', 'common', 68, (42, 257), 'night'),
"f_blue_tang": ('蓝吊', '⚪', 'common', 79, (46, 276), 'day'),
"f_moorish_idol": ('镰鱼', '⚪', 'common', 98, (54, 314), 'night'),
"f_neopercularis_hogfish": ('拟须髯鱼', '⚪', 'common', 115, (62, 352), 'day'),
"f_pufferfish": ('河豚', '⚪', 'common', 130, (70, 390), 'night'),
"f_ruby_red_dragonet": ('红色小黄瓜鱼', '⚪', 'common', 144, (78, 428), 'day'),
"f_spotted_ribbonfish": ('斑点带鱼', '⚪', 'common', 170, (94, 504), 'night'),
"f_triggerfish": ('扳机鱼', '⚪', 'common', 193, (110, 580), 'day'),
"f_flame_angelfish": ('火焰神仙鱼', '⚪', 'common', 252, (158, 808), 'night'),
"f_banggai_cardinalfish": ('邦盖天竺鲷', '🟢', 'rare', 144, (78, 428), 'day'),
"f_mandarinfish": ('麒麟鱼', '🟢', 'rare', 170, (94, 504), 'night'),
"f_firefish_goby": ('火虾虎鱼', '🟢', 'rare', 214, (126, 656), 'day'),
"f_lyretail_anthias": ('燕尾花鲈', '🟢', 'rare', 252, (158, 808), 'night'),
"f_royal_gramma": ('皇家格瑞马', '🟢', 'rare', 287, (190, 960), 'day'),
"f_moray_eel": ('海鳝', '🟢', 'rare', 319, (222, 1112), 'night'),
"f_achilles_tang": ('阿喀琉斯吊', '🟢', 'rare', 479, (414, 2024), 'day'),
"f_black_tang": ('黑吊', '🟣', 'epic', 319, (222, 1112), 'night'),
"f_cowfish": ('牛角鲀', '🟣', 'epic', 377, (286, 1416), 'day'),
"f_banded_coral_shrimp": ('条纹珊瑚虾', '🟣', 'epic', 608, (606, 2936), 'night'),
"f_sea_slug": ('海蛞蝓', '🟣', 'epic', 618, (622, 3012), 'day'),
"f_lionfish": ('狮子鱼', '🔴', 'legend', 524, (478, 2328), 'night'),
"f_frogfish": ('躄鱼', '🔴', 'legend', 902, (1150, 5520), 'day'),
"f_masked_angelfish": ('面具神仙鱼', '🟡', 'myth', 1005, (1374, 6584), 'night'),
"f_bluefin_trevally": ('蓝鳍鲹', '⚪', 'common', 48, (36, 228), 'day'),
"f_seahorse": ('海马', '⚪', 'common', 56, (38, 238), 'night'),
"f_flounder": ('比目鱼', '⚪', 'common', 62, (40, 247), 'day'),
"f_longfin_batfish": ('长鳍蝙蝠鱼', '⚪', 'common', 79, (46, 276), 'night'),
"f_nassau_grouper": ('拿骚石斑', '⚪', 'common', 98, (54, 314), 'day'),
"f_pacific_lookdown": ('太平洋望星鱼', '⚪', 'common', 115, (62, 352), 'night'),
"f_bluespine_unicornfish": ('蓝角鼻鱼', '⚪', 'common', 144, (78, 428), 'day'),
"f_garibaldi": ('加里波第鱼', '⚪', 'common', 144, (78, 428), 'night'),
"f_hogfish": ('猪头鱼', '⚪', 'common', 214, (126, 656), 'day'),
"f_surge_wrasse": ('涌浪隆头鱼', '⚪', 'common', 214, (126, 656), 'night'),
"f_rock_greenling": ('岩黄盖鱼', '⚪', 'common', 252, (158, 808), 'day'),
"f_sea_bass": ('海鲈鱼', '⚪', 'common', 319, (222, 1112), 'night'),
"f_viperfish": ('蝰鱼', '🟢', 'rare', 170, (94, 504), 'day'),
"f_chinese_trumpetfish": ('中华管口鱼', '🟢', 'rare', 252, (158, 808), 'night'),
"f_lumpfish": ('圆鳍鱼', '🟢', 'rare', 252, (158, 808), 'day'),
"f_ghost_pipefish": ('鬼管鱼', '🟢', 'rare', 319, (222, 1112), 'night'),
"f_red_snapper": ('红鲷鱼', '🟢', 'rare', 404, (318, 1568), 'day'),
"f_scaly_foot_snail": ('鳞足螺', '🟢', 'rare', 479, (414, 2024), 'night'),
"f_lancetfish": ('帆蜥鱼', '🟢', 'rare', 479, (414, 2024), 'day'),
"f_gulper_eel": ('吞噬鳗', '🟣', 'epic', 319, (222, 1112), 'night'),
"f_mantis_shrimp": ('螳螂虾', '🟣', 'epic', 479, (414, 2024), 'day'),
"f_scorpionfish": ('蝎子鱼', '🟣', 'epic', 608, (606, 2936), 'night'),
"f_barred_knifejaw": ('条石鲷', '🟣', 'epic', 721, (798, 3848), 'day'),
"f_leafy_seadragon": ('叶海龙', '🔴', 'legend', 721, (798, 3848), 'night'),
"f_blobfish": ('水滴鱼', '🔴', 'legend', 902, (1150, 5520), 'day'),
"f_saw_shark": ('锯鲨', '🟡', 'myth', 1005, (1374, 6584), 'night'),
"f_swordfish": ('剑鱼', '⚪', 'common', 56, (38, 238), 'day'),
"f_atlantic_tarpon": ('大西洋大海鲢', '⚪', 'common', 79, (46, 276), 'night'),
"f_barracuda": ('梭鱼', '⚪', 'common', 98, (54, 314), 'day'),
"f_california_sheephead": ('加州羊头鱼', '⚪', 'common', 115, (62, 352), 'night'),
"f_halibut": ('大比目鱼', '⚪', 'common', 130, (70, 390), 'day'),
"f_yellowfin_tuna": ('黄鳍金枪鱼', '⚪', 'common', 170, (94, 504), 'night'),
"f_wahoo": ('刺鲅', '⚪', 'common', 181, (102, 542), 'day'),
"f_giant_trevally": ('巨型鲹', '⚪', 'common', 214, (126, 656), 'night'),
"f_mahi_mahi": ('鬼头刀鱼', '⚪', 'common', 252, (158, 808), 'day'),
"f_lingcod": ('龙趸鱼', '⚪', 'common', 252, (158, 808), 'night'),
"f_goliath_grouper": ('伊氏石斑', '⚪', 'common', 319, (222, 1112), 'day'),
"f_mola_mola": ('翻车鱼', '⚪', 'common', 404, (318, 1568), 'night'),
"f_humphead_parrotfish": ('隆头鹦哥', '🟢', 'rare', 252, (158, 808), 'day'),
"f_blue_marlin": ('蓝枪鱼', '🟢', 'rare', 319, (222, 1112), 'night'),
"f_giant_pacific_octopus": ('太平洋巨型章鱼', '🟢', 'rare', 319, (222, 1112), 'day'),
"f_wolf_eel": ('狼鳗', '🟢', 'rare', 479, (414, 2024), 'night'),
"f_manta_ray": ('魔鬼鱼', '🟢', 'rare', 479, (414, 2024), 'day'),
"f_oarfish": ('皇带鱼', '🟢', 'rare', 608, (606, 2936), 'night'),
"f_moonfish": ('月亮鱼', '🟢', 'rare', 608, (606, 2936), 'day'),
"f_great_white_shark": ('大白鲨', '🟣', 'epic', 479, (414, 2024), 'night'),
"f_hammerhead_shark": ('锤头鲨', '🟣', 'epic', 608, (606, 2936), 'day'),
"f_basking_shark": ('姥鲨', '🟣', 'epic', 721, (798, 3848), 'night'),
"f_whale_shark": ('鲸鲨', '🟣', 'epic', 823, (990, 4760), 'day'),
"f_coelacanth": ('矛尾鱼', '🔴', 'legend', 902, (1150, 5520), 'night'),
"f_giant_squid": ('大王乌贼', '🔴', 'legend', 990, (1342, 6432), 'day'),
"f_colossal_squid": ('大王酸浆鱿', '🟡', 'myth', 1005, (1374, 6584), 'night'),
"f_killifish": ('鳉鱼', '⚪', 'common', 68, (42, 257), 'day'),
"f_kuhli_loach": ('蛇仔鱼', '⚪', 'common', 79, (46, 276), 'night'),
"f_rummy_nose_tetra": ('红鼻剪刀鱼', '⚪', 'common', 89, (50, 295), 'day'),
"f_celestial_pearl_danio": ('银河斑马鱼', '⚪', 'common', 98, (54, 314), 'night'),
"f_pea_puffer": ('迷你河豚', '⚪', 'common', 115, (62, 352), 'day'),
"f_rainbowfish": ('彩虹鱼', '⚪', 'common', 144, (78, 428), 'night'),
"f_african_cichlid": ('非洲慈鲷', '⚪', 'common', 157, (86, 466), 'day'),
"f_hatchetfish": ('斧头鱼', '⚪', 'common', 170, (94, 504), 'night'),
"f_halfbeak": ('水针鱼', '⚪', 'common', 193, (110, 580), 'day'),
"f_panda_garras": ('熊猫墨头鱼', '⚪', 'common', 214, (126, 656), 'night'),
"f_horse_face_loach": ('马脸鳅', '⚪', 'common', 252, (158, 808), 'day'),
"f_otocinclus_catfish": ('小精灵吸鳅', '⚪', 'common', 319, (222, 1112), 'night'),
"f_pictus_catfish": ('花皮鲶鱼', '🟢', 'rare', 115, (62, 352), 'day'),
"f_peacock_bass": ('孔雀鲈', '🟢', 'rare', 170, (94, 504), 'night'),
"f_dwarf_cichlid": ('矮慈鲷', '🟢', 'rare', 252, (158, 808), 'day'),
"f_giant_barb": ('巨鲅鱼', '🟢', 'rare', 319, (222, 1112), 'night'),
"f_kijimuna_goby": ('基吉木虾虎', '🟢', 'rare', 404, (318, 1568), 'day'),
"f_threadfin_rainbowfish": ('丝鳍彩虹鱼', '🟢', 'rare', 479, (414, 2024), 'night'),
"f_ninja_woodcat": ('忍者木鲶', '🟢', 'rare', 608, (606, 2936), 'day'),
"f_tiger_shovelnose_catfish": ('虎纹铲鼻鲶', '🟣', 'epic', 608, (606, 2936), 'night'),
"f_upside_down_catfish": ('倒游鲶鱼', '🟣', 'epic', 721, (798, 3848), 'day'),
"f_payara": ('虎视鲶', '🟣', 'epic', 823, (990, 4760), 'night'),
"f_ranchu_goldfish": ('兰寿金鱼', '🟣', 'epic', 917, (1182, 5672), 'day'),
"f_australian_lungfish": ('澳洲肺鱼', '🔴', 'legend', 1005, (1374, 6584), 'night'),
"f_king_salmon": ('帝王鲑', '🔴', 'legend', 1167, (1758, 8408), 'day'),
"f_devils_hole_pupfish": ('魔泉鱂鱼', '🟡', 'myth', 1242, (1950, 9320), 'night'),
"f_bluelined_dottyback": ('蓝线彩点鱼', '⚪', 'common', 115, (62, 352), 'day'),
"f_blue_chromis": ('蓝魔', '⚪', 'common', 144, (78, 428), 'night'),
"f_barred_hamlet": ('条纹哈姆鱼', '⚪', 'common', 157, (86, 466), 'day'),
"f_flame_hawkfish": ('火焰鹰鱼', '⚪', 'common', 170, (94, 504), 'night'),
"f_v_tail_grouper": ('V尾石斑', '⚪', 'common', 181, (102, 542), 'day'),
"f_rainbow_goby": ('彩虹虾虎', '⚪', 'common', 193, (110, 580), 'night'),
"f_yellow_stripe_clingfish": ('黄条纹吸附鱼', '⚪', 'common', 214, (126, 656), 'day'),
"f_indian_ocean_sweetlips": ('印度洋甜唇鱼', '⚪', 'common', 252, (158, 808), 'night'),
"f_golden_stripe_soapfish": ('金纹香鱼', '⚪', 'common', 319, (222, 1112), 'day'),
"f_harlequin_bass": ('彩格鲈', '⚪', 'common', 377, (286, 1416), 'night'),
"f_saddle_grouper": ('鞍斑石斑', '⚪', 'common', 479, (414, 2024), 'day'),
"f_horseshoe_filefish": ('马蹄扳机鱼', '⚪', 'common', 608, (606, 2936), 'night'),
"f_redlined_tilefish": ('红线瓦鲽', '🟢', 'rare', 243, (150, 770), 'day'),
"f_azure_damsel": ('蔚蓝雀鲷', '🟢', 'rare', 319, (222, 1112), 'night'),
"f_harlequin_tuskfish": ('彩妆鱼', '🟢', 'rare', 349, (254, 1264), 'day'),
"f_blue_assessor": ('蓝婆罗鲷', '🟢', 'rare', 377, (286, 1416), 'night'),
"f_sailfin_blenny": ('帆鳍鳚', '🟢', 'rare', 479, (414, 2024), 'day'),
"f_goatfish": ('羊鱼', '🟢', 'rare', 479, (414, 2024), 'night'),
"f_domino_damselfish": ('圆点雀鲷', '🟢', 'rare', 608, (606, 2936), 'day'),
"f_flasher_wrasse": ('闪光隆头鱼', '🟣', 'epic', 567, (542, 2632), 'night'),
"f_orange_spotted_filefish": ('橙点扳机鱼', '🟣', 'epic', 721, (798, 3848), 'day'),
"f_emperor_angelfish": ('帝王神仙鱼', '🟣', 'epic', 823, (990, 4760), 'night'),
"f_emperor_snapper": ('帝王笛鲷', '🟣', 'epic', 917, (1182, 5672), 'day'),
"f_royal_angelfish": ('皇家神仙鱼', '🔴', 'legend', 1019, (1406, 6736), 'night'),
"f_sailfin_snapper": ('帆鳍笛鲷', '🔴', 'legend', 1242, (1950, 9320), 'day'),
"f_french_angelfish": ('法国神仙鱼', '🟡', 'myth', 1315, (2142, 10232), 'night'),
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
    for _ in range(luck_lv):             # 海之眷顾：神话提升
        _shift(w, "myth", 0.02)
    s = sum(w.values()) or 1.0
    return {k: v / s for k, v in w.items()}


def hook_rate(float_lv, lure_lv):
    """上钩率：基础 85% + 鱼漂加成 + 饵钓附魔（每级 +5%）。"""
    base = 0.85 + FLOATS.get(float_lv, FLOATS[1])[2] + lure_lv * 0.05
    return min(base, 1.0)


def _maybe_gold(rng, fish):
    """黄金鱼判定：独立于稀有度的专属词条，极小概率（GOLD_CHANCE=1%）触发。"""
    if rng.random() < GOLD_CHANCE:
        fish = dict(fish)
        fish["gold"] = True
        fish["name"] = GOLD_PREFIX + fish["name"]
        fish["value"] = int(fish["value"] * GOLD_MULT)
    return fish


def roll_fish(rod, has_bait=None, rng=None, hook_lv=1, line_lv=1, fortune_lv=0, luck_lv=0):
    """抽取一条鱼。返回 dict {id,name,emoji,rarity,weight_g,value,gold?}。"""
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
    fish = {"id": fid, "name": name, "emoji": emoji,
            "rarity": rr, "weight_g": wg, "value": value}
    return _maybe_gold(rng, fish)


def fmt_weight(g):
    return f"{g/1000:g}kg" if g >= 1000 else f"{g}g"


# ---------- 扭蛋（喵币回收口） ----------
# 稀有度严格递减：普通 > 稀有 > 史诗 > 传说 > 神话；史诗概率由 24% 下调至 13%
GACHA_CHANCE = {"common": 0.50, "rare": 0.30, "epic": 0.13, "legend": 0.05, "myth": 0.02}


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
    fish = {"id": fid, "name": name, "emoji": emoji,
            "rarity": rr, "weight_g": wg, "value": base + int(wg / 8)}
    return _maybe_gold(rng, fish)