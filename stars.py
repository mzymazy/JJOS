# -*- coding: utf-8 -*-
"""全球著名影星头像库：内置名单 + 本地抓图补档。

定位：给主体库（subjects）预置一份「世界级影星」名录，名字与代表作离线可用，
缺参考图时按名字自动抓单人正脸图落到 ComfyUI input/_subjects/stars/<key>/，
再挂进 subjects.json 的 refs，生成时用 @影星名 直接锚定。

边界：
  - 抓到的图片一律留在本机，不进版本库、不入发布包（见 PRIVACY.md）。
  - 本文件只内置「名字 + 国别 + 代表作」这类事实性文本，不含任何人像素材。
  - desc 只写可核实的事实（性别 / 国别 / 代表作），不编造五官与身材描述。

对外接口（供 app/routes/media.py 调用）：
    items()                      名片清单（不含本地状态）
    find(key)                    按 key / 中文名 / 英文名 查一条
    status()                     清单 + 本地档案状态（有图? 几张? 原图是否还在）
    sync(...)                    批量抓图入库，逐人返回结果
    prefill_dict()               把中英名灌进 data/celeb.json，供提示词占位符识别
"""

import json
import os
import time

import webimg as WEB

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CELEB_PATH = os.path.join(DATA_DIR, "celeb.json")
SUB_DIR_NAME = "stars"          # _subjects/stars/<key>/

