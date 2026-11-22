# -*- coding: utf-8 -*-
"""人名保护与规范化层（Name Guard）

解决的问题：
1. 小模型（qwen3:8b）没有名人知识，中文人名会被按字音硬译：刘德华 -> Huading Liu
2. 人名被当作普通名词丢弃：周杰伦和成龙 -> two men

做法：
- 词典优先：内置常见名人 中文名 -> 标准英文名（Andy Lau / Jay Chou / Jackie Chan）
- 占位替换：翻译前把人名换成 __PERSON_1__，模型完全接触不到中文人名，翻译后回填标准英文名
- 词典未命中：走拼音兜底，并标记 unknown，前端可提示用户补录
- 支持用户自定义（主体库 subjects.json 里登记的英文名/参考图）
"""

import json
import os
import re

# ============ 用户自定义人名表（data/celeb.json，热加载）============
_DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "celeb.json")
_USER_CACHE = {"mtime": None, "data": {}}


def load_user_dict(force=False):
    """读取 data/celeb.json：{"中文名": "English Name"}，按 mtime 热更新，改完不用重启"""
    try:
        mt = os.path.getmtime(_DICT_PATH)
    except OSError:
        return {}
    if not force and _USER_CACHE["mtime"] == mt:
        return _USER_CACHE["data"]
    try:
        with open(_DICT_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        data = {str(k).strip(): str(v).strip() for k, v in raw.items()
                if str(k).strip() and str(v).strip() and not str(k).startswith("_")}
    except Exception:
        data = {}
    _USER_CACHE["mtime"] = mt
    _USER_CACHE["data"] = data
    return data


def add_user_name(zh, en):
    """补录一个人名到 data/celeb.json，立即可用"""
    zh, en = (zh or "").strip(), (en or "").strip()
    if not zh or not en:
        raise ValueError("中文名和英文名都不能为空")
    data = load_user_dict(force=True)
    data[zh] = en
    os.makedirs(os.path.dirname(_DICT_PATH), exist_ok=True)
    with open(_DICT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    _USER_CACHE["mtime"] = None
    load_user_dict(force=True)
    return {"zh": zh, "en": en, "total": len(data)}


def all_names():
    """当前生效的全部人名（内置 + 自定义）"""
    d = dict(CELEB)
    d.update(load_user_dict())
    return d


# ============ 名人词典：中文名 -> 标准英文名 ============
CELEB = {
    # ---- 华语影视（香港 / 台湾）----
    "刘德华": "Andy Lau",
    "成龙": "Jackie Chan",
    "周润发": "Chow Yun-fat",
    "周星驰": "Stephen Chow",
    "梁朝伟": "Tony Leung",
    "张国荣": "Leslie Cheung",
    "梅艳芳": "Anita Mui",
    "张曼玉": "Maggie Cheung",
    "林青霞": "Brigitte Lin",
    "关之琳": "Rosamund Kwan",
    "王祖贤": "Joey Wong",
    "钟楚红": "Cherie Chung",
    "邱淑贞": "Chingmy Yau",
    "舒淇": "Shu Qi",
    "汤唯": "Tang Wei",
    "章子怡": "Zhang Ziyi",
    "巩俐": "Gong Li",
    "李连杰": "Jet Li",
    "甄子丹": "Donnie Yen",
    "吴京": "Wu Jing",
    "古天乐": "Louis Koo",
    "刘青云": "Lau Ching-wan",
    "张家辉": "Nick Cheung",
    "谢霆锋": "Nicholas Tse",
    "陈小春": "Jordan Chan",
    "郑伊健": "Ekin Cheng",
    "任达华": "Simon Yam",
    "黄秋生": "Anthony Wong",
    "吴镇宇": "Francis Ng",
    "刘嘉玲": "Carina Lau",
    "张柏芝": "Cecilia Cheung",
    "蔡少芬": "Ada Choi",
    "佘诗曼": "Charmaine Sheh",
    "邓萃雯": "Sheren Tang",
    "成龙儿子": "Jackie Chan's son",
    "洪金宝": "Sammo Hung",
    "元彪": "Yuen Biao",
    "惠英红": "Kara Wai",
    "杨紫琼": "Michelle Yeoh",
    "郑秀文": "Sammi Cheng",
    "容祖儿": "Joey Yung",
    "陈奕迅": "Eason Chan",
    "谭咏麟": "Alan Tam",
    "张学友": "Jacky Cheung",
    "郭富城": "Aaron Kwok",
    "黎明": "Leon Lai",
    "李克勤": "Hacken Lee",
    "古巨基": "Leo Ku",

    # ---- 香港黄金配角 / 老戏骨 ----
    "吴孟达": "Ng Man-tat",
    "梁家辉": "Tony Leung Ka-fai",
    "万梓良": "Alex Man",
    "林正英": "Lam Ching-ying",
    "午马": "Wu Ma",
    "秦沛": "Paul Chun",
    "曾志伟": "Eric Tsang",
    "苗侨伟": "Michael Miu",
    "黄日华": "Felix Wong",
    "刘松仁": "Damian Lau",
    "吕良伟": "Ray Lui",
    "郑少秋": "Adam Cheng",
    "狄龙": "Ti Lung",
    "姜大卫": "David Chiang",
    "王羽": "Jimmy Wang Yu",
    "李修贤": "Danny Lee",
    "秦祥林": "Chin Hsiang-lin",
    "秦汉": "Chin Han",
    "尔冬升": "Derek Yee",
    "黄百鸣": "Raymond Wong",
    "陈百祥": "Natalis Chan",
    "吴耀汉": "Richard Ng",
    "许冠文": "Michael Hui",
    "许冠杰": "Sam Hui",
    "谭咏麟": "Alan Tam",
    "钟镇涛": "Kenny Bee",
    "叶童": "Cecilia Yip",
    "罗兰": "Helena Law Lan",
    "薛家燕": "Nancy Sit",
    "汪明荃": "Liza Wang",
    "米雪": "Michelle Yim",
    "李司棋": "Louise Lee",
    "关菊英": "Susanna Kwan",
    "陈慧珊": "Flora Chan",
    "宣萱": "Jessica Hsuan",
    "郭可盈": "Kenix Kwok",
    "张可颐": "Maggie Cheung Ho-yee",
    "黎姿": "Gigi Lai",
    "郭羡妮": "Sonija Kwok",
    "滕丽名": "Joyce Tang",
    "黄宗泽": "Bosco Wong",
    "林峯": "Raymond Lam",
    "吴卓羲": "Ron Ng",
    "陈键锋": "Sammul Chan",
    "马国明": "Kenneth Ma",
    "陈豪": "Moses Chan",
    "黎耀祥": "Wayne Lai",
    "邓萃雯": "Sheren Tang",

    # ---- 内地演员补充 ----
    "李幼斌": "Li Youbin",
    "侯勇": "Hou Yong",
    "尤勇": "You Yong",
    "何冰": "He Bing",
    "倪大红": "Ni Dahong",
    "王劲松": "Wang Jinsong",
    "刘奕君": "Liu Yijun",
    "于和伟": "Yu Hewei",
    "张译": "Zhang Yi",
    "王千源": "Wang Qianyuan",
    "富大龙": "Fu Dalong",
    "张嘉益": "Zhang Jiayi",
    "靳东": "Jin Dong",
    "王凯": "Wang Kai",
    "刘涛": "Liu Tao",
    "蒋欣": "Jiang Xin",
    "姚晨": "Yao Chen",
    "闫妮": "Yan Ni",
    "秦海璐": "Qin Hailu",
    "袁泉": "Yuan Quan",
    "俞飞鸿": "Yu Feihong",
    "许晴": "Xu Qing",
    "江疏影": "Jiang Shuying",
    "古力娜扎": "Gulnazar",
    "佟丽娅": "Tong Liya",
    "张天爱": "Zhang Tianai",
    "周笔畅": "Bibi Zhou",
    "李健": "Li Jian",
    "陈粒": "Chen Li",
    "赵雷": "Zhao Lei",
    "万能青年旅店": "Omnipotent Youth Society",
    "高晓松": "Gao Xiaosong",
    "罗永浩": "Luo Yonghao",
    "董宇辉": "Dong Yuhui",
    "李子柒": "Li Ziqi",
    "papi酱": "Papi Jiang",


    # ---- 华语影视（内地）----
    "周杰伦": "Jay Chou",
    "王菲": "Faye Wong",
    "那英": "Na Ying",
    "刘欢": "Liu Huan",
    "孙楠": "Sun Nan",
    "汪峰": "Wang Feng",
    "朴树": "Pu Shu",
    "许巍": "Xu Wei",
    "崔健": "Cui Jian",
    "李宇春": "Li Yuchun",
    "张靓颖": "Jane Zhang",
    "邓紫棋": "G.E.M.",
    "毛不易": "Mao Buyi",
    "张杰": "Jason Zhang",
    "华晨宇": "Hua Chenyu",
    "薛之谦": "Joker Xue",
    "李荣浩": "Li Ronghao",
    "周深": "Zhou Shen",
    "费玉清": "Fei Yu-ching",
    "王力宏": "Wang Leehom",
    "陶喆": "David Tao",
    "林俊杰": "JJ Lin",
    "罗志祥": "Show Lo",
    "蔡依林": "Jolin Tsai",
    "张韶涵": "Angela Chang",
    "杨丞琳": "Rainie Yang",
    "田馥甄": "Hebe Tien",
    "陈乔恩": "Joe Chen",
    "林志玲": "Lin Chi-ling",
    "徐熙娣": "Dee Hsu",
    "小S": "Dee Hsu",
    "范冰冰": "Fan Bingbing",
    "李冰冰": "Li Bingbing",
    "赵薇": "Zhao Wei",
    "周迅": "Zhou Xun",
    "孙俪": "Sun Li",
    "杨幂": "Yang Mi",
    "刘亦菲": "Liu Yifei",
    "赵丽颖": "Zhao Liying",
    "迪丽热巴": "Dilraba Dilmurat",
    "杨颖": "Angelababy",
    "唐嫣": "Tiffany Tang",
    "刘诗诗": "Liu Shishi",
    "高圆圆": "Gao Yuanyuan",
    "倪妮": "Ni Ni",
    "白百何": "Bai Baihe",
    "马伊琍": "Ma Yili",
    "海清": "Hai Qing",
    "陈道明": "Chen Daoming",
    "陈宝国": "Chen Baoguo",
    "李雪健": "Li Xuejian",
    "张国立": "Zhang Guoli",
    "王刚": "Wang Gang",
    "葛优": "Ge You",
    "黄渤": "Huang Bo",
    "徐峥": "Xu Zheng",
    "王宝强": "Wang Baoqiang",
    "沈腾": "Shen Teng",
    "马丽": "Ma Li",
    "贾玲": "Jia Ling",
    "邓超": "Deng Chao",
    "黄晓明": "Huang Xiaoming",
    "陈坤": "Chen Kun",
    "陆毅": "Lu Yi",
    "胡歌": "Hu Ge",
    "霍建华": "Wallace Huo",
    "钟汉良": "Wallace Chung",
    "吴磊": "Wu Lei",
    "易烊千玺": "Jackson Yee",
    "王俊凯": "Karry Wang",
    "王源": "Roy Wang",
    "杨洋": "Yang Yang",
    "李现": "Li Xian",
    "肖战": "Xiao Zhan",
    "王一博": "Wang Yibo",
    "张艺兴": "Lay Zhang",
    "吴亦凡": "Kris Wu",
    "鹿晗": "Luhan",
    "黄子韬": "Huang Zitao",
    "朱一龙": "Zhu Yilong",
    "白宇": "Bai Yu",
    "雷佳音": "Lei Jiayin",
    "郭涛": "Guo Tao",
    "段奕宏": "Duan Yihong",
    "张涵予": "Zhang Hanyu",
    "廖凡": "Liao Fan",
    "秦昊": "Qin Hao",
    "王景春": "Wang Jingchun",
    "咏梅": "Yong Mei",
    "周冬雨": "Zhou Dongyu",

    # ---- 导演 / 编剧 ----
    "张艺谋": "Zhang Yimou",
    "陈凯歌": "Chen Kaige",
    "冯小刚": "Feng Xiaogang",
    "姜文": "Jiang Wen",
    "王家卫": "Wong Kar-wai",
    "吴宇森": "John Woo",
    "徐克": "Tsui Hark",
    "李安": "Ang Lee",
    "侯孝贤": "Hou Hsiao-hsien",
    "贾樟柯": "Jia Zhangke",
    "宁浩": "Ning Hao",
    "管虎": "Guan Hu",
    "郭帆": "Guo Fan",
    "陈思诚": "Chen Sicheng",
    "王晶": "Wong Jing",
    "杜琪峰": "Johnnie To",

    # ---- 港台 / 内地主持与综艺 ----
    "何炅": "He Jiong",
    "汪涵": "Wang Han",
    "谢娜": "Xie Na",
    "董卿": "Dong Qing",
    "撒贝宁": "Sa Beining",
    "康辉": "Kang Hui",
    "吴宗宪": "Jacky Wu",
    "蔡康永": "Kevin Tsai",

    # ---- 日韩 ----
    "木村拓哉": "Takuya Kimura",
    "石原里美": "Satomi Ishihara",
    "新垣结衣": "Yui Aragaki",
    "北野武": "Takeshi Kitano",
    "宫崎骏": "Hayao Miyazaki",
    "是枝裕和": "Hirokazu Kore-eda",
    "安倍晋三": "Shinzo Abe",
    "宫本茂": "Shigeru Miyamoto",
    "鸟山明": "Akira Toriyama",
    "尾田荣一郎": "Eiichiro Oda",
    "久石让": "Joe Hisaishi",
    "防弹少年团": "BTS",
    "李政宰": "Lee Jung-jae",
    "宋慧乔": "Song Hye-kyo",
    "孔刘": "Gong Yoo",
    "孙兴慜": "Son Heung-min",
    "奉俊昊": "Bong Joon-ho",
    "朴赞郁": "Park Chan-wook",

    # ---- 好莱坞与国际影星 ----
    "莱昂纳多": "Leonardo DiCaprio",
    "莱昂纳多·迪卡普里奥": "Leonardo DiCaprio",
    "汤姆·克鲁斯": "Tom Cruise",
    "汤姆克鲁斯": "Tom Cruise",
    "汤姆·汉克斯": "Tom Hanks",
    "布拉德·皮特": "Brad Pitt",
    "布拉德皮特": "Brad Pitt",
    "安吉丽娜·朱莉": "Angelina Jolie",
    "约翰尼·德普": "Johnny Depp",
    "小罗伯特·唐尼": "Robert Downey Jr.",
    "斯嘉丽·约翰逊": "Scarlett Johansson",
    "迈克尔·杰克逊": "Michael Jackson",
    "迈克尔杰克逊": "Michael Jackson",
    "麦当娜": "Madonna",
    "泰勒·斯威夫特": "Taylor Swift",
    "泰勒·斯威夫特": "Taylor Swift",
    "碧昂丝": "Beyoncé",
    "艾薇儿": "Avril Lavigne",
    "艾米纳姆": "Eminem",
    "奥黛丽·赫本": "Audrey Hepburn",
    "玛丽莲·梦露": "Marilyn Monroe",
    "卓别林": "Charlie Chaplin",
    "史泰龙": "Sylvester Stallone",
    "施瓦辛格": "Arnold Schwarzenegger",
    "基努·里维斯": "Keanu Reeves",
    "汤姆·霍兰德": "Tom Holland",
    "克里斯·埃文斯": "Chris Evans",
    "克里斯·海姆斯沃斯": "Chris Hemsworth",
    "罗伯特·帕丁森": "Robert Pattinson",
    "艾玛·沃森": "Emma Watson",
    "丹尼尔·雷德克里夫": "Daniel Radcliffe",
    "摩根·弗里曼": "Morgan Freeman",
    "马特·达蒙": "Matt Damon",
    "本·阿弗莱克": "Ben Affleck",
    "尼古拉斯·凯奇": "Nicolas Cage",
    "乔治·克鲁尼": "George Clooney",
    "威尔·史密斯": "Will Smith",
    "道恩·强森": "Dwayne Johnson",
    "巨石强森": "Dwayne Johnson",
    "盖尔·加朵": "Gal Gadot",
    "卡梅隆": "James Cameron",
    "詹姆斯·卡梅隆": "James Cameron",
    "斯皮尔伯格": "Steven Spielberg",
    "克里斯托弗·诺兰": "Christopher Nolan",
    "诺兰": "Christopher Nolan",
    "昆汀": "Quentin Tarantino",
    "斯坦利·库布里克": "Stanley Kubrick",
    "阿尔弗雷德·希区柯克": "Alfred Hitchcock",
    "黑泽明": "Akira Kurosawa",

    # ---- 科技 / 商业（含国区高频）----
    "马斯克": "Elon Musk",
    "埃隆·马斯克": "Elon Musk",
    "比尔·盖茨": "Bill Gates",
    "乔布斯": "Steve Jobs",
    "史蒂夫·乔布斯": "Steve Jobs",
    "贝索斯": "Jeff Bezos",
    "扎克伯格": "Mark Zuckerberg",
    "山姆·奥特曼": "Sam Altman",
    "黄仁勋": "Jensen Huang",
    "苏姿丰": "Lisa Su",
    "库克": "Tim Cook",
    "蒂姆·库克": "Tim Cook",
    "马云": "Jack Ma",
    "马化腾": "Pony Ma",
    "雷军": "Lei Jun",
    "任正非": "Ren Zhengfei",
    "李彦宏": "Robin Li",
    "刘强东": "Richard Liu",
    "张一鸣": "Zhang Yiming",
    "黄峥": "Colin Huang",
    "王兴": "Wang Xing",
    "丁磊": "William Ding",
    "董明珠": "Dong Mingzhu",
    "曹德旺": "Cao Dewang",
    "宗庆后": "Zong Qinghou",
    "钟睒睒": "Zhong Shanshan",
    "王健林": "Wang Jianlin",
    "李嘉诚": "Li Ka-shing",
    "巴菲特": "Warren Buffett",
    "沃伦·巴菲特": "Warren Buffett",
    "索罗斯": "George Soros",
    "稻盛和夫": "Kazuo Inamori",
    "孙正义": "Masayoshi Son",

    # ---- 科学家 / 思想家 ----
    "爱因斯坦": "Albert Einstein",
    "牛顿": "Isaac Newton",
    "达尔文": "Charles Darwin",
    "特斯拉": "Nikola Tesla",
    "尼古拉·特斯拉": "Nikola Tesla",
    "居里夫人": "Marie Curie",
    "霍金": "Stephen Hawking",
    "图灵": "Alan Turing",
    "冯诺依曼": "John von Neumann",
    "钱学森": "Qian Xuesen",
    "袁隆平": "Yuan Longping",
    "屠呦呦": "Tu Youyou",
    "杨振宁": "Chen-Ning Yang",
    "李政道": "Tsung-Dao Lee",
    "达芬奇": "Leonardo da Vinci",
    "达·芬奇": "Leonardo da Vinci",
    "梵高": "Vincent van Gogh",
    "毕加索": "Pablo Picasso",
    "莫奈": "Claude Monet",
    "齐白石": "Qi Baishi",
    "徐悲鸿": "Xu Beihong",
    "张大千": "Zhang Daqian",
    "贝多芬": "Ludwig van Beethoven",
    "莫扎特": "Wolfgang Amadeus Mozart",
    "巴赫": "Johann Sebastian Bach",
    "肖邦": "Frédéric Chopin",
    "李白": "Li Bai",
    "杜甫": "Du Fu",
    "苏轼": "Su Shi",
    "孔子": "Confucius",
    "老子": "Laozi",
    "孙子": "Sun Tzu",
    "关羽": "Guan Yu",
    "诸葛亮": "Zhuge Liang",
    "曹操": "Cao Cao",
    "刘备": "Liu Bei",
    "唐僧": "Tang Sanzang",
    "孙悟空": "Sun Wukong",
    "猪八戒": "Zhu Bajie",
    "林黛玉": "Lin Daiyu",
    "贾宝玉": "Jia Baoyu",
    "武松": "Wu Song",
    "林冲": "Lin Chong",
    "花木兰": "Mulan",

    # ---- 政治 / 历史人物 ----
    "毛泽东": "Mao Zedong",
    "周恩来": "Zhou Enlai",
    "邓小平": "Deng Xiaoping",
    "孙中山": "Sun Yat-sen",
    "蒋介石": "Chiang Kai-shek",
    "秦始皇": "Qin Shi Huang",
    "汉武帝": "Emperor Wu of Han",
    "唐太宗": "Emperor Taizong of Tang",
    "武则天": "Wu Zetian",
    "成吉思汗": "Genghis Khan",
    "康熙": "Kangxi Emperor",
    "乾隆": "Qianlong Emperor",
    "拿破仑": "Napoleon Bonaparte",
    "华盛顿": "George Washington",
    "林肯": "Abraham Lincoln",
    "丘吉尔": "Winston Churchill",
    "戴高乐": "Charles de Gaulle",
    "甘地": "Mahatma Gandhi",
    "曼德拉": "Nelson Mandela",
    "马丁·路德·金": "Martin Luther King Jr.",
    "普京": "Vladimir Putin",
    "特朗普": "Donald Trump",
    "拜登": "Joe Biden",
    "奥巴马": "Barack Obama",
    "克林顿": "Bill Clinton",
    "泽连斯基": "Volodymyr Zelenskyy",
    "金正恩": "Kim Jong-un",
    "默克尔": "Angela Merkel",

    # ---- 体育 ----
    "姚明": "Yao Ming",
    "刘翔": "Liu Xiang",
    "李娜": "Li Na",
    "林丹": "Lin Dan",
    "张继科": "Zhang Jike",
    "马龙": "Ma Long",
    "孙杨": "Sun Yang",
    "全红婵": "Quan Hongchan",
    "谷爱凌": "Eileen Gu",
    "苏炳添": "Su Bingtian",
    "武大靖": "Wu Dajing",
    "郎平": "Lang Ping",
    "乔丹": "Michael Jordan",
    "迈克尔·乔丹": "Michael Jordan",
    "科比": "Kobe Bryant",
    "科比·布莱恩特": "Kobe Bryant",
    "詹姆斯": "LeBron James",
    "勒布朗·詹姆斯": "LeBron James",
    "库里": "Stephen Curry",
    "梅西": "Lionel Messi",
    "C罗": "Cristiano Ronaldo",
    "贝克汉姆": "David Beckham",
    "齐达内": "Zinedine Zidane",
    "罗纳尔多": "Ronaldo",
    "内马尔": "Neymar",
    "费德勒": "Roger Federer",
    "纳达尔": "Rafael Nadal",
    "德约科维奇": "Novak Djokovic",
    "泰森": "Mike Tyson",
    "博尔特": "Usain Bolt",
    "菲尔普斯": "Michael Phelps",
    "泰格·伍兹": "Tiger Woods",

    # ---- 动漫 / 虚拟角色 ----
    "哆啦A梦": "Doraemon",
    "皮卡丘": "Pikachu",
    "米老鼠": "Mickey Mouse",
    "唐老鸭": "Donald Duck",
    "蜘蛛侠": "Spider-Man",
    "钢铁侠": "Iron Man",
    "美国队长": "Captain America",
    "雷神": "Thor",
    "绿巨人": "Hulk",
    "超人": "Superman",
    "蝙蝠侠": "Batman",
    "神奇女侠": "Wonder Woman",
    "死侍": "Deadpool",
    "金刚狼": "Wolverine",
    "哈利波特": "Harry Potter",
    "赫敏": "Hermione Granger",
    "阿凡达": "Avatar",
    "蜡笔小新": "Crayon Shin-chan",
    "海绵宝宝": "SpongeBob",
    "名侦探柯南": "Detective Conan",
    "路飞": "Monkey D. Luffy",
    "鸣人": "Naruto Uzumaki",
    "奥特曼": "Ultraman",
}

# ============ 常见中文姓氏（用于词典未命中时的姓名识别）============
SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平"
    "黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝"
    "董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊"
    "胡凌霍虞万支柯管卢莫房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚"
    "程嵇邢滑裴陆荣翁荀羊惠甄曲封芮储靳汲邴糜松井段富巫乌焦巴弓牧隗"
    "山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶"
    "幸司韶郜黎蓟薄印宿白怀蒲台从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍双"
    "闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚"
    "农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿"
    "满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那"
    "简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)

# 明确不是人名的干扰词（避免把"王者荣耀""李子柒"之类的片段误判）
_FALSE_HITS = {
    "王者", "李子", "杨梅", "黄瓜", "孙子", "李子柒", "白衣", "白领",
    "高个", "小女", "老王", "张罗", "胡同", "陈设", "周旋", "孙子兵法",
}

# 人名占位符：模型看到的是这个，翻译后我们回填标准英文名
_TOKEN = "__PERSON_%d__"

# 人物出现语境（出现这些词才认为是"要画的人"）
_CTX_WORDS = (
    "生成", "画", "照片", "写真", "图片", "合影", "站在", "穿", "和", "与",
    "跟", "合影", "样子", "形象", "造型", "拍摄", "出镜", "主演", "肖像",
)


def _has_zh(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def _pinyin_fallback(name):
    """词典未命中时的拼音兜底：按字转拼音（仅覆盖常用姓名字）
    返回 (英文拼写, 是否可信)
    """
    try:
        from pypinyin import lazy_pinyin
        parts = lazy_pinyin(name)
        if parts:
            return " ".join(p.capitalize() for p in parts), False
    except Exception:
        pass
    return name, False


def detect_names(text, extra=None):
    """从中文文本中识别人名，返回 [(中文名, 标准英文名, 是否词典命中)]，按出现顺序。

    优先级：extra（调用方指定） > data/celeb.json（用户补录） > 内置 CELEB
    """
    if not text:
        return []
    pool = {}
    pool.update(CELEB)
    pool.update(load_user_dict())
    if extra:
        pool.update({k: v for k, v in extra.items() if k and v})

    hits = []
    # 长名优先匹配（"莱昂纳多·迪卡普里奥" 先于 "莱昂纳多"）
    for zh in sorted(pool, key=len, reverse=True):
        idx = text.find(zh)
        if idx < 0:
            continue
        # 已被更长的名字覆盖则跳过
        if any(h[0].find(zh) >= 0 and h[0] != zh for h in hits):
            continue
        en = pool[zh]
        hits.append((zh, en, True, idx))

    hits.sort(key=lambda x: x[3])
    return [(zh, en, ok) for zh, en, ok, _ in hits]


def detect_unknown_names(text, known=None):
    """识别词典未收录的中文姓名（保守策略：姓 + 1~2 字，且需要有人物语境）"""
    if not text or not _has_zh(text):
        return []
    known = set(known or ())
    if not any(w in text for w in _CTX_WORDS):
        return []
    out = []
    # 连续 2~3 个汉字，首字为常见姓氏
    for m in re.finditer(r"[\u4e00-\u9fff]{2,3}", text):
        w = m.group()
        if w in CELEB or w in known or w in _FALSE_HITS:
            continue
        if w[0] not in SURNAMES:
            continue
        # 排除常见非人名词（成语/地名/物品）
        if w in ("中国", "北京", "上海", "广州", "深圳", "沈阳", "世界", "公司"):
            continue
        out.append(w)
    # 去重保序
    seen, uniq = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq


def protect(text, extra=None):
    """把人名替换成占位符。返回 (masked_text, mapping)
    mapping: {占位符: {"zh": 中文名, "en": 标准英文名, "known": 是否词典命中}}
    """
    if not text:
        return text, {}
    mapping = {}
    masked = text
    idx = 0

    for zh, en, ok in detect_names(text, extra):
        tok = _TOKEN % idx
        masked = masked.replace(zh, tok)
        mapping[tok] = {"zh": zh, "en": en, "known": ok}
        idx += 1

    return masked, mapping


def restore(en_text, mapping):
    """把占位符回填为英文名。返回 (最终英文, 未收录人名列表)"""
    if not en_text:
        return en_text, []
    out = en_text
    unknown = []
    for tok, info in mapping.items():
        # 模型可能改动占位符（大小写/空格），做宽松匹配
        out = re.sub(re.escape(tok), info["en"], out, flags=re.I)
        if not info["known"]:
            unknown.append(info["zh"])
    # 兜底：模型把占位符写坏了，按顺序塞回去
    for tok, info in mapping.items():
        if re.search(r"__\s*PERSON\s*_?\s*\d+\s*__", out, re.I):
            out = re.sub(r"__\s*PERSON\s*_?\s*\d+\s*__", info["en"], out, count=1, flags=re.I)
    # 最后一道闸：小模型会自己伪造占位符（system prompt 里出现过示例），一律清掉，
    # 绝不让 __PERSON_x__ 这种垃圾 token 流到 ComfyUI
    out = re.sub(r"[_\s]*__\s*PERSON\s*_?\s*\d*\s*__[_\s]*", " ", out, flags=re.I)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,")
    return out, unknown


def normalize_for_zh(text, extra=None):
    """供不需要翻译的场景（Wan/H3 中文直送）用：把生僻写法统一成标准中文名。
    目前只是原样返回，保留接口以便后续做别名归一（如"发哥"->"周润发"）。
    """
    return text
