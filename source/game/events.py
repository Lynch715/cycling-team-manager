"""事件系统：给经营层装上故事。

比赛引擎负责「谁赢」，经营层负责「三年后这支队是什么样」，事件负责
**让这两件事之间发生一些你会记住的东西**。

设计上有三条规矩，每一条都是为了避开这类系统最常见的失败：

1. **每个选项都要有代价。** 如果有一个选项永远是对的，玩家点三次就
   看穿了，之后所有事件都变成无脑点确认。所以下面每条事件的选项都是
   「拿 A 换 B」，没有免费的好处。

2. **后果要能被追溯。** 士气掉了、属性涨了、钱少了——都写进数据，
   玩家能在阵容表里看见。弹一个窗然后什么都不改，是最糟糕的做法。

3. **不确定性放在「结果」上，不放在「是否发生」上。** 玩家做了选择之后
   掷骰子，而不是选项本身随机生效。前者是赌博，后者是欺骗。

每条事件都带一份 `art_brief`——这是给 GPT 出那 20 张事件插画用的简报，
`export_art_briefs.py` 会把它们整理成一个文件。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    INJURY = "伤病"
    MORALE = "士气"
    SPONSOR = "赞助"
    TRANSFER = "转会"
    MEDIA = "媒体"
    YOUTH = "青训"
    EQUIPMENT = "器材"
    INTEGRITY = "操守"


class When(str, Enum):
    """事件在什么时候可能发生。"""

    AFTER_RACE = "赛后"
    OFF_SEASON = "休赛期"
    ANYTIME = "任意"


@dataclass
class Choice:
    """一个选项。

    effects 里的键：
      money        车队资金（万元）
      prestige     车队声望
      morale       当事车手士气（乘数增量）
      team_morale  全队士气
      fatigue      当事车手疲劳（负数是恢复）
      attr         当事车手属性变化，如 {"resilience": 2}
      contract     合同年数变化
      out_days     缺赛天数（折算成疲劳与状态损失）
    risk 是一个 (概率, 额外 effects, 结果文案) 三元组；不填就是必然结果。
    """

    label: str
    effects: dict = field(default_factory=dict)
    outcome: str = ""
    risk: tuple[float, dict, str] | None = None


@dataclass
class Event:
    event_id: str
    title: str
    category: Category
    when: When
    text: str                       # 可用 {rider} {team} {race} 占位
    choices: list[Choice]
    art: str                        # 09_misc/events/<art>.png
    art_brief: str                  # 给 GPT 的出图简报
    weight: float = 1.0             # 相对触发权重


# --------------------------------------------------------------------------
# 二十个事件
# --------------------------------------------------------------------------

EVENTS: list[Event] = [

    Event(
        "EV01", "训练中摔车", Category.INJURY, When.ANYTIME,
        "{rider} 在雨天下坡训练时摔了。锁骨有裂纹，队医说保守治疗六周，"
        "打钢板的话三周能上车，但今年剩下的赛季他都得带着那块金属骑。",
        [
            Choice("保守治疗，等他彻底好",
                   {"out_days": 42, "morale": 0.02, "attr": {"resilience": 1}},
                   "他错过了两场目标赛事，但回来时身体是完整的。"),
            Choice("手术，三周后复出",
                   {"money": -60, "out_days": 21},
                   "钢板打上了。他赶上了赛季后半段。",
                   risk=(0.30, {"attr": {"climbing": -2, "endurance": -2},
                                "morale": -0.06},
                         "复出后他一直说肩膀发僵，成绩明显下滑。")),
        ],
        "event-01",
        "雨中的公路弯道，一名三头身车手倒在湿滑的路面上，自行车翻在一旁，"
        "远处队车正在靠边停下。冷灰蓝色调，雨丝与地面反光，气氛压抑但不血腥。"),

    Event(
        "EV02", "队长与副将的冲突", Category.MORALE, When.AFTER_RACE,
        "{race} 的最后一座山上，{rider} 没有等待掉队的队长，自己冲了出去，"
        "拿到了赛段第四。更衣室里两个人吵了起来，其他人都不说话。",
        [
            Choice("公开支持队长，罚 {rider}",
                   {"morale": -0.10, "team_morale": 0.03, "prestige": 1},
                   "队内秩序保住了，但那个年轻人一整周没跟任何人说话。"),
            Choice("公开表扬 {rider} 的进攻性",
                   {"morale": 0.10, "team_morale": -0.04},
                   "他从此更敢打了，但队长开始怀疑自己在这支队里的位置。"),
            Choice("私下调解，对外说无事发生",
                   {"team_morale": -0.01},
                   "风波过去了。三个月后媒体挖出了这件事，你被问了很多次。",
                   risk=(0.35, {"prestige": -2}, "处理不当的传闻还是传了出去。")),
        ],
        "event-02",
        "更衣室内两名三头身车手隔着一排挂满队服的衣架对视，其他队友低头擦车、"
        "系鞋带，刻意回避。暖色顶灯，长影子，紧张的沉默感。"),

    Event(
        "EV03", "主赞助商要看到回报", Category.SPONSOR, When.AFTER_RACE,
        "主赞助商的市场总监来到车队大巴，措辞客气但意思很清楚：这个赛季的"
        "曝光量低于合同预期，董事会在问值不值。",
        [
            Choice("承诺主攻下一场大赛，全队围绕一个人",
                   {"prestige": 1, "team_morale": -0.03},
                   "你把所有筹码押在一场比赛上。队里其他人明白自己今年没戏了。"),
            Choice("提出加码社交媒体和纪录片",
                   {"money": -80, "prestige": 2},
                   "钱花出去了，赞助商暂时被安抚。"),
            Choice("坦白说今年阵容就是这个水平",
                   {"money": -150, "team_morale": 0.04},
                   "他们削了预算，但队员们知道你没有拿他们当挡箭牌。"),
        ],
        "event-03",
        "车队大巴内部，一名西装革履的赞助商代表和穿队服的经理隔着窄桌对坐，"
        "桌上摊着数据报表和一杯没动过的咖啡。窗外是模糊的赛道景象。"),

    Event(
        "EV04", "豪门来挖人", Category.TRANSFER, When.OFF_SEASON,
        "一支世巡赛顶级队开出了 {rider} 现在薪水两倍半的报价。他今年"
        "进步很快，合同还剩一年。经纪人已经在打电话了。",
        [
            Choice("提前续约，给他涨到接近对方的价",
                   {"money": -220, "contract": 2, "morale": 0.08},
                   "他留下了，但你的薪资表被这一笔撑得很难看。"),
            Choice("放他走，拿一笔转会费",
                   {"money": 180, "team_morale": -0.05},
                   "队里失去了最有希望的人，更衣室的气氛变了。"),
            Choice("什么都不做，赌他自己想留",
                   {},
                   "你什么也没说。",
                   risk=(0.65, {"team_morale": -0.08},
                         "赛季结束他自由身走了，你一分钱没拿到。")),
        ],
        "event-04",
        "转会市场大厅，一名三头身车手站在两队展台中间，左右各有一名工作人员"
        "向他伸手示意。背景是模糊的人群与队徽墙。构图强调「被拉扯」的张力。"),

    Event(
        "EV05", "发布会上的尖锐提问", Category.MEDIA, When.AFTER_RACE,
        "记者当着所有人的面问：你们队这两年一场大赛都没赢过，是不是"
        "该换个经理了？",
        [
            Choice("承认成绩不好，说明重建计划",
                   {"prestige": 1, "team_morale": 0.02},
                   "第二天的报道意外地正面。"),
            Choice("反击，列举对手的预算是你的三倍",
                   {"prestige": -1, "team_morale": 0.06},
                   "队员们在网上转发了这段视频。同行觉得你输不起。"),
            Choice("拒绝回答，结束发布会",
                   {"prestige": -3},
                   "这个画面被反复播放了一个星期。"),
        ],
        "event-05",
        "新闻发布会厅，三头身经理坐在铺着赞助商背景板的长桌后，面前一排话筒，"
        "台下举着录音笔的记者剪影。冷白灯光，正面构图，压迫感来自密集的话筒。"),

    Event(
        "EV06", "青训营出了个苗子", Category.YOUTH, When.OFF_SEASON,
        "青训教练拿来一份数据：一个 19 岁的孩子，功率体重比已经接近队里"
        "第三好的爬坡手，但他从没跑过职业赛，性格也非常内向。",
        [
            Choice("直接签进一队，让他在大赛里学",
                   {"money": -40},
                   "他上了大舞台。",
                   risk=(0.45, {"team_morale": -0.02},
                         "节奏太快，他前半个赛季被打得没脾气，信心受损。")),
            Choice("再练一年，跟着二队跑小比赛",
                   {"money": -15},
                   "稳妥的选择。一年后他更强了，但也被更多球队注意到了。"),
            Choice("现在卖掉他的合同权益换现金",
                   {"money": 120, "prestige": -1},
                   "三年后你会在电视上看见他穿着别人的队服站上领奖台。"),
        ],
        "event-06",
        "青训基地的室内训练房，一名瘦小的三头身少年在功率车上低头踩踏，"
        "旁边教练举着平板看数据。窗外是傍晚的天光。安静、专注、有希望感。"),

    Event(
        "EV07", "新车架的风洞数据", Category.EQUIPMENT, When.OFF_SEASON,
        "器材商拿出了新款车架的风洞报告：在 45 km/h 下比现款省 6 瓦。"
        "但要换全队的车，而且新车架偏硬，几个老队员试骑后抱怨腰疼。",
        [
            Choice("全队换新",
                   {"money": -180, "team_morale": -0.03},
                   "计时赛成绩肉眼可见地好了，老队员们花了两个月适应。"),
            Choice("只给计时赛和冲刺手换",
                   {"money": -70},
                   "折中方案。队里出现了「一等车」和「二等车」的说法。"),
            Choice("不换，把钱留着签人",
                   {"money": 0, "team_morale": 0.02},
                   "器材商有点失望，但队员们喜欢你听他们的话。"),
        ],
        "event-07",
        "机械维修车间，两台三头身比例的公路车并排架在维修台上，一新一旧，"
        "机械师拿着扭力扳手在对比。工具墙、零件盒、冷白工作灯。"),

    Event(
        "EV08", "药检风波", Category.INTEGRITY, When.ANYTIME,
        "{rider} 的一次赛外药检出现了异常读数。他坚称是感冒药的问题，"
        "B 瓶检测结果还要三周才出来。媒体已经收到风声。",
        [
            Choice("立即停赛，等待结果",
                   {"out_days": 21, "prestige": 1, "morale": -0.08},
                   "你被称赞处理得体。他觉得你没有信任他。"),
            Choice("公开力挺，让他继续比赛",
                   {"prestige": -1, "morale": 0.10},
                   "他在下一场比赛拼命。",
                   risk=(0.25, {"prestige": -12, "money": -200,
                                "team_morale": -0.12},
                         "B 瓶结果呈阳性。赞助商当天下午就发了解约函。")),
        ],
        "event-08",
        "医疗理疗室一角，桌上一个贴着封条的检测样本瓶被聚光照亮，"
        "背景里一名三头身车手坐在检查床边低头。冷绿色调，克制，不猎奇。"),

    Event(
        "EV09", "老将的最后一年", Category.MORALE, When.OFF_SEASON,
        "{rider} 今年 35 岁了，数据一年不如一年。他来找你，说想再骑一个赛季，"
        "在家乡那场比赛结束职业生涯。",
        [
            Choice("给他一份象征性的合同",
                   {"money": -30, "contract": 1, "team_morale": 0.07},
                   "全队都看见了你怎么对待一个为队伍拼过十年的人。"),
            Choice("请他转任教练",
                   {"team_morale": 0.04, "prestige": 1},
                   "他接受了，虽然眼里有掩不住的失落。年轻队员从他身上学到很多。"),
            Choice("直接告诉他不续约",
                   {"money": 0, "team_morale": -0.09},
                   "更衣室安静了很久。年轻队员开始想自己老了会是什么下场。"),
        ],
        "event-09",
        "荣誉陈列室，一名年长的三头身车手独自站在自己当年夺冠的照片前，"
        "手里拎着头盔。暖黄射灯，长影，安静的告别气息。"),

    Event(
        "EV10", "赛前突降暴雨", Category.EQUIPMENT, When.AFTER_RACE,
        "明天的石板路赛段预报有暴雨。机械师问要不要换宽胎降胎压——"
        "更安全，但会慢一点。",
        [
            Choice("换，安全第一",
                   {"team_morale": 0.03},
                   "全队完赛。你们的名次都不算好。"),
            Choice("不换，赌雨下不大",
                   {},
                   "机械师照原方案准备了。",
                   risk=(0.40, {"out_days": 10, "team_morale": -0.06},
                         "雨确实下大了。有人在石板路上摔了。")),
        ],
        "event-10",
        "雨中的石板路赛段起点，三头身车手们裹着雨衣在队车旁等待，"
        "机械师蹲着调胎压。灰暗天空、水洼反光、路边观众撑着伞。"),

    Event(
        "EV11", "队内出现小圈子", Category.MORALE, When.ANYTIME,
        "几个同国籍的队员总是单独行动，训练分组、房间分配、饭桌都自成一派。"
        "其他人开始有意见。",
        [
            Choice("强制打散，重新分组",
                   {"team_morale": -0.02},
                   "短期内所有人都不舒服，但小圈子散了。"),
            Choice("找带头的人谈一次",
                   {},
                   "他表示理解。",
                   risk=(0.50, {"team_morale": -0.05},
                         "两个月后情况原样，其他人认为你没解决问题。")),
            Choice("不管，成绩好就行",
                   {"team_morale": -0.06},
                   "队伍变成了两支队伍。"),
        ],
        "event-11",
        "队车大巴内部，前半部分几名三头身车手挤在一起说笑，后半部分几个人"
        "各自戴着耳机看窗外，中间隔着一排空座。构图上的分割感是重点。"),

    Event(
        "EV12", "新赞助商找上门", Category.SPONSOR, When.OFF_SEASON,
        "一家新兴品牌想成为副赞助，出价相当可观。但他们要求队服主色"
        "改成他们的荧光橙，并且队名里要加上他们的名字。",
        [
            Choice("接受，换队服",
                   {"money": 260, "prestige": -1, "team_morale": -0.02},
                   "钱到账了。老球迷在网上骂了半个月。"),
            Choice("谈判，只加名字不改配色",
                   {"money": 120},
                   "对方接受了一半，出价也砍了一半。"),
            Choice("拒绝，保住队伍传统",
                   {"prestige": 2, "team_morale": 0.03},
                   "预算依然紧张，但你保住了这支队的样子。"),
        ],
        "event-12",
        "赞助商洽谈室，桌上摊着两套三头身队服设计稿：一套是传统深蓝，"
        "一套是刺眼的荧光橙。两只手分别指着不同的那件。"),

    Event(
        "EV13", "车手要求换角色", Category.MORALE, When.OFF_SEASON,
        "{rider} 说他不想再当工兵了。他觉得自己有能力争总成绩，"
        "只是从来没有得到过机会。",
        [
            Choice("给他一次机会，下场比赛围绕他",
                   {"morale": 0.12, "team_morale": -0.03},
                   "他得到了机会。全队的资源都压在一个未经证明的人身上。"),
            Choice("如实告诉他数据上的差距",
                   {"morale": -0.08, "attr": {"resilience": 1}},
                   "他消化了三天，然后回到了自己的位置上，更沉默也更可靠。"),
            Choice("答应他，但只在小比赛里",
                   {"morale": 0.05},
                   "折中方案。他知道这是安慰，但也确实是机会。"),
        ],
        "event-13",
        "经理办公室，一名三头身车手站在办公桌前，双手撑在桌沿俯身说话，"
        "经理坐着抬头看他。窗外是训练场。对峙但不敌对的姿态。"),

    Event(
        "EV14", "训练营地被取消", Category.EQUIPMENT, When.OFF_SEASON,
        "原定的高原训练营地临时被主办方收回。距离赛季开始只剩五周，"
        "现在重新找地方费用要翻倍。",
        [
            Choice("加钱，照原计划高原集训",
                   {"money": -140, "attr": {"endurance": 2}},
                   "全队的有氧基础打扎实了。账面很难看。"),
            Choice("改成平原集训",
                   {"money": -50},
                   "省了钱。赛季前两个月队伍状态明显偏软。"),
            Choice("取消集训，让队员自行准备",
                   {"team_morale": -0.05},
                   "有人练得很好，有人胖了三公斤。"),
        ],
        "event-14",
        "室内训练房与窗外高原雪山形成对比，几名三头身车手在功率车上训练，"
        "教练看着挂在墙上被划掉的日程表。清冷的高原光线。"),

    Event(
        "EV15", "对手来打招呼", Category.TRANSFER, When.OFF_SEASON,
        "一支中游队的经理私下问你：愿不愿意用 {rider} 换他们那个"
        "已经三十岁但计时赛非常强的老将，外加一点现金。",
        [
            Choice("换",
                   {"money": 60, "team_morale": -0.02},
                   "你用未来换了当下。"),
            Choice("不换",
                   {},
                   "你婉拒了。对方点点头，说理解。"),
        ],
        "event-16",
        "转会市场大厅的角落，两名三头身经理站着低声交谈，各自手里拿着"
        "一份文件夹，背景是熙攘的人群。半私密的构图。"),

    Event(
        "EV16", "队医的警告", Category.INJURY, When.ANYTIME,
        "队医找到你：{rider} 的血液指标连续三次偏低，他建议强制休息两周。"
        "但下周就是本赛季最重要的一场比赛。",
        [
            Choice("听队医的，强制休息",
                   {"out_days": 14, "fatigue": -0.4, "morale": -0.04},
                   "他错过了目标赛事，但身体恢复得很好。"),
            Choice("让他上，赛后再休",
                   {"fatigue": 0.25},
                   "他上场了。",
                   risk=(0.40, {"out_days": 35, "attr": {"recovery": -3}},
                         "他在比赛中彻底垮掉，之后一个多月都没缓过来。")),
        ],
        "event-15",
        "医疗理疗室，队医举着一张血检报告单，三头身车手坐在检查床上"
        "抬头看着他。冷白灯光，医疗设备，克制的临床感。"),

    Event(
        "EV17", "球迷的信", Category.MEDIA, When.ANYTIME,
        "一个患病的少年球迷写信来，说 {rider} 是他坚持治疗的理由，"
        "想在下一场比赛的起点见他一面。安排这件事要占用赛前准备时间。",
        [
            Choice("安排见面",
                   {"morale": 0.06, "team_morale": 0.05, "prestige": 2,
                    "fatigue": 0.03},
                   "那张照片传遍了整个自行车圈。全队那天状态都不太一样。"),
            Choice("寄一件签名队服，不见面",
                   {"prestige": 1},
                   "得体的处理。没有人会记住这件事。"),
        ],
        "event-17",
        "赛段起点的护栏边，一名三头身车手蹲下身与一个坐轮椅的小孩击掌，"
        "周围观众举着手机。晨光，暖色，克制的温情，不煽情。"),

    Event(
        "EV18", "领骑衫的分配", Category.MORALE, When.AFTER_RACE,
        "{race} 打完第三个赛段，队里两个人同分并列爬坡积分榜首。"
        "剩下的赛段只能有一个人去争。",
        [
            Choice("给资历更老的那个",
                   {"team_morale": 0.01, "morale": -0.05},
                   "秩序为先。年轻的那个把不满咽了下去。"),
            Choice("给状态更好的那个",
                   {"morale": 0.07, "team_morale": -0.03},
                   "成绩导向。老队员当着所有人说了句「随便吧」。"),
            Choice("让他们自己在路上决出来",
                   {"team_morale": -0.02},
                   "两个人互相消耗了一整个赛段。",
                   risk=(0.55, {"morale": -0.06},
                         "结果衫被别队拿走了，队内谁也不服谁。")),
        ],
        "event-18",
        "颁奖台侧后方，两名同队的三头身车手站在台阶下，一件爬坡圆点衫"
        "被工作人员捧在中间。观众席虚化。"),

    Event(
        "EV19", "资金链紧张", Category.SPONSOR, When.OFF_SEASON,
        "会计给了你一个数字：按目前的薪资和运营，现金流在赛季中段会断。"
        "必须砍掉一块。",
        [
            Choice("裁掉两名低薪工兵",
                   {"money": 90, "team_morale": -0.10},
                   "账平了。剩下的人都在想下一个会不会是自己。"),
            Choice("削减器材和差旅预算",
                   {"money": 110, "attr": {"positioning": -1},
                    "team_morale": -0.03},
                   "全队坐廉价航班、用去年的轮组。成绩会有代价。"),
            Choice("找短期贷款撑过去",
                   {"money": 0, "prestige": -2},
                   "撑住了。这笔债明年还在。"),
        ],
        "event-19",
        "经理办公室深夜，台灯下摊满账单与报表，一个三头身经理背影坐在桌前，"
        "窗外全黑。冷色调，孤独感。"),

    Event(
        "EV20", "夺冠之后", Category.MEDIA, When.AFTER_RACE,
        "{rider} 拿下了 {race}。这是队史上最重要的一场胜利。"
        "庆功、媒体、赞助商活动排满了接下来两周。",
        [
            Choice("全部接下来，把曝光吃干净",
                   {"money": 150, "prestige": 4, "fatigue": 0.3,
                    "team_morale": 0.05},
                   "赞助商非常满意。他在下一场比赛明显没恢复过来。"),
            Choice("只做必要的，让他回家休息",
                   {"prestige": 1, "fatigue": -0.2, "morale": 0.08},
                   "他保住了状态。市场部有点意见。"),
            Choice("把镜头让给全队",
                   {"prestige": 2, "team_morale": 0.10},
                   "工兵们第一次被叫到名字。这支队伍从此不太一样了。"),
        ],
        "event-20",
        "终点颁奖台，三头身车手高举双臂站在最高一级，彩带与闪光灯，"
        "台下队友们在欢呼。饱和暖色，最强烈的正向情绪，全套资源里最"
        "有海报感的一张。"),
]

BY_ID = {e.event_id: e for e in EVENTS}


# --------------------------------------------------------------------------
# 触发与结算
# --------------------------------------------------------------------------

def pick_event(when: When, rng: random.Random,
               exclude: set[str] | None = None) -> Event | None:
    """按时机和权重抽一个事件。exclude 用来避免同一赛季重复。"""
    pool = [e for e in EVENTS
            if e.when in (when, When.ANYTIME)
            and e.event_id not in (exclude or set())]
    if not pool:
        return None
    return rng.choices(pool, weights=[e.weight for e in pool], k=1)[0]


def fill(text: str, rider=None, team=None, race: str = "") -> str:
    return (text.replace("{rider}", getattr(rider, "name", "某位车手"))
                .replace("{team}", getattr(team, "name", "车队"))
                .replace("{race}", race or "这场比赛"))


@dataclass
class Resolution:
    """一次事件结算的结果，给界面展示用。"""

    event_id: str
    choice: str
    outcome: str
    changes: list[str] = field(default_factory=list)


def apply(event: Event, choice_index: int, world, team, rider,
          rng: random.Random) -> Resolution:
    """把一个选项的后果真正写进世界数据。

    这里是整套系统能不能站住的地方：如果只弹窗不改数据，玩家两小时后
    就会开始无脑点第一个选项。
    """
    choice = event.choices[choice_index]
    effects = dict(choice.effects)
    outcome = fill(choice.outcome, rider, team)

    if choice.risk:
        chance, extra, bad_text = choice.risk
        if rng.random() < chance:
            for k, v in extra.items():
                if isinstance(v, dict):
                    effects.setdefault(k, {})
                    for kk, vv in v.items():
                        effects[k][kk] = effects[k].get(kk, 0) + vv
                else:
                    effects[k] = effects.get(k, 0) + v
            outcome = fill(bad_text, rider, team)

    changes: list[str] = []

    if "money" in effects and team is not None:
        team.budget = max(50, team.budget + effects["money"])
        sign = "+" if effects["money"] >= 0 else ""
        changes.append(f"车队资金 {sign}{effects['money']} 万")

    if "prestige" in effects and team is not None:
        team.prestige = max(5, min(99, team.prestige + effects["prestige"]))
        changes.append(f"车队声望 {effects['prestige']:+d}")

    if "morale" in effects and rider is not None:
        rider.morale = round(max(0.80, min(1.20,
                                           rider.morale + effects["morale"])), 3)
        changes.append(f"{rider.name} 士气 {effects['morale']:+.2f}")

    if "team_morale" in effects and team is not None:
        for r in world.roster(team.team_id):
            r.morale = round(max(0.80, min(1.20,
                                           r.morale + effects["team_morale"])), 3)
        changes.append(f"全队士气 {effects['team_morale']:+.2f}")

    if "fatigue" in effects and rider is not None:
        rider.fatigue = round(max(0.0, min(1.0,
                                           rider.fatigue + effects["fatigue"])), 3)
        changes.append(f"{rider.name} 疲劳 {effects['fatigue']:+.2f}")

    if "attr" in effects and rider is not None:
        for key, delta in effects["attr"].items():
            cur = getattr(rider.attributes, key)
            setattr(rider.attributes, key, max(1, min(99, cur + delta)))
            changes.append(f"{rider.name} {key} {delta:+d}")

    if "contract" in effects and rider is not None:
        rider.contract_years += effects["contract"]
        changes.append(f"{rider.name} 合同 {effects['contract']:+d} 年")

    if "out_days" in effects and rider is not None:
        # 缺赛折算成疲劳恢复 + 状态下滑：休息能恢复身体，但会掉比赛感觉
        days = effects["out_days"]
        rider.fatigue = round(max(0.0, rider.fatigue - days * 0.010), 3)
        rider.form = round(max(0.85, rider.form - days * 0.0035), 3)
        changes.append(f"{rider.name} 缺赛 {days} 天（状态下滑）")

    return Resolution(event.event_id, choice.label, outcome, changes)