# key 用拉丁短名，做目录名与 @ 名称的稳定锚；zh 是界面与提示词用的主名。
STARS = [
    # ---------------- 好莱坞 男
    {"k": "tom_cruise", "zh": "汤姆·克鲁斯", "en": "Tom Cruise", "c": "美国", "w": "《壮志凌云》《碟中谍》"},
    {"k": "leonardo", "zh": "莱昂纳多·迪卡普里奥", "en": "Leonardo DiCaprio", "c": "美国", "w": "《泰坦尼克号》《盗梦空间》"},
    {"k": "brad_pitt", "zh": "布拉德·皮特", "en": "Brad Pitt", "c": "美国", "w": "《搏击俱乐部》《史密斯夫妇》"},
    {"k": "johnny_depp", "zh": "约翰尼·德普", "en": "Johnny Depp", "c": "美国", "w": "《加勒比海盗》《剪刀手爱德华》"},
    {"k": "rdj", "zh": "小罗伯特·唐尼", "en": "Robert Downey Jr.", "c": "美国", "w": "《钢铁侠》《复仇者联盟》"},
    {"k": "tom_hanks", "zh": "汤姆·汉克斯", "en": "Tom Hanks", "c": "美国", "w": "《阿甘正传》《拯救大兵瑞恩》"},
    {"k": "morgan_freeman", "zh": "摩根·弗里曼", "en": "Morgan Freeman", "c": "美国", "w": "《肖申克的救赎》《七宗罪》"},
    {"k": "will_smith", "zh": "威尔·史密斯", "en": "Will Smith", "c": "美国", "w": "《独立日》《当幸福来敲门》"},
    {"k": "keanu", "zh": "基努·里维斯", "en": "Keanu Reeves", "c": "加拿大", "w": "《黑客帝国》《疾速追杀》"},
    {"k": "nicolas_cage", "zh": "尼古拉斯·凯奇", "en": "Nicolas Cage", "c": "美国", "w": "《变脸》《国家宝藏》"},
    {"k": "matt_damon", "zh": "马特·达蒙", "en": "Matt Damon", "c": "美国", "w": "《谍影重重》《火星救援》"},
    {"k": "christian_bale", "zh": "克里斯蒂安·贝尔", "en": "Christian Bale", "c": "英国", "w": "《蝙蝠侠：黑暗骑士》"},
    {"k": "hugh_jackman", "zh": "休·杰克曼", "en": "Hugh Jackman", "c": "澳大利亚", "w": "《金刚狼》《悲惨世界》"},
    {"k": "tom_hardy", "zh": "汤姆·哈迪", "en": "Tom Hardy", "c": "英国", "w": "《毒液》《疯狂的麦克斯4》"},
    {"k": "tom_holland", "zh": "汤姆·赫兰德", "en": "Tom Holland", "c": "英国", "w": "《蜘蛛侠》系列"},
    {"k": "the_rock", "zh": "道恩·强森", "en": "Dwayne Johnson", "c": "美国", "w": "《速度与激情》系列"},
    {"k": "ryan_gosling", "zh": "瑞恩·高斯林", "en": "Ryan Gosling", "c": "加拿大", "w": "《爱乐之城》《芭比》"},
    {"k": "joaquin", "zh": "华金·菲尼克斯", "en": "Joaquin Phoenix", "c": "美国", "w": "《小丑》《角斗士》"},
    # ---------------- 好莱坞 女
    {"k": "scarlett", "zh": "斯嘉丽·约翰逊", "en": "Scarlett Johansson", "c": "美国", "w": "《黑寡妇》《迷失东京》"},
    {"k": "angelina", "zh": "安吉丽娜·朱莉", "en": "Angelina Jolie", "c": "美国", "w": "《古墓丽影》《史密斯夫妇》"},
    {"k": "natalie", "zh": "娜塔莉·波特曼", "en": "Natalie Portman", "c": "美国", "w": "《这个杀手不太冷》《黑天鹅》"},
    {"k": "emma_watson", "zh": "艾玛·沃特森", "en": "Emma Watson", "c": "英国", "w": "《哈利·波特》系列"},
    {"k": "anne_hathaway", "zh": "安妮·海瑟薇", "en": "Anne Hathaway", "c": "美国", "w": "《穿普拉达的女王》《星际穿越》"},
    {"k": "jennifer_lawrence", "zh": "詹妮弗·劳伦斯", "en": "Jennifer Lawrence", "c": "美国", "w": "《饥饿游戏》系列"},
    {"k": "charlize", "zh": "查理兹·塞隆", "en": "Charlize Theron", "c": "南非", "w": "《疯狂的麦克斯4》《女魔头》"},
    {"k": "gal_gadot", "zh": "盖尔·加朵", "en": "Gal Gadot", "c": "以色列", "w": "《神奇女侠》"},
    {"k": "margot", "zh": "玛格特·罗比", "en": "Margot Robbie", "c": "澳大利亚", "w": "《芭比》《自杀小队》"},
    {"k": "emma_stone", "zh": "艾玛·斯通", "en": "Emma Stone", "c": "美国", "w": "《爱乐之城》《可怜的东西》"},
    {"k": "nicole_kidman", "zh": "妮可·基德曼", "en": "Nicole Kidman", "c": "澳大利亚", "w": "《红磨坊》《时时刻刻》"},
    {"k": "julia_roberts", "zh": "朱莉娅·罗伯茨", "en": "Julia Roberts", "c": "美国", "w": "《诺丁山》《永不妥协》"},
    {"k": "kate_winslet", "zh": "凯特·温斯莱特", "en": "Kate Winslet", "c": "英国", "w": "《泰坦尼克号》《朗读者》"},
    {"k": "meryl", "zh": "梅丽尔·斯特里普", "en": "Meryl Streep", "c": "美国", "w": "《穿普拉达的女王》《铁娘子》"},
    # ---------------- 华语
    {"k": "jackie_chan", "zh": "成龙", "en": "Jackie Chan", "c": "中国香港", "w": "《警察故事》《尖峰时刻》"},
    {"k": "jet_li", "zh": "李连杰", "en": "Jet Li", "c": "中国", "w": "《黄飞鸿》《英雄》"},
    {"k": "chow_yunfat", "zh": "周润发", "en": "Chow Yun-fat", "c": "中国香港", "w": "《英雄本色》《卧虎藏龙》"},
    {"k": "andy_lau", "zh": "刘德华", "en": "Andy Lau", "c": "中国香港", "w": "《无间道》《天下无贼》"},
    {"k": "tony_leung", "zh": "梁朝伟", "en": "Tony Leung", "c": "中国香港", "w": "《花样年华》《无间道》"},
    {"k": "stephen_chow", "zh": "周星驰", "en": "Stephen Chow", "c": "中国香港", "w": "《功夫》《少林足球》"},
    {"k": "zhang_ziyi", "zh": "章子怡", "en": "Zhang Ziyi", "c": "中国", "w": "《卧虎藏龙》《一代宗师》"},
    {"k": "gong_li", "zh": "巩俐", "en": "Gong Li", "c": "中国", "w": "《霸王别姬》《满城尽带黄金甲》"},
    {"k": "tang_wei", "zh": "汤唯", "en": "Tang Wei", "c": "中国", "w": "《色·戒》《晚秋》"},
    {"k": "michelle_yeoh", "zh": "杨紫琼", "en": "Michelle Yeoh", "c": "马来西亚", "w": "《瞬息全宇宙》《卧虎藏龙》"},
    {"k": "liu_yifei", "zh": "刘亦菲", "en": "Liu Yifei", "c": "中国", "w": "《花木兰》《梦华录》"},
    {"k": "louis_koo", "zh": "古天乐", "en": "Louis Koo", "c": "中国香港", "w": "《窃听风云》《扫毒》"},
    # ---------------- 日韩印
    {"k": "kimura", "zh": "木村拓哉", "en": "Takuya Kimura", "c": "日本", "w": "《悠长假期》《HERO》"},
    {"k": "ishihara", "zh": "石原里美", "en": "Satomi Ishihara", "c": "日本", "w": "《校对女孩》《失恋巧克力职人》"},
    {"k": "song_hyekyo", "zh": "宋慧乔", "en": "Song Hye-kyo", "c": "韩国", "w": "《太阳的后裔》《黑暗荣耀》"},
    {"k": "jun_jihyun", "zh": "全智贤", "en": "Jun Ji-hyun", "c": "韩国", "w": "《来自星星的你》《我的野蛮女友》"},
    {"k": "aamir", "zh": "阿米尔·汗", "en": "Aamir Khan", "c": "印度", "w": "《三傻大闹宝莱坞》《摔跤吧！爸爸》"},
    {"k": "srk", "zh": "沙鲁克·汗", "en": "Shah Rukh Khan", "c": "印度", "w": "《宝莱坞生死恋》《我的名字叫可汗》"},

    # ================ 第二批（g 字段显式声明性别） ================
    # ---------------- 好莱坞 男
    {"k": "george_clooney", "zh": "乔治·克鲁尼", "en": "George Clooney", "c": "美国", "w": "《十一罗汉》《在云端》", "g": "男"},
    {"k": "de_niro", "zh": "罗伯特·德尼罗", "en": "Robert De Niro", "c": "美国", "w": "《教父2》《出租车司机》", "g": "男"},
    {"k": "pacino", "zh": "阿尔·帕西诺", "en": "Al Pacino", "c": "美国", "w": "《教父》《闻香识女人》", "g": "男"},
    {"k": "denzel", "zh": "丹泽尔·华盛顿", "en": "Denzel Washington", "c": "美国", "w": "《训练日》《怒火救援》", "g": "男"},
    {"k": "sam_jackson", "zh": "塞缪尔·杰克逊", "en": "Samuel L. Jackson", "c": "美国", "w": "《低俗小说》《复仇者联盟》", "g": "男"},
    {"k": "bradley_cooper", "zh": "布莱德利·库珀", "en": "Bradley Cooper", "c": "美国", "w": "《宿醉》《一个明星的诞生》", "g": "男"},
    {"k": "ryan_reynolds", "zh": "瑞安·雷诺兹", "en": "Ryan Reynolds", "c": "加拿大", "w": "《死侍》《失控玩家》", "g": "男"},
    {"k": "statham", "zh": "杰森·斯坦森", "en": "Jason Statham", "c": "英国", "w": "《敢死队》《速度与激情》系列", "g": "男"},
    {"k": "vin_diesel", "zh": "范·迪塞尔", "en": "Vin Diesel", "c": "美国", "w": "《速度与激情》系列", "g": "男"},
    {"k": "chris_evans", "zh": "克里斯·埃文斯", "en": "Chris Evans", "c": "美国", "w": "《美国队长》《利刃出鞘》", "g": "男"},
    {"k": "hemsworth", "zh": "克里斯·海姆斯沃斯", "en": "Chris Hemsworth", "c": "澳大利亚", "w": "《雷神》《极速风流》", "g": "男"},
    {"k": "gylenhaal", "zh": "杰克·吉伦哈尔", "en": "Jake Gyllenhaal", "c": "美国", "w": "《断背山》《夜行者》", "g": "男"},
    {"k": "garfield", "zh": "安德鲁·加菲尔德", "en": "Andrew Garfield", "c": "英国", "w": "《蜘蛛侠：英雄归来》《血战钢锯岭》", "g": "男"},
    {"k": "cumberbatch", "zh": "本尼迪克特·康伯巴奇", "en": "Benedict Cumberbatch", "c": "英国", "w": "《神探夏洛克》《模仿游戏》", "g": "男"},
    {"k": "chalamet", "zh": "提莫西·查拉梅", "en": "Timothée Chalamet", "c": "美国", "w": "《沙丘》《请以你的名字呼唤我》", "g": "男"},
    {"k": "pedro_pascal", "zh": "佩德罗·帕斯卡", "en": "Pedro Pascal", "c": "智利", "w": "《曼达洛人》《最后生还者》", "g": "男"},
    {"k": "hiddleston", "zh": "汤姆·希德勒斯顿", "en": "Tom Hiddleston", "c": "英国", "w": "《雷神》《夜班经理》", "g": "男"},
    {"k": "jude_law", "zh": "裘德·洛", "en": "Jude Law", "c": "英国", "w": "《冷山》《大侦探福尔摩斯》", "g": "男"},
    {"k": "colin_firth", "zh": "科林·费尔斯", "en": "Colin Firth", "c": "英国", "w": "《国王的演讲》《王牌特工》", "g": "男"},
    {"k": "ewan", "zh": "伊万·麦克格雷格", "en": "Ewan McGregor", "c": "英国", "w": "《星球大战》《猜火车》", "g": "男"},
    {"k": "mads", "zh": "马兹·米克尔森", "en": "Mads Mikkelsen", "c": "丹麦", "w": "《狩猎》《奇异博士》", "g": "男"},
    {"k": "jean_reno", "zh": "让·雷诺", "en": "Jean Reno", "c": "法国", "w": "《这个杀手不太冷》《暗流》", "g": "男"},
    # ---------------- 好莱坞 女
    {"k": "zendaya", "zh": "赞达亚", "en": "Zendaya", "c": "美国", "w": "《沙丘》《亢奋》", "g": "女"},
    {"k": "florence_pugh", "zh": "佛罗伦斯·皮尤", "en": "Florence Pugh", "c": "英国", "w": "《小妇人》《奥本海默》", "g": "女"},
    {"k": "aniston", "zh": "詹妮弗·安妮斯顿", "en": "Jennifer Aniston", "c": "美国", "w": "《老友记》《冒牌家庭》", "g": "女"},
    {"k": "megan_fox", "zh": "梅根·福克斯", "en": "Megan Fox", "c": "美国", "w": "《变形金刚》《詹妮弗的肉体》", "g": "女"},
    {"k": "keira", "zh": "凯拉·奈特莉", "en": "Keira Knightley", "c": "英国", "w": "《加勒比海盗》《赎罪》", "g": "女"},
    {"k": "penelope", "zh": "佩内洛普·克鲁兹", "en": "Penélope Cruz", "c": "西班牙", "w": "《回归》《午夜巴塞罗那》", "g": "女"},
    {"k": "bellucci", "zh": "莫妮卡·贝鲁奇", "en": "Monica Bellucci", "c": "意大利", "w": "《西西里的美丽传说》《黑客帝国》", "g": "女"},
    {"k": "sophie_marceau", "zh": "苏菲·玛索", "en": "Sophie Marceau", "c": "法国", "w": "《初吻》《勇敢的心》", "g": "女"},
    {"k": "hepburn", "zh": "奥黛丽·赫本", "en": "Audrey Hepburn", "c": "英国", "w": "《罗马假日》《蒂凡尼的早餐》", "g": "女"},
    {"k": "monroe", "zh": "玛丽莲·梦露", "en": "Marilyn Monroe", "c": "美国", "w": "《七年之痒》《热情似火》", "g": "女"},
    {"k": "cate", "zh": "凯特·布兰切特", "en": "Cate Blanchett", "c": "澳大利亚", "w": "《蓝色茉莉》《指环王》", "g": "女"},
    {"k": "tilda", "zh": "蒂尔达·斯文顿", "en": "Tilda Swinton", "c": "英国", "w": "《康斯坦丁》《雪国列车》", "g": "女"},
    {"k": "julianne", "zh": "朱丽安·摩尔", "en": "Julianne Moore", "c": "美国", "w": "《依然爱丽丝》《不羁夜》", "g": "女"},
    # ---------------- 华语 男
    {"k": "leslie", "zh": "张国荣", "en": "Leslie Cheung", "c": "中国香港", "w": "《霸王别姬》《阿飞正传》", "g": "男"},
    {"k": "tony_leung_kf", "zh": "梁家辉", "en": "Tony Leung Ka-fai", "c": "中国香港", "w": "《寒战》《黑社会》", "g": "男"},
    {"k": "aaron_kwok", "zh": "郭富城", "en": "Aaron Kwok", "c": "中国香港", "w": "《寒战》《踏血寻梅》", "g": "男"},
    {"k": "sean_lau", "zh": "刘青云", "en": "Sean Lau", "c": "中国香港", "w": "《窃听风云》《神探》", "g": "男"},
    {"k": "wu_jing", "zh": "吴京", "en": "Wu Jing", "c": "中国", "w": "《战狼》《流浪地球》", "g": "男"},
    {"k": "huang_bo", "zh": "黄渤", "en": "Huang Bo", "c": "中国", "w": "《疯狂的石头》《一出好戏》", "g": "男"},
    {"k": "shen_teng", "zh": "沈腾", "en": "Shen Teng", "c": "中国", "w": "《夏洛特烦恼》《独行月球》", "g": "男"},
    {"k": "deng_chao", "zh": "邓超", "en": "Deng Chao", "c": "中国", "w": "《烈日灼心》《美人鱼》", "g": "男"},
    {"k": "chen_kun", "zh": "陈坤", "en": "Chen Kun", "c": "中国", "w": "《画皮》《寻龙诀》", "g": "男"},
    {"k": "hu_ge", "zh": "胡歌", "en": "Hu Ge", "c": "中国", "w": "《琅琊榜》《南方车站的聚会》", "g": "男"},
    {"k": "jackson_yee", "zh": "易烊千玺", "en": "Jackson Yee", "c": "中国", "w": "《少年的你》《长津湖》", "g": "男"},
    {"k": "chang_chen", "zh": "张震", "en": "Chang Chen", "c": "中国台湾", "w": "《一代宗师》《牯岭街少年杀人事件》", "g": "男"},
    {"k": "eddie_peng", "zh": "彭于晏", "en": "Eddie Peng", "c": "中国台湾", "w": "《翻滚吧！阿信》《邪不压正》", "g": "男"},
    # ---------------- 华语 女
    {"k": "maggie_cheung", "zh": "张曼玉", "en": "Maggie Cheung", "c": "中国香港", "w": "《花样年华》《阮玲玉》", "g": "女"},
    {"k": "brigitte_lin", "zh": "林青霞", "en": "Brigitte Lin", "c": "中国台湾", "w": "《东方不败》《滚滚红尘》", "g": "女"},
    {"k": "joey_wong", "zh": "王祖贤", "en": "Joey Wong", "c": "中国香港", "w": "《倩女幽魂》《青蛇》", "g": "女"},
    {"k": "shu_qi", "zh": "舒淇", "en": "Shu Qi", "c": "中国台湾", "w": "《最好的时光》《非诚勿扰》", "g": "女"},
    {"k": "fan_bingbing", "zh": "范冰冰", "en": "Fan Bingbing", "c": "中国", "w": "《我不是潘金莲》《观音山》", "g": "女"},
    {"k": "zhou_xun", "zh": "周迅", "en": "Zhou Xun", "c": "中国", "w": "《如果·爱》《李米的猜想》", "g": "女"},
    {"k": "zhao_liying", "zh": "赵丽颖", "en": "Zhao Liying", "c": "中国", "w": "《花千骨》《知否知否应是绿肥红瘦》", "g": "女"},
    {"k": "yang_mi", "zh": "杨幂", "en": "Yang Mi", "c": "中国", "w": "《三生三世十里桃花》《小时代》", "g": "女"},
    {"k": "dilraba", "zh": "迪丽热巴", "en": "Dilraba Dilmurat", "c": "中国", "w": "《三生三世枕上书》《长歌行》", "g": "女"},
    # ---------------- 日韩
    {"k": "gong_yoo", "zh": "孔刘", "en": "Gong Yoo", "c": "韩国", "w": "《釜山行》《孤单又灿烂的神-鬼怪》", "g": "男"},
    {"k": "song_joongki", "zh": "宋仲基", "en": "Song Joong-ki", "c": "韩国", "w": "《太阳的后裔》《狼少年》", "g": "男"},
    {"k": "lee_jungjae", "zh": "李政宰", "en": "Lee Jung-jae", "c": "韩国", "w": "《鱿鱼游戏》《新世界》", "g": "男"},
    {"k": "hyun_bin", "zh": "玄彬", "en": "Hyun Bin", "c": "韩国", "w": "《爱的迫降》《秘密花园》", "g": "男"},
    {"k": "abe_hiroshi", "zh": "阿部宽", "en": "Hiroshi Abe", "c": "日本", "w": "《步履不停》《龙樱》", "g": "男"},
    {"k": "oguri_shun", "zh": "小栗旬", "en": "Shun Oguri", "c": "日本", "w": "《银魂》《花样男子》", "g": "男"},
    {"k": "aragaki", "zh": "新垣结衣", "en": "Yui Aragaki", "c": "日本", "w": "《恋空》《逃避虽可耻但有用》", "g": "女"},
    {"k": "nagasawa", "zh": "长泽雅美", "en": "Masami Nagasawa", "c": "日本", "w": "《海街日记》《求婚大作战》", "g": "女"},
    {"k": "kim_taeri", "zh": "金泰梨", "en": "Kim Tae-ri", "c": "韩国", "w": "《小姐》《二十五，二十一》", "g": "女"},
    {"k": "lee_youngae", "zh": "李英爱", "en": "Lee Young-ae", "c": "韩国", "w": "《大长今》《亲切的金子》", "g": "女"},
    # ---------------- 印度及其他
    {"k": "salman", "zh": "萨尔曼·汗", "en": "Salman Khan", "c": "印度", "w": "《小萝莉的猴神大叔》《 Sultan》", "g": "男"},
    {"k": "deepika", "zh": "迪皮卡·帕度柯妮", "en": "Deepika Padukone", "c": "印度", "w": "《帕德玛瓦蒂王后》《极限特工3》", "g": "女"},
]

