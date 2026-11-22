---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dbc1c155fc9201133fd4bf5993fb4878_d9ac303ee50511f19f9d525400cd780f
    ReservedCode1: hHxLxk3iYk3/R4u5PW67Nn3tKYQD3gb9AT5zcg5e55QAipMk5XEIqe/8vNVWjwpKxlYqkx4GX1kjJaI8rWr27W7v/A8mrABSY+vBcFFfWArIyhrbjvVho/wVvBs++nXil2JkqB2hwufHiHYccvZB9DBMJfTVNfE0+ai9CdG3aqmXue74oXfYUBpUlYM=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dbc1c155fc9201133fd4bf5993fb4878_d9ac303ee50511f19f9d525400cd780f
    ReservedCode2: hHxLxk3iYk3/R4u5PW67Nn3tKYQD3gb9AT5zcg5e55QAipMk5XEIqe/8vNVWjwpKxlYqkx4GX1kjJaI8rWr27W7v/A8mrABSY+vBcFFfWArIyhrbjvVho/wVvBs++nXil2JkqB2hwufHiHYccvZB9DBMJfTVNfE0+ai9CdG3aqmXue74oXfYUBpUlYM=
---

# 影星头像库（STARS）

本机自用的「全球著名影星」参考图库，接在主体库（`_subjects/`）之上，供 `@人名` 锚定使用。
抓到的图片只留在本机 ComfyUI input，**不进版本库**（见 `PRIVACY.md`）。

## 一、数据在哪

| 项 | 路径 |
| --- | --- |
| 名单与逻辑 | `stars.py`（50 位，字段 k/en/zh/c/w） |
| 图片落盘 | `{ComfyUI}/input/_subjects/stars/<k>/*.jpg`，每人最多 4 张 |
| 主体档案 | `data/subjects.json`（与手建主体同库，可直接被生成链路使用） |
| 人名词典 | `data/celeb.json`（中文名 → 英文名，喂模型时自动替换） |
| 抓取日志 | `logs/stars_sync.log` |

## 二、四个接口

| 方法 | 路由 | 作用 |
| --- | --- | --- |
| GET | `/api/stars` | 回全量状态：总数、已入档数、每人参考图数量与绝对路径 |
| POST | `/api/stars/sync` | 抓图入档。body：`{"names":["汤姆·克鲁斯"], "count":3, "only_missing":true, "save":true}`，`names` 空=全量；默认 `official=true`，百科主图优先置首位 |
| POST | `/api/stars/repair` | 整档换官方图。body：`{"names":["章子怡"]}`，一次最多 10 位，旧图不删磁盘、只从档案 refs 摘除 |
| POST | `/api/stars/prefill` | 把 50 位中英名写进 `data/celeb.json`，让 `@中文名` 自动转英文 |


## 三、怎么用

1. 创作台 → 顶部页签 **影星库**（`studio-pro.js` 注入），可先「一键补全缺图」，再「写入人词典」。
2. 提示词里直接写 `@汤姆·克鲁斯`：`names.protect` 把它换成占位符，`restore` 时替换为 `Tom Cruise`，
   同时 `subjects.usable` 命中档案、把参考图挂到 Kontext 输入上做换脸/融合锚定。
3. 单人镜稳定；**双人同框时 Kontext 链式参考只能锚一张脸**，需走 inpaint 两步或换多人参考方案。

## 四、抓图源与口径

**优先级：百科官方主图 > 360 图片 > 必应图片。**

1. 官方主图（`starsrc.py`，主源）：走百度百科开放接口
   `baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103&format=json&appid=379020&bk_key=<词条>`，
   取 `image` 字段（bcebos CDN），短边不足 400px 时用 `?x-bce-process=image/resize,m_lfit,w_1200` 取大图；
   顺手回填外文名（`clean_en` 去掉脚注编号与多语种）。词条名查不到时去掉间隔号再试一次
   （如「沙鲁克·汗」→「沙鲁克汗」），不做前缀模糊匹配，结果缓存 `data/baike_cache.json` 30 天。