_BY_KEY = {s["k"]: s for s in STARS}


def _gender_desc(s):
    # 新增条目一律带 g 字段；早期条目靠 key 白名单兜底，避免提示词里出现错性别
    g = s.get("g")
    if g in ("男", "女"):
        return g
    male = ("tom_cruise leonardo brad_pitt johnny_depp rdj tom_hanks morgan_freeman will_smith keanu "
            "nicolas_cage matt_damon christian_bale hugh_jackman tom_hardy tom_holland the_rock "
            "ryan_gosling joaquin jackie_chan jet_li chow_yunfat andy_lau tony_leung stephen_chow "
            "louis_koo kimura aamir srk").split()
    return "男" if s["k"] in male else "女"


def desc_of(s):
    return "%s，%s演员，代表作%s" % (_gender_desc(s), s["c"], s["w"])


def items():
    out = []
    for s in STARS:
        d = dict(s)
        d["desc"] = desc_of(s)
        out.append(d)
    return out


def find(name):
    if not name:
        return None
    q = name.strip()
    if q in _BY_KEY:
        return _BY_KEY[q]
    for s in STARS:
        if q in (s["zh"], s["en"]) or q.replace("·", "") == s["zh"].replace("·", ""):
            return s
        if q.lower() == s["en"].lower():
            return s
    return None