2. 兜底图（`webimg.py`，用于补满 4 张）：主源 360 图片 `image.so.com/j`，备源 `cn.bing.com/images`（境内易污染，只兜底）。
3. 通用过滤：短边 ≥ 450px、宽高比 0.55~1.55、40KB~8MB、MD5 去重、标题噪声词（神似/撞脸/书法/海报/合照/漫画/表情包）丢弃。
4. 查询词轮换：`{名} 写真 高清` / `{名} 正脸 红毯` / `{名} 官方 照片` / `{名} 人脸 特写` / `{名} 活动 高清` / `{名} 高清头像`。

## 五、已知瑕疵与修法

1. **某些人整档是搜图**：50 位已全部换成百科主图打头。个别（汤唯、全智贤）百科主图本身偏生活照/时尚照，
   语义上仍是官方词条图，如需标准证件感正脸可用 `/api/stars/repair` 或面板「补图」再抓。
2. **首位图可人工微调**：如阿米尔·汗百科主图是抱猫照，已用视觉核验过的正脸图置首、百科图退居第二位。
   调整方式：`POST /api/subjects {"action":"save","name":"阿米尔·汗","refs":[...]}` 直接改 refs 顺序。
3. **抓到不像本人**：视觉核验常用手段 `analyze_image`，判 B 就 `POST /api/stars/repair` 整档换官方图，
   或 `/api/subjects/fetch` 手动挑图，无需改代码。
4. **别用失效源**：维基百科 zh/en API 与 Commons 境内超时、百科 HTML/移动端 403、搜狗百科无图、
   豆瓣 celebrity 接口 404，均不可作为采集源。


## 六、名单