def _subjects_db():
    try:
        with open(os.path.join(DATA_DIR, "subjects.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": []}


def _input_root():
    try:
        from app.config import COMFY_INPUT
        return COMFY_INPUT
    except Exception:
        return ""


def _abs_ref(ref):
    """把 _subjects/... 这类相对 ComfyUI input 的 ref 还原成绝对路径，供本机预览。"""
    root = _input_root()
    if not root or not ref:
        return ""
    rel = ref.replace("/", os.sep).lstrip(os.sep)
    return os.path.join(root, rel)


def status(store=None):
    """清单 + 本地档案状态。store 传 app.store 模块时用它读，避免重复造轮子。"""
    db = store.subjects() if store is not None else _subjects_db()
    by_name = {}
    for it in db.get("items", []):
        by_name.setdefault((it.get("name") or "").strip(), it)
    out, ready = [], 0
    for s in items():
        it = by_name.get(s["zh"])
        refs = [r for r in ((it or {}).get("refs") or []) if r]
        if it:
            ready += 1
        s["subject_id"] = (it or {}).get("id", "")
        s["refs"] = refs
        s["ref_count"] = len(refs)
        s["ref_files"] = [_abs_ref(r) for r in refs]
        s["in_library"] = bool(it)
        s["dir"] = "%s/%s" % (SUB_DIR_NAME, s["k"])
        out.append(s)
    return {"total": len(out), "in_library": ready, "stars": out}


def _dest_dir(comfy_input, key):
    return os.path.join(comfy_input, "_subjects", SUB_DIR_NAME, key)


def _official(s, dest, ref_prefix, log=None):
    """百科词条主图（官方标准照）优先，失败不抛。"""
    try:
        import starsrc
        ph = starsrc.official_photo(s["zh"], dest, ref_prefix=ref_prefix)
        if ph:
            ph["url_src"] = "baike"
            return ph
    except Exception as ex:
        if log:
            log("百科主图失败 %s: %s" % (s["zh"], ex))
    return None


def official_fill(comfy_input, names=None, store=None, log=None, keep=4):
    """给每人补一张百科官方主图并置为 refs 首位，旧图保留在后（不删磁盘文件）。

    与 repair 的区别：repair 是整档替换（旧 ref 全丢），official_fill 是叠加补强，
    适合已经有一批搜图、只想把身份锚点换成官方标准照的场景。
    """
    targets = [find(n) for n in names] if names else list(STARS)
    targets = [t for t in targets if t]
    db = store.subjects() if store is not None else _subjects_db()
    have = {(it.get("name") or "").strip(): it for it in db.get("items", [])}
    out = {"done": [], "fail": [], "skip": []}
    for s in targets:
        cur = have.get(s["zh"]) or {}
        cur_refs = [r for r in (cur.get("refs") or []) if r]
        dest = _dest_dir(comfy_input, s["k"])
        ref_prefix = "_subjects/%s/%s/" % (SUB_DIR_NAME, s["k"])
        ph = _official(s, dest, ref_prefix, log=log)
        if not ph:
            out["fail"].append({"key": s["k"], "name": s["zh"], "why": "百科主图取不到"})
            continue
        if cur_refs[:1] == [ph["ref"]]:
            out["skip"].append({"key": s["k"], "name": s["zh"], "why": "首位已是百科图"})
            continue
        merged = [ph["ref"]] + [r for r in cur_refs if r != ph["ref"]]
        payload = {"action": "save", "name": s["zh"], "desc": desc_of(s),
                   "refs": merged[:keep]}
        if cur:
            payload["id"] = cur.get("id")
        item = store.subjects(payload) if store is not None else None
        if store is not None and not (item or {}).get("ok"):
            out["fail"].append({"key": s["k"], "name": s["zh"],
                                "why": (item or {}).get("error", "建档失败")})
            continue
        out["done"].append({"key": s["k"], "name": s["zh"], "ref": ph["ref"],
                            "size": "%dx%d" % (ph["w"], ph["h"]),
                            "refs": len(merged[:keep])})
    return out


def repair(comfy_input, names, store=None, log=None, official_only=True):
    """把指定人的参考图换成百科官方主图（用于修正抓错人的脸）。

    只替换档案 refs，旧图留在磁盘上不删，便于回退。
    """
    out = {"fixed": [], "fail": []}
    for n in names or []:
        s = find(n)
        if not s:
            out["fail"].append({"name": n, "why": "名单里查不到"})
            continue
        dest = _dest_dir(comfy_input, s["k"])
        ref_prefix = "_subjects/%s/%s/" % (SUB_DIR_NAME, s["k"])
        ph = _official(s, dest, ref_prefix, log=log)
        if not ph:
            out["fail"].append({"name": s["zh"], "why": "百科主图取不到"})
            continue
        payload = {"action": "save", "name": s["zh"], "desc": desc_of(s),
                   "refs": [ph["ref"]]}
        db = store.subjects() if store is not None else _subjects_db()
        for it in db.get("items", []):
            if (it.get("name") or "").strip() == s["zh"]:
                payload["id"] = it.get("id")
                break
        item = store.subjects(payload) if store is not None else None
        if store is not None and not (item or {}).get("ok"):
            out["fail"].append({"name": s["zh"], "why": (item or {}).get("error", "建档失败")})
            continue
        out["fixed"].append({"key": s["k"], "name": s["zh"], "ref": ph["ref"],
                             "size": "%dx%d" % (ph["w"], ph["h"]),
                             "desc_official": ph.get("desc", "")})
    return out


def sync(comfy_input, names=None, count=3, save=True, only_missing=True,
         keyword=None, store=None, log=None, official=True):
    """按名单抓图并挂档。

    names     : key / 中文名 / 英文名 列表；None 表示全部缺图的
    only_missing: 已有 3 张以上参考图的跳过
    save      : False 只抓不建档（返回候选，供人工挑选）
    返回 {"done": [...], "skip": [...], "fail": [...], "total": n}
    """
    targets = []
    if names:
        for n in names:
            s = find(n)
            if s:
                targets.append(s)
    else:
        targets = list(STARS)
    db = store.subjects() if store is not None else _subjects_db()
    have = {(it.get("name") or "").strip(): it for it in db.get("items", [])}

    done, skip, fail = [], [], []
    for s in targets:
        cur = have.get(s["zh"])
        cur_refs = [r for r in ((cur or {}).get("refs") or []) if r]
        if only_missing and len(cur_refs) >= 3:
            skip.append({"key": s["k"], "name": s["zh"], "why": "已有 %d 张参考图" % len(cur_refs)})
            continue
        dest = _dest_dir(comfy_input, s["k"])
        ref_prefix = "_subjects/%s/%s/" % (SUB_DIR_NAME, s["k"])
        got = []
        if official:
            ph = _official(s, dest, ref_prefix, log=log)
            if ph:
                got.append(ph)
        for kw in [keyword, s["zh"], s["en"]]:
            if len(got) >= count:
                break
            try:
                more = WEB.grab(s["zh"], dest, count=count - len(got), keyword=kw,
                                ref_prefix=ref_prefix)
            except Exception as ex:
                if log:
                    log("抓图异常 %s: %s" % (s["zh"], ex))
                more = []
            for g in more:
                if g["ref"] not in [x["ref"] for x in got]:
                    got.append(g)
        if not got:
            fail.append({"key": s["k"], "name": s["zh"], "why": "没抓到符合规格的图"})
            continue
        refs = [g["ref"] for g in got]
        if save:
            merged = [r for r in cur_refs]
            for r in refs:
                if r not in merged:
                    merged.append(r)
            # 百科官方主图放首位：Kontext 取参考图按顺序取前几张，官方照最像本人
            if got and got[0].get("source") == "baike":
                first = got[0]["ref"]
                merged = [first] + [r for r in merged if r != first]
            payload = {"action": "save", "name": s["zh"], "desc": desc_of(s),
                       "refs": merged[:4]}
            if cur:
                payload["id"] = cur.get("id")
            item = store.subjects(payload) if store is not None else None
            if store is not None and not item.get("ok"):
                fail.append({"key": s["k"], "name": s["zh"], "why": item.get("error", "建档失败")})
                continue
            done.append({"key": s["k"], "name": s["zh"], "refs": merged[:4],
                         "added": refs, "item": (item or {}).get("item")})
        else:
            done.append({"key": s["k"], "name": s["zh"], "added": refs, "saved": False})
    return {"done": done, "skip": skip, "fail": fail, "total": len(targets),
            "ts": time.time()}


def prefill_dict(extra_path=None):
    """把名单的中英名写进 data/celeb.json；已存在的键不覆盖。"""
    path = extra_path or CELEB_PATH
    cur = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cur = json.load(f) or {}
    except Exception:
        cur = {}
    added = []
    for s in STARS:
        if s["zh"] not in cur:
            cur[s["zh"]] = s["en"]
            added.append(s["zh"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    return {"path": path, "added": added, "total": len(cur)}