| 中文 | English | 国别 | 代表作 |
| --- | --- | --- | --- |
| 汤姆·克鲁斯 | Tom Cruise | 美国 | 《壮志凌云》《碟中谍》 |
| 莱昂纳多·迪卡普里奥 | Leonardo DiCaprio | 美国 | 《泰坦尼克号》《盗梦空间》 |
| 布拉德·皮特 | Brad Pitt | 美国 | 《搏击俱乐部》《史密斯夫妇》 |
| 约翰尼·德普 | Johnny Depp | 美国 | 《加勒比海盗》《剪刀手爱德华》 |
| 小罗伯特·唐尼 | Robert Downey Jr. | 美国 | 《钢铁侠》《复仇者联盟》 |
| 汤姆·汉克斯 | Tom Hanks | 美国 | 《阿甘正传》《拯救大兵瑞恩》 |
| 摩根·弗里曼 | Morgan Freeman | 美国 | 《肖申克的救赎》《七宗罪》 |
| 威尔·史密斯 | Will Smith | 美国 | 《独立日》《当幸福来敲门》 |
| 基努·里维斯 | Keanu Reeves | 加拿大 | 《黑客帝国》《疾速追杀》 |
| 尼古拉斯·凯奇 | Nicolas Cage | 美国 | 《变脸》《国家宝藏》 |
| 马特·达蒙 | Matt Damon | 美国 | 《谍影重重》《火星救援》 |
| 克里斯蒂安·贝尔 | Christian Bale | 英国 | 《蝙蝠侠：黑暗骑士》 |
| 休·杰克曼 | Hugh Jackman | 澳大利亚 | 《金刚狼》《悲惨世界》 |
| 汤姆·哈迪 | Tom Hardy | 英国 | 《毒液》《疯狂的麦克斯4》 |
| 汤姆·赫兰德 | Tom Holland | 英国 | 《蜘蛛侠》系列 |
| 道恩·强森 | Dwayne Johnson | 美国 | 《速度与激情》系列 |
| 瑞恩·高斯林 | Ryan Gosling | 加拿大 | 《爱乐之城》《芭比》 |
| 华金·菲尼克斯 | Joaquin Phoenix | 美国 | 《小丑》《角斗士》 |
| 斯嘉丽·约翰逊 | Scarlett Johansson | 美国 | 《黑寡妇》《迷失东京》 |
| 安吉丽娜·朱莉 | Angelina Jolie | 美国 | 《古墓丽影》《史密斯夫妇》 |
| 娜塔莉·波特曼 | Natalie Portman | 美国 | 《这个杀手不太冷》《黑天鹅》 |
| 艾玛·沃特森 | Emma Watson | 英国 | 《哈利·波特》系列 |
| 安妮·海瑟薇 | Anne Hathaway | 美国 | 《穿普拉达的女王》《星际穿越》 |
| 詹妮弗·劳伦斯 | Jennifer Lawrence | 美国 | 《饥饿游戏》系列 |
| 查理兹·塞隆 | Charlize Theron | 南非 | 《疯狂的麦克斯4》《女魔头》 |
| 盖尔·加朵 | Gal Gadot | 以色列 | 《神奇女侠》 |
| 玛格特·罗比 | Margot Robbie | 澳大利亚 | 《芭比》《自杀小队》 |
| 艾玛·斯通 | Emma Stone | 美国 | 《爱乐之城》《可怜的东西》 |
| 妮可·基德曼 | Nicole Kidman | 澳大利亚 | 《红磨坊》《时时刻刻》 |
| 朱莉娅·罗伯茨 | Julia Roberts | 美国 | 《诺丁山》《永不妥协》 |
| 凯特·温斯莱特 | Kate Winslet | 英国 | 《泰坦尼克号》《朗读者》 |
| 梅丽尔·斯特里普 | Meryl Streep | 美国 | 《穿普拉达的女王》《铁娘子》 |
| 成龙 | Jackie Chan | 中国香港 | 《警察故事》《尖峰时刻》 |
| 李连杰 | Jet Li | 中国 | 《黄飞鸿》《英雄》 |
| 周润发 | Chow Yun-fat | 中国香港 | 《英雄本色》《卧虎藏龙》 |
| 刘德华 | Andy Lau | 中国香港 | 《无间道》《天下无贼》 |
| 梁朝伟 | Tony Leung | 中国香港 | 《花样年华》《无间道》 |
| 周星驰 | Stephen Chow | 中国香港 | 《功夫》《少林足球》 |
| 章子怡 | Zhang Ziyi | 中国 | 《卧虎藏龙》《一代宗师》 |
| 巩俐 | Gong Li | 中国 | 《霸王别姬》《满城尽带黄金甲》 |
| 汤唯 | Tang Wei | 中国 | 《色·戒》《晚秋》 |
| 杨紫琼 | Michelle Yeoh | 马来西亚 | 《瞬息全宇宙》《卧虎藏龙》 |
| 刘亦菲 | Liu Yifei | 中国 | 《花木兰》《梦华录》 |
| 古天乐 | Louis Koo | 中国香港 | 《窃听风云》《扫毒》 |
| 木村拓哉 | Takuya Kimura | 日本 | 《悠长假期》《HERO》 |
| 石原里美 | Satomi Ishihara | 日本 | 《校对女孩》《失恋巧克力职人》 |
| 宋慧乔 | Song Hye-kyo | 韩国 | 《太阳的后裔》《黑暗荣耀》 |
| 全智贤 | Jun Ji-hyun | 韩国 | 《来自星星的你》《我的野蛮女友》 |
| 阿米尔·汗 | Aamir Khan | 印度 | 《三傻大闹宝莱坞》《摔跤吧！爸爸》 |
| 沙鲁克·汗 | Shah Rukh Khan | 印度 | 《宝莱坞生死恋》《我的名字叫可汗》 |

## 七、开源边界

代码可发，**素材不可发**：`_subjects/stars/`、`data/subjects.json`、`data/celeb.json` 一律本地保留；
仓库只留空入口 + 本说明，使用者自行抓取填充。
*（内容由AI生成，仅供参考）*
